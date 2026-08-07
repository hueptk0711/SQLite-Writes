import argparse, json, sqlite3
from typing import Any, Dict
from nldbwrite.common import find_db_path, load_json, quote_ident
from nldbwrite.eval.evaluate import record_probes

REQUIRED_FIELDS = ['id','db_id','input_text','operation_type','gold_tables','gold_columns','gold_sql']

def list_tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()}

def list_columns(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info({quote_ident(table)})').fetchall()}

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

def execute_sqls_on_conn(conn, sqls):
    conn.execute(f'SAVEPOINT {quote_ident("dataset_check")}')
    try:
        cur = conn.cursor()
        for sql in sqls:
            cur.execute(sql)
        conn.execute(f'ROLLBACK TO {quote_ident("dataset_check")}')
        conn.execute(f'RELEASE {quote_ident("dataset_check")}')
    except Exception:
        conn.execute(f'ROLLBACK TO {quote_ident("dataset_check")}')
        conn.execute(f'RELEASE {quote_ident("dataset_check")}')
        raise

def get_conn(db_root, db_id, cache):
    if db_id not in cache:
        cache[db_id] = memory_copy(find_db_path(db_root, db_id))
    return cache[db_id]

def get_schema(conn, cache, db_id):
    if db_id not in cache:
        tables = list_tables(conn)
        cache[db_id] = {
            'tables': tables,
            'columns': {table: list_columns(conn, table) for table in tables},
        }
    return cache[db_id]

def close_all(cache):
    for conn in cache.values():
        try:
            conn.close()
        except Exception:
            pass

def check_sample(sample: Dict[str, Any], db_root, conn_cache, schema_cache, strict=False) -> list[str]:
    errors=[]; sid=sample.get('id','<missing-id>')
    for field in REQUIRED_FIELDS:
        if field not in sample: errors.append(f'{sid}: missing field {field}')
    if errors: return errors
    try:
        conn=get_conn(db_root, sample['db_id'], conn_cache)
        schema=get_schema(conn, schema_cache, sample['db_id'])
    except Exception as e:
        return [f'{sid}: {e}']
    tables=schema['tables']
    columns=schema['columns']
    for t in sample.get('gold_tables', []):
        if t not in tables: errors.append(f'{sid}: gold table does not exist: {t}')
    for full_col in sample.get('gold_columns', []):
        if '.' not in full_col:
            errors.append(f'{sid}: gold column should be table.column: {full_col}'); continue
        table,col=full_col.split('.',1)
        if table in tables and col not in columns[table]: errors.append(f'{sid}: gold column does not exist: {full_col}')
    for rec in sample.get('gold_records', []):
        t=rec.get('table')
        if t not in tables: errors.append(f'{sid}: gold record table does not exist: {t}'); continue
        cols=columns[t]
        for c in rec.get('values',{}).keys():
            if c not in cols: errors.append(f'{sid}: gold record column does not exist: {t}.{c}')
            if strict and f'{t}.{c}' not in set(sample.get('gold_columns') or []):
                errors.append(f'{sid}: gold record column missing from gold_columns: {t}.{c}')
        if strict and t not in set(sample.get('gold_tables') or []):
            errors.append(f'{sid}: gold record table missing from gold_tables: {t}')
    if strict and not sample.get('gold_records'):
        errors.append(f'{sid}: gold_records must be non-empty in strict mode')
    if strict and sample.get('gold_records'):
        parsed_records, unparsed = record_probes(sample.get('gold_sql') or [])
        canonical = lambda rec: (str(rec.get('table')), tuple(sorted((str(k), '<NULL>' if v is None else str(v)) for k,v in (rec.get('values') or {}).items())))
        expected = sorted(canonical(rec) for rec in sample.get('gold_records') or [])
        parsed = sorted(canonical(rec) for rec in parsed_records)
        if unparsed or expected != parsed:
            errors.append(f'{sid}: gold_records do not match deterministic parse of gold_sql')
    if not isinstance(sample.get('gold_sql'), list) or not sample.get('gold_sql'):
        errors.append(f'{sid}: gold_sql must be a non-empty list')
        return errors
    try:
        execute_sqls_on_conn(conn, sample['gold_sql'])
    except Exception as e:
        errors.append(f'{sid}: gold_sql execution error: {e}')
    return errors

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--data', required=True); ap.add_argument('--db-root', required=True); ap.add_argument('--max-errors', type=int, default=50); ap.add_argument('--strict', action='store_true', help='Require complete gold_records consistent with gold tables/columns.'); ap.add_argument('--db-id', help='Validate only one database subset.'); ap.add_argument('--report-out'); args=ap.parse_args()
    data=load_json(args.data)
    if args.db_id:
        data=[sample for sample in data if str(sample.get('db_id')) == args.db_id]
    all_errors=[]; ids=set(); conn_cache={}; schema_cache={}
    try:
        for sample in data:
            sid=str(sample.get('id'))
            if sid in ids: all_errors.append(f"duplicate id: {sample.get('id')}")
            ids.add(sid); all_errors.extend(check_sample(sample,args.db_root,conn_cache,schema_cache,args.strict))
            if len(all_errors)>=args.max_errors: break
    finally:
        close_all(conn_cache)
    if all_errors:
        print('Dataset check failed:'); [print(' -', e) for e in all_errors[:args.max_errors]]; raise SystemExit(1)
    if args.report_out:
        report={'status':'passed','num_samples':len(data),'db_ids':sorted({str(x.get('db_id')) for x in data}),'strict':bool(args.strict)}
        from nldbwrite.common import save_json
        save_json(report,args.report_out)
    print(f'OK: {len(data)} samples checked.')
if __name__=='__main__': main()
