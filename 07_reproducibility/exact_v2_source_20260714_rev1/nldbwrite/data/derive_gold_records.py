import argparse
import json
from collections import Counter
from pathlib import Path

from nldbwrite.common import load_json, save_json
from nldbwrite.eval.evaluate import record_probes


def derive_sample(sample, overwrite=False):
    sample = dict(sample)
    if sample.get('gold_records') and not overwrite:
        return sample, 'preserved', 0
    records, unparsed_tables = record_probes(sample.get('gold_sql') or [])
    operation = sample.get('operation_type', 'insert')
    sample['gold_records'] = [
        {'table': record.get('table'), 'operation': operation, 'values': record.get('values') or {}}
        for record in records
    ]
    sample['gold_records_source'] = 'deterministic_gold_sql_parser'
    sample['gold_records_unparsed_tables'] = sorted(unparsed_tables)
    return sample, ('unparsed' if unparsed_tables else 'derived'), len(records)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--out')
    ap.add_argument('--overwrite', action='store_true')
    ap.add_argument('--strict', action='store_true')
    args = ap.parse_args()
    output = []
    statuses = Counter()
    record_count = 0
    for sample in load_json(args.data):
        converted, status, count = derive_sample(sample, args.overwrite)
        output.append(converted)
        statuses[status] += 1
        record_count += count
    if args.strict and (statuses['unparsed'] or any(not x.get('gold_records') for x in output)):
        raise SystemExit(f'Cannot derive complete gold_records: {dict(statuses)}')
    out = Path(args.out or args.data)
    save_json(output, out)
    print(json.dumps({'samples': len(output), 'records': record_count, 'status': dict(statuses), 'out': str(out)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
