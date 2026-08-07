import argparse
import json
from pathlib import Path

from nldbwrite.common import load_config, save_json


BASES = {
    's_fs': 'configs/experiments/paper_v2/qwen7b_s_fs.yaml',
    's_cbr_h': 'configs/experiments/paper_v2/qwen7b_s_cbr_h.yaml',
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out-dir', default='configs/experiments/paper_v2/generated_order_robustness')
    parser.add_argument('--split-ids', default='data/splits/augmented900_v2_final/model_validation_subset300_ids.txt')
    parser.add_argument('--seeds', default='11,22,33')
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    configs = []
    for seed in [int(value) for value in args.seeds.split(',') if value.strip()]:
        for family, base_path in BASES.items():
            config = load_config(base_path)
            run_name = f'qwen7b_{family}_order_seed{seed}'
            config.update({
                'run_name': run_name,
                'split_ids': args.split_ids,
                'example_order_seed': seed,
                'output_dir': f'results/server_aug900_v2_order_robustness/{run_name}',
            })
            path = out_dir / f'{run_name}.json'
            save_json(config, path)
            configs.append(str(path).replace('\\', '/'))
    save_json({'configs': configs}, out_dir / 'manifest.json')
    print(json.dumps({'configs': configs}, indent=2))


if __name__ == '__main__':
    main()
