import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from nldbwrite.common import load_json, write_jsonl


def normalize_text(value: Any) -> str:
    text = str(value if value is not None else '').strip().casefold()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace('_', ' ').replace('-', ' ')
    return re.sub(r'\s+', ' ', text).strip()


def normalize_value(value: Any) -> str:
    if value is None:
        return '<null>'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'{value:g}'
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = str(value).strip()
    lowered = normalize_text(text)
    if lowered in {'true', 'false', 'yes', 'no'}:
        return lowered
    numeric = text.replace(',', '')
    if re.fullmatch(r'[+-]?\d+(?:\.\d+)?', numeric):
        try:
            number = float(numeric)
            return f'{number:g}'
        except ValueError:
            pass
    return lowered


def infer_value_type(value: Any) -> str:
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return 'number'
    text = str(value).strip()
    if re.fullmatch(r'[+-]?\d+(?:[.,]\d+)?', text):
        return 'number'
    if re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?', text):
        return 'date'
    if text.casefold() in {'true', 'false', 'yes', 'no'}:
        return 'boolean'
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{2,}', text) and any(ch.isdigit() for ch in text):
        return 'identifier'
    return 'string'


def load_profiles(profile_dir: str | Path | None) -> dict[str, dict[str, Any]]:
    if not profile_dir:
        return {}
    root = Path(profile_dir)
    if not root.exists():
        return {}
    return {path.stem: load_json(path) for path in root.glob('*.json')}


def table_metadata(profile: dict[str, Any] | None, table_name: str) -> dict[str, Any]:
    if not profile:
        return {'primary_keys': set(), 'unique_columns': set(), 'required_columns': set()}
    for table in profile.get('tables') or []:
        if table.get('name') != table_name:
            continue
        primary_keys = set(table.get('primary_keys') or [])
        unique_columns = set(primary_keys)
        for idx in table.get('unique_indexes') or []:
            unique_columns.update(idx.get('columns') or [])
        required = set(table.get('required_insert_columns') or [])
        required.update(col.get('name') for col in table.get('columns') or [] if col.get('not_null'))
        return {
            'primary_keys': primary_keys,
            'unique_columns': unique_columns,
            'required_columns': {x for x in required if x},
        }
    return {'primary_keys': set(), 'unique_columns': set(), 'required_columns': set()}


def fact_value_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def facts_context_from_facts(facts: list[dict[str, Any]]) -> str:
    if not facts:
        return 'Extracted facts: none'
    lines = ['Extracted facts:']
    for idx, fact in enumerate(facts, start=1):
        fact_id = fact.get('fact_id') or f'f{idx:04d}'
        attr = fact.get('attribute') or fact.get('gold_column') or 'unknown_attribute'
        lines.append(f'- {fact_id}: {attr} = {fact_value_text(fact.get("value"))}')
    return '\n'.join(lines)


def derive_gold_facts(sample: dict[str, Any], profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    fact_idx = 1
    sample_op = str(sample.get('operation_type') or 'insert').lower()
    for rec_idx, rec in enumerate(sample.get('gold_records') or [], start=1):
        table = rec.get('table')
        record_id = rec.get('record_id') or f'r{rec_idx:04d}'
        operation = str(rec.get('operation') or sample_op or 'insert').lower()
        meta = table_metadata(profile, table)
        for column, value in (rec.get('values') or {}).items():
            facts.append({
                'fact_id': f'f{fact_idx:04d}',
                'record_id': record_id,
                'attribute': str(column),
                'value': value,
                'value_type': infer_value_type(value),
                'source': 'gold_record',
                'gold_table': table,
                'gold_column': column,
                'record_operation': operation,
                'normalized_attribute': normalize_text(column),
                'normalized_value': normalize_value(value),
                'is_required_value': column in meta['required_columns'],
                'is_conflict_key': operation != 'insert' and column in meta['unique_columns'],
            })
            fact_idx += 1
    return facts


def gold_fact_rows(data: list[dict[str, Any]], profiles: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = []
    profiles = profiles or {}
    for sample in data:
        facts = derive_gold_facts(sample, profiles.get(sample.get('db_id')))
        rows.append({
            'sample_id': str(sample['id']),
            'db_id': sample.get('db_id'),
            'facts': facts,
        })
    return rows


def gold_context_rows(data: list[dict[str, Any]], profiles: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = []
    profiles = profiles or {}
    for sample in data:
        facts = derive_gold_facts(sample, profiles.get(sample.get('db_id')))
        rows.append({
            'sample_id': str(sample['id']),
            'db_id': sample.get('db_id'),
            'facts_context': facts_context_from_facts(facts),
            'facts': facts,
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data/processed/nl_db_write_augmented900_v1.json')
    ap.add_argument('--profile-dir', default='artifacts/profiles_aug900')
    ap.add_argument('--out-facts', default='artifacts/facts/aug900_gold_facts.jsonl')
    ap.add_argument('--out-context', default='artifacts/facts/aug900_gold_fact_context.jsonl')
    args = ap.parse_args()
    data = load_json(args.data)
    profiles = load_profiles(args.profile_dir)
    write_jsonl(gold_fact_rows(data, profiles), args.out_facts)
    write_jsonl(gold_context_rows(data, profiles), args.out_context)
    print(f'Wrote {args.out_facts}')
    print(f'Wrote {args.out_context}')


if __name__ == '__main__':
    main()

