import argparse
import csv
import json
import math
from pathlib import Path

from nldbwrite.common import iter_jsonl


def pct(x):
    return '-' if x is None else f'{100 * float(x):.2f}'


def num(x):
    return '-' if x is None else f'{float(x):.2f}'


def collect_runs(root):
    out = []
    for sp in sorted(Path(root).glob('*/summary.json')):
        run = sp.parent
        s = json.load(open(sp, encoding='utf-8'))
        m = {}
        mp = run / 'run_manifest.json'
        if mp.exists():
            m = json.load(open(mp, encoding='utf-8'))
        out.append((run.name, m, s))
    return out


def row_for_run(name, manifest, summary):
    case_cfg = manifest.get('case_retrieval') or {}
    repair_cfg = manifest.get('repair') or {}
    builder_cfg = manifest.get('builder_options') or {}
    return {
        'Run': name,
        'Method': manifest.get('method', name),
        'N': summary.get('num_samples', 0),
        'CaseMode': case_cfg.get('example_mode', '-'),
        'DemoType': case_cfg.get('demo_type', '-'),
        'CBRRetriever': case_cfg.get('cbr_retriever', '-'),
        'CBRK': case_cfg.get('cbr_k', '-'),
        'CBRSetting': case_cfg.get('cbr_setting', '-'),
        'CBRMMR': case_cfg.get('cbr_use_mmr', '-'),
        'CBRMetadataPolicy': case_cfg.get('metadata_policy', '-'),
        'CBRGoldQueryMetadataRate': pct(summary.get('case_retrieval_gold_query_metadata_rate')),
        'Repair': 'yes' if repair_cfg.get('enabled') else 'no',
        'StateAcc': pct(summary.get('state_accuracy')),
        'StrictStateAcc': pct(summary.get('strict_full_state_accuracy')),
        'SideEffectRate': pct(summary.get('side_effect_rate')),
        'SourceGroupMacroAcc': pct(summary.get('source_group_macro_state_accuracy')),
        'SourceGroupCI95Low': pct(summary.get('source_group_macro_ci95_low')),
        'SourceGroupCI95High': pct(summary.get('source_group_macro_ci95_high')),
        'CI95Low': pct(summary.get('state_accuracy_ci95_low')),
        'CI95High': pct(summary.get('state_accuracy_ci95_high')),
        'ExecSuccess': pct(summary.get('execution_success_rate')),
        'JsonValid': pct(summary.get('json_valid_rate')),
        'ParseSuccess': pct(summary.get('parse_success_rate')),
        'BuildSuccess': pct(summary.get('builder_success_rate')),
        'TableAcc': pct(summary.get('table_accuracy')),
        'ColumnF1': pct(summary.get('column_f1')),
        'CellF1': pct(summary.get('cell_f1')),
        'RowExact': pct(summary.get('row_level_exact_match')),
        'RecordCountAcc': pct(summary.get('record_count_accuracy')),
        'SyntaxErr': pct(summary.get('syntax_error_rate')),
        'ConstraintErr': pct(summary.get('constraint_error_rate')),
        'BuilderErr': pct(summary.get('builder_error_rate')),
        'WrongUpsert': pct(summary.get('wrong_upsert_behavior_rate')),
        'AvgLatencySec': num(summary.get('avg_latency_sec')),
        'ThroughputPerHour': num(summary.get('throughput_samples_per_hour')),
        'AvgInputTokens': num(summary.get('avg_input_tokens')),
        'AvgOutputTokens': num(summary.get('avg_output_tokens')),
        'GPUHours': num(summary.get('gpu_hours')),
        'TotalTokens': summary.get('total_tokens', '-'),
        'TokensPerCorrect': num(summary.get('tokens_per_target_correct')),
        'CorrectPerGPUHour': num(summary.get('target_correct_per_gpu_hour')),
        'BuilderTimeSec': num(summary.get('builder_time_sec')),
        'EvaluatorTimeSec': num(summary.get('evaluator_time_sec')),
        'RetrievalTableRecall': pct(summary.get('retrieval_table_recall')),
        'RetrievalColumnRecall': pct(summary.get('retrieval_column_recall')),
        'RequiredColumnRecall': pct(summary.get('retrieval_required_column_recall')),
        'SchemaCompression': pct(summary.get('retrieval_schema_compression_ratio')),
        'CBRLeakagePass': pct(summary.get('case_retrieval_leakage_pass_rate')),
        'CBRAvgCases': num(summary.get('case_retrieval_avg_cases')),
        'RepairTargets': summary.get('repair_targets', '-'),
        'RepairAccepted': summary.get('repair_accepted', '-'),
        'RepairAcceptRate': pct(summary.get('repair_accept_rate')),
        'RepairExecuted': summary.get('repair_executed_candidates', '-'),
        'RepairCorrect': summary.get('repair_correct_candidates', '-'),
        'RepairRolledBack': summary.get('repair_rolled_back', '-'),
        'RepairPolicy': summary.get('repair_rollback_policy', '-'),
        'BuilderFKOrder': builder_cfg.get('fk_ordering', '-'),
        'BuilderRequiredCheck': builder_cfg.get('required_column_check', '-'),
        'BuilderTypeNormalization': builder_cfg.get('type_normalization', '-'),
        'BuilderConflictInference': builder_cfg.get('conflict_target_inference', '-'),
        'BuilderSafetyFilter': builder_cfg.get('safety_filter', '-'),
        'BuilderConflictPolicy': builder_cfg.get('insert_conflict_policy', '-'),
    }


def write_latex(rows, path):
    cols = ['Run', 'Method', 'N', 'StateAcc', 'StrictStateAcc', 'SourceGroupMacroAcc', 'ExecSuccess', 'TableAcc', 'ColumnF1', 'CellF1', 'AvgLatencySec']
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\\begin{tabular}{llrrrrrrrrr}\n\\toprule\n')
        f.write('Run & Method & N & Target & Strict & SG-Macro & Exec. & Table & Col. F1 & Cell F1 & Lat. \\\\ \n')
        f.write('\\midrule\n')
        for r in rows:
            f.write(' & '.join(str(r[c]) for c in cols) + ' \\\\ \n')
        f.write('\\bottomrule\n\\end{tabular}\n')


BREAKDOWN_FIELDS = {
    'by_difficulty_state_accuracy': 'DifficultyOriginal',
    'by_auto_difficulty_state_accuracy': 'AutoDifficulty',
    'by_impact_scope_state_accuracy': 'ImpactScope',
    'by_operation_type_state_accuracy': 'Operation',
    'by_row_count_bucket_state_accuracy': 'RowCountBucket',
    'by_input_type_state_accuracy': 'InputType',
    'by_augmentation_type_state_accuracy': 'AugmentationType',
    'by_example_origin_category_state_accuracy': 'ExampleOriginCategory',
    'by_is_augmented_state_accuracy': 'IsAugmented',
    'by_db_state_accuracy': 'Database',
}


MODEL_FAMILY_RUNS = [
    ('Qwen2.5-Coder-7B', {
        'M0': 'subset300_qwen7b_m0',
        'M2': 'subset300_qwen7b_m2',
        'M5-Facts+CBR': 'subset300_qwen7b_m5_facts_cbr',
    }),
    ('DeepSeek-Coder-6.7B', {
        'M0': 'subset300_deepseek_m0',
        'M2': 'subset300_deepseek_m2',
        'M5-Facts+CBR': 'subset300_deepseek_m5_facts_cbr',
    }),
]


def write_subset_csv(rows, group_type, path):
    selected = [row for row in rows if row['GroupType'] == group_type]
    fields = ['Run', 'Method', 'Group', 'StateAcc']
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row[field] for field in fields} for row in selected)


def write_simple_latex(rows, fields, headers, path):
    alignment = 'l' * max(1, len(fields) - 1) + 'r'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f'\\begin{{tabular}}{{{alignment}}}\n\\toprule\n')
        f.write(' & '.join(headers) + ' \\\\ \n\\midrule\n')
        for row in rows:
            f.write(' & '.join(str(row.get(field, '-')) for field in fields) + ' \\\\ \n')
        f.write('\\bottomrule\n\\end{tabular}\n')


def write_retrieval_table(csv_path, out):
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return
    with open(csv_path, encoding='utf-8') as f:
        source = list(csv.DictReader(f))
    rows = []
    for row in source:
        rows.append({
            'TopK': row.get('schema_top_k', '-'),
            'Closure': row.get('closure', '-'),
            'ValueThreshold': row.get('value_threshold', '-'),
            'TableRecall': pct(row.get('table_recall')),
            'ColumnRecall': pct(row.get('column_recall')),
            'RequiredColumnRecall': pct(row.get('required_column_recall')),
            'SchemaCompression': pct(row.get('schema_compression_ratio')),
        })
    fields = ['TopK','Closure','ValueThreshold','TableRecall','ColumnRecall','RequiredColumnRecall','SchemaCompression']
    with open(out / 'schema_recall_table.csv', 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    write_simple_latex(rows, fields, ['Top K','Closure','Value thr.','Table rec.','Column rec.','Required rec.','Compression'], out / 'schema_recall_table.tex')


def write_cbr_and_repair_tables(rows, out):
    cbr_rows = [
        {key: row.get(key, '-') for key in ['Run','Method','CaseMode','DemoType','CBRRetriever','CBRK','CBRSetting','CBRMMR','N','StateAcc','SourceGroupMacroAcc','ExecSuccess','CBRLeakagePass','AvgLatencySec','AvgInputTokens']}
        for row in rows
        if row.get('CaseMode') not in (None, '-', '') or 'cbr' in str(row.get('Method', '')).lower() or 'cbr' in str(row.get('Run', '')).lower()
    ]
    cbr_fields = ['Run','Method','CaseMode','DemoType','CBRRetriever','CBRK','CBRSetting','CBRMMR','N','StateAcc','SourceGroupMacroAcc','ExecSuccess','CBRLeakagePass','AvgLatencySec','AvgInputTokens']
    with open(out / 'cbr_ablation_results.csv', 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cbr_fields); w.writeheader(); w.writerows(cbr_rows)
    write_simple_latex(cbr_rows, ['Run','CBRRetriever','CBRK','CBRSetting','StateAcc','ExecSuccess','CBRLeakagePass'], ['Run','Retriever','k','Setting','State Acc.','Exec.','Leak pass'], out / 'cbr_ablation_results.tex')

    repair_rows = [
        {key: row.get(key, '-') for key in ['Run','Method','Repair','RepairTargets','RepairAccepted','RepairAcceptRate','RepairExecuted','RepairCorrect','RepairRolledBack','RepairPolicy','N','StateAcc','ExecSuccess','ConstraintErr','AvgLatencySec']}
        for row in rows
        if row.get('Repair') == 'yes'
    ]
    repair_fields = ['Run','Method','Repair','RepairTargets','RepairAccepted','RepairAcceptRate','RepairExecuted','RepairCorrect','RepairRolledBack','RepairPolicy','N','StateAcc','ExecSuccess','ConstraintErr','AvgLatencySec']
    with open(out / 'repair_results.csv', 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=repair_fields); w.writeheader(); w.writerows(repair_rows)
    write_simple_latex(repair_rows, ['Run','RepairTargets','RepairAccepted','RepairAcceptRate','StateAcc','ExecSuccess'], ['Run','Targets','Accepted','Accept rate','State Acc.','Exec.'], out / 'repair_results.tex')

    builder_fields = ['Run','Method','BuilderFKOrder','BuilderRequiredCheck','BuilderTypeNormalization','BuilderConflictInference','BuilderSafetyFilter','BuilderConflictPolicy','StateAcc','BuildSuccess','ExecSuccess','BuilderErr','ConstraintErr','WrongUpsert']
    builder_rows = [
        {key: row.get(key, '-') for key in builder_fields}
        for row in rows
        if (
            str(row.get('Run', '')).endswith('_builder_full')
            or '_builder_no_' in str(row.get('Run', ''))
            or any(str(row.get(key, '')).lower() == 'false' for key in ['BuilderFKOrder','BuilderRequiredCheck','BuilderTypeNormalization','BuilderConflictInference','BuilderSafetyFilter'])
        )
    ]
    with open(out / 'builder_ablation_results.csv', 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=builder_fields); w.writeheader(); w.writerows(builder_rows)
    write_simple_latex(builder_rows, ['Run','StateAcc','BuildSuccess','ExecSuccess','BuilderErr','WrongUpsert'], ['Run','State Acc.','Build','Exec.','Builder err.','Upsert err.'], out / 'builder_ablation_results.tex')


def write_rows_csv(rows, fields, path):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def write_model_family_tables(results_root, out):
    rows = []
    detailed_rows = []
    for model_label, runs in MODEL_FAMILY_RUNS:
        row = {'Model': model_label}
        for method_label, run_name in runs.items():
            run_dir = find_run_dir(results_root, run_name)
            summary = load_json_if_exists(run_dir / 'summary.json') or {}
            row[method_label] = pct_from_summary(summary, 'state_accuracy')
            row[f'{method_label}_N'] = summary.get('num_samples', '-' if not summary else 0)
            row[f'{method_label}_ExecSuccess'] = pct_from_summary(summary, 'execution_success_rate')
            detailed_rows.append({
                'Model': model_label,
                'Method': method_label,
                'Run': run_dir.name,
                'N': summary.get('num_samples', '-' if not summary else 0),
                'StateAcc': pct_from_summary(summary, 'state_accuracy'),
                'ExecSuccess': pct_from_summary(summary, 'execution_success_rate'),
                'CellF1': pct_from_summary(summary, 'cell_f1'),
                'AvgInputTokens': num_from_summary(summary, 'avg_input_tokens'),
                'AvgOutputTokens': num_from_summary(summary, 'avg_output_tokens'),
                'GPUHours': num_from_summary(summary, 'gpu_hours'),
            })
        rows.append(row)

    family_fields = [
        'Model',
        'M0', 'M0_N', 'M0_ExecSuccess',
        'M2', 'M2_N', 'M2_ExecSuccess',
        'M5-Facts+CBR', 'M5-Facts+CBR_N', 'M5-Facts+CBR_ExecSuccess',
    ]
    write_rows_csv(rows, family_fields, out / 'model_family_results.csv')
    write_simple_latex(
        rows,
        ['Model', 'M0', 'M2', 'M5-Facts+CBR'],
        ['Model', 'M0', 'M2', 'M5-Facts+CBR'],
        out / 'model_family_results.tex',
    )
    detail_fields = ['Model','Method','Run','N','StateAcc','ExecSuccess','CellF1','AvgInputTokens','AvgOutputTokens','GPUHours']
    write_rows_csv(detailed_rows, detail_fields, out / 'model_family_detailed_results.csv')


def load_json_if_exists(path):
    path = Path(path)
    if not path.exists():
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def find_run_dir(results_root, run_name):
    root = Path(results_root)
    direct = root / run_name
    if (direct / 'summary.json').exists() or (direct / 'run_manifest.json').exists():
        return direct
    for manifest_path in sorted(root.glob('*/run_manifest.json')):
        try:
            manifest = json.load(open(manifest_path, encoding='utf-8'))
        except Exception:
            continue
        cfg = manifest.get('config') or {}
        candidates = {
            str(manifest.get('run_name') or ''),
            Path(str(cfg.get('output_dir') or '')).name,
            manifest_path.parent.name,
        }
        if run_name in candidates:
            return manifest_path.parent
    return direct


def run_has_summary(results_root, run_name):
    return (find_run_dir(results_root, run_name) / 'summary.json').exists()


def pct_from_summary(summary, key):
    return pct(summary.get(key)) if summary else '-'


def num_from_summary(summary, key):
    return num(summary.get(key)) if summary else '-'


def m5_summary_row(label, run_name, results_root):
    run_dir = find_run_dir(results_root, run_name)
    summary = load_json_if_exists(run_dir / 'summary.json') or {}
    manifest = load_json_if_exists(run_dir / 'run_manifest.json') or {}
    missing = not bool(summary)
    return {
        'System': label,
        'Run': run_dir.name,
        'Method': manifest.get('method', '-'),
        'N': summary.get('num_samples', '-' if missing else 0),
        'StateAcc': pct_from_summary(summary, 'state_accuracy'),
        'ExecSuccess': pct_from_summary(summary, 'execution_success_rate'),
        'JsonValid': pct_from_summary(summary, 'json_valid_rate'),
        'BuildSuccess': pct_from_summary(summary, 'builder_success_rate'),
        'TableAcc': pct_from_summary(summary, 'table_accuracy'),
        'ColumnF1': pct_from_summary(summary, 'column_f1'),
        'CellF1': pct_from_summary(summary, 'cell_f1'),
        'RecordCountAcc': pct_from_summary(summary, 'record_count_accuracy'),
        'AvgInputTokens': num_from_summary(summary, 'avg_input_tokens'),
        'AvgOutputTokens': num_from_summary(summary, 'avg_output_tokens'),
        'GPUHours': num_from_summary(summary, 'gpu_hours'),
        'Missing': 'yes' if missing else 'no',
    }


def write_m5_fact_tables(results_root, out):
    results_root = Path(results_root)

    fact_summary = load_json_if_exists(results_root / 'qwen7b_m5_stage1_facts' / 'fact_eval_summary.json') or {}
    fact_metrics = [
        {'Metric': key, 'Value': pct(fact_summary.get(key))}
        for key in [
            'value_precision',
            'value_recall',
            'value_f1',
            'attribute_value_precision',
            'attribute_value_recall',
            'attribute_value_f1',
            'required_value_recall',
            'conflict_key_fact_recall',
            'row_count_accuracy',
            'hallucinated_fact_rate',
        ]
    ]
    fact_metrics.append({'Metric': 'num_samples', 'Value': fact_summary.get('num_samples', 0)})
    write_rows_csv(fact_metrics, ['Metric', 'Value'], out / 'm5_stage1_fact_metrics.csv')
    write_simple_latex(fact_metrics, ['Metric', 'Value'], ['Metric', 'Value'], out / 'm5_stage1_fact_metrics.tex')

    comparison_specs = [
        ('M2 One-stage Extract+Build', 'qwen7b_m2_builder_full'),
        ('M5-PredFacts', 'qwen7b_m5_fact_first'),
        ('M5-GoldFacts', 'qwen7b_m5_gold_facts'),
        ('M5-NoOriginalText', 'qwen7b_m5_no_original_text'),
        ('M5-Facts+CBR', 'qwen7b_m5_facts_cbr_hybrid_k3'),
    ]
    for label, run_name in [
        ('M5-Facts+CBR SameDB', 'qwen7b_m5_facts_cbr_hybrid_same_db_k3'),
        ('M5-Facts+CBR CrossDB', 'qwen7b_m5_facts_cbr_hybrid_cross_db_k3'),
    ]:
        if run_has_summary(results_root, run_name):
            comparison_specs.append((label, run_name))
    comparison_rows = [m5_summary_row(label, run_name, results_root) for label, run_name in comparison_specs]
    comparison_fields = ['System','Run','Method','N','StateAcc','ExecSuccess','JsonValid','BuildSuccess','TableAcc','ColumnF1','CellF1','RecordCountAcc','AvgInputTokens','AvgOutputTokens','GPUHours']
    write_rows_csv(comparison_rows, comparison_fields, out / 'm5_main_comparison.csv')
    write_simple_latex(comparison_rows, ['System','N','StateAcc','ExecSuccess','CellF1','AvgInputTokens'], ['System','N','State Acc.','Exec.','Cell F1','Input tok.'], out / 'm5_main_comparison.tex')

    oracle_rows = [
        m5_summary_row('M5-PredFacts', 'qwen7b_m5_fact_first', results_root),
        m5_summary_row('M5-GoldFacts', 'qwen7b_m5_gold_facts', results_root),
        m5_summary_row('M5-NoOriginalText', 'qwen7b_m5_no_original_text', results_root),
    ]
    write_rows_csv(oracle_rows, comparison_fields, out / 'm5_oracle_upper_bound.csv')
    write_simple_latex(oracle_rows, ['System','N','StateAcc','ExecSuccess','CellF1'], ['System','N','State Acc.','Exec.','Cell F1'], out / 'm5_oracle_upper_bound.tex')

    fact_eval = results_root / 'qwen7b_m5_stage1_facts' / 'fact_eval_per_sample.csv'
    m5_eval = results_root / 'qwen7b_m5_fact_first' / 'evaluation.jsonl'
    corr_fields = ['FactF1Bucket','N','FinalStateAcc','ExecSuccess']
    corr_rows = []
    if fact_eval.exists() and m5_eval.exists():
        from nldbwrite.facts.fact_state_correlation import correlate
        corr_rows = correlate(fact_eval, m5_eval)
    write_rows_csv(corr_rows, corr_fields, out / 'm5_fact_f1_vs_state_acc.csv')
    write_simple_latex(corr_rows, corr_fields, ['Fact F1 bucket','N','State Acc.','Exec.'], out / 'm5_fact_f1_vs_state_acc.tex')

    fact_eval_path = results_root / 'qwen7b_m5_stage1_facts' / 'fact_eval_per_sample.csv'
    pred_eval_path = find_run_dir(results_root, 'qwen7b_m5_fact_first') / 'evaluation.jsonl'
    cbr_eval_path = find_run_dir(results_root, 'qwen7b_m5_facts_cbr_hybrid_k3') / 'evaluation.jsonl'
    if fact_eval_path.exists() and pred_eval_path.exists():
        from nldbwrite.analysis.fact_first_error_attribution import build_attribution_rows, write_tex
        error_rows_out = build_attribution_rows(fact_eval_path, pred_eval_path, cbr_eval_path if cbr_eval_path.exists() else None)
        write_rows_csv(error_rows_out, ['Error Stage','System','Count','Rate'], out / 'm5_error_attribution.csv')
        write_tex(error_rows_out, out / 'm5_error_attribution.tex')
    else:
        error_rows_out = []
        for label, run_name in comparison_specs[1:]:
            run_dir = find_run_dir(results_root, run_name)
            summary = load_json_if_exists(run_dir / 'summary.json') or {}
            for error_type, count in sorted((summary.get('error_distribution') or {}).items()):
                error_rows_out.append({
                    'System': label,
                    'Run': run_dir.name,
                    'ErrorType': error_type,
                    'Count': count,
                    'Rate': pct(count / int(summary.get('num_samples') or 1)),
                })
        write_rows_csv(error_rows_out, ['System','Run','ErrorType','Count','Rate'], out / 'm5_error_attribution.csv')
        write_simple_latex(error_rows_out, ['System','ErrorType','Count','Rate'], ['System','Error','Count','Rate'], out / 'm5_error_attribution.tex')


def error_rows(runs):
    rows = []
    for name, manifest, summary in runs:
        total = int(summary.get('num_samples') or 0)
        for error_type, count in sorted((summary.get('error_distribution') or {}).items()):
            rows.append({'Run': name, 'Method': manifest.get('method', name), 'ErrorType': error_type, 'Count': count, 'Rate': pct(count / total if total else 0)})
    return rows


def error_stage_rows(results_root):
    rows = []
    for path in sorted(Path(results_root).glob('*/error_analysis.json')):
        manifest_path = path.parent / 'run_manifest.json'
        manifest = json.load(open(manifest_path, encoding='utf-8')) if manifest_path.exists() else {}
        analysis = json.load(open(path, encoding='utf-8'))
        total = sum(int(v) for v in (analysis.get('error_stage_distribution') or {}).values())
        for stage, count in sorted((analysis.get('error_stage_distribution') or {}).items()):
            rows.append({
                'Run': path.parent.name,
                'Method': manifest.get('method', path.parent.name),
                'ErrorStage': stage,
                'Count': count,
                'Rate': pct(count / total if total else 0),
            })
    return rows


def exact_mcnemar_p(b, c):
    n = b + c
    if not n:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(0, min(b, c) + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def significance_rows(results_root):
    evaluations = []
    for path in sorted(Path(results_root).glob('*/evaluation.jsonl')):
        manifest_path = path.parent / 'run_manifest.json'
        manifest = json.load(open(manifest_path, encoding='utf-8')) if manifest_path.exists() else {}
        values = {str(x['sample_id']): bool(x.get('correct')) for x in iter_jsonl(path)}
        evaluations.append((path.parent.name, manifest.get('method', path.parent.name), values))
    rows = []
    for baseline in evaluations:
        for candidate in evaluations:
            if candidate is baseline:
                continue
            common = sorted(set(baseline[2]) & set(candidate[2]))
            if not common:
                continue
            b = sum(baseline[2][sid] and not candidate[2][sid] for sid in common)
            c = sum(not baseline[2][sid] and candidate[2][sid] for sid in common)
            rows.append({'BaselineRun': baseline[0], 'BaselineMethod': baseline[1], 'CandidateRun': candidate[0], 'CandidateMethod': candidate[1], 'NPaired': len(common), 'BaselineOnlyCorrect': b, 'CandidateOnlyCorrect': c, 'McNemarExactP': f'{exact_mcnemar_p(b,c):.6g}'})
    return rows


def breakdown_rows(runs):
    rows = []
    for name, manifest, summary in runs:
        for source, group_type in BREAKDOWN_FIELDS.items():
            for group, value in sorted((summary.get(source) or {}).items()):
                rows.append({
                    'Run': name,
                    'Method': manifest.get('method', name),
                    'GroupType': group_type,
                    'Group': group,
                    'StateAcc': pct(value),
                })
    return rows


def write_breakdown_latex(rows, path):
    cols = ['Run', 'Method', 'GroupType', 'Group', 'StateAcc']
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\\begin{tabular}{llllr}\n\\toprule\n')
        f.write('Run & Method & Group Type & Group & State Acc. \\\\ \n')
        f.write('\\midrule\n')
        for r in rows:
            f.write(' & '.join(str(r[c]) for c in cols) + ' \\\\ \n')
        f.write('\\bottomrule\n\\end{tabular}\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--results-root', default='results/final')
    ap.add_argument('--out-dir', default='paper/tables')
    args = ap.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    runs = collect_runs(args.results_root)
    rows = [row_for_run(name, m, s) for name, m, s in runs]
    fieldnames = list(rows[0].keys()) if rows else ['Run']
    with open(out / 'main_results.csv', 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    write_latex(rows, out / 'main_results.tex')
    with open(out / 'ablation_results.csv', 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames); w.writeheader(); w.writerows(rows)
    write_latex(rows, out / 'ablation_results.tex')
    write_cbr_and_repair_tables(rows, out)
    write_m5_fact_tables(args.results_root, out)
    b_rows = breakdown_rows(runs)
    b_fieldnames = list(b_rows[0].keys()) if b_rows else ['Run', 'Method', 'GroupType', 'Group', 'StateAcc']
    with open(out / 'breakdown_results.csv', 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=b_fieldnames)
        w.writeheader()
        w.writerows(b_rows)
    write_breakdown_latex(b_rows, out / 'breakdown_results.tex')
    for group_type, stem in [
        ('AutoDifficulty', 'per_difficulty_results'),
        ('Operation', 'per_operation_results'),
        ('InputType', 'per_input_type_results'),
        ('AugmentationType', 'augmentation_results'),
        ('ExampleOriginCategory', 'example_origin_results'),
        ('Database', 'per_database_results'),
    ]:
        write_subset_csv(b_rows, group_type, out / f'{stem}.csv')
        selected = [row for row in b_rows if row['GroupType'] == group_type]
        write_simple_latex(selected, ['Run','Method','Group','StateAcc'], ['Run','Method','Group','State Acc.'], out / f'{stem}.tex')
    e_rows = error_rows(runs)
    with open(out / 'error_analysis.csv', 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['Run','Method','ErrorType','Count','Rate']); w.writeheader(); w.writerows(e_rows)
    write_simple_latex(e_rows, ['Run','Method','ErrorType','Count','Rate'], ['Run','Method','Error','Count','Rate'], out / 'error_analysis.tex')
    stage_rows = error_stage_rows(args.results_root)
    with open(out / 'error_stage_analysis.csv', 'w', encoding='utf-8', newline='') as f:
        fields = ['Run','Method','ErrorStage','Count','Rate']; w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(stage_rows)
    write_simple_latex(stage_rows, ['Run','Method','ErrorStage','Count','Rate'], ['Run','Method','Stage','Count','Rate'], out / 'error_stage_analysis.tex')
    cost_rows = [
        {key: row.get(key, '-') for key in ['Run','Method','N','AvgLatencySec','BuilderTimeSec','EvaluatorTimeSec','ThroughputPerHour','AvgInputTokens','AvgOutputTokens','TotalTokens','TokensPerCorrect','GPUHours','CorrectPerGPUHour']}
        for row in rows
    ]
    with open(out / 'runtime_cost.csv', 'w', encoding='utf-8', newline='') as f:
        fields = ['Run','Method','N','AvgLatencySec','BuilderTimeSec','EvaluatorTimeSec','ThroughputPerHour','AvgInputTokens','AvgOutputTokens','TotalTokens','TokensPerCorrect','GPUHours','CorrectPerGPUHour']
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(cost_rows)
    write_simple_latex(cost_rows, ['Run','Method','N','AvgLatencySec','ThroughputPerHour','GPUHours'], ['Run','Method','N','Latency','Samples/h','GPU h'], out / 'runtime_cost.tex')
    s_rows = significance_rows(args.results_root)
    with open(out / 'paired_significance.csv', 'w', encoding='utf-8', newline='') as f:
        fields=['BaselineRun','BaselineMethod','CandidateRun','CandidateMethod','NPaired','BaselineOnlyCorrect','CandidateOnlyCorrect','McNemarExactP']; w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(s_rows)
    write_model_family_tables(args.results_root, out)
    write_retrieval_table('artifacts/retrieval/aug900_retrieval_ablation_dev.csv', out)
    print(f'Wrote paper tables under {out}')


if __name__ == '__main__':
    main()
