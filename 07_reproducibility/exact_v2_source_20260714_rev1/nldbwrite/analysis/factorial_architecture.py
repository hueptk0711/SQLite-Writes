import argparse
import csv
import json
import random
from pathlib import Path

from nldbwrite.common import iter_jsonl, load_json, save_json


CELLS = ('d0', 's0', 'd_static', 's_static', 'd_cbr', 's_cbr')


def load_correct(run_dir, metric):
    return {
        str(row['sample_id']): int(bool(row.get(metric, row.get('correct'))))
        for row in iter_jsonl(Path(run_dir) / 'evaluation.jsonl')
    }


def contrast_values(cell):
    return {
        'structured_main': ((cell['s0'] - cell['d0']) + (cell['s_static'] - cell['d_static']) + (cell['s_cbr'] - cell['d_cbr'])) / 3,
        'static_main': ((cell['d_static'] - cell['d0']) + (cell['s_static'] - cell['s0'])) / 2,
        'cbr_main': ((cell['d_cbr'] - cell['d0']) + (cell['s_cbr'] - cell['s0'])) / 2,
        'structured_x_static': (cell['s_static'] - cell['s0']) - (cell['d_static'] - cell['d0']),
        'structured_x_cbr': (cell['s_cbr'] - cell['s0']) - (cell['d_cbr'] - cell['d0']),
    }


def quantile(values, probability):
    values = sorted(values)
    if not values:
        return None
    return values[min(len(values) - 1, max(0, int(probability * len(values))))]


def analyze(run_specs, data_path, metric, iterations, seed, out_dir):
    runs = {}
    run_paths = {}
    for spec in run_specs:
        name, separator, path = spec.partition('=')
        if not separator or name not in CELLS:
            raise ValueError(f'Expected one of {CELLS} as CELL=RUN_DIR, got: {spec}')
        runs[name] = load_correct(path, metric)
        run_paths[name] = path
    missing = set(CELLS) - set(runs)
    if missing:
        raise ValueError(f'Missing factorial cells: {sorted(missing)}')
    gold = {str(row['id']): row for row in load_json(data_path)}
    ids = sorted(set.intersection(*(set(rows) for rows in runs.values())))
    groups = {}
    for sid in ids:
        group = str(gold.get(sid, {}).get('source_group_id') or sid)
        groups.setdefault(group, []).append(sid)
    group_contrasts = {}
    for group, group_ids in groups.items():
        cell = {name: sum(runs[name][sid] for sid in group_ids) / len(group_ids) for name in CELLS}
        group_contrasts[group] = contrast_values(cell)
    rng = random.Random(seed)
    group_names = sorted(groups)
    rows = []
    for contrast in contrast_values({name: 0 for name in CELLS}):
        values = [group_contrasts[group][contrast] for group in group_names]
        estimate = sum(values) / len(values) if values else 0.0
        boot = []
        for _ in range(iterations):
            selected = [values[rng.randrange(len(values))] for _ in values]
            boot.append(sum(selected) / len(selected))
        extreme = 0
        for _ in range(iterations):
            permuted = [value * (-1 if rng.random() < 0.5 else 1) for value in values]
            if abs(sum(permuted) / len(permuted)) >= abs(estimate):
                extreme += 1
        rows.append({
            'contrast': contrast,
            'metric': metric,
            'estimate': estimate,
            'ci95_low': quantile(boot, 0.025),
            'ci95_high': quantile(boot, 0.975),
            'sign_flip_p': (extreme + 1) / (iterations + 1),
            'num_samples': len(ids),
            'num_source_groups': len(groups),
        })
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / 'factorial_architecture.csv', 'w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
    save_json({'cells': run_paths, 'results': rows}, out_dir / 'factorial_architecture.json')
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='append', default=[], help='CELL=RUN_DIR')
    parser.add_argument('--data', required=True)
    parser.add_argument('--metric', default='target_state_correct')
    parser.add_argument('--iterations', type=int, default=10000)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--out-dir', required=True)
    args = parser.parse_args()
    analyze(args.run, args.data, args.metric, args.iterations, args.seed, args.out_dir)


if __name__ == '__main__':
    main()
