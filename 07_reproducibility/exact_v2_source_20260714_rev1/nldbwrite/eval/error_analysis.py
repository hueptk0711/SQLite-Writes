import argparse, csv, json, re
from collections import Counter
from pathlib import Path
from nldbwrite.common import iter_jsonl, load_json, save_json


_CONFLICT_TARGET_RE = re.compile(r'ON\s+CONFLICT\s*\(([^)]*)\)', re.I | re.S)
_UPDATE_SET_RE = re.compile(r'DO\s+UPDATE\s+SET\s+(.+?)(?:\s+WHERE\b|;|$)', re.I | re.S)


def sql_semantics(sqls):
    text = '\n'.join(sqls or [])
    target_match = _CONFLICT_TARGET_RE.search(text)
    update_match = _UPDATE_SET_RE.search(text)
    targets = []
    if target_match:
        targets = [part.strip().strip('"`[]') for part in target_match.group(1).split(',') if part.strip()]
    update_columns = []
    if update_match:
        for assignment in update_match.group(1).split(','):
            column = assignment.split('=', 1)[0].strip().strip('"`[]')
            if column:
                update_columns.append(column)
    return {
        'has_insert': bool(re.search(r'\b(?:INSERT|REPLACE)\b', text, re.I)),
        'has_update_statement': bool(re.search(r'\bUPDATE\s+', text, re.I)),
        'has_do_update': bool(update_match),
        'has_do_nothing': bool(re.search(r'DO\s+NOTHING', text, re.I)),
        'conflict_target': sorted(targets),
        'update_columns': sorted(update_columns),
    }


def upsert_taxonomy(eval_row, sample, pred_row):
    operation = str(sample.get('operation_type') or '').lower()
    if operation not in {'upsert', 'update', 'replace'}:
        return None
    pred = sql_semantics((pred_row or {}).get('pred_sql') or [])
    gold = sql_semantics(sample.get('gold_sql') or [])
    subtype = eval_row.get('error_subtype') or eval_row.get('error_type')
    if not pred['has_insert'] and not pred['has_update_statement']:
        return 'missing_write_statement'
    if gold['has_do_update'] and not pred['has_do_update'] and not pred['has_update_statement']:
        return 'insert_instead_of_update'
    if not gold['has_do_update'] and pred['has_do_update']:
        return 'update_instead_of_insert'
    if gold['conflict_target'] and not pred['conflict_target']:
        return 'missing_conflict_key'
    if gold['conflict_target'] != pred['conflict_target']:
        return 'wrong_conflict_target'
    if gold['update_columns'] != pred['update_columns']:
        return 'wrong_update_columns'
    if subtype == 'missing_rows':
        return 'incomplete_multi_row_upsert'
    if subtype == 'extra_rows':
        return 'wrong_matched_row'
    if subtype in {'wrong_value', 'missing_columns', 'extra_columns'}:
        return 'wrong_inserted_or_updated_values'
    return 'other_upsert_state_mismatch'


def detailed_taxonomy(eval_row, sample, pred_row, case_log):
    if eval_row.get('target_state_correct', eval_row.get('correct')):
        return 'correct'
    retrieval = retrieval_error(case_log)
    if retrieval:
        return retrieval
    stage = error_stage(eval_row, case_log)
    if stage != 'stage5_state_mismatch':
        return eval_row.get('error_type') or stage
    return upsert_taxonomy(eval_row, sample, pred_row) or eval_row.get('error_subtype') or eval_row.get('error_type') or 'wrong_state'

def retrieval_error(log):
    if not isinstance(log, dict):
        return None
    if not log.get('leakage_check_passed', True):
        return 'retrieval_leakage'
    cases = log.get('cases') or []
    if not cases:
        return 'no_retrieved_cases'
    components = [case.get('score_components') or {} for case in cases]
    if log.get('gold_query_metadata_used') and components and all(float(c.get('operation_match') or 0) == 0 for c in components):
        return 'retrieved_wrong_operation'
    query_signals = log.get('query_signals') or {}
    if query_signals.get('predicted_schema') and components and all(float(c.get('schema_overlap') or 0) == 0 for c in components):
        return 'retrieved_wrong_schema'
    return None


def error_stage(row, case_log=None):
    error_type = row.get('error_type') or 'none'
    if error_type == 'none':
        return 'correct'
    if retrieval_error(case_log):
        return 'stage1_prompt_retrieval'
    if error_type in {'missing_prediction', 'json_parse_error'} or row.get('parse_status') == 'error':
        return 'stage2_extraction'
    if error_type == 'builder_error' or row.get('builder_status') in {'error', 'parse_error', 'partial'}:
        return 'stage3_builder'
    if error_type in {'syntax_error', 'constraint_error', 'schema_error', 'unsafe_sql', 'timeout', 'execution_error'} and not row.get('execution_success'):
        return 'stage4_execution'
    if row.get('error_group') == 'wrong_state' or error_type in {'wrong_table', 'missing_rows', 'extra_rows', 'missing_columns', 'extra_columns', 'wrong_upsert_behavior', 'wrong_value'}:
        return 'stage5_state_mismatch'
    return 'stage4_execution'

def analyze(run_dir, data_path=None):
    run_dir = Path(run_dir)
    rows=list(iter_jsonl(run_dir/'evaluation.jsonl')); raw_path=run_dir/'raw_generations.jsonl'; case_logs={str(x['sample_id']):x.get('case_retrieval') for x in iter_jsonl(raw_path)} if raw_path.exists() else {}; by_error=Counter(r.get('error_type') or 'none' for r in rows); by_db={}; by_group=Counter(r.get('error_group') or r.get('error_type') or 'none' for r in rows); by_subtype=Counter(r.get('error_subtype') or 'none' for r in rows); by_stage=Counter(error_stage(r,case_logs.get(str(r.get('sample_id')))) for r in rows); by_retrieval=Counter(retrieval_error(case_logs.get(str(r.get('sample_id')))) or 'none' for r in rows if not r.get('correct'))
    for r in rows:
        by_db.setdefault(r.get('db_id'), Counter()); by_db[r.get('db_id')][r.get('error_type') or 'none'] += 1
    out={'error_distribution':dict(by_error),'error_group_distribution':dict(by_group),'error_subtype_distribution':dict(by_subtype),'retrieval_error_distribution':dict(by_retrieval),'error_stage_distribution':dict(by_stage),'error_by_db':{k:dict(v) for k,v in by_db.items()},'examples':{}}
    for error_type in by_error:
        out['examples'][error_type]=[r['sample_id'] for r in rows if (r.get('error_type') or 'none') == error_type][:20]
    if data_path:
        gold={str(x['id']):x for x in load_json(data_path)}
        pred_path = run_dir / 'pred_sql.jsonl'
        predictions = {str(x['sample_id']): x for x in iter_jsonl(pred_path)} if pred_path.exists() else {}
        taxonomy_rows = []
        taxonomy_counts = Counter()
        for row in rows:
            sid = str(row['sample_id'])
            sample = gold.get(sid, {})
            taxonomy = detailed_taxonomy(row, sample, predictions.get(sid), case_logs.get(sid))
            taxonomy_counts[taxonomy] += 1
            taxonomy_rows.append({
                'sample_id': sid,
                'db_id': row.get('db_id'),
                'source_group_id': sample.get('source_group_id') or sid,
                'operation_type': sample.get('operation_type'),
                'example_origin_category': sample.get('example_origin_category'),
                'target_state_correct': row.get('target_state_correct', row.get('correct')),
                'strict_full_state_correct': row.get('strict_full_state_correct'),
                'error_stage': error_stage(row, case_logs.get(sid)),
                'error_type': row.get('error_type'),
                'error_subtype': row.get('error_subtype'),
                'detailed_taxonomy': taxonomy,
            })
        out['detailed_error_taxonomy'] = dict(taxonomy_counts)
        if taxonomy_rows:
            with open(run_dir / 'error_taxonomy.csv', 'w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(taxonomy_rows[0]))
                writer.writeheader()
                writer.writerows(taxonomy_rows)
        for field in ['difficulty','auto_difficulty','impact_scope','operation_type','input_type','row_count_bucket']:
            grouped={}
            for row in rows:
                group=str(gold.get(row['sample_id'],{}).get(field,'unknown'))
                grouped.setdefault(group,Counter())[row.get('error_type') or 'none'] += 1
            out[f'error_by_{field}']={k:dict(v) for k,v in grouped.items()}
    save_json(out, run_dir/'error_analysis.json'); print(json.dumps(out, ensure_ascii=False, indent=2))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--run-dir', required=True); ap.add_argument('--data'); args=ap.parse_args(); analyze(args.run_dir,args.data)
if __name__=='__main__': main()
