import argparse
from collections import Counter
from pathlib import Path
from nldbwrite.common import load_json, save_json, sha256_file

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--data', required=True); ap.add_argument('--db-root', required=True); ap.add_argument('--out', default='artifacts/manifests/data_manifest.json'); ap.add_argument('--splits-dir', default='data/splits'); args=ap.parse_args()
    data=load_json(args.data); db_hashes={}; split_hashes={}
    for ext in ['sqlite','db','sqlite3']:
        for p in Path(args.db_root).glob(f'**/*.{ext}'): db_hashes[p.relative_to(args.db_root).as_posix()] = sha256_file(p)
    if Path(args.splits_dir).exists():
        for p in Path(args.splits_dir).rglob('*.txt'): split_hashes[p.relative_to(args.splits_dir).as_posix()] = sha256_file(p)
    stats={'num_samples':len(data),'gold_records_nonempty':sum(bool(x.get('gold_records')) for x in data),'gold_records_coverage':sum(bool(x.get('gold_records')) for x in data)/len(data) if data else 0.0}
    for field in ['db_id','difficulty','auto_difficulty','operation_type','input_type','impact_scope']:
        stats[field]=dict(Counter(str(x.get(field,'unknown')) for x in data))
    save_json({'dataset_path': args.data, 'dataset_sha256': sha256_file(args.data), 'db_root': args.db_root, 'db_hashes': db_hashes, 'split_hashes': split_hashes, 'dataset_statistics':stats}, args.out)
    print(f'Wrote {args.out}')
if __name__=='__main__': main()
