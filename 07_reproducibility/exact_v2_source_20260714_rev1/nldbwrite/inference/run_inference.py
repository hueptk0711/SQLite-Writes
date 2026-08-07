import argparse, json, os, platform, random, shutil, time, zipfile
from pathlib import Path
try:
    from tqdm import tqdm
except ModuleNotFoundError:
    def tqdm(iterable, **_kwargs):
        return iterable

from nldbwrite.common import code_version, ensure_dir, iter_jsonl, load_config, load_json, read_id_file, save_json, sha256_file, sha256_text
from nldbwrite.facts.deterministic_extractors import extract_deterministic_facts
from nldbwrite.facts.schema_hints import schema_light_hints_for_profile
from nldbwrite.prompts.build_prompt import compact_schema_context, full_schema_context, load_template, render_prompt
from nldbwrite.retrieval.case_retriever import CaseBank, format_cases
from nldbwrite.retrieval.schema_retriever import apply_schema_closure, retrieval_diagnostics, retrieve_schema
from nldbwrite.retrieval.value_matcher import match_values

def load_done_ids(path): return {str(x['sample_id']) for x in iter_jsonl(path)} if Path(path).exists() else set()


DIRECT_SQL_PREFIXES = ('m0', 'm1')
SCHEMA_RETRIEVAL_METHODS = {'m3_schema_grounded', 'm4_schema_value_grounded'}
VALUE_GROUNDED_METHODS = {'m4_schema_value_grounded', 'm4_value_grounded_only'}
CBR_METHODS = {'m2_cbr_text', 'm2_cbr_hybrid', 'm2_cbr_hybrid_repair', 'm2_write_semantic_ir_cbr', 'm5_facts_cbr'}
STATIC_EXAMPLE_METHODS = {'m1_direct_sql_fewshot', 'm2_extract_json_fewshot'}
FACT_STAGE_METHODS = {'m5_stage1_facts'}
FACT_CONTEXT_METHODS = {'m5_fact_first', 'm5_gold_facts', 'm5_no_original_text', 'm5_facts_cbr'}


def is_direct_sql_method(method):
    return str(method or '').startswith(DIRECT_SQL_PREFIXES)


def write_checkpoint_zip(run_dir, checkpoint_zip):
    if not checkpoint_zip:
        return
    run_dir = Path(run_dir)
    checkpoint_zip = Path(checkpoint_zip)
    checkpoint_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp = checkpoint_zip.with_suffix(checkpoint_zip.suffix + '.tmp')
    with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
        for path in run_dir.rglob('*'):
            if path.is_file():
                zf.write(path, (Path(run_dir.name) / path.relative_to(run_dir)).as_posix())
    try:
        os.replace(tmp, checkpoint_zip)
    except PermissionError:
        if checkpoint_zip.exists():
            checkpoint_zip.unlink()
        shutil.move(str(tmp), str(checkpoint_zip))


def mock_generate(sample, method):
    if is_direct_sql_method(method):
        return '\n'.join(sample.get('gold_sql', []))
    if method == 'm5_stage1_facts':
        facts = []
        for rec_idx, rec in enumerate(sample.get('gold_records', []), start=1):
            for col, val in (rec.get('values') or {}).items():
                facts.append({
                    'record_id': f'r{rec_idx}',
                    'attribute': str(col),
                    'value': val,
                    'value_type': 'string',
                    'evidence': '',
                    'confidence': 1.0,
                })
        return json.dumps({'facts': facts, 'uncertain_facts': [], 'ignored_text': []}, ensure_ascii=False, indent=2)
    records=[]
    for rec in sample.get('gold_records', []):
        item=dict(rec)
        item.setdefault('operation', sample.get('operation_type', 'insert'))
        records.append(item)
    return json.dumps({'records':records,'uncertain_fields':[],'ignored_text':[]}, ensure_ascii=False, indent=2)

def parse_optional_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except Exception:
            pass
        return [x.strip() for x in text.split(',') if x.strip()]
    return [str(value)]


def case_weights_from_config(cfg):
    return {
        'text': float(cfg.get('cbr_weight_text', 1.0)),
        'operation': float(cfg.get('cbr_weight_operation', 0.15)),
        'schema': float(cfg.get('cbr_weight_schema', 0.20)),
        'difficulty': float(cfg.get('cbr_weight_difficulty', 0.05)),
        'input_type': float(cfg.get('cbr_weight_input_type', 0.05)),
    }


def should_use_case_examples(method, cfg):
    if str(cfg.get('example_mode', '')).lower() in {'static', 'cbr'}:
        return True
    return method in STATIC_EXAMPLE_METHODS or method in CBR_METHODS


def load_fact_context_map(path):
    if not path:
        return {}
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f'Fact context path does not exist: {path}')
    return {str(row['sample_id']): row for row in iter_jsonl(path)}


def build_prompt(sample, cfg, template, case_bank=None, fact_context_map=None):
    profile=load_json(Path(cfg['profile_dir'])/f"{sample['db_id']}.json"); method=cfg['method']; retrieved=[]; linked=[]; matched=[]; cbr_log=None; examples=''
    need_schema_retrieval = method in SCHEMA_RETRIEVAL_METHODS or str(cfg.get('cbr_retriever', '')).lower().startswith('hybrid')
    if need_schema_retrieval:
        retrieved=retrieve_schema(sample['db_id'], sample['input_text'], cfg.get('schema_index_dir','artifacts/indexes/schema_bm25'), int(cfg.get('schema_top_k',30)))
        linked=apply_schema_closure(profile, retrieved, cfg.get('schema_closure', 'pk_fk_not_null_unique_parent'))
    if method in VALUE_GROUNDED_METHODS:
        matched=match_values(sample['input_text'], profile, int(cfg.get('value_match_threshold',80)), int(cfg.get('max_value_matches',30)))

    if case_bank is not None and should_use_case_examples(method, cfg):
        example_mode = str(cfg.get('example_mode') or ('cbr' if method in CBR_METHODS else 'static')).lower()
        k = int(cfg.get('cbr_k') or cfg.get('num_examples') or 3)
        setting = str(cfg.get('cbr_setting', 'mixed'))
        if example_mode == 'cbr':
            cases, cbr_log = case_bank.retrieve(
                sample,
                k=k,
                retriever=str(cfg.get('cbr_retriever', 'bm25')),
                setting=setting,
                linked_columns=linked,
                weights=case_weights_from_config(cfg),
                use_mmr=bool(cfg.get('cbr_use_mmr', False)),
                mmr_lambda=float(cfg.get('cbr_mmr_lambda', 0.75)),
                dense_model_name=str(cfg.get('cbr_dense_model', 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')),
                dense_batch_size=int(cfg.get('cbr_dense_batch_size', 64)),
                metadata_policy=cfg.get('cbr_metadata_policy'),
                unique_source_groups=bool(cfg.get('cbr_unique_source_groups', True)),
                max_demo_source_records=int(cfg.get('max_demo_source_records', 0)),
                max_demo_sql_chars=int(cfg.get('max_demo_sql_chars', 0)),
            )
        else:
            cases, cbr_log = case_bank.static_cases(
                sample,
                k=k,
                setting=setting,
                preferred_ids=parse_optional_list(cfg.get('static_example_ids')),
                selection=str(cfg.get('static_example_selection', 'curated')),
                seed=int(cfg.get('static_example_seed', 2026)),
            )
        if cfg.get('example_order_seed') is not None:
            order_rng = random.Random(f"{cfg['example_order_seed']}:{sample['id']}")
            order_rng.shuffle(cases)
            if isinstance(cbr_log, dict):
                cbr_log['prompt_case_ids'] = [case['case_id'] for case in cases]
                cbr_log['example_order_seed'] = int(cfg['example_order_seed'])
        examples = format_cases(
            cases,
            demo_type=str(cfg.get('demo_type') or ('sql' if is_direct_sql_method(method) else 'json')),
            max_input_chars=int(cfg.get('max_case_input_chars', 700)),
            max_records=int(cfg.get('max_case_records', 5)),
            max_sql_chars=int(cfg.get('max_case_sql_chars', 900)),
        )
        if isinstance(cbr_log, dict):
            max_records = int(cfg.get('max_case_records', 5))
            cbr_log['num_examples'] = len(cases)
            cbr_log['num_demo_records_shown'] = sum(
                len(case.get('gold_records') or []) if max_records <= 0 else min(max_records, len(case.get('gold_records') or []))
                for case in cases
            )
            cbr_log['example_chars'] = len(examples)

    fact_context = ''
    if method in FACT_CONTEXT_METHODS:
        fact_row = (fact_context_map or {}).get(str(sample['id']), {})
        fact_context = fact_row.get('facts_context') or ''

    full_schema_methods = {'m0_direct_sql','m1_direct_sql_fewshot','m1_direct_sql_cbr','m2_extract_json','m2_extract_json_fewshot','m2_cbr_text','m2_cbr_hybrid','m2_cbr_hybrid_repair','m2_write_semantic_ir','m2_write_semantic_ir_cbr','m4_value_grounded_only','m5_fact_first','m5_gold_facts','m5_no_original_text','m5_facts_cbr'}
    sample_value_count = int(cfg.get('max_sample_values_per_column', 3))
    sample_value_chars = int(cfg.get('max_sample_value_chars', 80))
    schema_ctx=full_schema_context(profile, int(cfg.get('max_columns_in_prompt',120)), sample_value_count, sample_value_chars) if method in full_schema_methods and not bool(cfg.get('force_compact_schema', False)) else compact_schema_context(profile, linked, int(cfg.get('max_columns_in_prompt',40)), sample_value_count, sample_value_chars)
    schema_light_hints = ''
    if method in FACT_STAGE_METHODS or bool(cfg.get('schema_light_hints', False)):
        schema_light_hints = schema_light_hints_for_profile(profile, int(cfg.get('max_schema_hint_fields', 120)))
    diagnostics = retrieval_diagnostics(profile, linked, sample, matched) if linked else None
    return render_prompt(template, schema_ctx, sample['input_text'], matched, examples, facts_context=fact_context, schema_light_hints=schema_light_hints), retrieved, linked, matched, diagnostics, cbr_log, examples, schema_light_hints


def count_tokens(tokenizer, text):
    if tokenizer is None:
        return None
    try:
        encoded = tokenizer(text, add_special_tokens=False, return_attention_mask=False)
        return len(encoded['input_ids'])
    except Exception:
        return None


def resume_fingerprint(manifest: dict) -> dict:
    case_bank = manifest.get('case_bank') or {}
    retrieval = manifest.get('case_retrieval') or {}
    model_config = manifest.get('model_config') or {}
    decoding = manifest.get('decoding') or manifest.get('generation_parameters') or {}
    return {
        'dataset_sha256': manifest.get('dataset_sha256'),
        'split_sha256': manifest.get('split_sha256'),
        'config_sha256': manifest.get('config_sha256'),
        'prompt_sha256': manifest.get('prompt_sha256'),
        'model_name': manifest.get('model_name') or model_config.get('model_name_or_path'),
        'model_revision': manifest.get('model_revision'),
        'case_bank_sha256': manifest.get('case_bank_sha256') or case_bank.get('data_sha256'),
        'case_bank_split_sha256': manifest.get('case_bank_split_sha256') or case_bank.get('split_sha256'),
        'retrieval_policy': manifest.get('retrieval_policy') or retrieval,
        'generation_parameters': decoding,
        'code_commit': manifest.get('code_commit'),
    }


def validate_resume_manifest(out_dir: Path, current_manifest: dict, allow_mismatch: bool = False) -> None:
    manifest_path = out_dir / 'run_manifest.json'
    raw_path = out_dir / 'raw_generations.jsonl'
    if not raw_path.exists():
        return
    if not manifest_path.exists():
        if allow_mismatch:
            return
        raise RuntimeError('Resume refused: existing raw generations have no run_manifest.json.')
    previous = load_json(manifest_path)
    old_fp = resume_fingerprint(previous)
    new_fp = resume_fingerprint(current_manifest)
    mismatches = [key for key in sorted(new_fp) if old_fp.get(key) != new_fp.get(key)]
    if mismatches and not allow_mismatch:
        details = ', '.join(mismatches)
        raise RuntimeError(
            'Resume refused: existing run manifest does not match current configuration. '
            f'Mismatched fields: {details}. Use --allow-resume-mismatch only for non-paper recovery.'
        )


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--config', required=True); ap.add_argument('--allow-resume-mismatch', action='store_true'); args=ap.parse_args(); cfg=load_config(args.config)
    out_dir=ensure_dir(cfg['output_dir']); raw_path=out_dir/'raw_generations.jsonl'
    data=load_json(cfg['data_path']); ids=read_id_file(cfg['split_ids']) if cfg.get('split_ids') else {str(x['id']) for x in data}; data=[x for x in data if str(x['id']) in ids]
    model_cfg=load_config(cfg['model_config']); backend=model_cfg.get('backend','transformers'); tokenizer=model=None
    if backend=='transformers':
        from nldbwrite.inference.load_model import load_local_model, generate_text
        tokenizer,model=load_local_model(model_cfg['model_name_or_path'], bool(model_cfg.get('load_in_4bit', True)), model_cfg.get('torch_dtype','float16'), model_cfg.get('device_map','auto'), model_cfg.get('revision','main'))
    elif backend!='mock': raise ValueError(f'Unsupported backend: {backend}')
    template=load_template(cfg['prompt_file'])
    fact_context_map = load_fact_context_map(cfg.get('fact_context_path'))
    case_bank = None
    if should_use_case_examples(cfg['method'], cfg):
        bank_data = cfg.get('case_bank_data_path') or cfg.get('data_path')
        bank_split = cfg.get('case_bank_split_ids') or 'data/splits/augmented900_v1/dev_ids.txt'
        case_bank = CaseBank(bank_data, bank_split)
    gpu_info=[]
    try:
        import torch
        if torch.cuda.is_available():
            gpu_info=[{'index':i,'name':torch.cuda.get_device_name(i)} for i in range(torch.cuda.device_count())]
    except Exception:
        gpu_info=[]
    split_hash=sha256_file(cfg['split_ids']) if cfg.get('split_ids') and Path(cfg['split_ids']).exists() else None
    version, version_source=code_version(Path.cwd())
    resolved_revision=getattr(getattr(model,'config',None),'_commit_hash',None) if model is not None else None
    max_new_tokens = int(cfg.get('max_new_tokens', model_cfg.get('max_new_tokens', 1024)))
    generation_seed = int(cfg.get('generation_seed', model_cfg.get('seed', 42)))
    num_return_sequences = int(cfg.get('num_return_sequences', model_cfg.get('num_return_sequences', 1)))
    decoding={'max_new_tokens':max_new_tokens,'temperature':model_cfg.get('temperature',0.0),'top_p':model_cfg.get('top_p',1.0),'num_return_sequences':num_return_sequences,'seed':generation_seed}
    manifest={'run_name':cfg.get('run_name'),'method':cfg['method'],'config':cfg,'config_sha256':sha256_file(args.config),'model_config':model_cfg,'model_config_sha256':sha256_file(cfg['model_config']),'model_name':model_cfg.get('model_name_or_path'),'model_revision':resolved_revision or model_cfg.get('revision','main'),'backend':backend,'num_samples':len(data),'platform':platform.platform(),'start_time':time.strftime('%Y-%m-%d %H:%M:%S'),'dataset_sha256':sha256_file(cfg['data_path']),'split_sha256':split_hash,'prompt_sha256':sha256_text(template),'prompt_version':Path(cfg['prompt_file']).name,'code_commit':version,'code_version_source':version_source,'gpu_info':gpu_info,'decoding':decoding,'generation_parameters':decoding}
    if cfg['method'] in FACT_STAGE_METHODS:
        manifest['fact_stage'] = {
            'deterministic_extractors': bool(cfg.get('deterministic_fact_extractors', True)),
            'schema_light_hints': bool(cfg.get('schema_light_hints', True)),
            'max_schema_hint_fields': int(cfg.get('max_schema_hint_fields', 120)),
        }
    if case_bank is not None:
        manifest['case_bank'] = case_bank.metadata()
        manifest['case_retrieval'] = {
            'example_mode': cfg.get('example_mode'),
            'demo_type': cfg.get('demo_type'),
            'cbr_retriever': cfg.get('cbr_retriever'),
            'cbr_k': cfg.get('cbr_k') or cfg.get('num_examples'),
            'cbr_setting': cfg.get('cbr_setting', 'mixed'),
            'cbr_use_mmr': bool(cfg.get('cbr_use_mmr', False)),
            'cbr_unique_source_groups': bool(cfg.get('cbr_unique_source_groups', True)),
            'cbr_dense_model': cfg.get('cbr_dense_model'),
            'static_example_selection': cfg.get('static_example_selection'),
            'metadata_policy': cfg.get('cbr_metadata_policy') or (
                'oracle' if 'oracle' in str(cfg.get('cbr_retriever', '')).lower() else 'deployable'
            ),
            'weights': case_weights_from_config(cfg),
        }
        manifest['case_bank_sha256'] = manifest['case_bank'].get('data_sha256')
        manifest['case_bank_split_sha256'] = manifest['case_bank'].get('split_sha256')
        manifest['retrieval_policy'] = manifest['case_retrieval']
    if cfg.get('resume', True):
        validate_resume_manifest(out_dir, manifest, bool(cfg.get('allow_resume_mismatch', False) or args.allow_resume_mismatch))
    existing=list(iter_jsonl(raw_path)) if cfg.get('resume', True) and raw_path.exists() else []
    done={str(x['sample_id']) for x in existing}
    total_latency=sum(float(x.get('latency_sec') or 0) for x in existing)
    total_input_chars=sum(int(x.get('input_chars') or 0) for x in existing)
    total_output_chars=sum(int(x.get('output_chars') or 0) for x in existing)
    total_input_tokens=sum(int(x.get('input_tokens') or 0) for x in existing)
    total_output_tokens=sum(int(x.get('output_tokens') or 0) for x in existing)
    checkpoint_zip=cfg.get('checkpoint_zip')
    checkpoint_every=max(1, int(cfg.get('checkpoint_every', 5)))
    generated=0
    save_json(manifest,out_dir/'run_manifest.json')
    with open(raw_path, 'a' if cfg.get('resume', True) else 'w', encoding='utf-8') as fout:
        for sample in tqdm(data, desc=cfg.get('run_name', cfg['method'])):
            sid=str(sample['id'])
            if sid in done: continue
            prompt,retrieved,linked,matched,retrieval_stats,cbr_log,examples,schema_light_hints=build_prompt(sample,cfg,template,case_bank,fact_context_map); input_tokens=count_tokens(tokenizer,prompt); example_tokens=count_tokens(tokenizer,examples) if examples else 0; sample_seed = generation_seed + int(sha256_text(sid)[:8], 16); start=time.time(); raw=mock_generate(sample,cfg['method']) if backend=='mock' else generate_text(tokenizer,model,prompt,max_new_tokens,temperature=float(model_cfg.get('temperature',0.0)),top_p=float(model_cfg.get('top_p',1.0)),num_return_sequences=num_return_sequences,seed=sample_seed); latency=time.time()-start; raw_for_count = '\n'.join(raw) if isinstance(raw, list) else raw; output_tokens=count_tokens(tokenizer,raw_for_count)
            deterministic_facts = []
            if cfg['method'] in FACT_STAGE_METHODS and bool(cfg.get('deterministic_fact_extractors', True)):
                deterministic_facts = extract_deterministic_facts(sample.get('input_text') or '', int(cfg.get('max_deterministic_facts', 500)))
            total_latency += latency
            total_input_chars += len(prompt)
            total_output_chars += len(raw_for_count)
            total_input_tokens += input_tokens or 0
            total_output_tokens += output_tokens or 0
            raw_row = {
                'sample_id': sid,
                'db_id': sample['db_id'],
                'method': cfg['method'],
                'raw_output': raw,
                'generation_seed': sample_seed,
                'latency_sec': latency,
                'input_chars': len(prompt),
                'output_chars': len(raw_for_count),
                'input_tokens': input_tokens,
                'example_tokens': example_tokens,
                'num_examples': (cbr_log or {}).get('num_examples', 0),
                'num_demo_records_shown': (cbr_log or {}).get('num_demo_records_shown', 0),
                'output_tokens': output_tokens,
                'hit_max_new_tokens': bool(output_tokens is not None and output_tokens >= max_new_tokens - 2),
                'retrieved_columns': retrieved,
                'linked_columns': linked,
                'matched_values': matched,
                'retrieval_diagnostics': retrieval_stats,
                'case_retrieval': cbr_log,
                'prompt_omitted': True,
            }
            if cfg['method'] in FACT_STAGE_METHODS:
                raw_row['deterministic_facts'] = deterministic_facts
                raw_row['schema_light_hints'] = schema_light_hints
            fout.write(json.dumps(raw_row, ensure_ascii=False) + '\n'); fout.flush()
            done.add(sid)
            generated += 1
            if checkpoint_zip and generated % checkpoint_every == 0:
                manifest['checkpoint_time']=time.strftime('%Y-%m-%d %H:%M:%S')
                manifest['completed_samples']=len(done)
                manifest['total_latency_sec']=total_latency
                manifest['total_input_chars']=total_input_chars
                manifest['total_output_chars']=total_output_chars
                manifest['total_input_tokens']=total_input_tokens
                manifest['total_output_tokens']=total_output_tokens
                manifest['gpu_hours']=total_latency * max(len(gpu_info), 1 if backend == 'transformers' else 0) / 3600
                save_json(manifest,out_dir/'run_manifest.json')
                write_checkpoint_zip(out_dir, checkpoint_zip)
    manifest['end_time']=time.strftime('%Y-%m-%d %H:%M:%S'); manifest['completed_samples']=len(done); manifest['total_latency_sec']=total_latency; manifest['total_input_chars']=total_input_chars; manifest['total_output_chars']=total_output_chars; manifest['total_input_tokens']=total_input_tokens; manifest['total_output_tokens']=total_output_tokens; manifest['gpu_hours']=total_latency * max(len(gpu_info), 1 if backend == 'transformers' else 0) / 3600; save_json(manifest,out_dir/'run_manifest.json')
    write_checkpoint_zip(out_dir, checkpoint_zip)
    print(f'Wrote {raw_path}')
    if checkpoint_zip:
        print(f'Wrote checkpoint {checkpoint_zip}')
if __name__=='__main__': main()
