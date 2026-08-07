import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from nldbwrite.common import load_json, read_id_file, save_json
from nldbwrite.data.annotate_complexity import annotate_sample
from nldbwrite.eval.evaluate import record_probes
from nldbwrite.sql.build_sql import build_sql_from_json
from nldbwrite.sql.safety import is_safe_sql


FORMATS = ['natural_language', 'bullet_list', 'table_markdown', 'json_like', 'natural_language', 'noisy_mixed']


def human_name(value):
    return str(value).replace('_', ' ').strip()


def display_value(value):
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def grouped_records(records):
    groups = defaultdict(list)
    for record in records:
        groups[str(record.get('table'))].append(record.get('values') or {})
    return groups


def render_natural(records):
    groups = grouped_records(records)
    parts = []
    for table, rows in groups.items():
        label = human_name(table)
        if len(rows) == 1:
            values = ', '.join(f'{human_name(k)} is {display_value(v)}' for k, v in rows[0].items())
            parts.append(f'Add one record to {label}: {values}.')
        else:
            rendered = []
            for index, row in enumerate(rows, start=1):
                values = ', '.join(f'{human_name(k)}={display_value(v)}' for k, v in row.items())
                rendered.append(f'record {index} ({values})')
            parts.append(f'Add {len(rows)} records to {label}: ' + '; '.join(rendered) + '.')
    return ' '.join(parts)


def render_bullets(records):
    lines = ['Please add the following database records:']
    for index, record in enumerate(records, start=1):
        values = ', '.join(f'{human_name(k)}={display_value(v)}' for k, v in (record.get('values') or {}).items())
        lines.append(f"{index}. {human_name(record.get('table'))}: {values}")
    return '\n'.join(lines)


def render_markdown(records):
    sections = ['Please import the following table data:']
    for table, rows in grouped_records(records).items():
        columns = []
        for row in rows:
            for column in row:
                if column not in columns:
                    columns.append(column)
        sections.extend([f'\nTable: {human_name(table)}', '| ' + ' | '.join(columns) + ' |', '| ' + ' | '.join('---' for _ in columns) + ' |'])
        for row in rows:
            sections.append('| ' + ' | '.join(display_value(row.get(column, '')) for column in columns) + ' |')
    return '\n'.join(sections)


def render_json(records):
    payload = [{'table': record.get('table'), **(record.get('values') or {})} for record in records]
    return 'Please add these records:\n```json\n' + json.dumps(payload, ensure_ascii=False, indent=2) + '\n```'


def render_noisy(records, source_id):
    return (
        'Import the data below. The batch label is metadata only and must not be stored: '
        f'BATCH-{source_id}. Some surrounding prose is irrelevant; use only the listed fields.\n\n'
        + render_bullets(records)
    )


def render_input(records, input_type, source_id):
    if input_type == 'natural_language':
        return render_natural(records)
    if input_type == 'bullet_list':
        return render_bullets(records)
    if input_type == 'table_markdown':
        return render_markdown(records)
    if input_type == 'json_like':
        return render_json(records)
    return render_noisy(records, source_id)


def sample_columns(records):
    columns = []
    seen = set()
    for record in records:
        table = record.get('table')
        for column in (record.get('values') or {}):
            full = f'{table}.{column}'
            if full not in seen:
                seen.add(full); columns.append(full)
    return columns


def stable_hash(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def make_variant(source, records, input_type, augmentation_type, serial, profile=None, keep_gold_sql=False):
    source_group = str(source.get('source_group_id') or source['id'])
    operation = str(source.get('operation_type') or 'insert')
    if keep_gold_sql:
        sqls = list(source.get('gold_sql') or [])
    else:
        status, sqls, errors, _ = build_sql_from_json({'records': [
            {'table': record.get('table'), 'operation': operation, 'values': record.get('values') or {}}
            for record in records
        ]}, profile)
        if status != 'success' or errors or not sqls:
            return None
        parsed_records, unparsed_tables = record_probes(sqls)
        if unparsed_tables or not parsed_records:
            return None
        # The deterministic builder can normalize values according to SQLite
        # affinity (for example a date-like string in an INTEGER column).
        # Gold records must describe the emitted SQL, not the pre-normalized
        # source values, otherwise strict checking reports a false mismatch.
        records = [
            {
                'table': record.get('table'),
                'operation': operation,
                'values': record.get('values') or {},
            }
            for record in parsed_records
        ]
    if not all(is_safe_sql(sql)[0] for sql in sqls):
        return None
    tables = list(dict.fromkeys(str(record.get('table')) for record in records))
    variant = {
        'id': f"aug_{source_group}_{augmentation_type}_{serial:03d}",
        'source_id': source.get('source_id'),
        'source_group_id': source_group,
        'db_id': source['db_id'],
        'input_text': render_input(records, input_type, source_group),
        'input_type': input_type,
        'operation_type': operation,
        'gold_tables': tables,
        'gold_columns': sample_columns(records),
        'gold_records': records,
        'gold_records_source': 'deterministic_augmentation_from_validated_gold_records',
        'gold_sql': sqls,
        'difficulty': source.get('difficulty'),
        'num_tables': len(tables),
        'num_records': len(records),
        'has_foreign_key': len(tables) > 1,
        'is_augmented': True,
        'augmentation_type': augmentation_type,
        'machine_validation_status': 'pending_execution_validation',
        'provenance': {
            'source_dataset': 'nl_db_write_seed359',
            'source_sample_id': source_group,
            'transformation': augmentation_type,
            'input_rendering': input_type,
        },
    }
    variant['provenance']['sha256'] = stable_hash({'source': source_group, 'records': records, 'sql': sqls, 'input_type': input_type})
    return annotate_sample(variant)


def round_robin(candidates_by_source, limit):
    output = []
    positions = {key: 0 for key in candidates_by_source}
    keys = sorted(candidates_by_source)
    while len(output) < limit:
        progress = False
        for key in keys:
            pos = positions[key]
            if pos < len(candidates_by_source[key]):
                output.append(candidates_by_source[key][pos])
                positions[key] += 1
                progress = True
                if len(output) >= limit:
                    break
        if not progress:
            break
    return output


def build_candidates(data, profiles, target_size):
    single_candidates = defaultdict(list)
    batch_candidates = defaultdict(list)
    relational_candidates = defaultdict(list)
    for source in sorted(data, key=lambda row: str(row['id'])):
        source_group = str(source['id'])
        records = list(source.get('gold_records') or [])
        operation = str(source.get('operation_type') or 'insert')
        table_count = len(source.get('gold_tables') or [])
        profile = profiles.get(source['db_id'])
        if operation == 'insert' and table_count == 1 and profile and len(records) >= 2:
            for index, record in enumerate(records[:3]):
                fmt = FORMATS[(index + int(stable_hash(source_group)[:2], 16)) % len(FORMATS)]
                variant = make_variant(source, [record], fmt, 'single_row_subset', index + 1, profile)
                if variant:
                    single_candidates[source_group].append(variant)
        if operation == 'insert' and table_count == 1 and profile and len(records) >= 6:
            for index, size in enumerate((3, 5)):
                fmt = FORMATS[(index + 2 + int(stable_hash(source_group)[2:4], 16)) % len(FORMATS)]
                variant = make_variant(source, records[index:index + size], fmt, 'small_batch_subset', index + 1, profile)
                if variant:
                    batch_candidates[source_group].append(variant)
        if operation == 'insert' and table_count > 1:
            for index, fmt in enumerate(('natural_language', 'bullet_list', 'table_markdown', 'json_like')):
                variant = make_variant(source, records, fmt, 'relational_format_variant', index + 1, keep_gold_sql=True)
                if variant:
                    relational_candidates[source_group].append(variant)

    needed = max(0, target_size - len(data))
    desired_single = min(300, needed)
    desired_relational = min(96, max(0, needed - desired_single))
    desired_batch = max(0, needed - desired_single - desired_relational)
    selected = round_robin(single_candidates, desired_single)
    selected += round_robin(relational_candidates, desired_relational)
    selected += round_robin(batch_candidates, desired_batch)
    if len(selected) < needed:
        all_remaining = []
        selected_ids = {row['id'] for row in selected}
        for pool in (single_candidates, batch_candidates, relational_candidates):
            for rows in pool.values():
                all_remaining.extend(row for row in rows if row['id'] not in selected_ids)
        selected.extend(sorted(all_remaining, key=lambda row: row['id'])[:needed - len(selected)])
    return selected[:needed]


def summarize(data):
    summary = {'num_samples': len(data), 'num_original': sum(not x.get('is_augmented') for x in data), 'num_augmented': sum(bool(x.get('is_augmented')) for x in data)}
    for field in ['db_id','operation_type','input_type','auto_difficulty','impact_scope','row_count_bucket','augmentation_type']:
        summary[field] = dict(Counter(str(x.get(field, 'unknown')) for x in data))
    summary['num_source_groups'] = len({str(x.get('source_group_id') or x['id']) for x in data})
    return summary


def write_splits(data, base_dev_ids, base_test_ids, out_dir):
    dev_sources = read_id_file(base_dev_ids); test_sources = read_id_file(base_test_ids)
    overlap = dev_sources & test_sources
    if overlap:
        raise ValueError(f'Base split leakage: {sorted(overlap)[:5]}')
    dev = []; test = []
    for sample in data:
        source = str(sample.get('source_group_id') or sample['id'])
        if source in dev_sources:
            dev.append(str(sample['id']))
        elif source in test_sources:
            test.append(str(sample['id']))
        else:
            raise ValueError(f'Source group not found in locked base splits: {source}')
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    (out / 'dev_ids.txt').write_text('\n'.join(dev) + '\n', encoding='utf-8')
    (out / 'test_ids.txt').write_text('\n'.join(test) + '\n', encoding='utf-8')
    by_id = {str(sample['id']): sample for sample in data}
    # Keep the local/server preflight fast by drawing smoke cases from the
    # smaller databases while retaining several schemas and input forms.
    smoke = []
    smoke_db_order = [
        'superhero', 'student_club', 'toxicology',
        'thrombosis_prediction', 'california_schools', 'formula_1',
    ]
    for db_id in smoke_db_order:
        smoke.extend([
            sample_id for sample_id in test
            if str(by_id[sample_id].get('db_id')) == db_id
        ][:2])
    smoke = smoke[:min(12, len(test))]
    (out / 'smoke_ids.txt').write_text('\n'.join(smoke) + '\n', encoding='utf-8')
    model2_buckets = defaultdict(list)
    for sample_id in test:
        sample = by_id[sample_id]
        key = (
            str(sample.get('db_id')),
            str(sample.get('operation_type')),
            str(sample.get('auto_difficulty')),
            str(bool(sample.get('is_augmented'))),
        )
        model2_buckets[key].append(sample)
    for rows in model2_buckets.values():
        rows.sort(key=lambda row: str(row['id']))
    model2 = []
    used_sources = set()
    positions = {key: 0 for key in model2_buckets}
    keys = sorted(model2_buckets, key=str)
    while len(model2) < min(200, len(test)):
        progress = False
        for key in keys:
            rows = model2_buckets[key]
            while positions[key] < len(rows):
                sample = rows[positions[key]]; positions[key] += 1
                source = str(sample.get('source_group_id') or sample['id'])
                if source in used_sources:
                    continue
                model2.append(str(sample['id'])); used_sources.add(source); progress = True
                break
            if len(model2) >= min(200, len(test)):
                break
        if not progress:
            break
    (out / 'model2_subset_ids.txt').write_text('\n'.join(model2) + '\n', encoding='utf-8')
    return {
        'dev': len(dev),
        'test': len(test),
        'smoke': len(smoke),
        'model2_subset': len(model2),
        'model2_source_groups': len(used_sources),
        'source_overlap': 0,
    }


def write_review_sheet(data, path, size=100):
    augmented = [x for x in data if x.get('is_augmented')]
    buckets = defaultdict(list)
    for sample in augmented:
        buckets[(sample.get('augmentation_type'), sample.get('input_type'), sample.get('db_id'))].append(sample)
    selected = []
    keys = sorted(buckets, key=str)
    positions = {key: 0 for key in keys}
    while len(selected) < min(size, len(augmented)):
        progress = False
        for key in keys:
            pos = positions[key]
            if pos < len(buckets[key]):
                selected.append(buckets[key][pos]); positions[key] += 1; progress = True
                if len(selected) >= size:
                    break
        if not progress:
            break
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fields = ['id','source_group_id','db_id','operation_type','augmentation_type','input_type','input_text','gold_sql','gold_records','review_status','review_notes']
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for sample in selected:
            writer.writerow({
                **{field: sample.get(field) for field in fields if field not in {'gold_sql','gold_records','review_status','review_notes'}},
                'gold_sql': json.dumps(sample.get('gold_sql'), ensure_ascii=False),
                'gold_records': json.dumps(sample.get('gold_records'), ensure_ascii=False),
                'review_status': 'pending',
                'review_notes': '',
            })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed-data', required=True)
    ap.add_argument('--profile-dir', required=True)
    ap.add_argument('--base-dev-ids', required=True)
    ap.add_argument('--base-test-ids', required=True)
    ap.add_argument('--out-data', required=True)
    ap.add_argument('--out-splits', required=True)
    ap.add_argument('--out-summary', required=True)
    ap.add_argument('--review-sheet', required=True)
    ap.add_argument('--target-size', type=int, default=900)
    ap.add_argument('--mark-machine-validated', action='store_true')
    args = ap.parse_args()
    seed = load_json(args.seed_data)
    profiles = {path.stem: load_json(path) for path in Path(args.profile_dir).glob('*.json')}
    originals = []
    for sample in seed:
        source_item = dict(sample)
        source_item.pop('input_type', None)
        item = annotate_sample(source_item)
        item['source_group_id'] = str(sample['id'])
        item['is_augmented'] = False
        item['augmentation_type'] = 'original'
        item['machine_validation_status'] = 'validated_original_gold_sql'
        item['provenance'] = {'source_dataset': 'nl_db_write_seed359', 'source_sample_id': str(sample['id']), 'transformation': 'none'}
        originals.append(item)
    variants = build_candidates(originals, profiles, args.target_size)
    data = originals + variants
    if args.mark_machine_validated:
        for sample in data:
            sample['machine_validation_status'] = 'validated_on_original_sqlite'
    if len(data) != args.target_size:
        raise SystemExit(f'Could only build {len(data)} samples, expected {args.target_size}')
    ids = [str(x['id']) for x in data]
    if len(ids) != len(set(ids)):
        raise SystemExit('Duplicate augmented ids')
    save_json(data, args.out_data)
    split_summary = write_splits(data, args.base_dev_ids, args.base_test_ids, args.out_splits)
    summary = summarize(data); summary['splits'] = split_summary; summary['dataset_sha256'] = stable_hash(data)
    save_json(summary, args.out_summary)
    write_review_sheet(data, args.review_sheet)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
