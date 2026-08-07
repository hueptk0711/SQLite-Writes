import argparse
import csv
import json
from pathlib import Path

from nldbwrite.common import iter_jsonl, save_json


def load_cases(run_dir):
    path = Path(run_dir) / 'raw_generations.jsonl'
    return {
        str(row['sample_id']): ((row.get('case_retrieval') or {}).get('retrieved_case_ids') or [])
        for row in iter_jsonl(path)
    }


def compare(spec):
    name, separator, value = spec.partition('=')
    left_path, comma, right_path = value.partition(',')
    if not separator or not comma:
        raise ValueError(f'Expected NAME=LEFT_RUN,RIGHT_RUN, got: {spec}')
    left = load_cases(left_path)
    right = load_cases(right_path)
    ids = sorted(set(left) & set(right))
    mismatches = [sid for sid in ids if left[sid] != right[sid]]
    return {
        'comparison': name,
        'left_run': left_path,
        'right_run': right_path,
        'shared_samples': len(ids),
        'matched_case_ids': len(ids) - len(mismatches),
        'mismatched_case_ids': len(mismatches),
        'match_rate': (len(ids) - len(mismatches)) / len(ids) if ids else 0.0,
        'mismatch_sample_ids': mismatches,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--comparison', action='append', default=[])
    parser.add_argument('--out-dir', required=True)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    details = [compare(spec) for spec in args.comparison]
    summary = [{key: value for key, value in row.items() if key != 'mismatch_sample_ids'} for row in details]
    with open(out_dir / 'retrieval_fairness.csv', 'w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0]) if summary else [])
        writer.writeheader()
        writer.writerows(summary)
    save_json(details, out_dir / 'retrieval_fairness.json')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
