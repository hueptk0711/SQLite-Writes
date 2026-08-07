import argparse
import time
from pathlib import Path

from nldbwrite.common import iter_jsonl, load_json, save_json, sha256_file


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--split-ids', required=True)
    ap.add_argument('--corrections', required=True)
    ap.add_argument('--out-data', required=True)
    ap.add_argument('--out-split-ids', required=True)
    ap.add_argument('--manifest-out', default='human_eval/final/post_correction_manifest.json')
    args = ap.parse_args()
    data = load_json(args.data)
    corrections = {str(row['sample_id']): row for row in iter_jsonl(args.corrections)}
    revised, removed, fixed = [], [], []
    for sample in data:
        sid = str(sample['id'])
        correction = corrections.get(sid)
        if not correction:
            revised.append(sample)
            continue
        decision = correction.get('decision')
        if decision == 'remove_sample':
            removed.append(sid)
            continue
        updated = dict(sample)
        if correction.get('new_input_text') is not None:
            updated['input_text'] = correction['new_input_text']
        if correction.get('new_gold_records') is not None:
            updated['gold_records'] = correction['new_gold_records']
        if correction.get('new_gold_sql') is not None:
            updated['gold_sql'] = correction['new_gold_sql']
        updated['human_correction'] = {
            'issue_type': correction.get('issue_type'),
            'decision': decision,
            'reason': correction.get('reason'),
        }
        revised.append(updated)
        fixed.append(sid)
    save_json(revised, args.out_data)
    original_ids = [line.strip() for line in Path(args.split_ids).read_text(encoding='utf-8').splitlines() if line.strip()]
    final_ids = [sid for sid in original_ids if sid not in set(removed)]
    Path(args.out_split_ids).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_split_ids).write_text('\n'.join(final_ids) + '\n', encoding='utf-8')
    manifest = {
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'original_data_sha256': sha256_file(args.data),
        'corrections_sha256': sha256_file(args.corrections),
        'num_fixed': len(fixed), 'num_removed': len(removed),
        'fixed_sample_ids': fixed, 'removed_sample_ids': removed,
        'out_data': args.out_data, 'out_split_ids': args.out_split_ids,
    }
    save_json(manifest, args.manifest_out)
    print(manifest)


if __name__ == '__main__':
    main()
