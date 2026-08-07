import argparse
import json
from pathlib import Path

from nldbwrite.common import load_config, load_json, read_id_file, save_json, sha256_file


def write_ids(path, ids):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(sorted(ids)) + '\n', encoding='utf-8')


def generate(data_path, dev_ids_path, test_ids_path, base_config_path, split_root, config_root, results_root):
    data = {str(row['id']): row for row in load_json(data_path)}
    dev_ids = read_id_file(dev_ids_path)
    test_ids = read_id_file(test_ids_path)
    databases = sorted({str(data[sid]['db_id']) for sid in test_ids if sid in data})
    split_root = Path(split_root)
    config_root = Path(config_root)
    config_root.mkdir(parents=True, exist_ok=True)
    base = load_config(base_config_path)
    folds = []
    for db_id in databases:
        fold_test = {sid for sid, row in data.items() if str(row['db_id']) == db_id}
        fold_bank = {sid for sid in dev_ids if sid in data and str(data[sid]['db_id']) != db_id}
        if not fold_test or not fold_bank:
            continue
        fold_dir = split_root / db_id
        test_path = fold_dir / 'test_ids.txt'
        bank_path = fold_dir / 'case_bank_ids.txt'
        write_ids(test_path, fold_test)
        write_ids(bank_path, fold_bank)
        config = dict(base)
        run_name = f'qwen7b_s_cbr_h_lodo_{db_id}'
        config.update({
            'run_name': run_name,
            'split_ids': str(test_path).replace('\\', '/'),
            'case_bank_split_ids': str(bank_path).replace('\\', '/'),
            'cbr_setting': 'cross_db',
            'output_dir': str(Path(results_root) / run_name).replace('\\', '/'),
        })
        config_path = config_root / f'{run_name}.json'
        save_json(config, config_path)
        folds.append({
            'held_out_db': db_id,
            'num_test': len(fold_test),
            'num_case_bank': len(fold_bank),
            'test_ids': str(test_path).replace('\\', '/'),
            'case_bank_ids': str(bank_path).replace('\\', '/'),
            'config': str(config_path).replace('\\', '/'),
        })
    manifest = {
        'protocol': 'leave-one-database-out case-bank adaptation',
        'data_path': str(data_path),
        'data_sha256': sha256_file(data_path),
        'source_dev_ids': str(dev_ids_path),
        'source_test_ids': str(test_ids_path),
        'held_out_test_scope': 'all examples from the held-out database',
        'folds': folds,
    }
    save_json(manifest, config_root / 'manifest.json')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default='data/processed/nl_db_write_augmented900_v2_final.json')
    parser.add_argument('--dev-ids', default='data/splits/augmented900_v2_final/dev_ids.txt')
    parser.add_argument('--test-ids', default='data/splits/augmented900_v2_final/test_ids.txt')
    parser.add_argument('--base-config', default='configs/experiments/paper_v2/qwen7b_s_cbr_h.yaml')
    parser.add_argument('--split-root', default='data/splits/augmented900_v2_final/lodo')
    parser.add_argument('--config-root', default='configs/experiments/paper_v2/generated_lodo')
    parser.add_argument('--results-root', default='results/server_aug900_v2_lodo')
    args = parser.parse_args()
    generate(args.data, args.dev_ids, args.test_ids, args.base_config, args.split_root, args.config_root, args.results_root)


if __name__ == '__main__':
    main()
