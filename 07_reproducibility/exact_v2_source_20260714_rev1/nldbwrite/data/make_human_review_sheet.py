import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from nldbwrite.common import load_json, read_id_file


FIELDS = [
    'review_id',
    'sample_id',
    'db_id',
    'source_group_id',
    'operation_type',
    'input_type',
    'auto_difficulty',
    'row_count',
    'gold_tables',
    'gold_columns',
    'input_text',
    'gold_records_json',
    'gold_sql',
    'input_matches_gold_records',
    'gold_sql_correct',
    'target_tables_columns_reasonable',
    'operation_label_correct',
    'difficulty_label_correct',
    'duplicate_or_overlapping_variant',
    'reviewer_id',
    'notes',
]


def bucket(sample: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(sample.get('operation_type') or 'unknown'),
        str(sample.get('input_type') or 'unknown'),
        str(sample.get('auto_difficulty') or sample.get('difficulty') or 'unknown'),
    )


def stratified_sample(rows: list[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    groups = defaultdict(list)
    for row in rows:
        groups[bucket(row)].append(row)
    for values in groups.values():
        rng.shuffle(values)
    selected = []
    keys = sorted(groups, key=lambda k: len(groups[k]), reverse=True)
    while len(selected) < n and any(groups.values()):
        for key in keys:
            if groups[key] and len(selected) < n:
                selected.append(groups[key].pop())
    selected.sort(key=lambda x: str(x.get('id')))
    return selected


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data/processed/nl_db_write_augmented900_v1.json')
    ap.add_argument('--split-ids', default='data/splits/augmented900_v1/test_ids.txt')
    ap.add_argument('--out', default='data/review/augmented900_human_review_300.csv')
    ap.add_argument('--n', type=int, default=300)
    ap.add_argument('--seed', type=int, default=2026)
    args = ap.parse_args()

    data = load_json(args.data)
    if args.split_ids and Path(args.split_ids).exists():
        ids = read_id_file(args.split_ids)
        data = [x for x in data if str(x.get('id')) in ids]
    rows = stratified_sample(data, args.n, args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for i, row in enumerate(rows, start=1):
            writer.writerow({
                'review_id': f'HR{i:04d}',
                'sample_id': row.get('id'),
                'db_id': row.get('db_id'),
                'source_group_id': row.get('source_group_id'),
                'operation_type': row.get('operation_type'),
                'input_type': row.get('input_type'),
                'auto_difficulty': row.get('auto_difficulty') or row.get('difficulty'),
                'row_count': row.get('row_count') or row.get('num_records'),
                'gold_tables': ';'.join(row.get('gold_tables') or []),
                'gold_columns': ';'.join(row.get('gold_columns') or []),
                'input_text': row.get('input_text'),
                'gold_records_json': json.dumps(row.get('gold_records') or [], ensure_ascii=False),
                'gold_sql': '\n'.join(row.get('gold_sql') or []),
                'input_matches_gold_records': '',
                'gold_sql_correct': '',
                'target_tables_columns_reasonable': '',
                'operation_label_correct': '',
                'difficulty_label_correct': '',
                'duplicate_or_overlapping_variant': '',
                'reviewer_id': '',
                'notes': '',
            })
    guideline = out.with_suffix('.guideline.md')
    guideline.write_text(
        '# Human review guideline\n\n'
        'Use yes/no/unclear for the binary fields. Mark unclear whenever the input text is ambiguous.\n'
        'Reviewers should verify that the input text, gold records, gold SQL, target tables/columns, operation label, and difficulty label agree.\n'
        'For overlap adjudication, flag variants that preserve nearly identical meaning or reuse the same record payload too strongly.\n',
        encoding='utf-8',
    )
    print(f'Wrote {out}')
    print(f'Wrote {guideline}')


if __name__ == '__main__':
    main()
