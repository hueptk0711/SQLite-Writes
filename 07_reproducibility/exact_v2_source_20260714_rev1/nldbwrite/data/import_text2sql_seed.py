import argparse
import json
import re
import shutil
from pathlib import Path

from nldbwrite.common import ensure_dir, save_json


_IDENT = r'''(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|\w+)'''
_INSERT_RE = re.compile(rf'INSERT\s+(?:OR\s+\w+\s+)?INTO\s+({_IDENT})', re.I | re.S)


def unquote_ident(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in ('"', '`') and value[-1] == value[0]:
        return value[1:-1].replace(value[0] * 2, value[0])
    if len(value) >= 2 and value[0] == '[' and value[-1] == ']':
        return value[1:-1]
    return value


def split_identifiers(text: str) -> list[str]:
    out = []
    buf = []
    quote = None
    bracket = False
    for ch in text:
        if bracket:
            buf.append(ch)
            if ch == ']':
                bracket = False
            continue
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', '`', "'"):
            quote = ch
            buf.append(ch)
        elif ch == '[':
            bracket = True
            buf.append(ch)
        elif ch == ',':
            item = ''.join(buf).strip()
            if item:
                out.append(unquote_ident(item))
            buf = []
        else:
            buf.append(ch)
    item = ''.join(buf).strip()
    if item:
        out.append(unquote_ident(item))
    return out


def extract_insert_header(sql: str) -> tuple[str, str] | None:
    match = _INSERT_RE.search(sql)
    if not match:
        return None
    table = unquote_ident(match.group(1))
    start = sql.find('(', match.end())
    if start < 0:
        return None
    quote = None
    bracket = False
    depth = 0
    for i, ch in enumerate(sql[start:], start=start):
        if bracket:
            if ch == ']':
                bracket = False
            continue
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in ('"', '`', "'"):
            quote = ch
        elif ch == '[':
            bracket = True
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return table, sql[start + 1:i]
    return None


def extract_tables_columns(sqls: list[str]) -> tuple[list[str], list[str]]:
    tables = []
    columns = []
    seen_tables = set()
    seen_cols = set()
    for sql in sqls:
        header = extract_insert_header(sql)
        if not header:
            continue
        table, column_text = header
        if table not in seen_tables:
            seen_tables.add(table)
            tables.append(table)
        for col in split_identifiers(column_text):
            full = f'{table}.{col}'
            if full not in seen_cols:
                seen_cols.add(full)
                columns.append(full)
    return tables, columns


def infer_operation(sample: dict) -> str:
    sql_text = '\n'.join(sample.get('gold_sql') or []).lower()
    request = str(sample.get('user_request') or '').lower()
    if 'do update' in sql_text or any(k in request for k in ['update', 'replace', 'modify', 'thay thế', 'cập nhật']):
        return 'upsert'
    return 'insert'


def infer_difficulty(metadata: dict) -> str:
    table_count = int(metadata.get('table_count') or 1)
    record_count = int(metadata.get('record_count') or 1)
    if table_count > 1 or record_count > 20:
        return 'hard'
    if record_count > 1:
        return 'medium'
    return 'easy'


def convert_sample(sample: dict) -> dict:
    sqls = sample.get('gold_sql') or []
    tables, columns = extract_tables_columns(sqls)
    metadata = sample.get('metadata') or {}
    return {
        'id': f"seed_{int(sample['id']):06d}" if str(sample.get('id')).isdigit() else str(sample.get('id')),
        'source_id': sample.get('id'),
        'db_id': sample['db_id'],
        'input_text': sample.get('user_request') or sample.get('input_text') or '',
        'operation_type': infer_operation(sample),
        'gold_tables': tables,
        'gold_columns': columns,
        'gold_records': [],
        'gold_sql': sqls,
        'difficulty': infer_difficulty(metadata),
        'num_tables': int(metadata.get('table_count') or len(tables) or 1),
        'num_records': int(metadata.get('record_count') or 1),
        'has_foreign_key': bool(metadata.get('table_count', 1) and int(metadata.get('table_count') or 1) > 1),
        'metadata': metadata,
    }


def copy_databases(source_db_root: Path, out_db_root: Path, db_ids: set[str], overwrite: bool = False) -> None:
    ensure_dir(out_db_root)
    for db_id in sorted(db_ids):
        src_dir = source_db_root / db_id
        dst_dir = out_db_root / db_id
        if not src_dir.exists():
            print(f'WARN: missing source DB dir: {src_dir}')
            continue
        if dst_dir.exists():
            if overwrite:
                shutil.rmtree(dst_dir)
            else:
                continue
        shutil.copytree(src_dir, dst_dir)
        print(f'copied {db_id}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source-data', required=True, help='text2sql/test.json')
    ap.add_argument('--source-db-root', required=True, help='text2sql BIRD dev_databases directory')
    ap.add_argument('--out-data', default='data/processed/nl_db_write_seed359.json')
    ap.add_argument('--out-db-root', default='data/databases')
    ap.add_argument('--copy-dbs', action='store_true')
    ap.add_argument('--overwrite-dbs', action='store_true')
    args = ap.parse_args()

    source_data = Path(args.source_data)
    data = json.load(open(source_data, encoding='utf-8'))
    converted = [convert_sample(sample) for sample in data]
    save_json(converted, args.out_data)
    print(f'Wrote {args.out_data}: {len(converted)} samples')
    if args.copy_dbs:
        copy_databases(Path(args.source_db_root), Path(args.out_db_root), {x['db_id'] for x in converted}, args.overwrite_dbs)


if __name__ == '__main__':
    main()
