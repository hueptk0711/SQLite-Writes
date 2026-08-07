import argparse
import json
from pathlib import Path

from nldbwrite.common import load_json, write_jsonl


TASKS = (
    'composite_key_or_partial_update_upsert',
    'multi_row_or_conflicting_value_upsert',
    'relational_parent_child_natural_key',
    'multi_table_relational_write',
    'large_batch_nested_or_semistructured_write',
)


def generate(profile_dir, out_path, count):
    candidates = []
    for profile_path in sorted(Path(profile_dir).glob('*.json')):
        profile = load_json(profile_path)
        db_id = str(profile.get('db_id') or profile.get('database') or profile_path.stem)
        tables = [table.get('name') for table in profile.get('tables') or [] if table.get('name')]
        if not db_id or not tables:
            continue
        for task_index, task in enumerate(TASKS):
            rotated = tables[task_index % len(tables):] + tables[:task_index % len(tables)]
            candidates.append((db_id, task, rotated[:3]))
    rows = []
    if not candidates:
        raise ValueError(f'No database profiles found under {profile_dir}')
    for index in range(1, count + 1):
        db_id, task, tables = candidates[(index - 1) % len(candidates)]
        rows.append({
            'id': f'hard_independent_{index:04d}',
            'db_id': db_id,
            'authoring_task': task,
            'target_tables_hint': tables,
            'input_text': '',
            'operation_type': '',
            'gold_records': [],
            'gold_sql': [],
            'gold_tables': [],
            'gold_columns': [],
            'independently_authored': True,
            'author_id': '',
            'reviewer_id': '',
            'adjudication_status': 'pending',
            'notes': '',
        })
    write_jsonl(rows, out_path)
    print(json.dumps({'templates': len(rows), 'out_path': str(out_path)}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile-dir', default='artifacts/profiles_aug900')
    parser.add_argument('--out', default='data/authoring/hard_examples_template.jsonl')
    parser.add_argument('--count', type=int, default=250)
    args = parser.parse_args()
    generate(args.profile_dir, args.out, args.count)


if __name__ == '__main__':
    main()
