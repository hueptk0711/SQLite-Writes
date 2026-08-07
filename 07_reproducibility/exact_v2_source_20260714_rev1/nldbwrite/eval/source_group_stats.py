import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from nldbwrite.common import iter_jsonl, load_json, save_json
from nldbwrite.eval.metrics import metric_correct


def group_scores(rows: list[dict[str, Any]], gold: dict[str, dict[str, Any]], metric: str) -> dict[str, float]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        sample = gold.get(str(row['sample_id']))
        if not sample:
            continue
        group_id = str(sample.get('source_group_id') or sample['id'])
        grouped[group_id].append(1 if metric_correct(row, metric) else 0)
    return {group_id: sum(values) / len(values) for group_id, values in grouped.items()}


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int(quantile * len(values))))
    return sorted(values)[index]


def paired_statistics(differences: list[float], iterations: int, seed: int) -> dict[str, float]:
    if not differences:
        raise ValueError('No common source groups were available for the paired comparison')
    rng = random.Random(seed)
    n = len(differences)
    observed = sum(differences) / n
    boot = [sum(differences[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations)]
    extreme = 0
    for _ in range(iterations):
        estimate = sum(value if rng.random() < 0.5 else -value for value in differences) / n
        if abs(estimate) >= abs(observed) - 1e-15:
            extreme += 1
    return {
        'difference': observed,
        'ci95_low': percentile(boot, 0.025),
        'ci95_high': percentile(boot, 0.975),
        'p_value': (extreme + 1) / (iterations + 1),
    }


def holm_adjust(rows: list[dict[str, Any]]) -> None:
    ordered = sorted(enumerate(rows), key=lambda item: item[1]['p_value'])
    running = 0.0
    total = len(rows)
    for rank, (index, row) in enumerate(ordered):
        adjusted = min(1.0, (total - rank) * row['p_value'])
        running = max(running, adjusted)
        rows[index]['p_value_holm'] = running


def parse_comparison(value: str) -> tuple[str, str, str]:
    try:
        label, run_pair = value.split('=', 1)
        run_a, run_b = run_pair.split(',', 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Comparison must be LABEL=RUN_A,RUN_B') from exc
    return label.strip(), run_a.strip(), run_b.strip()


def compare_runs(
    results_root: str | Path,
    data_path: str | Path,
    comparisons: list[tuple[str, str, str]],
    metric: str = 'target_state_correct',
    iterations: int = 10000,
    seed: int = 42,
) -> list[dict[str, Any]]:
    root = Path(results_root)
    gold = {str(row['id']): row for row in load_json(data_path)}
    cache: dict[str, dict[str, float]] = {}
    rows = []
    for offset, (label, run_a, run_b) in enumerate(comparisons):
        for run in (run_a, run_b):
            if run not in cache:
                evaluation_path = root / run / 'evaluation.jsonl'
                if not evaluation_path.exists():
                    raise FileNotFoundError(f'Missing evaluation file: {evaluation_path}')
                cache[run] = group_scores(list(iter_jsonl(evaluation_path)), gold, metric)
        common = sorted(set(cache[run_a]) & set(cache[run_b]))
        differences = [cache[run_a][group_id] - cache[run_b][group_id] for group_id in common]
        stats = paired_statistics(differences, iterations, seed + offset)
        rows.append({
            'comparison': label,
            'run_a': run_a,
            'run_b': run_b,
            'metric': metric,
            'source_groups': len(common),
            'run_a_sg_macro': sum(cache[run_a][group_id] for group_id in common) / len(common),
            'run_b_sg_macro': sum(cache[run_b][group_id] for group_id in common) / len(common),
            **stats,
        })
    holm_adjust(rows)
    return rows


def write_results(rows: list[dict[str, Any]], out_csv: str | Path, out_json: str | Path | None = None) -> None:
    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)
    if out_json:
        save_json(rows, out_json)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--results-root', required=True)
    parser.add_argument('--data', required=True)
    parser.add_argument('--comparison', action='append', type=parse_comparison, required=True)
    parser.add_argument('--metric', default='target_state_correct')
    parser.add_argument('--iterations', type=int, default=10000)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--out-csv', required=True)
    parser.add_argument('--out-json')
    args = parser.parse_args()
    rows = compare_runs(
        args.results_root,
        args.data,
        args.comparison,
        args.metric,
        args.iterations,
        args.seed,
    )
    write_results(rows, args.out_csv, args.out_json)
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
