import argparse
import json
from pathlib import Path

from nldbwrite.common import iter_jsonl, load_json, write_jsonl


def load_run(path):
    maps = {}
    for filename, key in [('evaluation.jsonl', 'evaluation'), ('raw_generations.jsonl', 'raw'), ('parsed_outputs.jsonl', 'parsed'), ('pred_sql.jsonl', 'built')]:
        file = path / filename
        if file.exists():
            for row in iter_jsonl(file):
                maps.setdefault(str(row['sample_id']), {})[key] = row
    return maps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data/processed/nl_db_write_augmented900_v1.json')
    ap.add_argument('--results-root', default='results/server_aug900')
    ap.add_argument('--out', default='paper/case_studies/server_aug900_case_studies.jsonl')
    args = ap.parse_args()
    gold = {str(x['id']): x for x in load_json(args.data)}
    root = Path(args.results_root)
    runs = {path.name: load_run(path) for path in root.iterdir() if path.is_dir() and (path / 'evaluation.jsonl').exists()}
    if not runs:
        raise RuntimeError('No completed runs found.')
    def find(name):
        return next((run for run in runs if name in run), None)
    m0, m2 = find('m0_direct'), find('m2_extract_builder')
    cbr = next((run for run in runs if 'cbr_hybrid_k3' in run and 'repair' not in run), None)
    repair = next((run for run in runs if 'repair' in run), None)
    selected = []
    for sid, sample in gold.items():
        labels = {run: bool((rows.get(sid, {}).get('evaluation') or {}).get('correct')) for run, rows in runs.items()}
        category = None
        if m0 and m2 and not labels.get(m0) and labels.get(m2):
            category = 'm0_fails_m2_correct'
        elif m2 and cbr and not labels.get(m2) and labels.get(cbr):
            category = 'm2_fails_cbr_correct'
        elif m2 and cbr and labels.get(m2) and not labels.get(cbr):
            category = 'cbr_hurts'
        elif repair and labels.get(repair) and (runs[repair].get(sid, {}).get('built') or {}).get('repair_status') == 'accepted':
            category = 'repair_fixes_failure'
        elif repair and (runs[repair].get(sid, {}).get('evaluation') or {}).get('error_type') and int(sample.get('table_count') or 1) > 1 and int(sample.get('row_count') or 1) >= 10:
            category = 'large_relational_unsolved'
        if category and category not in {x['category'] for x in selected}:
            systems = {}
            for run, rows in runs.items():
                item = rows.get(sid, {})
                systems[run] = {
                    'retrieved_cases': (item.get('raw') or {}).get('case_retrieval'),
                    'predicted_json': (item.get('parsed') or {}).get('pred_json'),
                    'built_sql': (item.get('built') or {}).get('pred_sql'),
                    'evaluation': item.get('evaluation'),
                }
            selected.append({
                'category': category, 'sample_id': sid, 'input_text': sample.get('input_text'),
                'gold_records': sample.get('gold_records'), 'gold_sql': sample.get('gold_sql'),
                'systems': systems, 'final_diagnosis': '',
            })
        if len(selected) >= 6:
            break
    write_jsonl(selected, args.out)
    Path(args.out).with_suffix('.md').write_text(
        '\n\n'.join(f"## {x['category']}: {x['sample_id']}\n\nInput: {x['input_text']}\n\nFinal diagnosis: _to be completed after review_" for x in selected),
        encoding='utf-8',
    )
    print(json.dumps({'case_studies': len(selected), 'out': args.out}, indent=2))


if __name__ == '__main__':
    main()
