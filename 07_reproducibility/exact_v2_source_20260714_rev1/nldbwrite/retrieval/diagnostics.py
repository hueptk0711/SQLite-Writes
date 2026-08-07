import argparse
import csv
import json
from pathlib import Path

from nldbwrite.common import load_config, load_json, read_id_file, save_json
from nldbwrite.retrieval.schema_retriever import apply_schema_closure, retrieval_diagnostics, retrieve_schema
from nldbwrite.retrieval.value_matcher import match_values


def mean(rows, field):
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    return sum(values) / len(values) if values else None


def save_flat_yaml(data, path):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in data.items():
        if isinstance(value, bool):
            scalar = 'true' if value else 'false'
        elif value is None:
            scalar = 'null'
        elif isinstance(value, (int, float)):
            scalar = str(value)
        else:
            scalar = json.dumps(str(value), ensure_ascii=False)
        lines.append(f'{key}: {scalar}')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    ap = argparse.ArgumentParser(description='Dev-only retrieval grid diagnostics; does not call the LLM.')
    ap.add_argument('--data', required=True)
    ap.add_argument('--split-ids', required=True)
    ap.add_argument('--profile-dir', required=True)
    ap.add_argument('--index-dir', required=True)
    ap.add_argument('--top-k', nargs='+', type=int, default=[30, 50, 80, 120])
    ap.add_argument('--closure', nargs='+', default=['none', 'pk_fk', 'pk_fk_not_null_unique_parent', 'full_table'])
    ap.add_argument('--value-threshold', nargs='+', type=int, default=[80, 75])
    ap.add_argument('--out-csv', default='artifacts/retrieval/retrieval_ablation_dev.csv')
    ap.add_argument('--out-json', default='artifacts/retrieval/best_m4_config_locked.json')
    ap.add_argument('--base-config')
    ap.add_argument('--locked-config')
    args = ap.parse_args()
    ids = read_id_file(args.split_ids)
    data = [x for x in load_json(args.data) if str(x['id']) in ids]
    profiles = {
        db_id: load_json(Path(args.profile_dir) / f'{db_id}.json')
        for db_id in sorted({str(sample['db_id']) for sample in data})
    }
    # Retrieval and value matching do not depend on the other grid axes. Cache
    # them once per sample/setting so the full dev grid does not repeat the same
    # expensive work for every Cartesian-product row.
    linked_cache = {}
    value_cache = {}
    for sample in data:
        sid = str(sample['id']); db_id = sample['db_id']; profile = profiles[db_id]
        max_retrieved = retrieve_schema(db_id, sample['input_text'], args.index_dir, max(args.top_k))
        for top_k in args.top_k:
            retrieved = max_retrieved[:top_k]
            for closure in args.closure:
                linked_cache[(sid, top_k, closure)] = apply_schema_closure(profile, retrieved, closure)
        broad_matches = match_values(sample['input_text'], profile, min(args.value_threshold), 30)
        for threshold in args.value_threshold:
            value_cache[(sid, threshold)] = [row for row in broad_matches if float(row.get('score', 0)) >= threshold]
    results = []
    for top_k in args.top_k:
        for closure in args.closure:
            for threshold in args.value_threshold:
                diagnostics = []
                for sample in data:
                    sid = str(sample['id']); profile = profiles[sample['db_id']]
                    linked = linked_cache[(sid, top_k, closure)]
                    matched = value_cache[(sid, threshold)]
                    diagnostics.append(retrieval_diagnostics(profile, linked, sample, matched))
                row = {'schema_top_k': top_k, 'closure': closure, 'value_threshold': threshold, 'num_samples': len(diagnostics)}
                for field in ['table_recall','column_recall','required_column_recall','schema_compression_ratio','value_match_recall','selected_tables','selected_columns']:
                    row[field] = mean(diagnostics, field)
                results.append(row)
    out_csv = Path(args.out_csv); out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]) if results else ['schema_top_k'])
        writer.writeheader(); writer.writerows(results)
    eligible = [
        row for row in results
        if (row.get('table_recall') or 0) >= 0.98
        and (row.get('column_recall') or 0) >= 0.95
        and (row.get('required_column_recall') or 0) >= 0.95
        and row.get('closure') in {'pk_fk_not_null_unique_parent', 'full_table'}
    ]
    pool = eligible or results
    best = min(
        pool,
        key=lambda row: (
            row.get('schema_compression_ratio') if row.get('schema_compression_ratio') is not None else 1,
            -(row.get('column_recall') or 0),
            -(row.get('table_recall') or 0),
            -(row.get('required_column_recall') or 0),
            -(row.get('value_match_recall') or 0),
        ),
    ) if pool else {}
    save_json({
        'selection_rule': 'dev only: enforce table>=0.98, column>=0.95, required>=0.95 and mandatory closure; then minimize schema ratio and maximize recall',
        'best': best,
        'all_configs': results,
    }, args.out_json)
    if args.base_config and args.locked_config and best:
        locked = load_config(args.base_config)
        locked.update({'run_name': str(locked.get('run_name', 'm4')) + '_locked_dev', 'schema_top_k': best['schema_top_k'], 'schema_closure': best['closure'], 'value_match_threshold': best['value_threshold']})
        save_flat_yaml(locked, args.locked_config)
    print(json.dumps({'out_csv': str(out_csv), 'out_json': args.out_json, 'locked_config': args.locked_config, 'best': best}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
