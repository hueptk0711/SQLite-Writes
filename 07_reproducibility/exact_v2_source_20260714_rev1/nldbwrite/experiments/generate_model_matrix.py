import argparse
import json
from pathlib import Path

from nldbwrite.common import load_config, load_json, save_json


def generate(matrix_path, out_dir):
    matrix = load_json(matrix_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    generated = []
    for model_name, model_config in matrix['models'].items():
        for method_name, base_path in matrix['methods'].items():
            config = load_config(base_path)
            run_name = f'{model_name}_{method_name}'
            config['run_name'] = run_name
            config['model_config'] = model_config
            if matrix.get('split_ids'):
                config['split_ids'] = matrix['split_ids']
            config['output_dir'] = str(Path(matrix['results_root']) / run_name).replace('\\', '/')
            path = out_dir / f'{run_name}.json'
            save_json(config, path)
            generated.append(str(path).replace('\\', '/'))
    manifest = {
        'matrix_path': str(matrix_path),
        'results_root': matrix['results_root'],
        'split_ids': matrix.get('split_ids'),
        'generated_configs': generated,
    }
    save_json(manifest, out_dir / 'manifest.json')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--matrix', default='configs/paper_v2_model_matrix.json')
    parser.add_argument('--out-dir', default='configs/experiments/paper_v2/generated_model_matrix')
    args = parser.parse_args()
    generate(args.matrix, args.out_dir)


if __name__ == '__main__':
    main()
