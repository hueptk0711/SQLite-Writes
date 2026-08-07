import argparse
import json
import re
from collections import Counter
from pathlib import Path

from nldbwrite.common import load_json, save_json
from nldbwrite.eval.evaluate import record_probes


def bucket_count(value: int) -> str:
    if value <= 1:
        return '1'
    if value <= 5:
        return '2-5'
    if value <= 20:
        return '6-20'
    return '21+'


def infer_scope(table_count: int, row_count: int) -> str:
    if table_count > 1:
        return 'relational_multi_table'
    if row_count > 20:
        return 'bulk_single_table'
    if row_count > 1:
        return 'batch_single_table'
    return 'row_single_table'


def infer_auto_difficulty(operation: str, table_count: int, row_count: int, column_count: int, sql_count: int) -> str:
    score = 0
    if table_count > 1:
        score += 2
    if row_count > 20:
        score += 2
    elif row_count > 1:
        score += 1
    if column_count > 8:
        score += 1
    if sql_count > 1:
        score += 1
    if operation and operation != 'insert':
        score += 1
    if score <= 1:
        return 'easy'
    if score <= 3:
        return 'medium'
    return 'hard'


def infer_input_type(text: str) -> str:
    text = str(text or '').strip()
    if not text:
        return 'unknown'
    if re.search(r'```\s*json\b', text, re.I) or re.search(r'\[\s*\{[\s\S]*\}\s*\]', text):
        return 'json_like'
    if text[:1] in '[{':
        try:
            json.loads(text)
            return 'json_like'
        except Exception:
            if re.search(r'["\']\w+["\']\s*:', text):
                return 'json_like'
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 2 and any('|' in line for line in lines) and any(re.search(r'\|?\s*:?-{3,}:?\s*\|', line) for line in lines):
        return 'table_markdown'
    comma_counts = [line.count(',') for line in lines[1:8] if not line.startswith('```')]
    if len(comma_counts) >= 2 and sum(count >= 1 for count in comma_counts) >= 2:
        return 'table_markdown'
    bullet_lines = sum(bool(re.match(r'^(?:[-*+]\s+|\d+[.)]\s+)', line)) for line in lines)
    if bullet_lines >= 2:
        return 'bullet_list'
    if re.search(r'\b(?:ignore|irrelevant|do not store|metadata only|ambiguous|unknown field)\b', text, re.I):
        return 'noisy_mixed'
    return 'natural_language'


def annotate_sample(sample: dict) -> dict:
    gold_sql = sample.get('gold_sql') or []
    records, unparsed = record_probes(gold_sql)
    row_count = len(records) or int(sample.get('num_records') or 1)
    table_count = len(sample.get('gold_tables') or []) or int(sample.get('num_tables') or 1)
    column_count = len(sample.get('gold_columns') or [])
    operation = str(sample.get('operation_type') or 'insert').lower()
    input_type = sample.get('input_type') or infer_input_type(sample.get('input_text', ''))
    scope = infer_scope(table_count, row_count)
    auto_difficulty = infer_auto_difficulty(operation, table_count, row_count, column_count, len(gold_sql))
    sample = dict(sample)
    sample.update({
        'impact_scope': scope,
        'auto_difficulty': auto_difficulty,
        'row_count': row_count,
        'row_count_bucket': bucket_count(row_count),
        'table_count': table_count,
        'column_count': column_count,
        'sql_statement_count': len(gold_sql),
        'input_type': input_type,
        'has_unparsed_gold_sql': bool(unparsed),
        'complexity': {
            'impact_scope': scope,
            'auto_difficulty': auto_difficulty,
            'row_count': row_count,
            'row_count_bucket': bucket_count(row_count),
            'table_count': table_count,
            'column_count': column_count,
            'sql_statement_count': len(gold_sql),
            'operation_type': operation,
            'has_unparsed_gold_sql': bool(unparsed),
            'input_type': input_type,
        },
    })
    return sample


def summarize_annotations(data: list[dict]) -> dict:
    fields = [
        'operation_type',
        'auto_difficulty',
        'impact_scope',
        'row_count_bucket',
        'db_id',
        'input_type',
    ]
    summary = {'num_samples': len(data)}
    for field in fields:
        summary[field] = dict(Counter(str(x.get(field, 'unknown')) for x in data))
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--out')
    ap.add_argument('--summary-out')
    args = ap.parse_args()
    data = [annotate_sample(sample) for sample in load_json(args.data)]
    out = Path(args.out or args.data)
    save_json(data, out)
    summary = summarize_annotations(data)
    if args.summary_out:
        save_json(summary, args.summary_out)
    print(f'Wrote {out}: {len(data)} samples')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
