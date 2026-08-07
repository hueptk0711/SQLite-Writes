import argparse
import ast
import json
import re
from pathlib import Path

from nldbwrite.common import iter_jsonl, write_jsonl
from nldbwrite.facts.merge_facts import merge_fact_payloads

DIRECT_SQL_PREFIXES = ('m0', 'm1')


def clean_fences(text):
    text = re.sub(r'```(?:json|sql)?', '', text, flags=re.I)
    return text.replace('```', '').strip()


def normalize_json_payload(obj):
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list):
        return {'records': obj, 'uncertain_fields': [], 'ignored_text': []}
    raise ValueError(f'Expected JSON object or array, got {type(obj).__name__}')


def extract_json_object(text):
    text = clean_fences(text)
    decoder = json.JSONDecoder()

    try:
        return normalize_json_payload(json.loads(text))
    except Exception:
        pass

    repaired = re.sub(r',\s*([}\]])', r'\1', text)
    if repaired != text:
        try:
            return normalize_json_payload(json.loads(repaired))
        except Exception:
            pass

    try:
        return normalize_json_payload(ast.literal_eval(text))
    except Exception:
        pass

    candidates = []
    for i, ch in enumerate(text):
        if ch not in '[{':
            continue
        try:
            obj, end = decoder.raw_decode(text[i:])
            candidates.append((i, end, normalize_json_payload(obj)))
        except Exception:
            continue

    if candidates:
        # Prefer the first object that looks like the requested schema.
        for _, _, obj in candidates:
            if isinstance(obj, dict) and 'records' in obj:
                return obj
        return candidates[0][2]

    raise ValueError('No valid JSON object found')


def extract_sql_statements(text):
    text = clean_fences(text)
    try:
        import sqlparse
        parts = sqlparse.split(text)
    except Exception:
        parts = [p.strip() for p in text.split(';') if p.strip()]
    return [p.strip().rstrip(';') + ';' for p in parts if p.strip()]


def parse_run(run_dir):
    run_dir = Path(run_dir)
    rows = []
    for item in iter_jsonl(run_dir / 'raw_generations.jsonl'):
        res = {
            'sample_id': item['sample_id'],
            'db_id': item['db_id'],
            'method': item['method'],
            'parse_status': 'success',
            'latency_sec': item.get('latency_sec'),
            'input_chars': item.get('input_chars'),
            'output_chars': item.get('output_chars'),
            'input_tokens': item.get('input_tokens'),
            'output_tokens': item.get('output_tokens'),
            'retrieved_columns': item.get('retrieved_columns', []),
            'linked_columns': item.get('linked_columns', []),
            'matched_values': item.get('matched_values', []),
            'retrieval_diagnostics': item.get('retrieval_diagnostics'),
            'case_retrieval': item.get('case_retrieval'),
        }
        try:
            if item['method'].startswith(DIRECT_SQL_PREFIXES):
                res['pred_sql'] = extract_sql_statements(item['raw_output'])
            elif item['method'] == 'm5_stage1_facts':
                deterministic_facts = item.get('deterministic_facts') or []
                try:
                    obj = extract_json_object(item['raw_output'])
                except Exception as exc:
                    if not deterministic_facts:
                        raise
                    obj = {'facts': [], 'uncertain_facts': [], 'ignored_text': []}
                    res['llm_parse_error'] = str(exc)
                merged = merge_fact_payloads(obj, deterministic_facts)
                res['pred_facts'] = merged
                res['pred_json'] = merged
                res['llm_pred_facts'] = obj
                res['deterministic_facts'] = deterministic_facts
            else:
                res['pred_json'] = extract_json_object(item['raw_output'])
        except Exception as e:
            res.update({'parse_status': 'error', 'parse_error': str(e), 'raw_output': item.get('raw_output')})
        rows.append(res)
    write_jsonl(rows, run_dir / 'parsed_outputs.jsonl')
    print(f'Wrote {run_dir/"parsed_outputs.jsonl"}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-dir', required=True)
    args = ap.parse_args()
    parse_run(args.run_dir)


if __name__ == '__main__':
    main()
