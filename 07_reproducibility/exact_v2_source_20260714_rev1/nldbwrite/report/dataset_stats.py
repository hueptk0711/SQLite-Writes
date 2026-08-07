import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from nldbwrite.common import load_json, read_id_file, save_json


def category(row):
    value = str(row.get('example_origin_category') or '').lower()
    aug = str(row.get('augmentation_type') or '').lower()
    if value == 'controlled_subtask_derivation':
        if 'single_row' in aug:
            return 'single_row_derivation'
        if 'small_batch' in aug:
            return 'small_batch_derivation'
    if value:
        return value
    if not row.get('is_augmented'):
        return 'original'
    if 'single_row' in aug:
        return 'single_row_derivation'
    if 'small_batch' in aug:
        return 'small_batch_derivation'
    return 'format_transformation'


def row_for_partition(name, rows):
    categories = Counter(category(row) for row in rows)
    operations = Counter(str(row.get('operation_type') or 'unknown').lower() for row in rows)
    return {
        'Partition': name,
        'Samples': len(rows),
        'SourceGroups': len({str(row.get('source_group_id') or row['id']) for row in rows}),
        'Original': categories.get('original', 0) + categories.get('independently_authored_original', 0),
        'FormatTransformations': categories.get('format_transformation', 0) + categories.get('semantics_preserving_transformation', 0),
        'SingleRowDerivations': categories.get('single_row_derivation', 0),
        'SmallBatchDerivations': categories.get('small_batch_derivation', 0),
        'Insert': operations.get('insert', 0),
        'UpsertOrUpdate': sum(operations.get(key, 0) for key in ('upsert', 'update', 'replace')),
        'Relational': sum(str(row.get('impact_scope') or '').lower().startswith('relational') or int(row.get('num_tables') or 0) > 1 for row in rows),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    parser.add_argument('--dev-ids', required=True)
    parser.add_argument('--test-ids', required=True)
    parser.add_argument('--out-dir', required=True)
    args = parser.parse_args()
    data = load_json(args.data)
    dev_ids = read_id_file(args.dev_ids)
    test_ids = read_id_file(args.test_ids)
    rows = [
        row_for_partition('Development', [row for row in data if str(row['id']) in dev_ids]),
        row_for_partition('Test', [row for row in data if str(row['id']) in test_ids]),
        row_for_partition('All', data),
    ]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / 'dataset_statistics.csv', 'w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    save_json(rows, out_dir / 'dataset_statistics.json')
    with open(out_dir / 'dataset_statistics.tex', 'w', encoding='utf-8') as handle:
        handle.write('\\begin{tabular}{lrrrrrrrrr}\n\\toprule\n')
        handle.write('Partition & Samples & Groups & Original & Format & Single & Small & INSERT & UPSERT & Relational \\\\ \n\\midrule\n')
        for row in rows:
            values = [row[key] for key in row]
            handle.write(' & '.join(map(str, values)) + ' \\\\ \n')
        handle.write('\\bottomrule\n\\end{tabular}\n')
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
