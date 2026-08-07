import argparse
import json
from copy import deepcopy
from pathlib import Path

from nldbwrite.common import load_config


def dump(config, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in config.items():
        if isinstance(value, (list, dict, bool)) or value is None:
            rendered = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, str):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            rendered = str(value)
        lines.append(f'{key}: {rendered}')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def named(base, name, **updates):
    config = deepcopy(base)
    config.update(updates)
    config['run_name'] = name
    config['output_dir'] = f'results/server_aug900/{name.removeprefix("aug900_")}'
    config['checkpoint_zip'] = f'results/server_checkpoints/{name}_live.zip'
    return config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out-dir', default='configs/experiments/generated_review')
    args = ap.parse_args()
    out = Path(args.out_dir)
    m2 = load_config('configs/experiments/aug900_qwen7b_m2_extract_builder.yaml')
    cbr = load_config('configs/experiments/aug900_qwen7b_m2_cbr_hybrid_k3.yaml')
    repair = load_config('configs/experiments/aug900_qwen7b_m2_cbr_hybrid_repair_k3.yaml')
    m3 = load_config('configs/experiments/aug900_qwen7b_m3_schema_grounded.yaml')
    m4 = load_config('configs/experiments/aug900_qwen7b_m4_full.yaml')
    fs = load_config('configs/experiments/aug900_qwen7b_m2_fs_static.yaml')
    configs = []

    for k in (1, 3, 5):
        configs.append(named(cbr, f'aug900_qwen7b_m2_cbr_dense_k{k}', cbr_retriever='dense', cbr_k=k))
    configs.append(named(cbr, 'aug900_qwen7b_m2_cbr_hybrid_dense_k3', cbr_retriever='hybrid_dense', cbr_k=3))

    configs.extend([
        named(fs, 'aug900_qwen7b_m2_fs_random_json_k3', static_example_selection='random', demo_type='json', num_examples=3),
        named(fs, 'aug900_qwen7b_m2_fs_curated_json_k3', static_example_selection='curated', demo_type='json', num_examples=3),
        named(fs, 'aug900_qwen7b_m2_fs_curated_sql_k3', static_example_selection='curated', demo_type='sql', num_examples=3),
        named(fs, 'aug900_qwen7b_m2_fs_curated_sql_json_k3', static_example_selection='curated', demo_type='sql_json', num_examples=3),
    ])

    configs.extend([
        named(repair, 'aug900_qwen7b_m2_cbr_repair_parse_only', repair_error_types=['json_parse_error'], repair_execution_failures=False),
        named(repair, 'aug900_qwen7b_m2_cbr_repair_builder_only', repair_error_types=['builder_error'], repair_execution_failures=False),
        named(repair, 'aug900_qwen7b_m2_cbr_repair_execution_only', repair_error_types=['syntax_error', 'constraint_error', 'execution_error', 'schema_error', 'unsafe_sql'], repair_execution_failures=True),
        named(repair, 'aug900_qwen7b_m2_cbr_repair_no_rollback', repair_rollback=False),
        named(repair, 'aug900_qwen7b_m2_cbr_repair_execution_rollback', repair_rollback=True, repair_rollback_policy='execution'),
    ])

    builder_flags = [
        ('no_fk_order', 'builder_fk_ordering'),
        ('no_required_check', 'builder_required_column_check'),
        ('no_type_normalization', 'builder_type_normalization'),
        ('no_conflict_inference', 'builder_conflict_target_inference'),
        ('no_safety_filter', 'builder_safety_filter'),
    ]
    configs.append(named(m2, 'aug900_qwen7b_m2_builder_full'))
    for suffix, flag in builder_flags:
        configs.append(named(m2, f'aug900_qwen7b_m2_builder_{suffix}', **{flag: False}))

    configs.extend([
        named(m3, 'aug900_qwen7b_m3_bm25_only', schema_closure='none'),
        named(m3, 'aug900_qwen7b_m3_bm25_constraint_closure', schema_closure='pk_fk_not_null_unique_parent'),
        named(m4, 'aug900_qwen7b_m4_value_only', force_compact_schema=False, method='m4_value_grounded_only'),
    ])
    for top_k in (40, 80, 120, 160):
        configs.append(named(m4, f'aug900_qwen7b_m4_schema_top{top_k}', schema_top_k=top_k, max_columns_in_prompt=160))
    for threshold in (70, 80, 90):
        configs.append(named(m4, f'aug900_qwen7b_m4_value_threshold{threshold}', value_match_threshold=threshold))

    stress_splits = ['upsert_stress', 'relational_stress', 'large_batch_10plus', 'large_batch_20plus', 'hard_cases', 'noisy_input', 'schema_large', 'schema_very_large']
    stress_bases = {
        'm0': load_config('configs/experiments/aug900_qwen7b_m0_direct.yaml'),
        'm2': m2,
        'cbr': cbr,
        'repair': repair,
    }
    for split in stress_splits:
        for method, base in stress_bases.items():
            configs.append(named(
                base,
                f'aug900_stress_{split}_{method}',
                split_ids=f'data/splits/augmented900_v1/stress/{split}_ids.txt',
            ))

    for config in configs:
        dump(config, out / f"{config['run_name']}.yaml")
    print(f'Wrote {len(configs)} review configs under {out}')


if __name__ == '__main__':
    main()
