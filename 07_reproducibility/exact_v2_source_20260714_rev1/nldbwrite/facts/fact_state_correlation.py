import argparse
import csv
from pathlib import Path
from typing import Any

from nldbwrite.common import iter_jsonl


BUCKETS = [
    ('0.90-1.00', 0.90, 1.01),
    ('0.70-0.90', 0.70, 0.90),
    ('0.50-0.70', 0.50, 0.70),
    ('<0.50', -0.01, 0.50),
]


def read_csv(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def bucket_for(value: float) -> str:
    for label, lo, hi in BUCKETS:
        if lo <= value < hi:
            return label
    return '<0.50'


def write_simple_latex(rows: list[dict[str, Any]], path: Path) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\\begin{tabular}{lrrr}\n\\toprule\n')
        f.write('Fact F1 bucket & N & Final State Acc. & Exec. Success \\\\\n\\midrule\n')
        for row in rows:
            f.write(f"{row['FactF1Bucket']} & {row['N']} & {row['FinalStateAcc']} & {row['ExecSuccess']} \\\\\n")
        f.write('\\bottomrule\n\\end{tabular}\n')


def correlate(fact_eval_path: Path, evaluation_path: Path) -> list[dict[str, Any]]:
    fact_rows = {str(row['sample_id']): row for row in read_csv(fact_eval_path)}
    eval_rows = {str(row['sample_id']): row for row in iter_jsonl(evaluation_path)}
    grouped: dict[str, list[dict[str, Any]]] = {label: [] for label, _, _ in BUCKETS}
    for sid, fact in fact_rows.items():
        if sid not in eval_rows:
            continue
        try:
            f1 = float(fact.get('attribute_value_f1') or fact.get('value_f1') or 0.0)
        except ValueError:
            f1 = 0.0
        grouped[bucket_for(f1)].append(eval_rows[sid])
    rows = []
    for label, _, _ in BUCKETS:
        values = grouped[label]
        n = len(values)
        rows.append({
            'FactF1Bucket': label,
            'N': n,
            'FinalStateAcc': '-' if not n else f'{100 * sum(bool(x.get("correct")) for x in values) / n:.2f}',
            'ExecSuccess': '-' if not n else f'{100 * sum(bool(x.get("execution_success")) for x in values) / n:.2f}',
        })
    return rows


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ['FactF1Bucket', 'N', 'FinalStateAcc', 'ExecSuccess']
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--fact-eval', default='results/server_aug900/qwen7b_m5_stage1_facts/fact_eval_per_sample.csv')
    ap.add_argument('--evaluation', default='results/server_aug900/qwen7b_m5_fact_first/evaluation.jsonl')
    ap.add_argument('--out-csv', default='paper/tables/m5_fact_f1_vs_state_acc.csv')
    ap.add_argument('--out-tex', default='paper/tables/m5_fact_f1_vs_state_acc.tex')
    args = ap.parse_args()
    rows = correlate(Path(args.fact_eval), Path(args.evaluation))
    write_csv(rows, Path(args.out_csv))
    write_simple_latex(rows, Path(args.out_tex))
    print(f'Wrote {args.out_csv}')
    print(f'Wrote {args.out_tex}')


if __name__ == '__main__':
    main()
