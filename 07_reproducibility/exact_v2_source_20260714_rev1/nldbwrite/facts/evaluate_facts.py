import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from nldbwrite.common import iter_jsonl, load_json, save_json
from nldbwrite.facts.gold_facts import derive_gold_facts, load_profiles, normalize_text, normalize_value


def coerce_facts(obj: Any) -> list[dict[str, Any]]:
    if isinstance(obj, dict):
        facts = obj.get('facts') or obj.get('predicted_facts') or []
    elif isinstance(obj, list):
        facts = obj
    else:
        facts = []
    output = []
    for idx, fact in enumerate(facts, start=1):
        if not isinstance(fact, dict):
            continue
        item = dict(fact)
        item.setdefault('fact_id', f'f{idx:04d}')
        item.setdefault('record_id', f'r{idx:04d}')
        output.append(item)
    return output


def fact_value_key(fact: dict[str, Any]) -> str:
    return normalize_value(fact.get('value'))


def fact_attr_value_key(fact: dict[str, Any]) -> tuple[str, str]:
    return (normalize_text(fact.get('attribute') or fact.get('gold_column') or ''), normalize_value(fact.get('value')))


def prf(pred: Counter, gold: Counter) -> tuple[float, float, float, int]:
    matches = sum((pred & gold).values())
    pred_total = sum(pred.values())
    gold_total = sum(gold.values())
    precision = matches / pred_total if pred_total else (1.0 if gold_total == 0 else 0.0)
    recall = matches / gold_total if gold_total else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1, matches


def distinct_record_count(facts: list[dict[str, Any]]) -> int:
    record_ids = {str(f.get('record_id')) for f in facts if f.get('record_id')}
    if record_ids:
        return len(record_ids)
    return 1 if facts else 0


def recall_for_subset(pred: Counter, gold_facts: list[dict[str, Any]], field: str) -> float | None:
    gold_subset = Counter(fact_value_key(f) for f in gold_facts if f.get(field))
    if not gold_subset:
        return None
    matches = sum((pred & gold_subset).values())
    return matches / sum(gold_subset.values())


def evaluate_sample(sample: dict[str, Any], pred_facts: list[dict[str, Any]], gold_facts: list[dict[str, Any]]) -> dict[str, Any]:
    pred_values = Counter(fact_value_key(f) for f in pred_facts)
    gold_values = Counter(fact_value_key(f) for f in gold_facts)
    pred_attr_values = Counter(fact_attr_value_key(f) for f in pred_facts)
    gold_attr_values = Counter(fact_attr_value_key(f) for f in gold_facts)
    value_p, value_r, value_f1, value_matches = prf(pred_values, gold_values)
    av_p, av_r, av_f1, av_matches = prf(pred_attr_values, gold_attr_values)
    pred_fact_total = sum(pred_values.values())
    hallucinated = max(0, pred_fact_total - value_matches)
    gold_record_count = len(sample.get('gold_records') or [])
    pred_record_count = distinct_record_count(pred_facts)
    row = {
        'sample_id': str(sample['id']),
        'db_id': sample.get('db_id'),
        'operation_type': sample.get('operation_type'),
        'gold_fact_count': len(gold_facts),
        'pred_fact_count': len(pred_facts),
        'gold_record_count': gold_record_count,
        'pred_record_count': pred_record_count,
        'value_precision': value_p,
        'value_recall': value_r,
        'value_f1': value_f1,
        'attribute_value_precision': av_p,
        'attribute_value_recall': av_r,
        'attribute_value_f1': av_f1,
        'required_value_recall': recall_for_subset(pred_values, gold_facts, 'is_required_value'),
        'conflict_key_fact_recall': recall_for_subset(pred_values, gold_facts, 'is_conflict_key'),
        'row_count_accuracy': 1.0 if pred_record_count == gold_record_count else 0.0,
        'hallucinated_fact_rate': hallucinated / pred_fact_total if pred_fact_total else 0.0,
    }
    return row


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        'sample_id', 'db_id', 'operation_type', 'gold_fact_count', 'pred_fact_count',
        'gold_record_count', 'pred_record_count', 'value_precision', 'value_recall',
        'value_f1', 'attribute_value_precision', 'attribute_value_recall',
        'attribute_value_f1', 'required_value_recall', 'conflict_key_fact_recall',
        'row_count_accuracy', 'hallucinated_fact_rate', 'parse_status',
        'parse_error',
    ]
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def mean(rows: list[dict[str, Any]], field: str, skip_none: bool = False) -> float | None:
    values = [row.get(field) for row in rows]
    if skip_none:
        values = [v for v in values if v is not None and v != '']
    if not values:
        return None
    return sum(float(v or 0.0) for v in values) / len(values)


def evaluate_run(run_dir: Path, data_path: Path, out_dir: Path, profile_dir: Path | None = None) -> dict[str, Any]:
    parsed_path = run_dir / 'parsed_outputs.jsonl'
    if not parsed_path.exists():
        raise FileNotFoundError(f'Missing parsed outputs: {parsed_path}')
    data = {str(x['id']): x for x in load_json(data_path)}
    profiles = load_profiles(profile_dir)
    parsed = {str(row['sample_id']): row for row in iter_jsonl(parsed_path)}
    rows = []
    for sid, item in sorted(parsed.items()):
        sample = data[sid]
        gold_facts = derive_gold_facts(sample, profiles.get(sample.get('db_id')))
        pred_obj = item.get('pred_facts') or item.get('pred_json') or {}
        pred_facts = coerce_facts(pred_obj)
        row = evaluate_sample(sample, pred_facts, gold_facts)
        row['parse_status'] = item.get('parse_status')
        row['parse_error'] = item.get('parse_error')
        rows.append(row)
    summary = {
        'num_samples': len(rows),
        'parse_success_rate': mean(rows, 'parse_status_success'),
        'value_precision': mean(rows, 'value_precision'),
        'value_recall': mean(rows, 'value_recall'),
        'value_f1': mean(rows, 'value_f1'),
        'attribute_value_precision': mean(rows, 'attribute_value_precision'),
        'attribute_value_recall': mean(rows, 'attribute_value_recall'),
        'attribute_value_f1': mean(rows, 'attribute_value_f1'),
        'required_value_recall': mean(rows, 'required_value_recall', skip_none=True),
        'conflict_key_fact_recall': mean(rows, 'conflict_key_fact_recall', skip_none=True),
        'row_count_accuracy': mean(rows, 'row_count_accuracy'),
        'hallucinated_fact_rate': mean(rows, 'hallucinated_fact_rate'),
        'avg_gold_fact_count': mean(rows, 'gold_fact_count'),
        'avg_pred_fact_count': mean(rows, 'pred_fact_count'),
    }
    if rows:
        summary['parse_success_rate'] = sum(row.get('parse_status') == 'success' for row in rows) / len(rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(rows, out_dir / 'fact_eval_per_sample.csv')
    save_json(summary, out_dir / 'fact_eval_summary.json')
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-dir', required=True)
    ap.add_argument('--data', default='data/processed/nl_db_write_augmented900_v1.json')
    ap.add_argument('--profile-dir', default='artifacts/profiles_aug900')
    ap.add_argument('--out-dir')
    args = ap.parse_args()
    run_dir = Path(args.run_dir)
    out_dir = Path(args.out_dir) if args.out_dir else run_dir
    summary = evaluate_run(run_dir, Path(args.data), out_dir, Path(args.profile_dir))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

