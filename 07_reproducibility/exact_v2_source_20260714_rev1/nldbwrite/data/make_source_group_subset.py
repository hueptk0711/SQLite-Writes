import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from nldbwrite.common import load_json, read_id_file, save_json


def stable_key(seed, value):
    return hashlib.sha256(f'{seed}:{value}'.encode('utf-8')).hexdigest()


def original_priority(row):
    origin = str(row.get('example_origin_category') or row.get('origin') or '').casefold()
    variant = str((row.get('provenance') or {}).get('variant_type') or row.get('variant_type') or '').casefold()
    if origin == 'original' or variant == 'original' or str(row.get('id')) == str(row.get('source_group_id')):
        return 0
    return 1


def make_subset(data_path, source_ids_path, out_ids, manifest_out, size, seed):
    data = {str(row['id']): row for row in load_json(data_path)}
    source_ids = read_id_file(source_ids_path)
    candidates = [data[sid] for sid in source_ids if sid in data]
    by_group = {}
    for row in candidates:
        group = str(row.get('source_group_id') or row['id'])
        by_group.setdefault(group, []).append(row)
    selected = []
    for group in sorted(by_group, key=lambda value: stable_key(seed, value)):
        rows = sorted(by_group[group], key=lambda row: (original_priority(row), stable_key(seed, row['id'])))
        selected.append(rows[0])
        if len(selected) == size:
            break
    if len(selected) < size:
        selected_ids = {str(row['id']) for row in selected}
        remaining = sorted(
            (row for row in candidates if str(row['id']) not in selected_ids),
            key=lambda row: stable_key(seed, row['id']),
        )
        selected.extend(remaining[:size - len(selected)])
    if len(selected) < size:
        raise ValueError(f'Requested {size} rows but only {len(candidates)} are available')
    ids = sorted(str(row['id']) for row in selected)
    out_ids = Path(out_ids)
    out_ids.parent.mkdir(parents=True, exist_ok=True)
    out_ids.write_text('\n'.join(ids) + '\n', encoding='utf-8')
    breakdown = {}
    for field in ('db_id', 'operation_type', 'example_origin_category', 'impact_scope', 'row_count_bucket'):
        breakdown[field] = dict(Counter(str(row.get(field) or 'unknown') for row in selected))
    save_json({
        'data_path': str(data_path),
        'source_ids': str(source_ids_path),
        'size': len(selected),
        'source_groups': len({str(row.get('source_group_id') or row['id']) for row in selected}),
        'seed': seed,
        'breakdown': breakdown,
    }, manifest_out)
    print(json.dumps({'size': len(selected), 'out_ids': str(out_ids), 'breakdown': breakdown}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default='data/processed/nl_db_write_augmented900_v2_final.json')
    parser.add_argument('--source-ids', default='data/splits/augmented900_v2_final/test_ids.txt')
    parser.add_argument('--out-ids', default='data/splits/augmented900_v2_final/model_validation_subset300_ids.txt')
    parser.add_argument('--manifest-out', default='artifacts/manifests/model_validation_subset300.json')
    parser.add_argument('--size', type=int, default=300)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    make_subset(args.data, args.source_ids, args.out_ids, args.manifest_out, args.size, args.seed)


if __name__ == '__main__':
    main()
