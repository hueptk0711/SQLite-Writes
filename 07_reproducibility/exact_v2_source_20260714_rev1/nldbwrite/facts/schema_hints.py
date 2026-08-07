import argparse
import re
from pathlib import Path
from typing import Any

from nldbwrite.common import load_json, write_jsonl


KEY_NAME_RE = re.compile(r'(^|_)(id|code|key|uuid|email|number|no)($|_)', re.I)


def _column_flags(table: dict[str, Any], column: dict[str, Any]) -> list[str]:
    name = column.get('name')
    flags = []
    if column.get('is_primary_key') or name in set(table.get('primary_keys') or []):
        flags.append('PK')
    unique_columns = {c for idx in table.get('unique_indexes') or [] for c in idx.get('columns') or []}
    if name in unique_columns:
        flags.append('UNIQUE')
    if column.get('not_null') or name in set(table.get('required_insert_columns') or []):
        flags.append('REQUIRED')
    if column.get('is_foreign_key'):
        flags.append('FK')
    if KEY_NAME_RE.search(str(name or '')):
        flags.append('KEY_LIKE')
    return flags


def _hint_rows(profile: dict[str, Any], max_fields: int) -> tuple[list[str], list[str]]:
    key_rows: list[str] = []
    required_rows: list[str] = []
    for table in profile.get('tables') or []:
        for column in table.get('columns') or []:
            flags = _column_flags(table, column)
            if not flags:
                continue
            field = f"{table.get('name')}.{column.get('name')}"
            detail = ', '.join(flags)
            rendered = f"- {field} ({column.get('type')}; {detail})"
            if any(flag in flags for flag in ['PK', 'UNIQUE', 'FK', 'KEY_LIKE']):
                key_rows.append(rendered)
            if 'REQUIRED' in flags:
                required_rows.append(rendered)
    return key_rows[:max_fields], required_rows[:max_fields]


def schema_light_hints_for_profile(profile: dict[str, Any], max_fields: int = 120) -> str:
    key_rows, required_rows = _hint_rows(profile, max_fields)
    lines = ['Schema-light hints for fact extraction:']
    lines.append('Important identifier/key-like fields:')
    lines.extend(key_rows or ['- none detected'])
    lines.append('Required fields:')
    lines.extend(required_rows or ['- none detected'])
    return '\n'.join(lines)


def load_profiles(profile_dir: str | Path) -> dict[str, dict[str, Any]]:
    root = Path(profile_dir)
    return {path.stem: load_json(path) for path in sorted(root.glob('*.json'))}


def schema_hint_rows(data_path: str | Path, profile_dir: str | Path, max_fields: int = 120) -> list[dict[str, Any]]:
    data = load_json(data_path)
    profiles = load_profiles(profile_dir)
    rows = []
    for sample in data:
        db_id = sample.get('db_id')
        profile = profiles.get(db_id) or {'tables': []}
        rows.append({
            'sample_id': str(sample['id']),
            'db_id': db_id,
            'schema_light_hints': schema_light_hints_for_profile(profile, max_fields=max_fields),
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data/processed/nl_db_write_augmented900_v1.json')
    ap.add_argument('--profile-dir', default='artifacts/profiles_aug900')
    ap.add_argument('--out', default='artifacts/facts/schema_light_hints.jsonl')
    ap.add_argument('--max-fields', type=int, default=120)
    args = ap.parse_args()
    rows = schema_hint_rows(args.data, args.profile_dir, max_fields=args.max_fields)
    write_jsonl(rows, args.out)
    print(f'Wrote {args.out}')


if __name__ == '__main__':
    main()
