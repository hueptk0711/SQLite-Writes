import argparse
import csv
import json
import statistics
from pathlib import Path

from nldbwrite.common import load_json, save_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--results-root', required=True)
    parser.add_argument('--out-dir', required=True)
    args = parser.parse_args()
    fold_rows = []
    for path in sorted(Path(args.results_root).glob('*/summary.json')):
        summary = load_json(path)
        prefix = 'qwen7b_s_cbr_h_lodo_'
        db_id = path.parent.name[len(prefix):] if path.parent.name.startswith(prefix) else path.parent.name
        fold_rows.append({
            'held_out_db': db_id,
            'num_samples': summary.get('num_samples'),
            'target_state_accuracy': summary.get('target_state_accuracy'),
            'strict_full_state_accuracy': summary.get('strict_full_state_accuracy'),
            'source_group_macro_accuracy': summary.get('source_group_macro_state_accuracy'),
            'execution_success_rate': summary.get('execution_success_rate'),
        })
    aggregate = {'num_databases': len(fold_rows)}
    for metric in ('target_state_accuracy', 'strict_full_state_accuracy', 'source_group_macro_accuracy', 'execution_success_rate'):
        values = [float(row[metric]) for row in fold_rows if row.get(metric) is not None]
        aggregate[f'{metric}_db_macro_mean'] = statistics.mean(values) if values else None
        aggregate[f'{metric}_db_std'] = statistics.stdev(values) if len(values) > 1 else 0.0 if values else None
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / 'lodo_per_database.csv', 'w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fold_rows[0]) if fold_rows else [])
        writer.writeheader()
        writer.writerows(fold_rows)
    save_json({'folds': fold_rows, 'aggregate': aggregate}, out_dir / 'lodo_summary.json')
    print(json.dumps({'folds': fold_rows, 'aggregate': aggregate}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
