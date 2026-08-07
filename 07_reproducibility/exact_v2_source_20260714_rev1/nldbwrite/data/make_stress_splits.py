import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from nldbwrite.common import load_json


def write_ids(rows: list[dict[str, Any]], path: Path, limit: int | None = None) -> int:
    selected = rows[:limit] if limit else rows
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(str(x['id']) for x in selected) + ('\n' if selected else ''), encoding='utf-8')
    return len(selected)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data/processed/nl_db_write_augmented900_v1.json')
    ap.add_argument('--out-dir', default='data/splits/augmented900_v1/stress')
    ap.add_argument('--profile-dir', default='artifacts/profiles_aug900')
    ap.add_argument('--limit-per-split', type=int, default=100)
    args = ap.parse_args()

    data = load_json(args.data)
    profile_dir = Path(args.profile_dir)
    profiles = {path.stem: load_json(path) for path in profile_dir.glob('*.json')}

    def target_profiles(sample):
        profile = profiles.get(str(sample.get('db_id')), {})
        targets = set(sample.get('gold_tables') or [])
        return [table for table in profile.get('tables') or [] if table.get('name') in targets]

    def schema_columns(sample):
        return sum(len(table.get('columns') or []) for table in profiles.get(str(sample.get('db_id')), {}).get('tables') or [])

    def has_composite_key(sample):
        return any(len(table.get('primary_keys') or []) > 1 for table in target_profiles(sample))

    def is_many_to_many(sample):
        return any(len(table.get('primary_keys') or []) > 1 and len(table.get('foreign_keys') or []) >= 2 for table in target_profiles(sample))

    def values(sample):
        return [value for record in sample.get('gold_records') or [] for value in (record.get('values') or {}).values()]

    reserved = {'order', 'group', 'select', 'table', 'index', 'constraint', 'references', 'values', 'type', 'status'}
    out_dir = Path(args.out_dir)
    predicates: dict[str, Callable[[dict[str, Any]], bool]] = {
        'upsert_stress': lambda x: str(x.get('operation_type')).lower() in {'upsert', 'update', 'replace'},
        'relational_stress': lambda x: int(x.get('table_count') or x.get('num_tables') or 0) > 1 or bool(x.get('has_foreign_key')),
        'large_batch_10plus': lambda x: int(x.get('row_count') or x.get('num_records') or 0) >= 10,
        'large_batch_20plus': lambda x: int(x.get('row_count') or x.get('num_records') or 0) >= 20,
        'hard_cases': lambda x: str(x.get('auto_difficulty') or x.get('difficulty')).lower() in {'hard', 'extra_hard', 'extra-hard'},
        'noisy_input': lambda x: str(x.get('input_type')).lower() == 'noisy_mixed',
        'markdown_table': lambda x: str(x.get('input_type')).lower() == 'table_markdown',
        'json_like': lambda x: str(x.get('input_type')).lower() == 'json_like',
        'single_row_insert': lambda x: str(x.get('operation_type')).lower() == 'insert' and int(x.get('row_count') or 1) == 1,
        'batch_insert_3': lambda x: str(x.get('operation_type')).lower() == 'insert' and int(x.get('row_count') or 1) == 3,
        'batch_insert_5': lambda x: str(x.get('operation_type')).lower() == 'insert' and int(x.get('row_count') or 1) == 5,
        'relational_parent_child': lambda x: int(x.get('table_count') or 1) > 1 and not is_many_to_many(x),
        'relational_many_to_many': is_many_to_many,
        'upsert_simple': lambda x: str(x.get('operation_type')).lower() != 'insert' and not has_composite_key(x),
        'upsert_composite_key': lambda x: str(x.get('operation_type')).lower() != 'insert' and has_composite_key(x),
        'upsert_update_mask': lambda x: str(x.get('operation_type')).lower() != 'insert' and any('update' in str(k).lower() or 'mask' in str(k).lower() for record in x.get('gold_records') or [] for k in record),
        'missing_optional_value': lambda x: any(value is None for value in values(x)),
        'missing_required_value': lambda x: bool(re.search(r'\b(missing|required|thiếu|không có)\b', str(x.get('input_text') or ''), re.I)),
        'ambiguous_table': lambda x: bool(re.search(r'\b(ambiguous|unknown table|which table|bảng nào)\b', str(x.get('input_text') or ''), re.I)),
        'sql_injection_like': lambda x: bool(re.search(r'(?:drop\s+table|delete\s+from|--|/\*|;\s*(?:drop|delete|alter))', str(x.get('input_text') or ''), re.I)),
        'reserved_keyword_identifier': lambda x: any(str(column).split('.')[-1].lower() in reserved for column in x.get('gold_columns') or []),
        'value_normalization': lambda x: any(isinstance(value, (bool, int, float)) or bool(re.search(r'\d{4}-\d{1,2}-\d{1,2}', str(value))) for value in values(x)),
        'null_default_handling': lambda x: any(value is None for value in values(x)) or any(table.get('forbidden_insert_columns') for table in target_profiles(x)),
        'schema_small': lambda x: schema_columns(x) < 30,
        'schema_medium': lambda x: 30 <= schema_columns(x) <= 100,
        'schema_large': lambda x: 100 < schema_columns(x) <= 300,
        'schema_very_large': lambda x: schema_columns(x) > 300,
    }
    manifest = {
        'data': args.data,
        'limit_per_split': args.limit_per_split,
        'splits': {},
    }
    for name, predicate in predicates.items():
        rows = sorted([x for x in data if predicate(x)], key=lambda x: (str(x.get('db_id')), str(x.get('source_group_id')), str(x.get('id'))))
        count = write_ids(rows, out_dir / f'{name}_ids.txt', args.limit_per_split)
        manifest['splits'][name] = {
            'available': len(rows),
            'written': count,
            'by_db': dict(Counter(str(x.get('db_id')) for x in rows)),
        }
    (out_dir / 'stress_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
