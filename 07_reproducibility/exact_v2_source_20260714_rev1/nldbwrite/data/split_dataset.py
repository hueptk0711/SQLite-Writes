import argparse, random
from collections import defaultdict
from pathlib import Path
from nldbwrite.common import load_json, ensure_dir

def stratified_split(data, test_ratio, seed):
    rng=random.Random(seed); groups=defaultdict(list)
    for item in data: groups[(item.get('db_id'), item.get('difficulty','unknown'), int(item.get('num_tables',len(item.get('gold_tables',[])) or 1)))].append(item)
    dev_ids=[]; test_ids=[]
    for items in groups.values():
        rng.shuffle(items); n_test=max(1,int(len(items)*test_ratio)) if len(items)>1 else len(items)
        test_ids += [str(x['id']) for x in items[:n_test]]; dev_ids += [str(x['id']) for x in items[n_test:]]
    return dev_ids,test_ids

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--data', required=True); ap.add_argument('--out-dir', default='data/splits'); ap.add_argument('--seed', type=int, default=42); ap.add_argument('--test-ratio', type=float, default=0.8); args=ap.parse_args()
    dev,test=stratified_split(load_json(args.data), args.test_ratio, args.seed); out=ensure_dir(args.out_dir)
    (out/'dev_ids.txt').write_text('\n'.join(dev)+'\n', encoding='utf-8'); (out/'test_ids.txt').write_text('\n'.join(test)+'\n', encoding='utf-8')
    print(f'dev={len(dev)} test={len(test)} seed={args.seed}')
if __name__=='__main__': main()
