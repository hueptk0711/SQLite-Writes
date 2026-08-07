import argparse
import csv
import json
from pathlib import Path

from nldbwrite.common import iter_jsonl, save_json


def load_optional_map(run_dir: Path, filename: str) -> dict[str, dict]:
    path = run_dir / filename
    return {str(row['sample_id']): row for row in iter_jsonl(path)} if path.exists() else {}


def sql_signature(row: dict | None) -> str:
    return '\n'.join(' '.join(str(sql).lower().split()) for sql in (row or {}).get('pred_sql') or [])


def average(values):
    values = [float(value) for value in values if value is not None]
    return sum(values) / len(values) if values else None


def confidence_features(raw: dict, built: dict, comparison: dict | None):
    diagnostics = raw.get('retrieval_diagnostics') or {}
    case_log = raw.get('case_retrieval') or {}
    scores = case_log.get('retrieval_scores') or []
    trace = built.get('builder_trace') or {}
    metadata = built.get('sql_metadata') or []
    dropped = sum(len(item.get('dropped_columns') or []) for item in metadata)
    warnings = len(built.get('builder_errors') or [])
    status = str(built.get('builder_status') or '')
    disagreement = bool(comparison is not None and sql_signature(built) != sql_signature(comparison))
    schema_scores = [
        item.get('score') for item in (raw.get('retrieved_columns') or [])
        if isinstance(item, dict) and item.get('score') is not None
    ]
    features = {
        'parse_ok': str(built.get('parse_status') or '') == 'success',
        'builder_ok': status in {'success', 'direct_sql'},
        'builder_partial': status == 'partial',
        'builder_warning_count': warnings,
        'dropped_column_count': dropped or int(trace.get('dropped_column_count') or 0),
        'required_check_applied': int(trace.get('required_column_check_applied') or 0),
        'schema_retrieval_score': average(schema_scores),
        'schema_selected_columns': diagnostics.get('selected_columns'),
        'schema_compression_ratio': diagnostics.get('schema_compression_ratio'),
        'retrieval_similarity': average(scores),
        'retrieval_top_similarity': max([float(value) for value in scores], default=None),
        'retrieval_leakage_passed': case_log.get('leakage_check_passed'),
        'predicted_statement_count': len(built.get('pred_sql') or []),
        'direct_structured_disagreement': disagreement,
        'repair_used': built.get('repair_status') is not None,
    }
    score = 1.0
    if not features['parse_ok']:
        score -= 0.45
    if not features['builder_ok']:
        score -= 0.35
    if features['builder_partial']:
        score -= 0.20
    score -= min(0.20, 0.04 * features['builder_warning_count'])
    score -= min(0.20, 0.04 * features['dropped_column_count'])
    if features['schema_retrieval_score'] is not None:
        schema_score = max(0.0, min(1.0, float(features['schema_retrieval_score'])))
        score -= 0.12 * (1.0 - schema_score)
    if features['retrieval_top_similarity'] is not None:
        top = max(0.0, min(1.0, float(features['retrieval_top_similarity'])))
        score -= 0.10 * (1.0 - top)
    if features['retrieval_leakage_passed'] is False:
        score -= 0.50
    if features['predicted_statement_count'] == 0:
        score -= 0.35
    if features['direct_structured_disagreement']:
        score -= 0.12
    if features['repair_used']:
        score -= 0.08
    features['confidence_score'] = max(0.0, min(1.0, score))
    features['deployment_risk_score'] = 1.0 - features['confidence_score']
    features['score_type'] = 'deployment_risk_heuristic'
    features['score_calibrated'] = False
    return features


def accuracy(rows, key):
    values = [int(bool(row.get(key))) for row in rows if row.get(key) is not None]
    return sum(values) / len(values) if values else None


def analyze(run_dir, out_dir, comparison_run=None):
    run_dir = Path(run_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = load_optional_map(run_dir, 'raw_generations.jsonl')
    built = load_optional_map(run_dir, 'pred_sql.jsonl')
    evaluation = load_optional_map(run_dir, 'evaluation.jsonl')
    comparison = load_optional_map(Path(comparison_run), 'pred_sql.jsonl') if comparison_run else {}
    rows = []
    for sid, eval_row in evaluation.items():
        features = confidence_features(raw.get(sid, {}), built.get(sid, {}), comparison.get(sid) if comparison else None)
        rows.append({
            'sample_id': sid,
            'db_id': eval_row.get('db_id'),
            **features,
            'execution_success': bool(eval_row.get('execution_success')),
            'target_state_correct': bool(eval_row.get('target_state_correct', eval_row.get('correct'))),
            'strict_full_state_correct': eval_row.get('strict_full_state_correct'),
            'error_type': eval_row.get('error_type'),
        })
    rows.sort(key=lambda row: (-row['confidence_score'], row['sample_id']))
    coverage_rows = []
    n = len(rows)
    for coverage in (1.0, 0.95, 0.90, 0.80, 0.70, 0.60, 0.50, 0.40, 0.30, 0.20, 0.10):
        accepted_n = max(1, round(n * coverage)) if n else 0
        accepted = rows[:accepted_n]
        coverage_rows.append({
            'requested_coverage': coverage,
            'accepted_samples': accepted_n,
            'actual_coverage': accepted_n / n if n else 0.0,
            'confidence_threshold': accepted[-1]['confidence_score'] if accepted else None,
            'target_state_accuracy': accuracy(accepted, 'target_state_correct'),
            'strict_full_state_accuracy': accuracy(accepted, 'strict_full_state_correct'),
            'execution_success_rate': accuracy(accepted, 'execution_success'),
        })
    for filename, output_rows in (
        ('selective_execution_samples.csv', rows),
        ('selective_execution_coverage.csv', coverage_rows),
    ):
        columns = list(output_rows[0]) if output_rows else []
        with open(out_dir / filename, 'w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(output_rows)
    save_json({
        'run_dir': str(run_dir),
        'comparison_run': str(comparison_run) if comparison_run else None,
        'score_type': 'deployment_risk_heuristic',
        'score_is_deployable': True,
        'score_uses_gold_labels': False,
        'score_calibrated': False,
        'notes': 'Hard-coded heuristic for risk ranking; tune on development data before calling it calibrated confidence.',
        'coverage': coverage_rows,
    }, out_dir / 'selective_execution_summary.json')
    print(json.dumps(coverage_rows, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--comparison-run')
    parser.add_argument('--out-dir', required=True)
    args = parser.parse_args()
    analyze(args.run_dir, args.out_dir, args.comparison_run)


if __name__ == '__main__':
    main()
