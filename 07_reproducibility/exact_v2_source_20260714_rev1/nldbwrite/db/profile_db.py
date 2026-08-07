import argparse, sqlite3
from pathlib import Path
from nldbwrite.common import ensure_dir, quote_ident, save_json

def list_tables(conn):
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]

def get_columns(conn, table):
    rows=conn.execute(f'PRAGMA table_xinfo({quote_ident(table)})').fetchall(); cols=[]
    for cid,name,col_type,notnull,default,pk,hidden in rows:
        cols.append({
            'name':name,
            'type':col_type or 'TEXT',
            'not_null':bool(notnull),
            'default':default,
            'is_primary_key':bool(pk),
            'pk_order':int(pk or 0),
            'hidden':int(hidden or 0),
            'is_generated':int(hidden or 0) in (2, 3),
            'is_insertable':int(hidden or 0) == 0,
        })
    return cols

def get_foreign_keys(conn, table):
    return [{'from_column':r[3],'to_table':r[2],'to_column':r[4]} for r in conn.execute(f'PRAGMA foreign_key_list({quote_ident(table)})').fetchall()]

def get_table_sql(conn, table):
    row=conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row[0] if row else None

def get_unique_indexes(conn, table, primary_keys):
    indexes=[]
    if primary_keys:
        indexes.append({'name':'PRIMARY_KEY','columns':primary_keys,'origin':'pk','is_primary_key':True})
    for row in conn.execute(f'PRAGMA index_list({quote_ident(table)})').fetchall():
        # seq, name, unique, origin, partial
        idx_name=row[1]; is_unique=bool(row[2]); origin=row[3] if len(row)>3 else None
        if not is_unique:
            continue
        cols=[r[2] for r in conn.execute(f'PRAGMA index_info({quote_ident(idx_name)})').fetchall()]
        if cols and cols not in [x['columns'] for x in indexes]:
            indexes.append({'name':idx_name,'columns':cols,'origin':origin,'is_primary_key':origin=='pk'})
    return indexes

def is_auto_integer_primary_key(cols, col):
    pk_cols=[c for c in cols if c.get('is_primary_key')]
    return len(pk_cols)==1 and pk_cols[0]['name']==col['name'] and 'INT' in (col.get('type') or '').upper()

def required_insert_columns(cols):
    required=[]
    for col in cols:
        if not col.get('is_insertable', True):
            continue
        if not col.get('not_null') and not col.get('is_primary_key'):
            continue
        default=col.get('default')
        if default not in (None, 'NULL'):
            continue
        if is_auto_integer_primary_key(cols, col):
            continue
        required.append(col['name'])
    return required

def get_column_stats(conn, table, column, sample_limit):
    t,c=quote_ident(table),quote_ident(column); total=conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    if total==0: return {'num_distinct':0,'null_ratio':0.0,'sample_values':[]}
    null_count=conn.execute(f'SELECT COUNT(*) FROM {t} WHERE {c} IS NULL').fetchone()[0]
    try: distinct_count=conn.execute(f'SELECT COUNT(DISTINCT {c}) FROM {t}').fetchone()[0]
    except Exception: distinct_count=None
    try: samples=[str(r[0]) for r in conn.execute(f'SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL LIMIT ?', (sample_limit,)).fetchall()]
    except Exception: samples=[]
    return {'num_distinct':distinct_count,'null_ratio':null_count/max(total,1),'sample_values':samples}

def profile_database(db_path, db_id, sample_limit=20):
    conn=sqlite3.connect(db_path); conn.execute('PRAGMA foreign_keys = ON'); profile={'db_id':db_id,'db_path':str(db_path),'tables':[]}
    try:
        for table in list_tables(conn):
            cols=get_columns(conn,table); fks=get_foreign_keys(conn,table); fk_map={fk['from_column']:fk for fk in fks}
            for col in cols:
                if col.get('is_insertable', True):
                    col.update(get_column_stats(conn,table,col['name'],sample_limit))
                else:
                    col.update({'num_distinct':None,'null_ratio':None,'sample_values':[]})
                col['is_foreign_key']=col['name'] in fk_map; col['foreign_key']=fk_map.get(col['name'])
            primary_keys=[c['name'] for c in sorted(cols, key=lambda x:x.get('pk_order',0)) if c.get('is_primary_key')]
            profile['tables'].append({
                'name':table,
                'row_count':conn.execute(f'SELECT COUNT(*) FROM {quote_ident(table)}').fetchone()[0],
                'table_sql':get_table_sql(conn, table),
                'columns':cols,
                'primary_keys':primary_keys,
                'foreign_keys':fks,
                'unique_indexes':get_unique_indexes(conn, table, primary_keys),
                'required_insert_columns':required_insert_columns(cols),
                'forbidden_insert_columns':[c['name'] for c in cols if not c.get('is_insertable', True)],
            })
    finally: conn.close()
    return profile

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db-root', required=True); ap.add_argument('--out-dir', default='artifacts/profiles'); ap.add_argument('--sample-limit', type=int, default=20); args=ap.parse_args(); out=ensure_dir(args.out_dir)
    paths=[]
    for ext in ['sqlite','db','sqlite3']: paths.extend(Path(args.db_root).glob(f'**/*.{ext}'))
    if not paths: raise SystemExit(f'No SQLite files found under {args.db_root}')
    for db_path in sorted(paths):
        db_id=db_path.stem; profile=profile_database(db_path,db_id,args.sample_limit); save_json(profile,out/f'{db_id}.json'); print(f'profiled {db_id}: {len(profile["tables"])} tables')
if __name__=='__main__': main()
