import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from nldbwrite.common import iter_jsonl, load_json, save_json
from nldbwrite.eval.evaluate import record_probes


def div(a, b):
    return a / b if b else 0.0


_IDENT = r'''(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|\w+)'''
_INSERT_RE = re.compile(rf'INSERT\s+(?:OR\s+\w+\s+)?INTO\s+({_IDENT})', re.I | re.S)


def unquote_ident(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in ('"', '`') and value[-1] == value[0]:
        return value[1:-1].replace(value[0] * 2, value[0])
    if len(value) >= 2 and value[0] == '[' and value[-1] == ']':
        return value[1:-1]
    return value


def split_csv_identifiers(text: str) -> list[str]:
    out = []
    buf = []
    quote = None
    bracket = False
    for ch in text:
        if bracket:
            buf.append(ch)
            if ch == ']':
                bracket = False
            continue
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', '`', "'"):
            quote = ch
            buf.append(ch)
        elif ch == '[':
            bracket = True
            buf.append(ch)
        elif ch == ',':
            item = ''.join(buf).strip()
            if item:
                out.append(unquote_ident(item))
            buf = []
        else:
            buf.append(ch)
    item = ''.join(buf).strip()
    if item:
        out.append(unquote_ident(item))
    return out


def extract_insert_header(sql: str) -> tuple[str, str] | None:
    match = _INSERT_RE.search(sql)
    if not match:
        return None
    table = unquote_ident(match.group(1))
    start = sql.find('(', match.end())
    if start < 0:
        return None
    quote = None
    bracket = False
    depth = 0
    for i, ch in enumerate(sql[start:], start=start):
        if bracket:
            if ch == ']':
                bracket = False
            continue
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in ('"', '`', "'"):
            quote = ch
        elif ch == '[':
            bracket = True
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return table, sql[start + 1:i]
    return None


def extract_tables_cols_from_sql(sqls):
    tables = set()
    cols = set()
    for sql in sqls:
        header = extract_insert_header(sql)
        if not header:
            continue
        table, column_text = header
        tables.add(table)
        for col in split_csv_identifiers(column_text):
            if col:
                cols.add(f'{table}.{col}')
    return tables, cols


def coerce_value_rows(values):
    if isinstance(values, dict):
        return [values]
    if not isinstance(values, list):
        return []

    pair_map = {}
    dict_rows = []
    for item in values:
        if isinstance(item, dict):
            if 'column' in item and 'value' in item:
                pair_map[str(item['column'])] = item.get('value')
            else:
                dict_rows.append(item)
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            pair_map[str(item[0])] = item[1]

    if pair_map:
        return [pair_map]
    return dict_rows


def add_value_cells(table, values, tables, cols, cells):
    has_cells = False
    for value_row in coerce_value_rows(values):
        if not isinstance(value_row, dict):
            continue
        if table:
            tables.add(table)
        for c, v in value_row.items():
            if not table:
                continue
            cols.add(f'{table}.{c}')
            cells.add((table, c, '' if v is None else str(v)))
            has_cells = True
    return has_cells


def row_key(table, values):
    normalized = [(str(k), '' if v is None else str(v)) for k, v in sorted((values or {}).items())]
    return json.dumps([table, normalized], ensure_ascii=False)


def add_record_rows(table, values, rows):
    count = 0
    for value_row in coerce_value_rows(values):
        if isinstance(value_row, dict) and table:
            rows[row_key(table, value_row)] += 1
            count += 1
    return count


def extract_pred(run_dir):
    out = {}
    parsed = Path(run_dir) / 'parsed_outputs.jsonl'
    predsql = Path(run_dir) / 'pred_sql.jsonl'
    if parsed.exists():
        for item in iter_jsonl(parsed):
            tables = set()
            cols = set()
            cells = set()
            rows = Counter()
            record_count = 0
            has_cells = False
            if item.get('parse_status') == 'success' and 'pred_json' in item:
                for rec in item['pred_json'].get('records', []) or []:
                    if not isinstance(rec, dict):
                        continue
                    t = rec.get('table')
                    if 'values' in rec:
                        has_cells = add_value_cells(t, rec.get('values') or {}, tables, cols, cells) or has_cells
                        record_count += add_record_rows(t, rec.get('values') or {}, rows)
                    else:
                        values = {k: v for k, v in rec.items() if k not in {'table', 'operation', 'operation_type'}}
                        has_cells = add_value_cells(t, values, tables, cols, cells) or has_cells
                        record_count += add_record_rows(t, values, rows)
                for group in item['pred_json'].get('tables', []) or []:
                    if not isinstance(group, dict):
                        continue
                    t = group.get('table')
                    for values in group.get('records', []) or []:
                        has_cells = add_value_cells(t, values or {}, tables, cols, cells) or has_cells
                        record_count += add_record_rows(t, values or {}, rows)
            out[item['sample_id']] = {'tables': tables, 'cols': cols, 'cells': cells, 'has_cells': has_cells, 'rows': rows, 'record_count': record_count}
    if predsql.exists():
        for item in iter_jsonl(predsql):
            if item['sample_id'] not in out or not out[item['sample_id']]['tables']:
                tables, cols = extract_tables_cols_from_sql(item.get('pred_sql', []))
                cells = set(); rows = Counter(); record_count = 0
                records, _ = record_probes(item.get('pred_sql', []))
                for record in records:
                    table = record.get('table'); values = record.get('values') or {}
                    add_value_cells(table, values, tables, cols, cells)
                    record_count += add_record_rows(table, values, rows)
                out[item['sample_id']] = {'tables': tables, 'cols': cols, 'cells': cells, 'has_cells': bool(cells), 'rows': rows, 'record_count': record_count}
    return out


def prf_counts(pred, gold):
    return len(pred & gold), len(pred - gold), len(gold - pred)


def f1(tp, fp, fn):
    p = div(tp, tp + fp)
    r = div(tp, tp + fn)
    return p, r, div(2 * p * r, p + r)


def bootstrap_accuracy_ci(values, iterations=10000, seed=42):
    values = [1 if x else 0 for x in values]
    if not values:
        return None, None
    rng = random.Random(seed)
    n = len(values)
    estimates = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations))
    return estimates[int(0.025 * iterations)], estimates[min(iterations - 1, int(0.975 * iterations))]


def bootstrap_mean_ci(values, iterations=10000, seed=42):
    values = [float(value) for value in values]
    if not values:
        return None, None
    rng = random.Random(seed)
    n = len(values)
    estimates = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations))
    return estimates[int(0.025 * iterations)], estimates[min(iterations - 1, int(0.975 * iterations))]


def metric_correct(row, metric='target_state_correct'):
    if row.get(metric) is not None:
        return bool(row.get(metric))
    return bool(row.get('correct'))


def by_field_accuracy(ev, gold, field, metric='target_state_correct'):
    groups = defaultdict(list)
    for row in ev:
        sample = gold.get(row['sample_id'])
        key = sample.get(field, 'unknown') if sample else 'unknown'
        groups[str(key)].append(row)
    return {k: div(sum(metric_correct(r, metric) for r in rows), len(rows)) for k, rows in sorted(groups.items())}


def source_group_macro_accuracy(ev, gold, metric='target_state_correct'):
    groups = defaultdict(list)
    for row in ev:
        sample = gold.get(row['sample_id'])
        if not sample:
            continue
        source = str(sample.get('source_group_id') or sample['id'])
        groups[source].append(1 if metric_correct(row, metric) else 0)
    scores = [sum(values) / len(values) for values in groups.values() if values]
    return div(sum(scores), len(scores)), len(scores), scores


def summarize(run_dir, data_path=None):
    run_dir = Path(run_dir)
    ev = list(iter_jsonl(run_dir / 'evaluation.jsonl'))
    evaluated_ids = {str(row['sample_id']) for row in ev}
    n = len(ev)
    errors = Counter(r.get('error_type') or 'none' for r in ev)
    target_values = [metric_correct(row, 'target_state_correct') for row in ev]
    strict_rows = [row for row in ev if row.get('strict_full_state_correct') is not None]
    strict_values = [metric_correct(row, 'strict_full_state_correct') for row in strict_rows]
    ci_low, ci_high = bootstrap_accuracy_ci(target_values)
    summary = {
        'num_samples': n,
        'state_accuracy': div(sum(target_values), n),
        'target_state_accuracy': div(sum(target_values), n),
        'strict_full_state_accuracy': div(sum(strict_values), len(strict_values)) if strict_values else None,
        'normalized_full_user_table_state_accuracy': div(sum(strict_values), len(strict_values)) if strict_values else None,
        'strict_state_metric_name': 'normalized_full_user_table_state_accuracy',
        'strict_full_state_num_samples': len(strict_values),
        'side_effect_rate': div(sum(bool(r.get('side_effect')) for r in strict_rows), len(strict_rows)) if strict_rows else None,
        'execution_success_rate': div(sum(r.get('execution_success') for r in ev), n),
        'error_distribution': dict(errors),
        'syntax_error_rate': div(errors.get('syntax_error', 0), n),
        'constraint_error_rate': div(errors.get('constraint_error', 0), n),
        'builder_error_rate': div(errors.get('builder_error', 0), n),
        'wrong_upsert_behavior_rate': div(errors.get('wrong_upsert_behavior', 0), n),
        'schema_error_rate': div(errors.get('schema_error', 0), n),
        'unsafe_sql_rate': div(errors.get('unsafe_sql', 0), n),
        'state_accuracy_ci95_low': ci_low,
        'state_accuracy_ci95_high': ci_high,
        'missing_row_rate': div(errors.get('missing_rows', 0), n),
        'extra_row_rate': div(errors.get('extra_rows', 0), n),
        'wrong_update_rate': div(errors.get('wrong_upsert_behavior', 0), n),
    }
    if strict_values:
        strict_low, strict_high = bootstrap_accuracy_ci(strict_values, seed=43)
        summary['strict_full_state_ci95_low'] = strict_low
        summary['strict_full_state_ci95_high'] = strict_high

    raw = [
        row for row in iter_jsonl(run_dir / 'raw_generations.jsonl')
        if str(row.get('sample_id')) in evaluated_ids
    ] if (run_dir / 'raw_generations.jsonl').exists() else []
    parsed = [
        row for row in iter_jsonl(run_dir / 'parsed_outputs.jsonl')
        if str(row.get('sample_id')) in evaluated_ids
    ] if (run_dir / 'parsed_outputs.jsonl').exists() else []
    built = [
        row for row in iter_jsonl(run_dir / 'pred_sql.jsonl')
        if str(row.get('sample_id')) in evaluated_ids
    ] if (run_dir / 'pred_sql.jsonl').exists() else []
    if raw:
        total_latency = sum(float(x.get('latency_sec') or 0) for x in raw)
        summary.update({
            'avg_latency_sec': div(total_latency, len(raw)),
            'total_latency_sec': total_latency,
            'throughput_samples_per_hour': div(len(raw) * 3600, total_latency),
            'avg_input_chars': div(sum(int(x.get('input_chars') or 0) for x in raw), len(raw)),
            'avg_output_chars': div(sum(int(x.get('output_chars') or 0) for x in raw), len(raw)),
        })
        input_token_rows = [int(x['input_tokens']) for x in raw if x.get('input_tokens') is not None]
        output_token_rows = [int(x['output_tokens']) for x in raw if x.get('output_tokens') is not None]
        if input_token_rows:
            summary['avg_input_tokens'] = div(sum(input_token_rows), len(input_token_rows))
            summary['total_input_tokens'] = sum(input_token_rows)
        if output_token_rows:
            summary['avg_output_tokens'] = div(sum(output_token_rows), len(output_token_rows))
            summary['total_output_tokens'] = sum(output_token_rows)
        if input_token_rows or output_token_rows:
            total_tokens = sum(input_token_rows) + sum(output_token_rows)
            correct_count = sum(target_values)
            summary['total_tokens'] = total_tokens
            summary['tokens_per_target_correct'] = div(total_tokens, correct_count)
        retrieval_rows = [x.get('retrieval_diagnostics') for x in raw if isinstance(x.get('retrieval_diagnostics'), dict)]
        if retrieval_rows:
            for field in ['table_recall','column_recall','required_column_recall','schema_compression_ratio','value_match_recall','selected_tables','selected_columns']:
                values = [float(x[field]) for x in retrieval_rows if x.get(field) is not None]
                if values:
                    summary[f'retrieval_{field}'] = div(sum(values), len(values))
        case_rows = [x.get('case_retrieval') for x in raw if isinstance(x.get('case_retrieval'), dict)]
        if case_rows:
            summary['case_retrieval_leakage_pass_rate'] = div(sum(bool(x.get('leakage_check_passed')) for x in case_rows), len(case_rows))
            summary['case_retrieval_avg_cases'] = div(sum(len(x.get('retrieved_case_ids') or []) for x in case_rows), len(case_rows))
            summary['case_retrieval_gold_query_metadata_rate'] = div(
                sum(bool(x.get('gold_query_metadata_used')) for x in case_rows),
                len(case_rows),
            )
            summary['case_retrieval_metadata_policies'] = dict(Counter(
                str(x.get('metadata_policy') or 'legacy_unspecified') for x in case_rows
            ))
            same_db = cross_db = 0
            for row in case_rows:
                labels = row.get('same_db_or_cross_db') or []
                same_db += sum(1 for label in labels if label == 'same_db')
                cross_db += sum(1 for label in labels if label == 'cross_db')
            total_case_links = same_db + cross_db
            summary['case_retrieval_same_db_rate'] = div(same_db, total_case_links)
            summary['case_retrieval_cross_db_rate'] = div(cross_db, total_case_links)
    if parsed:
        summary['parse_success_rate'] = div(sum(x.get('parse_status') == 'success' for x in parsed), len(parsed))
        json_rows = [x for x in parsed if not str(x.get('method', '')).startswith(('m0', 'm1'))]
        if json_rows:
            summary['json_valid_rate'] = div(sum(x.get('parse_status') == 'success' and 'pred_json' in x for x in json_rows), len(json_rows))
    if built:
        summary['builder_success_rate'] = div(sum(x.get('builder_status') in {'success', 'direct_sql'} for x in built), len(built))
        summary['builder_partial_rate'] = div(sum(x.get('builder_status') == 'partial' for x in built), len(built))

    manifest_path = run_dir / 'run_manifest.json'
    if manifest_path.exists():
        manifest = load_json(manifest_path)
        summary['gpu_hours'] = manifest.get('gpu_hours')
        summary['builder_time_sec'] = manifest.get('builder_time_sec')
        summary['evaluator_time_sec'] = manifest.get('evaluator_time_sec')
        gpu_hours = float(manifest.get('gpu_hours') or 0)
        if gpu_hours:
            summary['target_correct_per_gpu_hour'] = div(sum(target_values), gpu_hours)
            summary['strict_correct_per_gpu_hour'] = div(sum(strict_values), gpu_hours) if strict_values else None
        if isinstance(manifest.get('repair'), dict):
            repair = manifest['repair']
            summary['repair_targets'] = repair.get('targets')
            summary['repair_accepted'] = repair.get('accepted')
            summary['repair_accept_rate'] = div(int(repair.get('accepted') or 0), int(repair.get('targets') or 0))
            summary['repair_latency_sec'] = repair.get('total_latency_sec')
            summary['repair_executed_candidates'] = repair.get('executed_candidates')
            summary['repair_correct_candidates'] = repair.get('correct_candidates')
            summary['repair_rolled_back'] = repair.get('rolled_back')
            summary['repair_rollback_policy'] = repair.get('rollback_policy')

    if data_path:
        gold = {str(x['id']): x for x in load_json(data_path)}
        pred = extract_pred(run_dir)
        table_correct = count = col_tp = col_fp = col_fn = cell_tp = cell_fp = cell_fn = cell_count = row_exact = record_count_correct = 0
        for eval_row in ev:
            sid = eval_row['sample_id']
            if sid not in gold:
                continue
            item = pred.get(sid, {'tables': set(), 'cols': set(), 'cells': set(), 'has_cells': False, 'rows': Counter(), 'record_count': 0})
            g = gold[sid]
            gt = set(g.get('gold_tables', []))
            gc = set(g.get('gold_columns', []))
            gcell = set()
            grows = Counter()
            gold_record_count = 0
            for rec in g.get('gold_records', []) or []:
                t = rec.get('table')
                for values in coerce_value_rows(rec.get('values') or {}):
                    grows[row_key(t, values)] += 1
                    gold_record_count += 1
                    for c, v in values.items():
                        gcell.add((t, c, '' if v is None else str(v)))
            table_correct += int(item['tables'] == gt)
            tp, fp, fn = prf_counts(item['cols'], gc)
            col_tp += tp
            col_fp += fp
            col_fn += fn
            if gcell:
                tp, fp, fn = prf_counts(item['cells'], gcell)
                cell_tp += tp
                cell_fp += fp
                cell_fn += fn
                cell_count += 1
            row_exact += int(item.get('rows', Counter()) == grows)
            record_count_correct += int(int(item.get('record_count') or 0) == gold_record_count)
            count += 1
        cp, cr, cf = f1(col_tp, col_fp, col_fn)
        summary.update({
            'table_accuracy': div(table_correct, count),
            'column_precision': cp,
            'column_recall': cr,
            'column_f1': cf,
            'row_level_exact_match': div(row_exact, count),
            'record_count_accuracy': div(record_count_correct, count),
        })
        if cell_count:
            vp, vr, vf = f1(cell_tp, cell_fp, cell_fn)
            summary.update({'cell_precision': vp, 'cell_recall': vr, 'cell_f1': vf, 'cell_metric_samples': cell_count})
        else:
            summary.update({'cell_precision': None, 'cell_recall': None, 'cell_f1': None, 'cell_metric_samples': 0})
        summary['by_difficulty_state_accuracy'] = by_field_accuracy(ev, gold, 'difficulty')
        summary['by_auto_difficulty_state_accuracy'] = by_field_accuracy(ev, gold, 'auto_difficulty')
        summary['by_impact_scope_state_accuracy'] = by_field_accuracy(ev, gold, 'impact_scope')
        summary['by_operation_type_state_accuracy'] = by_field_accuracy(ev, gold, 'operation_type')
        summary['by_input_type_state_accuracy'] = by_field_accuracy(ev, gold, 'input_type')
        summary['by_row_count_bucket_state_accuracy'] = by_field_accuracy(ev, gold, 'row_count_bucket')
        summary['by_db_state_accuracy'] = by_field_accuracy(ev, gold, 'db_id')
        summary['by_augmentation_type_state_accuracy'] = by_field_accuracy(ev, gold, 'augmentation_type')
        summary['by_example_origin_category_state_accuracy'] = by_field_accuracy(ev, gold, 'example_origin_category')
        summary['by_is_augmented_state_accuracy'] = by_field_accuracy(ev, gold, 'is_augmented')
        group_macro, group_count, group_scores = source_group_macro_accuracy(ev, gold)
        summary['source_group_macro_state_accuracy'] = group_macro
        summary['source_group_count'] = group_count
        group_low, group_high = bootstrap_mean_ci(group_scores, seed=2027)
        summary['source_group_macro_ci95_low'] = group_low
        summary['source_group_macro_ci95_high'] = group_high

    save_json(summary, run_dir / 'summary.json')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-dir', required=True)
    ap.add_argument('--data')
    args = ap.parse_args()
    summarize(args.run_dir, args.data)


if __name__ == '__main__':
    main()
