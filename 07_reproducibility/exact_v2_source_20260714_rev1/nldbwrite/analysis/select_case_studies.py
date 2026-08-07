import argparse
import json
from pathlib import Path
from typing import Any

from nldbwrite.common import iter_jsonl, load_json, write_jsonl


RUN_KEYS = {
    'm0': 'qwen7b_m0_direct',
    'm2': 'qwen7b_m2_extract_builder',
    'm5_pred': 'qwen7b_m5_fact_first',
    'm5_gold': 'qwen7b_m5_gold_facts',
    'm5_cbr': 'qwen7b_m5_facts_cbr_hybrid_k3',
    'stage1': 'qwen7b_m5_stage1_facts',
}


def load_jsonl_map(path: Path, key: str = 'sample_id') -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return {str(row[key]): row for row in iter_jsonl(path)}


def load_csv_map(path: Path, key: str = 'sample_id') -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    import csv
    with open(path, encoding='utf-8', newline='') as f:
        return {str(row[key]): row for row in csv.DictReader(f)}


def load_run(run_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        'raw': load_jsonl_map(run_dir / 'raw_generations.jsonl'),
        'parsed': load_jsonl_map(run_dir / 'parsed_outputs.jsonl'),
        'built': load_jsonl_map(run_dir / 'pred_sql.jsonl'),
        'evaluation': load_jsonl_map(run_dir / 'evaluation.jsonl'),
    }


def truth(run: dict[str, dict[str, Any]], sid: str) -> bool:
    return bool((run.get('evaluation') or {}).get(sid, {}).get('correct'))


def fact_metric(fact_eval: dict[str, dict[str, Any]], sid: str, key: str) -> float | None:
    value = (fact_eval.get(sid) or {}).get(key)
    if value in (None, ''):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def system_snapshot(run: dict[str, dict[str, Any]], sid: str) -> dict[str, Any]:
    raw = (run.get('raw') or {}).get(sid, {})
    parsed = (run.get('parsed') or {}).get(sid, {})
    built = (run.get('built') or {}).get(sid, {})
    ev = (run.get('evaluation') or {}).get(sid, {})
    return {
        'retrieved_cases': raw.get('case_retrieval'),
        'predicted_records': (parsed.get('pred_json') or {}).get('records'),
        'built_sql': built.get('pred_sql'),
        'execution_status': {
            'correct': ev.get('correct'),
            'execution_success': ev.get('execution_success'),
            'error_type': ev.get('error_type'),
            'error_message': ev.get('error_message'),
        },
        'post_state_diff': {
            'pred_changed_tables': ev.get('pred_changed_tables'),
            'gold_changed_tables': ev.get('gold_changed_tables'),
            'affected_tables': ev.get('affected_tables'),
        },
    }


def select_cases(data: list[dict[str, Any]], runs: dict[str, dict[str, Any]], fact_eval: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used_categories: set[str] = set()

    def add(category: str, diagnosis: str, sample: dict[str, Any]) -> None:
        if category in used_categories:
            return
        sid = str(sample['id'])
        case = {
            'category': category,
            'sample_id': sid,
            'input_text': sample.get('input_text'),
            'gold_records': sample.get('gold_records'),
            'stage1_facts': ((runs.get('stage1') or {}).get('parsed') or {}).get(sid, {}).get('pred_facts'),
            'diagnosis': diagnosis,
            'systems': {
                name: system_snapshot(run, sid)
                for name, run in runs.items()
                if name != 'stage1'
            },
        }
        selected.append(case)
        used_categories.add(category)

    for sample in data:
        sid = str(sample['id'])
        if 'm0_wrong_m2_correct' not in used_categories and not truth(runs.get('m0', {}), sid) and truth(runs.get('m2', {}), sid):
            add('m0_wrong_m2_correct', 'Direct SQL failed while extract-build recovered the write structure.', sample)
        if 'm2_wrong_m5_cbr_correct' not in used_categories and not truth(runs.get('m2', {}), sid) and truth(runs.get('m5_cbr', {}), sid):
            add('m2_wrong_m5_cbr_correct', 'Fact-first CBR corrected a case missed by one-stage extraction.', sample)
        conflict_recall = fact_metric(fact_eval, sid, 'conflict_key_fact_recall')
        if (
            'm5_pred_missing_conflict_key' not in used_categories
            and str(sample.get('operation_type')).lower() in {'upsert', 'update', 'replace'}
            and not truth(runs.get('m5_pred', {}), sid)
            and conflict_recall is not None and conflict_recall < 1.0
        ):
            add('m5_pred_missing_conflict_key', 'Predicted facts missed at least one conflict key required for an UPSERT-like write.', sample)
        if 'm5_gold_correct_pred_wrong' not in used_categories and not truth(runs.get('m5_pred', {}), sid) and truth(runs.get('m5_gold', {}), sid):
            add('m5_gold_correct_pred_wrong', 'Gold facts succeeded while predicted facts failed, isolating Stage 1 fact extraction as the bottleneck.', sample)
        cbr_raw = ((runs.get('m5_cbr') or {}).get('raw') or {}).get(sid, {})
        if (
            'm5_cbr_retrieval_noise' not in used_categories
            and not truth(runs.get('m5_cbr', {}), sid)
            and isinstance(cbr_raw.get('case_retrieval'), dict)
            and cbr_raw['case_retrieval'].get('retrieved_case_ids')
        ):
            add('m5_cbr_retrieval_noise', 'M5-Facts+CBR failed despite retrieved cases, useful for discussing retrieval noise or misleading demonstrations.', sample)
        relational = str(sample.get('impact_scope') or '').startswith('relational') or len(sample.get('gold_tables') or []) > 1
        if 'relational_failure' not in used_categories and relational and not truth(runs.get('m5_cbr', {}), sid):
            add('relational_failure', 'A relational multi-table write remained unsolved and shows the remaining hard-case boundary.', sample)
        if len(used_categories) >= 6:
            break
    return selected


def write_markdown(cases: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    chunks = []
    for case in cases:
        chunks.append(
            f"## {case['category']}: {case['sample_id']}\n\n"
            f"Input: {case.get('input_text')}\n\n"
            f"Diagnosis: {case.get('diagnosis')}\n"
        )
    path.write_text('\n'.join(chunks), encoding='utf-8')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data/processed/nl_db_write_augmented900_v1.json')
    ap.add_argument('--results-root', default='results/server_aug900')
    ap.add_argument('--out-jsonl', default='paper/case_studies/case_studies.jsonl')
    ap.add_argument('--out-md', default='paper/case_studies/case_studies.md')
    args = ap.parse_args()
    data = load_json(args.data)
    root = Path(args.results_root)
    runs = {name: load_run(root / run_name) for name, run_name in RUN_KEYS.items() if (root / run_name).exists()}
    fact_eval = load_csv_map(root / RUN_KEYS['stage1'] / 'fact_eval_per_sample.csv')
    cases = select_cases(data, runs, fact_eval)
    write_jsonl(cases, args.out_jsonl)
    write_markdown(cases, Path(args.out_md))
    print(json.dumps({'case_studies': len(cases), 'out_jsonl': args.out_jsonl, 'out_md': args.out_md}, indent=2))


if __name__ == '__main__':
    main()
