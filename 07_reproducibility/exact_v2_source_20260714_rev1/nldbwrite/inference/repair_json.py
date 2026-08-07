import argparse
import json
import platform
import time
from pathlib import Path
from typing import Any

from nldbwrite.common import iter_jsonl, load_config, load_json, save_json, sha256_text, write_jsonl
from nldbwrite.eval.evaluate import evaluate_candidate_sql
from nldbwrite.inference.parse_outputs import extract_json_object
from nldbwrite.prompts.build_prompt import full_schema_context, load_template
from nldbwrite.sql.build_sql import build_sql_from_json, builder_options_from_config


DEFAULT_REPAIR_ERROR_TYPES = {
    'json_parse_error',
    'builder_error',
    'syntax_error',
    'constraint_error',
    'execution_error',
    'schema_error',
    'unsafe_sql',
}


def load_jsonl_map(path: str | Path, key: str = 'sample_id') -> dict[str, dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return {}
    return {str(item[key]): item for item in iter_jsonl(p)}


def should_repair(eval_row: dict[str, Any], cfg: dict[str, Any]) -> bool:
    configured = cfg.get('repair_error_types')
    if configured:
        allowed = {str(x).strip() for x in configured} if isinstance(configured, list) else {x.strip() for x in str(configured).split(',') if x.strip()}
    else:
        allowed = DEFAULT_REPAIR_ERROR_TYPES
    error_type = str(eval_row.get('error_type') or '')
    builder_status = str(eval_row.get('builder_status') or '')
    builder_errors = eval_row.get('builder_errors') or []
    dropped_columns = eval_row.get('dropped_columns') or eval_row.get('builder_dropped_columns') or []
    missing_required = eval_row.get('missing_required_fields') or []
    if builder_status == 'partial' or builder_errors or dropped_columns or missing_required:
        return True
    if error_type in allowed:
        return True
    if bool(cfg.get('repair_execution_failures', True)) and not bool(eval_row.get('execution_success')):
        return True
    if bool(cfg.get('repair_state_mismatches', False)) and eval_row.get('error_group') == 'wrong_state':
        return True
    return False


def method_accepts_json_repair(method: str) -> bool:
    return not str(method or '').startswith(('m0', 'm1'))


def mock_repair(sample: dict[str, Any]) -> str:
    records = []
    for rec in sample.get('gold_records', []) or []:
        item = dict(rec)
        item.setdefault('operation', sample.get('operation_type', 'insert'))
        records.append(item)
    return json.dumps({'records': records, 'uncertain_fields': [], 'ignored_text': []}, ensure_ascii=False, indent=2)


def render_repair_prompt(template: str, schema_context: str, sample: dict[str, Any], raw: dict[str, Any] | None, parsed: dict[str, Any] | None, built: dict[str, Any] | None, eval_row: dict[str, Any]) -> str:
    invalid_payload = ''
    if parsed and parsed.get('pred_json') is not None:
        invalid_payload = json.dumps(parsed.get('pred_json'), ensure_ascii=False, indent=2)
    elif raw:
        invalid_payload = str(raw.get('raw_output') or '')
    builder_errors = built.get('builder_errors') if built else []
    return template.format(
        schema_context=schema_context,
        input_text=sample.get('input_text', ''),
        invalid_json=invalid_payload,
        builder_errors=json.dumps(builder_errors or [], ensure_ascii=False, indent=2),
        execution_error=eval_row.get('error_message') or eval_row.get('error_type') or '',
        error_type=eval_row.get('error_type') or '',
    )


def backup_once(path: Path) -> None:
    backup = path.with_name(path.stem + '.before_repair' + path.suffix)
    if path.exists() and not backup.exists():
        backup.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')


def repair_run(config_path: str | Path, run_dir: str | Path, profile_dir: str | Path, data_path: str | Path) -> dict[str, Any]:
    cfg = load_config(config_path)
    run_dir = Path(run_dir)
    profile_dir = Path(profile_dir)
    data = {str(x['id']): x for x in load_json(data_path)}
    raw_map = load_jsonl_map(run_dir / 'raw_generations.jsonl')
    parsed_map = load_jsonl_map(run_dir / 'parsed_outputs.jsonl')
    built_map = load_jsonl_map(run_dir / 'pred_sql.jsonl')
    eval_rows = list(iter_jsonl(run_dir / 'evaluation.jsonl')) if (run_dir / 'evaluation.jsonl').exists() else []
    repair_targets = [row for row in eval_rows if should_repair(row, cfg) and method_accepts_json_repair((built_map.get(str(row.get('sample_id'))) or {}).get('method') or cfg.get('method'))]

    if not repair_targets:
        manifest_path = run_dir / 'run_manifest.json'
        manifest = load_json(manifest_path) if manifest_path.exists() else {}
        manifest['repair'] = {'enabled': True, 'targets': 0, 'accepted': 0, 'end_time': time.strftime('%Y-%m-%d %H:%M:%S')}
        save_json(manifest, manifest_path)
        return manifest['repair']

    model_cfg = load_config(cfg.get('repair_model_config') or cfg['model_config'])
    backend = model_cfg.get('backend', 'transformers')
    tokenizer = model = None
    if backend == 'transformers':
        from nldbwrite.inference.load_model import generate_text, load_local_model
        tokenizer, model = load_local_model(
            model_cfg['model_name_or_path'],
            bool(model_cfg.get('load_in_4bit', True)),
            model_cfg.get('torch_dtype', 'float16'),
            model_cfg.get('device_map', 'auto'),
            model_cfg.get('revision', 'main'),
        )
    elif backend != 'mock':
        raise ValueError(f'Unsupported backend: {backend}')

    template = load_template(cfg.get('repair_prompt_file', 'configs/prompts/m2_json_repair.txt'))
    generations = []
    candidates = []
    accepted = 0
    accept_partial = bool(cfg.get('repair_accept_partial', False))
    rollback = bool(cfg.get('repair_rollback', True))
    rollback_policy = str(cfg.get('repair_rollback_policy', 'execution')).lower()
    db_root = cfg.get('db_root', 'data/bird_databases')
    strict_state_eval = bool(cfg.get('repair_strict_state_eval', False))
    builder_options = builder_options_from_config(cfg)
    total_latency = 0.0
    executed_candidates = 0
    correct_candidates = 0
    rolled_back = 0
    repair_max_new_tokens = int(cfg.get('repair_max_new_tokens', model_cfg.get('max_new_tokens', 1024)))
    repair_temperature = float(cfg.get('repair_temperature', model_cfg.get('temperature', 0.0)))
    repair_top_p = float(cfg.get('repair_top_p', model_cfg.get('top_p', 1.0)))
    repair_seed_base = int(cfg.get('repair_generation_seed', model_cfg.get('seed', 42)))
    for eval_row in repair_targets:
        sid = str(eval_row['sample_id'])
        repair_seed = repair_seed_base + int(sha256_text(sid)[:8], 16)
        sample = data[sid]
        profile = load_json(profile_dir / f"{sample['db_id']}.json")
        schema_context = full_schema_context(
            profile,
            int(cfg.get('repair_max_columns_in_prompt', cfg.get('max_columns_in_prompt', 160))),
            int(cfg.get('max_sample_values_per_column', 3)),
            int(cfg.get('max_sample_value_chars', 80)),
        )
        prompt = render_repair_prompt(template, schema_context, sample, raw_map.get(sid), parsed_map.get(sid), built_map.get(sid), eval_row)
        start = time.time()
        raw_output = mock_repair(sample) if backend == 'mock' else generate_text(
            tokenizer,
            model,
            prompt,
            max_new_tokens=repair_max_new_tokens,
            temperature=repair_temperature,
            top_p=repair_top_p,
            seed=repair_seed,
        )
        latency = time.time() - start
        total_latency += latency
        generation = {
            'sample_id': sid,
            'db_id': sample['db_id'],
            'method': cfg.get('method'),
            'repair_prompt': prompt,
            'repair_raw_output': raw_output,
            'repair_seed': repair_seed,
            'repair_max_new_tokens': repair_max_new_tokens,
            'repair_temperature': repair_temperature,
            'repair_top_p': repair_top_p,
            'repair_latency_sec': latency,
            'original_error_type': eval_row.get('error_type'),
            'original_error_message': eval_row.get('error_message'),
        }
        try:
            pred_json = extract_json_object(raw_output)
            status, sqls, errors, metadata = build_sql_from_json(pred_json, profile, builder_options)
            parse_status = 'success'
            parse_error = None
        except Exception as exc:
            pred_json = None
            status = 'parse_error'
            sqls = []
            errors = [str(exc)]
            metadata = []
            parse_status = 'error'
            parse_error = str(exc)
        build_ok = status == 'success' or (accept_partial and status == 'partial')
        candidate_eval = None
        if build_ok:
            candidate_eval = evaluate_candidate_sql(
                sample,
                sqls,
                db_root,
                builder_status=status,
                parse_status=parse_status,
                strict_all_tables=strict_state_eval,
            )
            executed_candidates += int(bool(candidate_eval.get('execution_success')))
            correct_candidates += int(bool(candidate_eval.get('correct')))
        if not rollback:
            replace = build_ok
        elif rollback_policy == 'build':
            replace = build_ok
        elif rollback_policy in {'oracle', 'oracle_state', 'state'}:
            # Analysis-only upper bound. Do not use this policy for the main
            # paper method because it selects with gold post-state correctness.
            replace = bool(build_ok and candidate_eval and candidate_eval.get('correct'))
        else:
            # Deployable default: accept only when the repaired candidate builds
            # and executes successfully on an isolated database copy.
            replace = bool(build_ok and candidate_eval and candidate_eval.get('execution_success'))
        if build_ok and not replace:
            rolled_back += 1
        generation['accepted'] = bool(replace)
        generations.append(generation)
        candidate = {
            'sample_id': sid,
            'db_id': sample['db_id'],
            'method': cfg.get('method'),
            'parse_status': parse_status,
            'parse_error': parse_error,
            'pred_json': pred_json,
            'builder_status': status,
            'pred_sql': sqls,
            'builder_errors': errors,
            'sql_metadata': metadata,
            'repair_accepted': bool(replace),
            'repair_rollback': rollback,
            'repair_rollback_policy': rollback_policy,
            'candidate_evaluation': candidate_eval,
            'original_error_type': eval_row.get('error_type'),
        }
        candidates.append(candidate)
        if replace:
            parsed_map[sid] = {
                'sample_id': sid,
                'db_id': sample['db_id'],
                'method': cfg.get('method'),
                'parse_status': parse_status,
                'pred_json': pred_json,
                'repair_status': 'accepted',
                'repair_original_error_type': eval_row.get('error_type'),
            }
            if parse_error:
                parsed_map[sid]['parse_error'] = parse_error
            built_map[sid] = {
                'sample_id': sid,
                'db_id': sample['db_id'],
                'method': cfg.get('method'),
                'builder_status': status,
                'pred_sql': sqls,
                'builder_errors': errors,
                'sql_metadata': metadata,
                'parse_status': parse_status,
                'repair_status': 'accepted',
                'repair_original_error_type': eval_row.get('error_type'),
            }
            accepted += 1

    write_jsonl(generations, run_dir / 'repair_generations.jsonl')
    write_jsonl(candidates, run_dir / 'repair_candidates.jsonl')
    backup_once(run_dir / 'evaluation.jsonl')
    backup_once(run_dir / 'parsed_outputs.jsonl')
    backup_once(run_dir / 'pred_sql.jsonl')
    order = [str(item['sample_id']) for item in iter_jsonl(run_dir / 'raw_generations.jsonl')]
    write_jsonl([parsed_map[sid] for sid in order if sid in parsed_map], run_dir / 'parsed_outputs.jsonl')
    write_jsonl([built_map[sid] for sid in order if sid in built_map], run_dir / 'pred_sql.jsonl')

    manifest_path = run_dir / 'run_manifest.json'
    manifest = load_json(manifest_path) if manifest_path.exists() else {}
    repair_summary = {
        'enabled': True,
        'targets': len(repair_targets),
        'accepted': accepted,
        'rollback': rollback,
        'accept_partial': accept_partial,
        'rollback_policy': rollback_policy,
        'executed_candidates': executed_candidates,
        'correct_candidates': correct_candidates,
        'rolled_back': rolled_back,
        'total_latency_sec': total_latency,
        'platform': platform.platform(),
        'end_time': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    manifest['repair'] = repair_summary
    save_json(manifest, manifest_path)
    return repair_summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True)
    ap.add_argument('--run-dir', required=True)
    ap.add_argument('--profile-dir', required=True)
    ap.add_argument('--data', required=True)
    args = ap.parse_args()
    summary = repair_run(args.config, args.run_dir, args.profile_dir, args.data)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
