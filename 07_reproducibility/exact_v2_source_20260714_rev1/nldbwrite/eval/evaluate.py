import argparse
import csv
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from nldbwrite.common import find_db_path, iter_jsonl, load_json, quote_ident, read_id_file, save_json, sha256_file, write_jsonl
from nldbwrite.sql.safety import is_safe_sql


def memory_copy(db_path):
    src = sqlite3.connect(db_path)
    conn = sqlite3.connect(':memory:')
    try:
        src.backup(conn)
    finally:
        src.close()
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = MEMORY')
    return conn


def set_write_tracker(conn, write_tables):
    if write_tables is None:
        return
    write_actions = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}

    def track_writes(action, table, _column, _database, _trigger):
        if action in write_actions and table and not str(table).startswith('sqlite_'):
            write_tables.add(str(table))
        return sqlite3.SQLITE_OK

    conn.set_authorizer(track_writes)


def execute_sqls(conn, sqls, write_tables=None):
    executed = 0
    set_write_tracker(conn, write_tables)
    try:
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute('PRAGMA journal_mode = MEMORY')
        ok, err, executed = execute_sqls_no_commit(conn, sqls)
        if not ok:
            conn.rollback()
            return ok, err, executed
        conn.commit()
        return True, None, executed
    except Exception as e:
        conn.rollback()
        return False, str(e), executed
    finally:
        if write_tables is not None:
            conn.set_authorizer(None)


def execute_sqls_no_commit(conn, sqls):
    cur = conn.cursor()
    executed = 0
    for sql in sqls:
        ok, reason = is_safe_sql(sql)
        if not ok:
            return False, f'unsafe_sql: {reason}', executed
        cur.execute(sql)
        executed += 1
    return True, None, executed


def apply_savepoint(conn, name, sqls, write_tables=None):
    conn.execute(f'SAVEPOINT {quote_ident(name)}')
    set_write_tracker(conn, write_tables)
    try:
        ok, err, executed = execute_sqls_no_commit(conn, sqls)
        if not ok:
            conn.execute(f'ROLLBACK TO {quote_ident(name)}')
            conn.execute(f'RELEASE {quote_ident(name)}')
        return ok, err, executed
    except Exception as e:
        conn.execute(f'ROLLBACK TO {quote_ident(name)}')
        conn.execute(f'RELEASE {quote_ident(name)}')
        return False, str(e), 0
    finally:
        if write_tables is not None:
            conn.set_authorizer(None)


def rollback_savepoint(conn, name):
    conn.execute(f'ROLLBACK TO {quote_ident(name)}')
    conn.execute(f'RELEASE {quote_ident(name)}')


def list_tables(conn):
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]


def table_info(conn, table):
    return conn.execute(f'PRAGMA table_info({quote_ident(table)})').fetchall()


def ignored_columns(conn, table, gold_columns: set[str]):
    cols = table_info(conn, table)
    pk_cols = [r for r in cols if r[5]]
    ignored = set()
    if len(pk_cols) == 1:
        _, name, col_type, _, _, _ = pk_cols[0]
        if 'INT' in (col_type or '').upper() and f'{table}.{name}' not in gold_columns:
            ignored.add(name)
    return ignored


def canonical_value(value, storage_type: str) -> dict[str, Any]:
    storage_type = str(storage_type or 'null')
    if value is None:
        return {'type': 'null', 'value': None}
    if isinstance(value, bytes):
        return {'type': storage_type, 'value': value.hex()}
    return {'type': storage_type, 'value': value}


def canonical_rows(conn, table, gold_columns: set[str]) -> list[dict[str, dict[str, Any]]]:
    base_cur = conn.execute(f'SELECT * FROM {quote_ident(table)}')
    cols = [d[0] for d in base_cur.description]
    ignored = ignored_columns(conn, table, gold_columns)
    kept = [c for c in cols if c not in ignored]
    select_parts = []
    for col in kept:
        quoted = quote_ident(col)
        select_parts.append(quoted)
        select_parts.append(f'typeof({quoted}) AS {quote_ident("__typeof_" + col)}')
    cur = conn.execute(f'SELECT {", ".join(select_parts)} FROM {quote_ident(table)}') if kept else base_cur
    rows = []
    for row in cur.fetchall():
        item = {}
        for idx, col in enumerate(kept):
            item[col] = canonical_value(row[idx * 2], row[idx * 2 + 1])
        rows.append(item)
    rows.sort(key=lambda r: json.dumps(r, ensure_ascii=False, sort_keys=True))
    return rows


def table_hash(conn, table, gold_columns: set[str]) -> str:
    h = hashlib.sha256()
    for row in canonical_rows(conn, table, gold_columns):
        h.update(json.dumps(row, ensure_ascii=False, sort_keys=True).encode('utf-8'))
        h.update(b'\n')
    return h.hexdigest()


def database_hashes(conn, gold_columns: set[str]) -> dict[str, str]:
    return {t: table_hash(conn, t, gold_columns) for t in list_tables(conn)}


def changed_tables(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {t for t in set(before) | set(after) if before.get(t) != after.get(t)}


def dump_state(conn, tables, gold_columns: set[str]):
    existing = set(list_tables(conn))
    return {t: canonical_rows(conn, t, gold_columns) for t in sorted(tables) if t in existing}


_IDENT = r'''(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][\w$]*)'''
_TARGET_PATTERNS = [
    re.compile(rf'\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+({_IDENT})', re.I | re.S),
    re.compile(rf'\bREPLACE\s+(?:OR\s+\w+\s+)?INTO\s+({_IDENT})', re.I | re.S),
    re.compile(rf'\bUPDATE\s+(?:OR\s+\w+\s+)?({_IDENT})', re.I | re.S),
    re.compile(rf'\bDELETE\s+FROM\s+({_IDENT})', re.I | re.S),
]
_LITERAL_CONN = sqlite3.connect(':memory:')


def unquote_ident(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in ('"', '`') and value[-1] == value[0]:
        return value[1:-1].replace(value[0] * 2, value[0])
    if len(value) >= 2 and value[0] == '[' and value[-1] == ']':
        return value[1:-1]
    return value


def extract_target_tables(sqls) -> set[str]:
    tables = set()
    for sql in sqls or []:
        for pattern in _TARGET_PATTERNS:
            match = pattern.search(sql)
            if match:
                tables.add(unquote_ident(match.group(1)))
                break
    return tables


def split_sql_csv(text: str) -> list[str]:
    out = []
    buf = []
    quote = None
    bracket = False
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if bracket:
            buf.append(ch)
            if ch == ']':
                bracket = False
            i += 1
            continue
        if quote:
            buf.append(ch)
            if ch == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    buf.append(text[i + 1])
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in ('"', '`', "'"):
            quote = ch
            buf.append(ch)
        elif ch == '[':
            bracket = True
            buf.append(ch)
        elif ch == '(':
            depth += 1
            buf.append(ch)
        elif ch == ')':
            depth -= 1
            buf.append(ch)
        elif ch == ',' and depth == 0:
            item = ''.join(buf).strip()
            if item:
                out.append(item)
            buf = []
        else:
            buf.append(ch)
        i += 1
    item = ''.join(buf).strip()
    if item:
        out.append(item)
    return out


def extract_parenthesized(text: str, start: int) -> tuple[str, int] | None:
    quote = None
    bracket = False
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if bracket:
            if ch == ']':
                bracket = False
            i += 1
            continue
        if quote:
            if ch == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in ('"', '`', "'"):
            quote = ch
        elif ch == '[':
            bracket = True
        elif ch == '(':
            depth += 1
            if depth == 1:
                content_start = i + 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return text[content_start:i], i + 1
        i += 1
    return None


def find_top_level_keyword(text: str, keyword: str, start: int = 0) -> int:
    quote = None
    bracket = False
    depth = 0
    key = keyword.lower()
    i = start
    while i < len(text):
        ch = text[i]
        if bracket:
            if ch == ']':
                bracket = False
            i += 1
            continue
        if quote:
            if ch == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in ('"', '`', "'"):
            quote = ch
        elif ch == '[':
            bracket = True
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        elif depth == 0 and text[i:i + len(keyword)].lower() == key:
            left_ok = i == 0 or not (text[i - 1].isalnum() or text[i - 1] == '_')
            right = i + len(keyword)
            right_ok = right >= len(text) or not (text[right].isalnum() or text[right] == '_')
            if left_ok and right_ok:
                return i
        i += 1
    return -1


def literal_value(expr: str):
    expr = expr.strip().rstrip(';')
    if not expr:
        return ''
    if expr.upper() == 'NULL':
        return None
    try:
        return _LITERAL_CONN.execute(f'SELECT {expr}').fetchone()[0]
    except sqlite3.Error:
        if len(expr) >= 2 and expr[0] == "'" and expr[-1] == "'":
            return expr[1:-1].replace("''", "'")
        return expr


def parse_insert_records(sql: str) -> list[dict[str, Any]]:
    match = _TARGET_PATTERNS[0].search(sql) or _TARGET_PATTERNS[1].search(sql)
    if not match:
        return []
    table = unquote_ident(match.group(1))
    col_start = sql.find('(', match.end())
    if col_start < 0:
        return []
    col_group = extract_parenthesized(sql, col_start)
    if not col_group:
        return []
    column_text, after_cols = col_group
    columns = [unquote_ident(x) for x in split_sql_csv(column_text)]
    values_pos = find_top_level_keyword(sql, 'values', after_cols)
    if values_pos < 0:
        return []
    pos = values_pos + len('values')
    records = []
    while pos < len(sql):
        while pos < len(sql) and sql[pos].isspace():
            pos += 1
        if pos < len(sql) and sql[pos] == ',':
            pos += 1
            continue
        if pos >= len(sql) or sql[pos] != '(':
            break
        value_group = extract_parenthesized(sql, pos)
        if not value_group:
            break
        value_text, pos = value_group
        values = split_sql_csv(value_text)
        if len(values) == len(columns):
            records.append({
                'table': table,
                'values': {col: literal_value(value) for col, value in zip(columns, values)},
            })
    return records


def record_probes(sqls) -> tuple[list[dict[str, Any]], set[str]]:
    records = []
    unparsed_tables = set()
    for sql in sqls or []:
        targets = extract_target_tables([sql])
        if not targets:
            continue
        parsed = parse_insert_records(sql)
        if parsed:
            records.extend(parsed)
        else:
            unparsed_tables.update(targets)
    return records, unparsed_tables


def probe_key(record: dict[str, Any]) -> str:
    values = [(k, None if v is None else str(v)) for k, v in sorted(record.get('values', {}).items())]
    return json.dumps([record.get('table'), values], ensure_ascii=False, sort_keys=True)


def count_matching(conn, record: dict[str, Any]) -> int | None:
    table = record.get('table')
    values = record.get('values') or {}
    if not table or not values:
        return None
    clauses = []
    params = []
    for col, value in values.items():
        if value is None:
            clauses.append(f'{quote_ident(col)} IS NULL')
        else:
            clauses.append(f'{quote_ident(col)} = ?')
            params.append(value)
    sql = f'SELECT COUNT(*) FROM {quote_ident(table)} WHERE ' + ' AND '.join(clauses)
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    except sqlite3.Error:
        return None


def probe_counts(conn, records: list[dict[str, Any]]) -> dict[str, int | None]:
    return {probe_key(record): count_matching(conn, record) for record in records}


def classify_error(msg):
    if not msg:
        return None
    m = msg.lower()
    if 'unsafe_sql' in m:
        return 'unsafe_sql'
    if 'timeout' in m or 'interrupted' in m:
        return 'timeout'
    if 'syntax' in m or 'near "' in m:
        return 'syntax_error'
    if 'no such table' in m or 'no such column' in m:
        return 'schema_error'
    if any(x in m for x in ['foreign key', 'constraint', 'not null', 'unique']):
        return 'constraint_error'
    return 'execution_error'


def state_error_subtype(pred_sqls, gold_sqls, sample, pred_changed, gold_changed):
    if set(pred_changed) != set(gold_changed):
        return 'wrong_table'
    pred_records, _ = record_probes(pred_sqls)
    gold_records, _ = record_probes(gold_sqls)
    if len(pred_records) < len(gold_records):
        return 'missing_rows'
    if len(pred_records) > len(gold_records):
        return 'extra_rows'
    pred_columns = {(r.get('table'), c) for r in pred_records for c in (r.get('values') or {})}
    gold_columns = {(r.get('table'), c) for r in gold_records for c in (r.get('values') or {})}
    if gold_columns - pred_columns:
        return 'missing_columns'
    if pred_columns - gold_columns:
        return 'extra_columns'
    if str(sample.get('operation_type', '')).lower() in {'upsert', 'update', 'replace'}:
        pred_text = '\n'.join(pred_sqls or []).upper()
        if 'DO UPDATE' not in pred_text and not pred_text.lstrip().startswith('UPDATE'):
            return 'wrong_upsert_behavior'
    return 'wrong_value'


def compare_post_state(
    original_conn,
    pred_conn,
    gold_conn,
    sample,
    pred_sqls=None,
    strict_all_tables=False,
    pred_write_tables=None,
    gold_write_tables=None,
) -> dict[str, Any]:
    gold_columns = set(sample.get('gold_columns') or [])
    if strict_all_tables:
        if pred_write_tables is not None and gold_write_tables is not None:
            pred_changed = set(pred_write_tables)
            gold_changed = set(gold_write_tables)
            compare_tables = pred_changed | gold_changed | set(sample.get('gold_tables') or [])
        else:
            before = database_hashes(original_conn, gold_columns)
            pred_hashes = database_hashes(pred_conn, gold_columns)
            gold_hashes = database_hashes(gold_conn, gold_columns)
            pred_changed = changed_tables(before, pred_hashes)
            gold_changed = changed_tables(before, gold_hashes)
            compare_tables = pred_changed | gold_changed | set(sample.get('gold_tables') or [])
        pred_state = dump_state(pred_conn, compare_tables, gold_columns)
        gold_state = dump_state(gold_conn, compare_tables, gold_columns)
        correct = pred_state == gold_state
    else:
        pred_changed = extract_target_tables(pred_sqls)
        gold_changed = extract_target_tables(sample.get('gold_sql', []))
        compare_tables = pred_changed | gold_changed | set(sample.get('gold_tables') or [])
        pred_records, pred_unparsed = record_probes(pred_sqls)
        gold_records, gold_unparsed = record_probes(sample.get('gold_sql', []))
        records_by_key = {probe_key(r): r for r in pred_records + gold_records}
        mismatches = []
        for key, record in records_by_key.items():
            pred_count = count_matching(pred_conn, record)
            gold_count = count_matching(gold_conn, record)
            if pred_count != gold_count:
                mismatches.append({'record': key, 'pred_count': pred_count, 'gold_count': gold_count})
        fallback_tables = pred_unparsed | gold_unparsed
        if fallback_tables:
            pred_state = dump_state(pred_conn, fallback_tables, gold_columns)
            gold_state = dump_state(gold_conn, fallback_tables, gold_columns)
            fallback_correct = pred_state == gold_state
        else:
            fallback_correct = True
        correct = not mismatches and fallback_correct
    return {
        'correct': correct,
        'affected_tables': sorted(compare_tables),
        'pred_changed_tables': sorted(pred_changed),
        'gold_changed_tables': sorted(gold_changed),
    }


def compare_probe_counts(pred_sqls, gold_sqls, pred_counts, gold_counts, pred_records, gold_records, sample) -> dict[str, Any]:
    pred_changed = extract_target_tables(pred_sqls)
    gold_changed = extract_target_tables(gold_sqls)
    compare_tables = pred_changed | gold_changed | set(sample.get('gold_tables') or [])
    record_keys = set(probe_key(r) for r in pred_records + gold_records)
    mismatches = [key for key in record_keys if pred_counts.get(key) != gold_counts.get(key)]
    return {
        'correct': not mismatches,
        'affected_tables': sorted(compare_tables),
        'pred_changed_tables': sorted(pred_changed),
        'gold_changed_tables': sorted(gold_changed),
    }


def evaluate_state_pair(db_path, pred_sqls, gold_sqls, sample):
    """Evaluate gold and prediction in one database using isolated savepoints.

    SQLite's authorizer records every table written by direct statements and
    triggers. Tables outside the union of those write sets remain identical to
    the original database, so comparing that union is a full-state comparison
    without repeatedly scanning unrelated large tables.
    """
    conn = memory_copy(db_path)
    gold_columns = set(sample.get('gold_columns') or [])
    declared_gold_tables = set(sample.get('gold_tables') or [])
    pred_records, pred_unparsed = record_probes(pred_sqls)
    gold_records, gold_unparsed = record_probes(gold_sqls)
    probe_records = pred_records + gold_records
    gold_writes: set[str] = set()
    pred_writes: set[str] = set()
    gold_state = {}
    pred_state = {}
    gold_counts = {}
    pred_counts = {}
    try:
        gold_ok, gold_err, gold_executed = apply_savepoint(conn, 'gold_eval', gold_sqls, gold_writes)
        if gold_ok:
            gold_counts = probe_counts(conn, probe_records)
            gold_state = dump_state(conn, gold_writes | declared_gold_tables, gold_columns)
            rollback_savepoint(conn, 'gold_eval')

        pred_ok, pred_err, pred_executed = apply_savepoint(conn, 'pred_eval', pred_sqls, pred_writes)
        if pred_ok:
            compare_tables = gold_writes | pred_writes | declared_gold_tables
            pred_counts = probe_counts(conn, probe_records)
            pred_state = dump_state(conn, compare_tables, gold_columns)
            rollback_savepoint(conn, 'pred_eval')
            missing_gold_tables = compare_tables - set(gold_state)
            if missing_gold_tables:
                gold_state.update(dump_state(conn, missing_gold_tables, gold_columns))
        else:
            compare_tables = gold_writes | pred_writes | declared_gold_tables

        target_cmp = compare_probe_counts(
            pred_sqls,
            gold_sqls,
            pred_counts,
            gold_counts,
            pred_records,
            gold_records,
            sample,
        )
        fallback_tables = pred_unparsed | gold_unparsed
        if pred_ok and gold_ok and fallback_tables:
            target_cmp['correct'] = (
                target_cmp['correct']
                and {table: pred_state.get(table) for table in fallback_tables}
                == {table: gold_state.get(table) for table in fallback_tables}
            )
        strict_cmp = {
            'correct': bool(pred_ok and gold_ok and pred_state == gold_state),
            'affected_tables': sorted(compare_tables),
            'pred_changed_tables': sorted(pred_writes),
            'gold_changed_tables': sorted(gold_writes),
        }
        return {
            'gold_ok': gold_ok,
            'gold_err': gold_err,
            'gold_executed': gold_executed,
            'pred_ok': pred_ok,
            'pred_err': pred_err,
            'pred_executed': pred_executed,
            'target_cmp': target_cmp,
            'strict_cmp': strict_cmp,
        }
    finally:
        conn.close()


def evaluate_candidate_sql(
    sample: dict[str, Any],
    pred_sqls: list[str],
    db_root: str | Path,
    builder_status: str = 'success',
    parse_status: str = 'success',
    strict_all_tables: bool = False,
) -> dict[str, Any]:
    """Evaluate one candidate without mutating a run directory.

    This is used by the repair rollback gate. It executes the repaired SQL on an
    isolated in-memory database and never uses test correctness to construct the
    candidate. The returned state result is logged for analysis, while the
    default rollback policy accepts candidates based on execution success only.
    """
    db_path = find_db_path(db_root, sample['db_id'])
    gold_sqls = sample.get('gold_sql', [])
    pred_records, pred_unparsed = record_probes(pred_sqls)
    gold_records, gold_unparsed = record_probes(gold_sqls)
    use_probe_eval = not strict_all_tables and not (pred_unparsed or gold_unparsed)
    original_conn = pred_conn = gold_conn = None
    if use_probe_eval:
        conn = memory_copy(db_path)
        gold_ok, gold_err, _ = apply_savepoint(conn, 'gold_eval', gold_sqls)
        if gold_ok:
            gold_counts = probe_counts(conn, pred_records + gold_records)
            rollback_savepoint(conn, 'gold_eval')
        else:
            gold_counts = {}
        pred_ok, pred_err, pred_executed = apply_savepoint(conn, 'pred_eval', pred_sqls)
        if pred_ok:
            pred_counts = probe_counts(conn, pred_records + gold_records)
            rollback_savepoint(conn, 'pred_eval')
        else:
            pred_counts = {}
        conn.close()
    else:
        pred_write_tables = set() if strict_all_tables else None
        gold_write_tables = set() if strict_all_tables else None
        original_conn = None if strict_all_tables else memory_copy(db_path)
        pred_conn = memory_copy(db_path)
        gold_conn = memory_copy(db_path)
        gold_ok, gold_err, _ = execute_sqls(gold_conn, gold_sqls, gold_write_tables)
        pred_ok, pred_err, pred_executed = execute_sqls(pred_conn, pred_sqls, pred_write_tables)
    result = {
        'execution_success': bool(pred_ok),
        'correct': False,
        'error_type': None,
        'error_message': None,
        'num_pred_sql': len(pred_sqls),
        'num_executed_pred_sql': pred_executed,
        'builder_status': builder_status,
        'parse_status': parse_status,
    }
    if not gold_ok:
        result.update({'error_type': 'gold_sql_error', 'error_message': gold_err})
    elif parse_status != 'success':
        result.update({'execution_success': False, 'error_type': 'json_parse_error', 'error_message': 'Candidate JSON could not be parsed'})
    elif builder_status in {'error', 'parse_error'}:
        result.update({'execution_success': False, 'error_type': 'builder_error'})
    elif not pred_ok:
        result.update({'error_type': classify_error(pred_err), 'error_message': pred_err})
    else:
        cmp = (
            compare_probe_counts(pred_sqls, gold_sqls, pred_counts, gold_counts, pred_records, gold_records, sample)
            if use_probe_eval
            else compare_post_state(
                original_conn,
                pred_conn,
                gold_conn,
                sample,
                pred_sqls,
                strict_all_tables,
                pred_write_tables,
                gold_write_tables,
            )
        )
        result.update(cmp)
        if not result['correct']:
            result['error_group'] = 'wrong_state'
            result['error_type'] = state_error_subtype(
                pred_sqls,
                gold_sqls,
                sample,
                result.get('pred_changed_tables', []),
                result.get('gold_changed_tables', []),
            )
    for conn in (original_conn, pred_conn, gold_conn):
        if conn:
            conn.close()
    return result


def evaluate(
    run_dir,
    data_path,
    db_root,
    strict_all_tables=False,
    compute_strict_metrics=False,
    output_dir=None,
    split_ids=None,
):
    started = time.time()
    source_run_dir = Path(run_dir)
    run_dir = Path(output_dir) if output_dir else source_run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    gold = {str(x['id']): x for x in load_json(data_path)}
    pred = {x['sample_id']: x for x in iter_jsonl(source_run_dir / 'pred_sql.jsonl')}
    expected_ids = list(pred)
    source_manifest_path = source_run_dir / 'run_manifest.json'
    manifest_path = run_dir / 'run_manifest.json'
    if split_ids:
        expected_ids = sorted(read_id_file(split_ids))
    elif source_manifest_path.exists():
        manifest = load_json(source_manifest_path)
        split_path = (manifest.get('config') or {}).get('split_ids')
        if split_path and Path(split_path).exists():
            expected_ids = sorted(read_id_file(split_path))
    results = []
    exec_logs = []
    for sample_index, sid in enumerate(expected_ids, start=1):
        p = pred.get(sid)
        sample = gold[sid]
        if p is None:
            results.append({
                'sample_id': sid,
                'db_id': sample['db_id'],
                'method': None,
                'builder_status': 'missing',
                'parse_status': 'missing',
                'execution_success': False,
                'correct': False,
                'target_state_correct': False,
                'strict_full_state_correct': False if (strict_all_tables or compute_strict_metrics) else None,
                'side_effect': False,
                'error_type': 'missing_prediction',
                'error_group': 'pipeline_error',
                'error_message': 'No pred_sql row for expected split id',
                'num_pred_sql': 0,
                'num_executed_pred_sql': 0,
            })
            continue
        db_path = find_db_path(db_root, sample['db_id'])
        pred_sqls = p.get('pred_sql', [])
        gold_sqls = sample.get('gold_sql', [])
        pred_records, pred_unparsed = record_probes(pred_sqls)
        gold_records, gold_unparsed = record_probes(gold_sqls)
        track_full_state = strict_all_tables or compute_strict_metrics
        use_probe_eval = not track_full_state and not (pred_unparsed or gold_unparsed)
        original_conn = pred_conn = gold_conn = None
        state_pair = None
        if track_full_state:
            state_pair = evaluate_state_pair(db_path, pred_sqls, gold_sqls, sample)
            gold_ok = state_pair['gold_ok']
            gold_err = state_pair['gold_err']
            gold_executed = state_pair['gold_executed']
            pred_ok = state_pair['pred_ok']
            pred_err = state_pair['pred_err']
            pred_executed = state_pair['pred_executed']
        elif use_probe_eval:
            conn = memory_copy(db_path)
            gold_ok, gold_err, gold_executed = apply_savepoint(conn, 'gold_eval', gold_sqls)
            if gold_ok:
                gold_counts = probe_counts(conn, pred_records + gold_records)
                rollback_savepoint(conn, 'gold_eval')
            else:
                gold_counts = {}
            pred_ok, pred_err, pred_executed = apply_savepoint(conn, 'pred_eval', pred_sqls)
            if pred_ok:
                pred_counts = probe_counts(conn, pred_records + gold_records)
                rollback_savepoint(conn, 'pred_eval')
            else:
                pred_counts = {}
            conn.close()
        else:
            original_conn = memory_copy(db_path)
            pred_conn = memory_copy(db_path)
            gold_conn = memory_copy(db_path)
            gold_ok, gold_err, gold_executed = execute_sqls(gold_conn, gold_sqls)
            pred_ok, pred_err, pred_executed = execute_sqls(pred_conn, pred_sqls)
        res = {
            'sample_id': sid,
            'db_id': sample['db_id'],
            'method': p.get('method'),
            'builder_status': p.get('builder_status'),
            'parse_status': p.get('parse_status'),
            'execution_success': bool(pred_ok),
            'correct': False,
            'target_state_correct': False,
            'strict_full_state_correct': False if (strict_all_tables or compute_strict_metrics) else None,
            'side_effect': False,
            'error_type': None,
            'error_message': None,
            'num_pred_sql': len(p.get('pred_sql', [])),
            'num_executed_pred_sql': pred_executed,
        }
        exec_logs.append({
            'sample_id': sid,
            'db_id': sample['db_id'],
            'pred_sql': pred_sqls,
            'gold_sql': gold_sqls,
            'pred_ok': pred_ok,
            'pred_error': pred_err,
            'gold_ok': gold_ok,
            'gold_error': gold_err,
        })
        if not gold_ok:
            res.update({'error_type': 'gold_sql_error', 'error_message': gold_err})
        elif p.get('parse_status') != 'success':
            res.update({'execution_success': False, 'error_type': 'json_parse_error', 'error_group': 'pipeline_error', 'error_message': 'Model output could not be parsed'})
        elif p.get('builder_status') in {'error', 'parse_error'}:
            res.update({'execution_success': False, 'error_type': 'builder_error', 'error_group': 'pipeline_error', 'error_message': '; '.join(p.get('builder_errors') or [])})
        elif not pred_ok:
            res.update({'error_type': classify_error(pred_err), 'error_message': pred_err})
            if res['error_type'] == 'constraint_error' and 'foreign key' in str(pred_err).lower():
                res['error_subtype'] = 'fk_order_error'
        else:
            if state_pair is not None:
                target_cmp = state_pair['target_cmp']
            elif use_probe_eval:
                target_cmp = compare_probe_counts(pred_sqls, gold_sqls, pred_counts, gold_counts, pred_records, gold_records, sample)
            else:
                target_cmp = compare_post_state(original_conn, pred_conn, gold_conn, sample, pred_sqls, False)
            res.update(target_cmp)
            res['target_state_correct'] = bool(target_cmp['correct'])
            if strict_all_tables or compute_strict_metrics:
                strict_cmp = state_pair['strict_cmp']
                res['strict_full_state_correct'] = bool(strict_cmp['correct'])
                res['strict_affected_tables'] = strict_cmp['affected_tables']
                res['strict_pred_changed_tables'] = strict_cmp['pred_changed_tables']
                res['strict_gold_changed_tables'] = strict_cmp['gold_changed_tables']
                res['side_effect'] = bool(target_cmp['correct'] and not strict_cmp['correct'])
                res['correct'] = bool(strict_cmp['correct'] if strict_all_tables else target_cmp['correct'])
            else:
                res['correct'] = bool(target_cmp['correct'])
            if not res['correct']:
                res['error_group'] = 'wrong_state'
                res['error_type'] = state_error_subtype(pred_sqls, gold_sqls, sample, res.get('pred_changed_tables', []), res.get('gold_changed_tables', []))
        results.append(res)
        if original_conn:
            original_conn.close()
        if pred_conn:
            pred_conn.close()
        if gold_conn:
            gold_conn.close()
        if sample_index % 10 == 0 or sample_index == len(expected_ids):
            print(f'Evaluated {sample_index}/{len(expected_ids)} samples in {time.time() - started:.1f}s', flush=True)
    write_jsonl(results, run_dir / 'evaluation.jsonl')
    write_jsonl(exec_logs, run_dir / 'execution_logs.jsonl')
    fields = sorted({key for row in results for key in row})
    with open(run_dir / 'evaluation_per_sample.csv', 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in results:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for key, value in row.items()})
    if source_manifest_path.exists():
        manifest = load_json(source_manifest_path)
        config = manifest.setdefault('config', {})
        config['data_path'] = str(data_path)
        if split_ids:
            config['split_ids'] = str(split_ids)
        manifest['evaluation_source_run_dir'] = str(source_run_dir)
        manifest['evaluator_time_sec'] = time.time() - started
        manifest['dataset_sha256'] = sha256_file(data_path)
        manifest['test_split_sha256'] = sha256_file(split_ids) if split_ids and Path(split_ids).exists() else manifest.get('split_sha256')
        manifest['num_expected'] = len(expected_ids)
        manifest['num_completed'] = len(results)
        manifest['num_missing'] = sum(row.get('error_type') == 'missing_prediction' for row in results)
        manifest['evaluator'] = {
            'target_state_metric': 'target_state_correct',
            'strict_state_metric': 'strict_full_state_correct',
            'strict_state_metric_name': 'normalized_full_user_table_state_accuracy',
            'normalization_policy': {
                'storage_class_aware': True,
                'compares_user_tables': True,
                'excludes_sqlite_internal_tables': True,
                'ignores_auto_integer_primary_key_when_not_gold': True,
            },
            'strict_full_state_computed': bool(strict_all_tables or compute_strict_metrics),
            'primary_correctness': 'strict_full_state_correct' if strict_all_tables else 'target_state_correct',
            'source_sha256': sha256_file(Path(__file__)),
        }
        save_json(manifest, manifest_path)
    print(f'Wrote {run_dir/"evaluation.jsonl"}')
    print(f'Wrote {run_dir/"execution_logs.jsonl"}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-dir', required=True)
    ap.add_argument('--data', required=True)
    ap.add_argument('--db-root', required=True)
    ap.add_argument('--strict-all-tables', action='store_true', help='Hash every table before comparing states. Slower but catches trigger side effects outside target tables.')
    ap.add_argument('--compute-strict-metrics', action='store_true', help='Compute target-state and strict full-state correctness together while keeping target-state as the primary metric.')
    ap.add_argument('--out-dir', help='Write evaluation artifacts to a separate directory instead of overwriting the source run.')
    ap.add_argument('--split-ids', help='Override the split stored in the source run manifest.')
    args = ap.parse_args()
    evaluate(
        args.run_dir,
        args.data,
        args.db_root,
        args.strict_all_tables,
        args.compute_strict_metrics,
        args.out_dir,
        args.split_ids,
    )


if __name__ == '__main__':
    main()
