import json, math, re
from collections import Counter
from functools import lru_cache
from pathlib import Path

def tokenize(text): return re.findall(r'\w+', text.lower(), flags=re.UNICODE)

def bm25_scores(docs, query_tokens):
    tfs=[Counter(d) for d in docs]
    avgdl=sum(len(d) for d in docs)/max(len(docs),1)
    df=Counter()
    for d in docs:
        for term in set(d): df[term]+=1
    n=len(docs); idf={t:math.log(1+(n-c+0.5)/(c+0.5)) for t,c in df.items()}
    scores=[]; k1=1.5; b=0.75
    for d,tf in zip(docs,tfs):
        dl=len(d); score=0.0
        for q in query_tokens:
            f=tf.get(q,0)
            if not f: continue
            denom=f+k1*(1-b+b*dl/max(avgdl,1e-9))
            score += idf.get(q,0.0)*(f*(k1+1))/denom
        scores.append(score)
    return scores

@lru_cache(maxsize=64)
def _prepare_index(path):
    with open(path, 'r', encoding='utf-8') as f:
        index = json.load(f)
    docs = index['docs']
    tfs = [Counter(doc) for doc in docs]
    avgdl = sum(len(doc) for doc in docs) / max(len(docs), 1)
    df = Counter()
    for doc in docs:
        for term in set(doc):
            df[term] += 1
    n = len(docs)
    idf = {term: math.log(1 + (n - count + 0.5) / (count + 0.5)) for term, count in df.items()}
    return index, tfs, avgdl, idf


def _cached_bm25_scores(index, tfs, avgdl, idf, query_tokens):
    scores = []
    k1 = 1.5
    b = 0.75
    for doc, tf in zip(index['docs'], tfs):
        dl = len(doc)
        score = 0.0
        for query in query_tokens:
            frequency = tf.get(query, 0)
            if not frequency:
                continue
            denominator = frequency + k1 * (1 - b + b * dl / max(avgdl, 1e-9))
            score += idf.get(query, 0.0) * (frequency * (k1 + 1)) / denominator
        scores.append(score)
    return scores


def retrieve_schema(db_id, input_text, index_dir='artifacts/indexes/schema_bm25', top_k=30):
    index, tfs, avgdl, idf = _prepare_index(str(Path(index_dir) / f'{db_id}.json'))
    scores = _cached_bm25_scores(index, tfs, avgdl, idf, tokenize(input_text)); ranked=sorted(enumerate(scores), key=lambda x:x[1], reverse=True)
    out=[]
    for i,score in ranked[:top_k]:
        item=dict(index['meta'][i]); item['score']=float(score); out.append(item)
    return out


def apply_schema_closure(profile, retrieved, mode='pk_fk_not_null_unique_parent'):
    """Add constraint-critical columns without using gold annotations."""
    mode = str(mode or 'none').lower()
    selected = {}
    table_map = {table['name']: table for table in profile.get('tables', [])}
    column_map = {
        table['name']: {col['name']: col for col in table.get('columns', [])}
        for table in profile.get('tables', [])
    }

    def add(table, column, reason, score=None):
        col = column_map.get(table, {}).get(column)
        if not col:
            return False
        key = f'{table}.{column}'
        if key in selected:
            return False
        selected[key] = {
            'db_id': profile.get('db_id'),
            'table': table,
            'column': column,
            'type': col.get('type'),
            'sample_values': col.get('sample_values', [])[:5],
            'is_primary_key': col.get('is_primary_key', False),
            'is_foreign_key': col.get('is_foreign_key', False),
            'score': score,
            'closure_reason': reason,
        }
        return True

    for item in retrieved or []:
        add(item.get('table'), item.get('column'), 'retrieved', item.get('score'))
    if mode in {'none', 'retrieved_only'}:
        return list(selected.values())

    include_not_null = 'not_null' in mode or 'required' in mode or mode == 'full_table'
    include_unique = 'unique' in mode or mode == 'full_table'
    include_parent = 'parent' in mode or mode == 'full_table'
    full_table = mode in {'full', 'full_table', 'table_full_columns'}
    queue = list(dict.fromkeys(item.get('table') for item in selected.values() if item.get('table')))
    seen_tables = set()
    while queue:
        table_name = queue.pop(0)
        if table_name in seen_tables or table_name not in table_map:
            continue
        seen_tables.add(table_name)
        table = table_map[table_name]
        cols = table.get('columns', [])
        if full_table:
            for col in cols:
                add(table_name, col['name'], 'table_full_columns')
        for col in cols:
            if col.get('is_primary_key'):
                add(table_name, col['name'], 'primary_key')
            if col.get('is_foreign_key'):
                add(table_name, col['name'], 'foreign_key')
                fk = col.get('foreign_key') or {}
                parent = fk.get('to_table')
                parent_col = fk.get('to_column')
                if include_parent and parent:
                    add(parent, parent_col, 'parent_fk_target')
                    if parent not in seen_tables:
                        queue.append(parent)
        if include_not_null:
            for column in table.get('required_insert_columns', []) or []:
                add(table_name, column, 'required_not_null')
        if include_unique:
            for index in table.get('unique_indexes', []) or []:
                for column in index.get('columns', []) or []:
                    add(table_name, column, 'unique_constraint')
    return list(selected.values())


def retrieval_diagnostics(profile, linked_columns, sample, matched_values=None):
    selected_columns = {f"{x.get('table')}.{x.get('column')}" for x in linked_columns or []}
    selected_tables = {x.get('table') for x in linked_columns or [] if x.get('table')}
    gold_tables = set(sample.get('gold_tables') or [])
    gold_columns = set(sample.get('gold_columns') or [])
    total_columns = sum(len(t.get('columns', [])) for t in profile.get('tables', []))
    total_tables = len(profile.get('tables', []))
    required = set()
    for table in profile.get('tables', []):
        if gold_tables and table.get('name') not in gold_tables:
            continue
        tname = table.get('name')
        required.update(f'{tname}.{c}' for c in table.get('required_insert_columns', []) or [])
        for col in table.get('columns', []):
            if col.get('is_primary_key') or col.get('is_foreign_key'):
                required.add(f"{tname}.{col.get('name')}")

    gold_cells = set()
    for rec in sample.get('gold_records') or []:
        table = rec.get('table')
        values = rec.get('values') or {}
        if isinstance(values, dict):
            for column, value in values.items():
                gold_cells.add((str(table), str(column), '' if value is None else str(value).casefold()))
    matched_cells = {
        (str(x.get('table')), str(x.get('column')), str(x.get('value', '')).casefold())
        for x in matched_values or []
    }
    return {
        'selected_tables': len(selected_tables),
        'selected_columns': len(selected_columns),
        'total_tables': total_tables,
        'total_columns': total_columns,
        'table_recall': len(selected_tables & gold_tables) / len(gold_tables) if gold_tables else None,
        'column_recall': len(selected_columns & gold_columns) / len(gold_columns) if gold_columns else None,
        'required_column_recall': len(selected_columns & required) / len(required) if required else None,
        'schema_compression_ratio': len(selected_columns) / total_columns if total_columns else None,
        'value_match_recall': len(matched_cells & gold_cells) / len(gold_cells) if gold_cells else None,
    }
