import argparse
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from nldbwrite.common import iter_jsonl, load_config, load_json, quote_ident, save_json, write_jsonl
from nldbwrite.sql.normalize_values import normalize_value_for_sql
from nldbwrite.sql.safety import is_safe_sql

try:
    from rapidfuzz import process
except Exception:  # pragma: no cover - rapidfuzz is optional at import time
    process = None


UPDATE_OPS = {'update', 'upsert', 'replace'}
DIRECT_SQL_PREFIXES = ('m0', 'm1')
DEFAULT_BUILDER_OPTIONS = {
    'fk_ordering': True,
    'required_column_check': True,
    'type_normalization': True,
    'conflict_target_inference': True,
    'safety_filter': True,
    'allow_update': False,
    'insert_conflict_policy': 'operation_aware',
    'unknown_identifier_policy': 'error',
}


def builder_options_from_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or {}
    options = {
        name: bool(config.get(f'builder_{name}', default))
        for name, default in DEFAULT_BUILDER_OPTIONS.items()
        if isinstance(default, bool)
    }
    options['insert_conflict_policy'] = str(
        config.get('builder_insert_conflict_policy', DEFAULT_BUILDER_OPTIONS['insert_conflict_policy'])
    ).lower()
    options['unknown_identifier_policy'] = str(
        config.get('builder_unknown_identifier_policy', DEFAULT_BUILDER_OPTIONS['unknown_identifier_policy'])
    ).lower()
    return options


def sql_literal(value):
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def valid_map(profile):
    return {t['name']: {c['name']: c for c in t.get('columns', [])} for t in profile.get('tables', [])}


def table_map(profile):
    return {t['name']: t for t in profile.get('tables', [])}


def match_name(name: str | None, candidates: list[str] | set[str], cutoff: int = 85) -> str | None:
    if name is None:
        return None
    if name in candidates:
        return name
    if process is not None:
        match = process.extractOne(str(name), list(candidates), score_cutoff=cutoff)
        return match[0] if match else None
    scored = [(100 * SequenceMatcher(None, str(name).casefold(), str(candidate).casefold()).ratio(), candidate) for candidate in candidates]
    if not scored:
        return None
    score, candidate = max(scored)
    return candidate if score >= cutoff else None


def coerce_value_rows(values: Any) -> list[dict[str, Any]]:
    if isinstance(values, dict):
        return [values]
    if not isinstance(values, list):
        return []

    pair_map: dict[str, Any] = {}
    dict_rows: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, dict):
            if 'column' in item and 'value' in item:
                pair_map[str(item['column'])] = item.get('value')
            else:
                dict_rows.append(item)
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            pair_map[str(item[0])] = item[1]

    if pair_map:
        return [pair_map]
    return dict_rows


def normalize_pred_json(pred_json: dict[str, Any]) -> list[dict[str, Any]]:
    """Accept both {"records": [...]} and {"tables": [{"records": [...]}]} outputs."""
    out: list[dict[str, Any]] = []
    default_op = pred_json.get('operation') or pred_json.get('operation_type')

    for rec in pred_json.get('records') or []:
        if not isinstance(rec, dict):
            continue
        if 'values' in rec:
            value_rows = coerce_value_rows(rec.get('values')) or [{}]
            for values in value_rows:
                out.append({
                    'table': rec.get('table'),
                    'operation': rec.get('operation', default_op),
                    'values': values,
                    **{k: rec[k] for k in (
                        'record_id', 'conflict_target', 'conflict_action', 'update_columns',
                        'depends_on', 'foreign_key_bindings',
                    ) if k in rec},
                })
        else:
            table = rec.get('table')
            semantic_keys = {
                'table', 'operation', 'operation_type', 'record_id', 'conflict_target',
                'conflict_action', 'update_columns', 'depends_on', 'foreign_key_bindings',
            }
            values = {k: v for k, v in rec.items() if k not in semantic_keys}
            out.append({
                'table': table,
                'operation': rec.get('operation', default_op),
                'values': values,
                **{k: rec[k] for k in semantic_keys - {'table', 'operation', 'operation_type'} if k in rec},
            })

    for group in pred_json.get('tables') or []:
        if not isinstance(group, dict):
            continue
        table = group.get('table')
        op = group.get('operation', default_op)
        for values in group.get('records') or []:
            for value_row in coerce_value_rows(values):
                out.append({
                    'table': table,
                    'operation': op,
                    'values': value_row,
                    **{k: group[k] for k in (
                        'record_id', 'conflict_target', 'conflict_action', 'update_columns',
                        'depends_on', 'foreign_key_bindings',
                    ) if k in group},
                })
            if isinstance(values, dict) and not coerce_value_rows(values):
                out.append({'table': table, 'operation': op, 'values': values})
    return out


def _trace_increment(options: dict[str, Any], key: str, amount: int = 1):
    trace = options.get('_trace')
    if isinstance(trace, dict):
        trace[key] = int(trace.get(key, 0)) + amount


def _as_name_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(',') if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value).strip()]


def _fk_parent_tables(profile: dict[str, Any]) -> dict[str, set[str]]:
    parents: dict[str, set[str]] = {}
    for table in profile.get('tables', []) or []:
        child = table.get('name')
        if not child:
            continue
        for fk in table.get('foreign_keys') or []:
            parent = fk.get('to_table')
            if parent and parent != child:
                parents.setdefault(str(child), set()).add(str(parent))
    return parents


def order_records_by_dependencies(
    records: list[dict[str, Any]],
    profile: dict[str, Any] | None = None,
    fk_ordering: bool = True,
) -> list[dict[str, Any]]:
    """Stable topological order over explicit IR dependencies and FK edges."""
    by_id = {str(record.get('record_id')): record for record in records if record.get('record_id')}
    n = len(records)
    edges: dict[int, set[int]] = {idx: set() for idx in range(n)}
    indegree = [0 for _ in records]

    def add_edge(before: int, after: int) -> None:
        if before == after or after in edges[before]:
            return
        edges[before].add(after)
        indegree[after] += 1

    id_to_index = {str(record.get('record_id')): idx for idx, record in enumerate(records) if record.get('record_id')}
    for idx, record in enumerate(records):
        for dep in _as_name_list(record.get('depends_on')):
            if dep in id_to_index:
                add_edge(id_to_index[dep], idx)
            elif by_id:
                raise ValueError(f'dependency_cycle: unresolved dependency {dep}')

    if fk_ordering and profile:
        parents = _fk_parent_tables(profile)
        tables = [str(record.get('table') or '') for record in records]
        for child_idx, child_table in enumerate(tables):
            for parent_idx, parent_table in enumerate(tables):
                if parent_table in parents.get(child_table, set()):
                    add_edge(parent_idx, child_idx)

    ready = [idx for idx, degree in enumerate(indegree) if degree == 0]
    ordered_indices: list[int] = []
    while ready:
        idx = ready.pop(0)
        ordered_indices.append(idx)
        for child in sorted(edges[idx]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
        ready.sort()
    if len(ordered_indices) != n:
        cycle = [
            str(records[idx].get('record_id') or records[idx].get('table') or idx)
            for idx, degree in enumerate(indegree)
            if degree > 0
        ]
        raise ValueError(f"dependency_cycle: {', '.join(cycle)}")
    return [records[idx] for idx in ordered_indices]


def resolve_foreign_key_bindings(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve bindings such as {"child_id": "parent.id"} from explicit values."""
    by_id = {str(record.get('record_id')): record for record in records if record.get('record_id')}
    out = []
    for source in records:
        record = dict(source)
        values = dict(record.get('values') or {})
        bindings = record.get('foreign_key_bindings') or {}
        if bindings and not isinstance(bindings, dict):
            raise ValueError('foreign_key_bindings must be an object')
        for local_col, reference in bindings.items():
            if isinstance(reference, dict):
                ref_id = str(reference.get('record_id') or '')
                ref_col = str(reference.get('column') or '')
            else:
                ref_id, separator, ref_col = str(reference).partition('.')
                if not separator:
                    raise ValueError(f'Invalid foreign-key binding: {reference}')
            ref_id = ref_id.lstrip('$')
            parent = by_id.get(ref_id)
            parent_values = (parent or {}).get('values') or {}
            if ref_col not in parent_values:
                raise ValueError(f'Unresolved foreign-key binding: {ref_id}.{ref_col}')
            values[str(local_col)] = parent_values[ref_col]
        record['values'] = values
        out.append(record)
    return out


_ORACLE_INSERT_RE = re.compile(r'INSERT\s+(?:OR\s+\w+\s+)?INTO\s+["`\[]?([\w$]+)', re.I)
_ORACLE_CONFLICT_RE = re.compile(r'ON\s+CONFLICT\s*\(([^)]*)\)', re.I | re.S)
_ORACLE_UPDATE_RE = re.compile(r'DO\s+UPDATE\s+SET\s+(.+?)(?:\s+WHERE\b|;|$)', re.I | re.S)


def oracle_conflict_semantics(sample: dict[str, Any]) -> dict[str, dict[str, Any]]:
    semantics = {}
    for sql in sample.get('gold_sql') or []:
        table_match = _ORACLE_INSERT_RE.search(sql)
        if not table_match:
            continue
        table = table_match.group(1)
        target_match = _ORACLE_CONFLICT_RE.search(sql)
        update_match = _ORACLE_UPDATE_RE.search(sql)
        targets = []
        if target_match:
            targets = [part.strip().strip('"`[]') for part in target_match.group(1).split(',') if part.strip()]
        updates = []
        if update_match:
            updates = [
                part.split('=', 1)[0].strip().strip('"`[]')
                for part in update_match.group(1).split(',')
                if '=' in part
            ]
        if update_match:
            action = 'update'
        elif re.search(r'DO\s+NOTHING', sql, re.I):
            action = 'do_nothing'
        else:
            action = 'plain'
        semantics[table] = {
            'conflict_target': targets,
            'conflict_action': action,
            'update_columns': updates,
        }
    return semantics


def required_columns(table_profile: dict[str, Any], cols: dict[str, dict[str, Any]]) -> list[str]:
    if table_profile.get('required_insert_columns') is not None:
        return list(table_profile.get('required_insert_columns') or [])

    pk_cols = [c for c in cols.values() if c.get('is_primary_key')]
    required = []
    for name, col in cols.items():
        if not col.get('is_insertable', True):
            continue
        if not col.get('not_null') and not col.get('is_primary_key'):
            continue
        if col.get('default') not in (None, 'NULL'):
            continue
        if len(pk_cols) == 1 and col.get('is_primary_key') and 'INT' in (col.get('type') or '').upper():
            continue
        required.append(name)
    return required


def clean_record(
    record: dict[str, Any],
    profile: dict[str, Any],
    vmap: dict[str, dict[str, Any]],
    options: dict[str, bool] | None = None,
):
    options = {**DEFAULT_BUILDER_OPTIONS, **(options or {})}
    tmap = table_map(profile)
    table = match_name(record.get('table'), set(vmap))
    if not table:
        raise ValueError(f"Unknown table: {record.get('table')}")

    cols = vmap[table]
    table_profile = tmap[table]
    values = record.get('values') or {}
    cleaned: dict[str, Any] = {}
    dropped: list[str] = []
    identifier_policy = str(options.get('unknown_identifier_policy', 'error')).lower()
    if identifier_policy not in {'error', 'warn', 'drop'}:
        raise ValueError(f'Unsupported unknown_identifier_policy: {identifier_policy}')

    for raw_col, value in values.items():
        col = match_name(raw_col, set(cols))
        if not col:
            if identifier_policy == 'error':
                raise ValueError(f'{table}: unknown column: {raw_col}')
            dropped.append(str(raw_col))
            continue
        if not cols[col].get('is_insertable', True):
            if identifier_policy == 'error':
                raise ValueError(f'{table}: non-insertable column: {raw_col}')
            dropped.append(str(raw_col))
            continue
        normalized = (
            normalize_value_for_sql(value, cols[col].get('type'), cols[col].get('sample_values'))
            if options['type_normalization']
            else value
        )
        if options['type_normalization'] and normalized != value:
            _trace_increment(options, 'type_normalization_changed')
        cleaned[col] = normalized

    if options['required_column_check']:
        _trace_increment(options, 'required_column_check_applied')
        missing = [c for c in required_columns(table_profile, cols) if c not in cleaned or cleaned[c] is None]
        if missing:
            raise ValueError(f"{table}: missing required columns: {', '.join(missing)}")
    if not cleaned:
        raise ValueError(f'No valid values for table {table}')

    if dropped:
        _trace_increment(options, 'dropped_column_count', len(dropped))
        if identifier_policy == 'warn':
            _trace_increment(options, 'unknown_identifier_warning_count', len(dropped))

    return table, cleaned, dropped


def usable_unique_indexes(table_profile: dict[str, Any], cleaned: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for idx in table_profile.get('unique_indexes') or []:
        cols = idx.get('columns') or []
        if cols and all(c in cleaned and cleaned[c] is not None for c in cols):
            out.append(idx)
    return out


def build_on_conflict(
    table_profile: dict[str, Any],
    columns: list[str],
    cleaned: dict[str, Any],
    operation,
    infer_target: bool = True,
    record: dict[str, Any] | None = None,
    policy: str = 'operation_aware',
    trace: dict[str, Any] | None = None,
) -> str:
    record = record or {}
    op = str(operation or '').lower()
    policy = str(policy or 'operation_aware').lower()
    if policy == 'plain':
        return ''
    if policy == 'ignore':
        return 'ON CONFLICT DO NOTHING'
    if policy == 'predicted':
        raw_target = _as_name_list(record.get('conflict_target'))
        target_cols = []
        for raw_col in raw_target:
            matched = match_name(raw_col, set(cleaned))
            if not matched:
                raise ValueError(f'Unknown predicted conflict column: {raw_col}')
            target_cols.append(matched)
        action = str(record.get('conflict_action') or '').lower().replace(' ', '_')
        if action in {'', 'plain', 'insert'}:
            return ''
        if action in {'ignore', 'do_nothing', 'nothing'}:
            if trace is not None:
                trace['explicit_conflict_target_used'] = int(bool(target_cols))
            target = ', '.join(quote_ident(c) for c in target_cols)
            return f'ON CONFLICT ({target}) DO NOTHING' if target else 'ON CONFLICT DO NOTHING'
        if action not in {'update', 'do_update', 'upsert'}:
            raise ValueError(f'Unknown predicted conflict action: {action}')
        if not target_cols:
            raise ValueError('Predicted conflict update requires conflict_target')
        update_cols = []
        for raw_col in _as_name_list(record.get('update_columns')):
            matched = match_name(raw_col, set(cleaned))
            if matched and matched not in target_cols:
                update_cols.append(matched)
        if not update_cols:
            update_cols = [column for column in columns if column not in target_cols]
        target = ', '.join(quote_ident(c) for c in target_cols)
        if not update_cols:
            raise ValueError('Predicted conflict update has no non-conflict columns to update')
        if trace is not None:
            trace['explicit_conflict_target_used'] = 1
            trace['explicit_update_columns_used'] = int(bool(record.get('update_columns')))
        set_clause = ', '.join(f'{quote_ident(c)} = excluded.{quote_ident(c)}' for c in update_cols)
        return f'ON CONFLICT ({target}) DO UPDATE SET {set_clause}'
    if op not in UPDATE_OPS:
        return ''
    if not infer_target:
        raise ValueError(f"{table_profile.get('name')}: upsert requested but conflict target inference is disabled")

    candidates = usable_unique_indexes(table_profile, cleaned)
    if not candidates:
        raise ValueError(f"{table_profile.get('name')}: upsert requested but no PK/UNIQUE conflict target is present")

    pk_candidates = [idx for idx in candidates if idx.get('is_primary_key') or idx.get('origin') == 'pk']
    chosen = sorted(pk_candidates or candidates, key=lambda idx: (len(idx.get('columns') or []), idx.get('name') or ''))[0]
    if trace is not None:
        trace['inferred_conflict_target_used'] = 1
    conflict_cols = chosen.get('columns') or []
    update_cols = [c for c in columns if c not in conflict_cols]
    if not update_cols:
        raise ValueError(f"{table_profile.get('name')}: upsert requested but no non-conflict columns can be updated")

    target = ', '.join(quote_ident(c) for c in conflict_cols)
    set_clause = ', '.join(f'{quote_ident(c)} = excluded.{quote_ident(c)}' for c in update_cols)
    return f'ON CONFLICT ({target}) DO UPDATE SET {set_clause}'


def build_insert_sql(record, profile, vmap, options: dict[str, bool] | None = None):
    options = {**DEFAULT_BUILDER_OPTIONS, **(options or {})}
    tmap = table_map(profile)
    table, cleaned, dropped = clean_record(record, profile, vmap, options)
    cols = list(cleaned.keys())
    values_sql = ', '.join(sql_literal(cleaned[c]) for c in cols)
    conflict = build_on_conflict(
        tmap[table],
        cols,
        cleaned,
        record.get('operation'),
        infer_target=options['conflict_target_inference'],
        record=record,
        policy='predicted' if record.get('_oracle_conflict') else options.get('insert_conflict_policy', 'operation_aware'),
        trace=options.get('_trace'),
    )
    sql = (
        f"INSERT INTO {quote_ident(table)} ({', '.join(quote_ident(c) for c in cols)}) "
        f"VALUES ({values_sql})"
    )
    if conflict:
        sql += f'\n{conflict}'
    sql += ';'
    return sql, {
        'record_id': record.get('record_id'),
        'table': table,
        'columns': cols,
        'dropped_columns': dropped,
        'conflict_policy': options.get('insert_conflict_policy', 'operation_aware'),
        'conflict_clause': conflict,
    }


def build_sql_from_json(pred_json, profile, options: dict[str, bool] | None = None):
    options = {**DEFAULT_BUILDER_OPTIONS, **(options or {})}
    vmap = valid_map(profile)
    records = normalize_pred_json(pred_json or {})
    if options.get('insert_conflict_policy') == 'oracle':
        oracle_sample = options.get('_oracle_sample')
        if not isinstance(oracle_sample, dict):
            raise ValueError('Oracle conflict policy requires a gold sample')
        semantics = oracle_conflict_semantics(oracle_sample)
        for record in records:
            table = match_name(record.get('table'), set(vmap))
            if table in semantics:
                record.update(semantics[table])
                record['_oracle_conflict'] = True
        _trace_increment(options, 'oracle_conflict_policy_used')
    original_ids = [record.get('record_id') for record in records]
    try:
        records = order_records_by_dependencies(records, profile, bool(options.get('fk_ordering', True)))
        if [record.get('record_id') for record in records] != original_ids:
            _trace_increment(options, 'dependency_order_changed')
        records = resolve_foreign_key_bindings(records)
        if any(record.get('foreign_key_bindings') for record in records):
            _trace_increment(options, 'foreign_key_bindings_resolved')
    except Exception as exc:
        return 'error', [], [str(exc)], []

    sqls: list[str] = []
    errors: list[str] = []
    metadata: list[dict[str, Any]] = []
    for rec in records:
        try:
            sql, meta = build_insert_sql(rec, profile, vmap, options)
            if options['safety_filter']:
                _trace_increment(options, 'safety_filter_applied')
                safe, reason = is_safe_sql(sql, allow_update=bool(options.get('allow_update', False)))
                if not safe:
                    raise ValueError(reason or 'unsafe SQL generated by builder')
            sqls.append(sql)
            metadata.append(meta)
        except Exception as e:
            errors.append(str(e))
    status = 'partial' if sqls and errors else ('success' if sqls else 'error')
    return status, sqls, errors, metadata


def build_run(run_dir, profile_dir, config_path=None):
    started = time.time()
    run_dir = Path(run_dir)
    config = load_config(config_path) if config_path else {}
    options = builder_options_from_config(config)
    oracle_samples = {}
    if options.get('insert_conflict_policy') == 'oracle':
        data_path = config.get('builder_oracle_data_path') or config.get('data_path')
        if not data_path:
            raise ValueError('builder_insert_conflict_policy=oracle requires data_path')
        oracle_samples = {str(sample['id']): sample for sample in load_json(data_path)}
    rows = []
    for item in iter_jsonl(run_dir / 'parsed_outputs.jsonl'):
        trace: dict[str, Any] = {'record_count': 0}
        res = {
            'sample_id': item['sample_id'],
            'db_id': item['db_id'],
            'method': item['method'],
            'builder_status': 'success',
            'pred_sql': [],
            'builder_errors': [],
            'parse_status': item.get('parse_status'),
        }
        if item.get('parse_status') != 'success':
            res.update({'builder_status': 'parse_error', 'builder_errors': [item.get('parse_error', 'parse_error')]})
        elif item['method'].startswith(DIRECT_SQL_PREFIXES):
            safe = []
            errors = []
            for sql in item.get('pred_sql', []):
                ok, reason = is_safe_sql(sql, allow_update=bool(options.get('allow_update', False))) if options['safety_filter'] else (True, None)
                if ok:
                    safe.append(sql)
                else:
                    errors.append(reason or 'unsafe')
            res.update({'builder_status': 'direct_sql' if safe else 'error', 'pred_sql': safe, 'builder_errors': errors})
        else:
            profile = load_json(Path(profile_dir) / f"{item['db_id']}.json")
            sample_options = {**options, '_trace': trace}
            if oracle_samples:
                sample_options['_oracle_sample'] = oracle_samples.get(str(item['sample_id']))
            trace['record_count'] = len(normalize_pred_json(item.get('pred_json', {})))
            status, sqls, errors, metadata = build_sql_from_json(item.get('pred_json', {}), profile, sample_options)
            res.update({
                'builder_status': status,
                'pred_sql': sqls,
                'builder_errors': errors,
                'sql_metadata': metadata,
                'builder_trace': trace,
            })
        rows.append(res)
    write_jsonl(rows, run_dir / 'pred_sql.jsonl')
    manifest_path = run_dir / 'run_manifest.json'
    if manifest_path.exists():
        manifest = load_json(manifest_path)
        manifest['builder_options'] = options
        manifest['builder_config_path'] = str(config_path) if config_path else None
        manifest['postprocess_config'] = config
        if config.get('run_name'):
            manifest['run_name'] = config['run_name']
        manifest['builder_time_sec'] = time.time() - started
        save_json(manifest, manifest_path)
    print(f'Wrote {run_dir/"pred_sql.jsonl"}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-dir', required=True)
    ap.add_argument('--profile-dir', default='artifacts/profiles')
    ap.add_argument('--config')
    args = ap.parse_args()
    build_run(args.run_dir, args.profile_dir, args.config)


if __name__ == '__main__':
    main()
