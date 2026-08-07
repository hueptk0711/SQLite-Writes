import argparse
from pathlib import Path
from typing import Any

from nldbwrite.common import iter_jsonl, load_json, write_jsonl
from nldbwrite.facts.evaluate_facts import coerce_facts
from nldbwrite.facts.gold_facts import facts_context_from_facts


def normalize_fact_ids(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for idx, fact in enumerate(facts, start=1):
        item = dict(fact)
        item.setdefault('fact_id', f'f{idx:04d}')
        item.setdefault('record_id', f'r{idx:04d}')
        output.append(item)
    return output


def prepare_context_rows(stage1_run_dir: Path, data_path: Path | None = None) -> list[dict[str, Any]]:
    parsed_path = stage1_run_dir / 'parsed_outputs.jsonl'
    if not parsed_path.exists():
        raise FileNotFoundError(f'Missing parsed outputs: {parsed_path}')
    parsed = {str(row['sample_id']): row for row in iter_jsonl(parsed_path)}
    order = list(parsed)
    sample_meta = {}
    if data_path and data_path.exists():
        data = load_json(data_path)
        order = [str(x['id']) for x in data if str(x['id']) in parsed]
        sample_meta = {str(x['id']): x for x in data}
    rows = []
    for sid in order:
        row = parsed[sid]
        facts = normalize_fact_ids(coerce_facts(row.get('pred_facts') or row.get('pred_json') or {}))
        sample = sample_meta.get(sid, {})
        rows.append({
            'sample_id': sid,
            'db_id': row.get('db_id') or sample.get('db_id'),
            'facts_context': facts_context_from_facts(facts),
            'facts': facts,
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage1-run-dir', required=True)
    ap.add_argument('--data', default='data/processed/nl_db_write_augmented900_v1.json')
    ap.add_argument('--out', default='artifacts/facts/aug900_qwen7b_stage1_fact_context.jsonl')
    args = ap.parse_args()
    rows = prepare_context_rows(Path(args.stage1_run_dir), Path(args.data))
    write_jsonl(rows, args.out)
    print(f'Wrote {args.out}')


if __name__ == '__main__':
    main()

