import argparse
import csv
import json
from pathlib import Path

from nldbwrite.common import iter_jsonl, load_json, save_json


TRACE_KEYS = {
    'fk_order': {'fk_order_changed'},
    'required_check': {'required_column_check_applied'},
    'type_normalization': {'type_normalization_changed'},
    'conflict_inference': {'inferred_conflict_target_used'},
    'safety_filter': {'safety_filter_applied'},
}


def load_map(path: Path, filename: str) -> dict[str, dict]:
    file_path = path / filename
    if not file_path.exists():
        raise FileNotFoundError(file_path)
    return {str(row['sample_id']): row for row in iter_jsonl(file_path)}


def normalized_prediction(row: dict | None):
    row = row or {}
    sql = [' '.join(str(statement).split()) for statement in row.get('pred_sql') or []]
    return row.get('builder_status'), sql, row.get('builder_errors') or []


def metric(row: dict | None, name: str) -> int:
    row = row or {}
    if name == 'execution_success':
        return int(bool(row.get(name)))
    value = row.get(name)
    if value is None and name == 'target_state_correct':
        value = row.get('correct')
    return int(bool(value))


def summarize_pair(name: str, base_dir: Path, ablation_dir: Path, gold: dict[str, dict]):
    base_pred = load_map(base_dir, 'pred_sql.jsonl')
    ablation_pred = load_map(ablation_dir, 'pred_sql.jsonl')
    base_eval = load_map(base_dir, 'evaluation.jsonl')
    ablation_eval = load_map(ablation_dir, 'evaluation.jsonl')
    ids = sorted(set(base_eval) & set(ablation_eval))
    trace_keys = TRACE_KEYS.get(name, set())
    changed = {
        sid for sid in ids
        if normalized_prediction(base_pred.get(sid)) != normalized_prediction(ablation_pred.get(sid))
    }
    activated = {
        sid for sid in ids
        if any((base_pred.get(sid, {}).get('builder_trace') or {}).get(key) for key in trace_keys)
    }
    affected = changed | activated
    scopes = {'all': ids, 'affected': sorted(affected), 'changed_sql': sorted(changed)}
    rows = []
    for scope, scope_ids in scopes.items():
        n = len(scope_ids)
        row = {
            'component': name,
            'scope': scope,
            'num_samples': n,
            'activation_rate': len(affected) / len(ids) if ids else 0.0,
            'changed_prediction_rate': len(changed) / len(ids) if ids else 0.0,
        }
        for key in ('target_state_correct', 'strict_full_state_correct', 'execution_success'):
            base_values = [metric(base_eval[sid], key) for sid in scope_ids]
            ablated_values = [metric(ablation_eval[sid], key) for sid in scope_ids]
            base_score = sum(base_values) / n if n else None
            ablated_score = sum(ablated_values) / n if n else None
            label = key.replace('_correct', '').replace('_success', '')
            row[f'base_{label}'] = base_score
            row[f'ablated_{label}'] = ablated_score
            row[f'delta_{label}'] = (base_score - ablated_score) if n else None
        row['affected_source_groups'] = len({
            str(gold.get(sid, {}).get('source_group_id') or sid) for sid in scope_ids
        })
        rows.append(row)
    return rows, sorted(affected), sorted(changed)


def analyze(base_run, ablations, data_path, out_dir):
    base_dir = Path(base_run)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gold = {str(row['id']): row for row in load_json(data_path)}
    all_rows = []
    details = {}
    for spec in ablations:
        name, separator, path = spec.partition('=')
        if not separator or not name or not path:
            raise ValueError(f'Expected NAME=RUN_DIR, got: {spec}')
        rows, affected, changed = summarize_pair(name, base_dir, Path(path), gold)
        all_rows.extend(rows)
        details[name] = {
            'run_dir': path,
            'affected_sample_ids': affected,
            'changed_prediction_sample_ids': changed,
        }
    columns = list(all_rows[0]) if all_rows else []
    with open(out_dir / 'conditional_builder_ablation.csv', 'w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(all_rows)
    save_json({
        'base_run': str(base_dir),
        'data_path': str(data_path),
        'rows': all_rows,
        'details': details,
    }, out_dir / 'conditional_builder_ablation.json')
    print(json.dumps(all_rows, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-run', required=True)
    parser.add_argument('--ablation', action='append', default=[], help='NAME=RUN_DIR')
    parser.add_argument('--data', required=True)
    parser.add_argument('--out-dir', required=True)
    args = parser.parse_args()
    analyze(args.base_run, args.ablation, args.data, args.out_dir)


if __name__ == '__main__':
    main()
