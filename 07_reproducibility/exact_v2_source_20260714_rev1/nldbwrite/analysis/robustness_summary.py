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
    grouped = {}
    for path in sorted(Path(args.results_root).glob('*/summary.json')):
        summary = load_json(path)
        run_name = path.parent.name
        family = 's_cbr_h' if '_s_cbr_h_' in run_name else ('s_fs' if '_s_fs_' in run_name else 'other')
        grouped.setdefault(family, []).append(summary)
    rows = []
    for family, summaries in sorted(grouped.items()):
        row = {'family': family, 'num_orderings': len(summaries)}
        for metric in ('target_state_accuracy', 'strict_full_state_accuracy', 'execution_success_rate'):
            values = [float(summary[metric]) for summary in summaries if summary.get(metric) is not None]
            row[f'{metric}_mean'] = statistics.mean(values) if values else None
            row[f'{metric}_std'] = statistics.stdev(values) if len(values) > 1 else 0.0 if values else None
        rows.append(row)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / 'order_robustness.csv', 'w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
    save_json(rows, out_dir / 'order_robustness.json')
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
