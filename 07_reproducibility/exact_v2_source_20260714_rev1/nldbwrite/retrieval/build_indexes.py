import argparse, json, re
from pathlib import Path
from nldbwrite.common import ensure_dir, load_json

def tokenize(text): return re.findall(r'\w+', text.lower(), flags=re.UNICODE)

def column_doc(db_id, table, col):
    samples=', '.join(col.get('sample_values', [])[:5]); fk=col.get('foreign_key') or {}; fk_text=f" foreign key to {fk.get('to_table')}.{fk.get('to_column')}" if fk else ''
    return f"database {db_id} table {table['name']} column {col['name']} type {col.get('type')} samples {samples}{fk_text}"

def build_index(profile):
    docs=[]; meta=[]
    for table in profile.get('tables', []):
        for col in table.get('columns', []):
            docs.append(tokenize(column_doc(profile['db_id'], table, col)))
            meta.append({'db_id':profile['db_id'],'table':table['name'],'column':col['name'],'type':col.get('type'),'sample_values':col.get('sample_values', [])[:5],'is_primary_key':col.get('is_primary_key',False),'is_foreign_key':col.get('is_foreign_key',False)})
    return {'docs':docs,'meta':meta}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--profile-dir', default='artifacts/profiles'); ap.add_argument('--out-dir', default='artifacts/indexes/schema_bm25'); args=ap.parse_args(); out=ensure_dir(args.out_dir)
    for path in Path(args.profile_dir).glob('*.json'):
        profile=load_json(path); index=build_index(profile)
        with open(out/f'{profile["db_id"]}.json','w',encoding='utf-8') as f: json.dump(index,f,ensure_ascii=False)
        print(f'indexed {profile["db_id"]}: {len(index["meta"])} columns')
if __name__=='__main__': main()
