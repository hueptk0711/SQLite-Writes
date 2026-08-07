import argparse
import hashlib
import json
import random
from pathlib import Path

from nldbwrite.common import iter_jsonl, load_json, save_json, sha256_file


def load_rows(path):
    path = Path(path)
    if path.suffix.lower() == '.jsonl':
        return list(iter_jsonl(path))
    value = load_json(path)
    if not isinstance(value, list):
        raise ValueError('External input must be a JSON array or JSONL rows')
    return value


def validate_row(row, mode):
    missing = [key for key in ('id', 'db_id', 'input_text', 'gold_records', 'gold_sql') if not row.get(key)]
    if missing:
        raise ValueError(f"{row.get('id', '<unknown>')}: missing {', '.join(missing)}")
    if not isinstance(row['gold_records'], list) or not isinstance(row['gold_sql'], list):
        raise ValueError(f"{row['id']}: gold_records and gold_sql must be lists")
    if mode == 'hard':
        if row.get('source_sample_id'):
            raise ValueError(f"{row['id']}: independent hard examples cannot have source_sample_id")
        if row.get('independently_authored') is not True:
            raise ValueError(f"{row['id']}: independently_authored must be true")
    if mode == 'external':
        provenance = row.get('provenance') or {}
        missing_provenance = [key for key in ('source_dataset', 'license', 'dialect') if not provenance.get(key)]
        if missing_provenance:
            raise ValueError(f"{row['id']}: missing provenance fields {', '.join(missing_provenance)}")


def stable_group(row):
    return str(row.get('source_group_id') or row['id'])


def prepare(input_path, out_data, out_dev_ids, out_test_ids, manifest_out, mode, dev_ratio, seed):
    rows = load_rows(input_path)
    seen = set()
    for row in rows:
        validate_row(row, mode)
        sid = str(row['id'])
        if sid in seen:
            raise ValueError(f'Duplicate id: {sid}')
        seen.add(sid)
        row['id'] = sid
        row['source_group_id'] = stable_group(row)
        row['example_origin_category'] = 'independent_hard' if mode == 'hard' else 'external'
        row['is_augmented'] = False
        row['augmentation_type'] = None
    groups = sorted({stable_group(row) for row in rows})
    rng = random.Random(seed)
    rng.shuffle(groups)
    dev_count = max(1, round(len(groups) * dev_ratio)) if len(groups) > 1 else 0
    dev_groups = set(groups[:dev_count])
    dev_ids = sorted(str(row['id']) for row in rows if stable_group(row) in dev_groups)
    test_ids = sorted(str(row['id']) for row in rows if stable_group(row) not in dev_groups)
    if set(dev_ids) & set(test_ids):
        raise AssertionError('Split overlap detected')
    Path(out_data).parent.mkdir(parents=True, exist_ok=True)
    Path(out_data).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    Path(out_dev_ids).parent.mkdir(parents=True, exist_ok=True)
    Path(out_dev_ids).write_text('\n'.join(dev_ids) + ('\n' if dev_ids else ''), encoding='utf-8')
    Path(out_test_ids).write_text('\n'.join(test_ids) + ('\n' if test_ids else ''), encoding='utf-8')
    save_json({
        'mode': mode,
        'source_path': str(input_path),
        'source_sha256': sha256_file(input_path),
        'num_rows': len(rows),
        'num_source_groups': len(groups),
        'num_dev': len(dev_ids),
        'num_test': len(test_ids),
        'seed': seed,
        'dev_ratio': dev_ratio,
        'id_set_sha256': hashlib.sha256('\n'.join(sorted(seen)).encode('utf-8')).hexdigest(),
    }, manifest_out)
    print(json.dumps({'num_rows': len(rows), 'num_dev': len(dev_ids), 'num_test': len(test_ids)}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--out-data', required=True)
    parser.add_argument('--out-dev-ids', required=True)
    parser.add_argument('--out-test-ids', required=True)
    parser.add_argument('--manifest-out', required=True)
    parser.add_argument('--mode', choices=('hard', 'external'), required=True)
    parser.add_argument('--dev-ratio', type=float, default=0.2)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    prepare(args.input, args.out_data, args.out_dev_ids, args.out_test_ids, args.manifest_out, args.mode, args.dev_ratio, args.seed)


if __name__ == '__main__':
    main()
