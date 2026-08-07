import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from nldbwrite.common import iter_jsonl


STAGE_LABELS = [
    'Fact extraction error',
    'Schema mapping error',
    'Builder error',
    'Execution error',
    'State mismatch',
]


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes'}


def _to_float(value: Any) -> float | None:
    if value in (None, ''):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_fact_eval(path: str | Path) -> dict[str, dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, encoding='utf-8', newline='') as f:
        return {str(row['sample_id']): row for row in csv.DictReader(f)}


def read_evaluation(path: str | Path) -> dict[str, dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return {}
    if p.suffix == '.jsonl':
        return {str(row['sample_id']): row for row in iter_jsonl(p)}
    with open(p, encoding='utf-8', newline='') as f:
        return {str(row['sample_id']): row for row in csv.DictReader(f)}


def classify_stage(eval_row: dict[str, Any], fact_row: dict[str, Any] | None) -> str | None:
    if _to_bool(eval_row.get('correct')):
        return None
    error_type = str(eval_row.get('error_type') or '')
    builder_status = str(eval_row.get('builder_status') or '')
    if error_type in {'builder_error'} or builder_status in {'error', 'parse_error', 'partial'}:
        return 'Builder error'
    if not _to_bool(eval_row.get('execution_success')) or error_type in {
        'syntax_error', 'constraint_error', 'schema_error', 'unsafe_sql',
        'timeout', 'execution_error',
    }:
        return 'Execution error'
    if fact_row:
        value_recall = _to_float(fact_row.get('value_recall'))
        attr_recall = _to_float(fact_row.get('attribute_value_recall'))
        required_recall = _to_float(fact_row.get('required_value_recall'))
        conflict_recall = _to_float(fact_row.get('conflict_key_fact_recall'))
        if (
            (value_recall is not None and value_recall < 0.999)
            or (attr_recall is not None and attr_recall < 0.999)
            or (required_recall is not None and required_recall < 0.999)
            or (conflict_recall is not None and conflict_recall < 0.999)
        ):
            return 'Fact extraction error'
    if error_type in {'wrong_table', 'missing_columns', 'extra_columns'}:
        return 'Schema mapping error'
    return 'State mismatch'


def summarize_system(label: str, eval_rows: dict[str, dict[str, Any]], fact_rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter()
    total_errors = 0
    for sid, eval_row in eval_rows.items():
        stage = classify_stage(eval_row, fact_rows.get(sid))
        if stage:
            counts[stage] += 1
            total_errors += 1
    rows = []
    for stage in STAGE_LABELS:
        count = counts.get(stage, 0)
        rows.append({
            'Error Stage': stage,
            'System': label,
            'Count': count,
            'Rate': f'{100 * count / total_errors:.2f}' if total_errors else '0.00',
        })
    return rows


def build_attribution_rows(
    fact_eval: str | Path,
    m5_pred_eval: str | Path,
    m5_cbr_eval: str | Path | None = None,
) -> list[dict[str, Any]]:
    fact_rows = read_fact_eval(fact_eval)
    systems = [('M5-PredFacts', read_evaluation(m5_pred_eval))]
    if m5_cbr_eval:
        cbr_rows = read_evaluation(m5_cbr_eval)
        if cbr_rows:
            systems.append(('M5-Facts+CBR', cbr_rows))
    out = []
    for label, rows in systems:
        out.extend(summarize_system(label, rows, fact_rows))
    return out


def write_csv(rows: list[dict[str, Any]], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fields = ['Error Stage', 'System', 'Count', 'Rate']
    with open(p, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def write_tex(rows: list[dict[str, Any]], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    systems = sorted({row['System'] for row in rows})
    by_key = {(row['Error Stage'], row['System']): row for row in rows}
    with open(p, 'w', encoding='utf-8') as f:
        f.write('\\begin{tabular}{l' + 'r' * len(systems) + '}\n\\toprule\n')
        f.write('Error Stage & ' + ' & '.join(systems) + ' \\\\ \n\\midrule\n')
        for stage in STAGE_LABELS:
            values = [str(by_key.get((stage, system), {}).get('Count', 0)) for system in systems]
            f.write(stage + ' & ' + ' & '.join(values) + ' \\\\ \n')
        f.write('\\bottomrule\n\\end{tabular}\n')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--fact-eval', required=True)
    ap.add_argument('--m5-pred-eval', required=True)
    ap.add_argument('--m5-cbr-eval')
    ap.add_argument('--out-csv', default='paper/tables/server_aug900/m5_error_attribution.csv')
    ap.add_argument('--out-tex', default='paper/tables/server_aug900/m5_error_attribution.tex')
    args = ap.parse_args()
    rows = build_attribution_rows(args.fact_eval, args.m5_pred_eval, args.m5_cbr_eval)
    write_csv(rows, args.out_csv)
    write_tex(rows, args.out_tex)
    print(json.dumps({'rows': len(rows), 'out_csv': args.out_csv, 'out_tex': args.out_tex}, indent=2))


if __name__ == '__main__':
    main()
