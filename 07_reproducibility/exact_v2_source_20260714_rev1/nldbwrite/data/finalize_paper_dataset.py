import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nldbwrite.common import iter_jsonl, load_json, read_id_file, save_json, sha256_file


ORIGINAL_CATEGORY = 'independently_authored_original'
FORMAT_CATEGORY = 'semantics_preserving_transformation'
DERIVED_CATEGORY = 'controlled_subtask_derivation'


def source_group(sample: dict[str, Any]) -> str:
    provenance = sample.get('provenance') or {}
    return str(sample.get('source_group_id') or provenance.get('source_sample_id') or sample['id'])


def is_original(sample: dict[str, Any]) -> bool:
    return (
        str(sample.get('augmentation_type') or '').lower() == 'original'
        or sample.get('is_augmented') is False
        or str(sample.get('id')) == source_group(sample)
    )


def origin_category(sample: dict[str, Any]) -> str:
    if is_original(sample):
        return ORIGINAL_CATEGORY
    augmentation = str(sample.get('augmentation_type') or '').lower()
    if augmentation in {'single_row_subset', 'small_batch_subset'}:
        return DERIVED_CATEGORY
    return FORMAT_CATEGORY


def write_ids(path: str | Path, ids: list[str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(f'{sample_id}\n' for sample_id in ids), encoding='utf-8')


def correction_exclusions(path: str | Path | None) -> list[dict[str, Any]]:
    if not path or not Path(path).exists():
        return []
    exclusions = []
    for row in iter_jsonl(path):
        if row.get('decision') != 'remove_sample':
            continue
        exclusions.append({
            'id': str(row['sample_id']),
            'source_group_id': str(row['sample_id']),
            'reason': row.get('reason') or row.get('issue_type') or 'removed_after_human_review',
            'issue_type': row.get('issue_type'),
            'exclusion_stage': 'human_gold_review',
        })
    return exclusions


def partition_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        'samples': len(rows),
        'source_groups': len({source_group(row) for row in rows}),
        'origin_categories': dict(Counter(row['example_origin_category'] for row in rows)),
        'augmentation_types': dict(Counter(str(row.get('augmentation_type') or 'unknown') for row in rows)),
        'operation_types': dict(Counter(str(row.get('operation_type') or 'unknown') for row in rows)),
        'databases': len({str(row.get('db_id')) for row in rows}),
    }


def finalize_dataset(
    data_path: str | Path,
    dev_ids_path: str | Path,
    test_ids_path: str | Path,
    out_data_path: str | Path,
    out_dev_ids_path: str | Path,
    out_test_ids_path: str | Path,
    manifest_path: str | Path,
    version: str,
    corrections_path: str | Path | None = None,
) -> dict[str, Any]:
    data_path = Path(data_path)
    dev_ids_path = Path(dev_ids_path)
    test_ids_path = Path(test_ids_path)
    out_data_path = Path(out_data_path)
    out_dev_ids_path = Path(out_dev_ids_path)
    out_test_ids_path = Path(out_test_ids_path)
    manifest_path = Path(manifest_path)

    data = load_json(data_path)
    by_id = {str(row['id']): row for row in data}
    dev_ids = [str(x) for x in read_id_file(dev_ids_path)]
    test_ids = [str(x) for x in read_id_file(test_ids_path)]
    expected = set(dev_ids) | set(test_ids)
    if set(dev_ids) & set(test_ids):
        raise ValueError('Development and test IDs overlap')
    if expected != set(by_id):
        missing = sorted(expected - set(by_id))
        unassigned = sorted(set(by_id) - expected)
        raise ValueError(f'Dataset/split mismatch: missing={missing[:5]}, unassigned={unassigned[:5]}')

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in data:
        groups[source_group(row)].append(row)
    orphan_groups = {
        group_id for group_id, rows in groups.items()
        if not any(is_original(row) for row in rows)
    }
    orphan_ids = {
        str(row['id']) for group_id in orphan_groups for row in groups[group_id]
    }

    final_data = []
    for row in data:
        if str(row['id']) in orphan_ids:
            continue
        normalized = dict(row)
        normalized['dataset_version'] = version
        normalized['example_origin_category'] = origin_category(normalized)
        final_data.append(normalized)

    final_dev_ids = [sample_id for sample_id in dev_ids if sample_id not in orphan_ids]
    final_test_ids = [sample_id for sample_id in test_ids if sample_id not in orphan_ids]
    final_by_id = {str(row['id']): row for row in final_data}
    if set(final_dev_ids) | set(final_test_ids) != set(final_by_id):
        raise AssertionError('Final dataset and split IDs are inconsistent')
    dev_groups = {source_group(final_by_id[sample_id]) for sample_id in final_dev_ids}
    test_groups = {source_group(final_by_id[sample_id]) for sample_id in final_test_ids}
    if dev_groups & test_groups:
        raise AssertionError('Source groups overlap between development and test')

    out_data_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(final_data, out_data_path)
    write_ids(out_dev_ids_path, final_dev_ids)
    write_ids(out_test_ids_path, final_test_ids)

    exclusions = correction_exclusions(corrections_path)
    exclusions.extend({
        'id': sample_id,
        'source_group_id': source_group(by_id[sample_id]),
        'reason': 'Removed with its source group because the independently authored original was excluded after gold review.',
        'issue_type': 'orphaned_augmented_variant',
        'exclusion_stage': 'source_group_integrity',
    } for sample_id in sorted(orphan_ids))

    dev_rows = [final_by_id[sample_id] for sample_id in final_dev_ids]
    test_rows = [final_by_id[sample_id] for sample_id in final_test_ids]
    manifest = {
        'dataset_version': version,
        'created_at': datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds'),
        'source_dataset': str(data_path),
        'source_dataset_sha256': sha256_file(data_path),
        'source_dev_split': str(dev_ids_path),
        'source_test_split': str(test_ids_path),
        'dataset_path': str(out_data_path),
        'dev_split_path': str(out_dev_ids_path),
        'test_split_path': str(out_test_ids_path),
        'dataset_sha256': sha256_file(out_data_path),
        'dev_split_sha256': sha256_file(out_dev_ids_path),
        'test_split_sha256': sha256_file(out_test_ids_path),
        'total_examples': len(final_data),
        'development_examples': len(final_dev_ids),
        'test_examples': len(final_test_ids),
        'total_source_groups': len(dev_groups | test_groups),
        'development_source_groups': len(dev_groups),
        'test_source_groups': len(test_groups),
        'source_group_overlap': len(dev_groups & test_groups),
        'removed_orphan_source_groups': sorted(orphan_groups),
        'removed_orphan_examples': sorted(orphan_ids),
        'excluded_examples': exclusions,
        'taxonomy': {
            ORIGINAL_CATEGORY: 'Independently authored source requests.',
            FORMAT_CATEGORY: 'Semantics-preserving representation or wording transformations.',
            DERIVED_CATEGORY: 'Controlled sub-tasks with changed target records.',
        },
        'partitions': {
            'development': partition_stats(dev_rows),
            'test': partition_stats(test_rows),
            'all': partition_stats(final_data),
        },
        'evaluator': {
            'module': 'nldbwrite.eval.evaluate',
            'target_state_metric': 'target_state_correct',
            'strict_state_metric': 'strict_full_state_correct',
            'strict_mode': 'all_user_tables',
        },
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(manifest, manifest_path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    parser.add_argument('--dev-ids', required=True)
    parser.add_argument('--test-ids', required=True)
    parser.add_argument('--out-data', required=True)
    parser.add_argument('--out-dev-ids', required=True)
    parser.add_argument('--out-test-ids', required=True)
    parser.add_argument('--manifest-out', required=True)
    parser.add_argument('--version', default='augmented900_v2_final')
    parser.add_argument('--corrections')
    args = parser.parse_args()
    manifest = finalize_dataset(
        args.data,
        args.dev_ids,
        args.test_ids,
        args.out_data,
        args.out_dev_ids,
        args.out_test_ids,
        args.manifest_out,
        args.version,
        args.corrections,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
