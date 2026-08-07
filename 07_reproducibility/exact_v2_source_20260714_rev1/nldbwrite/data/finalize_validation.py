import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from nldbwrite.common import load_json, save_json


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    parser.add_argument('--split-dir', required=True)
    parser.add_argument('--report-dir', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--verify-existing', action='store_true')
    args = parser.parse_args()

    data_path = Path(args.data)
    split_dir = Path(args.split_dir)
    report_dir = Path(args.report_dir)
    data = load_json(data_path)
    expected_dbs = sorted({str(sample['db_id']) for sample in data})
    reports = []
    for db_id in expected_dbs:
        report_path = report_dir / f'{db_id}.json'
        if not report_path.exists():
            raise SystemExit(f'Missing validation report: {report_path}')
        report = load_json(report_path)
        if report.get('status') != 'passed' or not report.get('strict'):
            raise SystemExit(f'Validation did not pass in strict mode: {report_path}')
        if report.get('db_ids') != [db_id]:
            raise SystemExit(f'Unexpected database coverage: {report_path}')
        reports.append(report)
    validated_samples = sum(int(report.get('num_samples', 0)) for report in reports)
    if validated_samples != len(data):
        raise SystemExit(f'Validation coverage mismatch: {validated_samples} != {len(data)}')
    if any(sample.get('machine_validation_status') != 'validated_on_original_sqlite' for sample in data):
        raise SystemExit('Dataset contains samples not marked as machine validated')

    split_files = ['dev_ids.txt', 'test_ids.txt', 'smoke_ids.txt', 'model2_subset_ids.txt']
    split_manifest = {}
    for filename in split_files:
        path = split_dir / filename
        ids = [line.strip() for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        split_manifest[filename] = {'count': len(ids), 'sha256': file_sha256(path)}

    manifest = {
        'status': 'passed',
        'validated_at_utc': datetime.now(timezone.utc).isoformat(),
        'validation_mode': 'strict_parse_and_execute_on_fresh_copy_of_original_sqlite',
        'num_samples': len(data),
        'num_validated_samples': validated_samples,
        'num_databases': len(expected_dbs),
        'db_ids': expected_dbs,
        'dataset_file': str(data_path.as_posix()),
        'dataset_file_sha256': file_sha256(data_path),
        'splits': split_manifest,
        'per_database_reports': {report['db_ids'][0]: report['num_samples'] for report in reports},
    }
    out_path = Path(args.out)
    if args.verify_existing:
        if not out_path.exists():
            raise SystemExit(f'Missing frozen validation manifest: {out_path}')
        frozen = load_json(out_path)
        if frozen.get('dataset_file_sha256') != manifest['dataset_file_sha256']:
            raise SystemExit('Frozen dataset SHA-256 mismatch')
        for filename, details in manifest['splits'].items():
            frozen_details = (frozen.get('splits') or {}).get(filename) or {}
            if frozen_details.get('sha256') != details['sha256']:
                raise SystemExit(f'Frozen split SHA-256 mismatch: {filename}')
        print(json.dumps({'status': 'verified', 'manifest': str(out_path)}, ensure_ascii=False))
        return
    save_json(manifest, out_path)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
