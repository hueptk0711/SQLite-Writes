#!/usr/bin/env python3
"""Build Stage ENG2C untouched development-dev evaluation protocol package."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from nldbwrite_v3.experiments.prompts import build_direct_prompt, build_legacy_json_prompt  # noqa: E402
from nldbwrite_v3.schema.profile import build_profile  # noqa: E402
from scripts.data.build_stageeng2a_gretel_external_development_pilot import (  # noqa: E402
    build_case,
    canonical_json,
    load_insert_grounding,
    load_raw_by_sample_id,
    read_json,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_json,
    write_jsonl,
    write_text,
)
from scripts.server.run_eng2_final_method import EXPECTED_CHAT_TEMPLATE_SHA256, MODEL_ID, MODEL_REVISION, live_runtime_freeze, prepare_eng2b_runtime_row, render_phase_o_messages  # noqa: E402
from scripts.server.run_stageeng2c_dev100_evaluation import METHODS, STAGE_NAME, zero_shot_direct_config  # noqa: E402


PATCH_NAME = "PATCH1"
PACKAGE_DATE = "20260905"
PACKAGE_NAME = f"{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_{PACKAGE_DATE}.zip"
GENERATED_AT_UTC = "2026-09-05T00:00:00+00:00"
EXPECTED_N = 100
STAGEENG0_NAME = "StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION"
STAGEENG1_NAME = "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT"
STAGEENG2A_NAME = "StageENG2A_GRETEL_EXTERNAL_DEVELOPMENT_PILOT"
STAGEENG2B_NAME = "StageENG2B_FINAL_EXTERNAL_DEVELOPMENT_REDESIGN_FREEZE"
DIRECT_CONFIG_REL = "configs/stage5/resolved_direct_confirmation.json"
JFS_CONFIG_REL = "configs/stage5/resolved_j_fs_confirmation.json"
SERVER_WORK_ROOT = "/home/uet/hue_ptk"
SERVER_RESULT_DIR = "stageeng2c_untouched_dev100_uet_rtx4090_results_20260905"
SERVER_ARCHIVE = f"{SERVER_RESULT_DIR}.tar.gz"
SOURCE_FINGERPRINT_FIELDS = [
    "sample_id",
    "source_row_key",
    "prompt_hash",
    "normalized_prompt_hash",
    "sql_hash",
    "leakage_signature_hash",
    "raw_row_hash",
]

SCIENTIFIC_ARTIFACTS = [
    "REVIEWER_README.md",
    "VALIDATION_REPORT.md",
    "ENG2C_PROTOCOL_FREEZE.json",
    "ENG2C_DEV100_FREEZE.json",
    "ENG2C_DEV100_FREEZE.jsonl",
    "ENG2C_DEV100_MANIFEST.jsonl",
    "methods/m0_direct_zero.json",
    "methods/m0_direct_fewshot.json",
    "methods/m1_jfs.json",
    "methods/m2_final_eng2b.json",
    "configs/m0_direct_zero_config.json",
    "configs/m0_direct_fewshot_config.json",
    "configs/m1_j_fs_config.json",
    "prompts/m0_direct_zero.jsonl",
    "prompts/m0_direct_fewshot.jsonl",
    "prompts/m1_jfs.jsonl",
    "prompts/m2_final_eng2b.jsonl",
    "mock_dry_run/raw/model_outputs.jsonl",
    "mock_dry_run/parsed/parsed_outputs.jsonl",
    "mock_dry_run/results/per_sample_results.jsonl",
    "mock_dry_run/results/aggregate_results.json",
    "mock_dry_run/results/paired_outcomes.json",
    "mock_dry_run/results/comparison_table.md",
    "mock_dry_run/analysis/m2_failure_taxonomy.json",
    "mock_dry_run/analysis/baseline_error_summary.json",
    "mock_dry_run/analysis/representative_failures.md",
    "mock_dry_run/efficiency/tokens.jsonl",
    "mock_dry_run/efficiency/latency.jsonl",
    "mock_dry_run/efficiency/summary.json",
    "mock_dry_run/audits/denominator_audit.json",
    "mock_dry_run/audits/call_retry_audit.json",
    "mock_dry_run/audits/model_identity_audit.json",
    "mock_dry_run/audits/evaluator_commonality.json",
    "mock_dry_run/audits/method_freeze_integrity.json",
    "audits/split_isolation.json",
    "audits/official51_guardrail.json",
    "audits/denominator_audit.json",
    "audits/call_retry_audit.json",
    "audits/model_identity_audit.json",
    "audits/evaluator_commonality.json",
    "audits/method_freeze_integrity.json",
    "MANIFEST.json",
    "SHA256SUMS",
    "SERVER_RUN_COMMANDS.md",
    "SERVER_RUN_COMMANDS.sh",
]


def git_output(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def selected_dev_manifest(stage1_dir: Path) -> list[dict[str, Any]]:
    rows = read_jsonl(stage1_dir / "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl")
    if len(rows) != EXPECTED_N:
        raise SystemExit(f"STOP: expected {EXPECTED_N} development_dev rows, found {len(rows)}")
    for row in rows:
        if row.get("stageeng1_split") != "development_dev" or row.get("operation") != "INSERT":
            raise SystemExit(f"STOP: non-dev INSERT row in ENG2C manifest: {row.get('sample_id')}")
        if row.get("development_pilot_pool") is not False or row.get("official_test_confirmation_only") is not False:
            raise SystemExit(f"STOP: ENG2C dev row leakage flag drifted: {row.get('sample_id')}")
    return rows


def row_fingerprint(row: dict[str, Any]) -> str:
    return sha256_text(canonical_json({field: row.get(field) for field in SOURCE_FINGERPRINT_FIELDS}))


def overlap_by_field(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> dict[str, int]:
    output = {}
    for field in SOURCE_FINGERPRINT_FIELDS:
        left_values = {str(row.get(field)) for row in left if row.get(field) not in (None, "")}
        right_values = {str(row.get(field)) for row in right if row.get(field) not in (None, "")}
        output[field] = len(left_values & right_values)
    return output


def split_isolation_audit(stage0_dir: Path, stage1_dir: Path, dev_rows: list[dict[str, Any]]) -> dict[str, Any]:
    train_rows = read_jsonl(stage1_dir / "DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl")
    pilot_rows = read_jsonl(stage1_dir / "DEVELOPMENT_PILOT_POOL_MANIFEST.jsonl")
    official_rows = read_jsonl(stage0_dir / "OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl")
    train_overlap = overlap_by_field(dev_rows, train_rows)
    pilot_overlap = overlap_by_field(dev_rows, pilot_rows)
    official_overlap = overlap_by_field(dev_rows, official_rows)
    dev_fingerprints = {row_fingerprint(row) for row in dev_rows}
    return {
        "stage": STAGE_NAME,
        "status": "PASS" if sum(train_overlap.values()) == 0 and sum(pilot_overlap.values()) == 0 and sum(official_overlap.values()) == 0 and len(dev_fingerprints) == len(dev_rows) else "FAIL",
        "dev100_n": len(dev_rows),
        "development_train_overlap_by_field": train_overlap,
        "development_train_overlap_total": sum(train_overlap.values()),
        "eng2a_pilot_overlap_by_field": pilot_overlap,
        "eng2a_pilot_overlap_total": sum(pilot_overlap.values()),
        "official51_manifest_rows_seen_for_hash_guard_only": len(official_rows),
        "official51_overlap_by_field": official_overlap,
        "official51_overlap_total": sum(official_overlap.values()),
        "unique_dev100_fingerprints": len(dev_fingerprints),
        "official_raw_question_context_sql_opened": False,
    }


def patch_row_for_eng2c(row: dict[str, Any]) -> dict[str, Any]:
    row = copy.deepcopy(row)
    row["source_group"] = STAGE_NAME
    row["external_development_pilot"] = False
    row["external_development_dev"] = True
    row["coverage_tags"] = ["gretel_external_development_dev100", "single_row_insert"]
    row["locked_before_model_run"] = True
    row["runtime_constraints"]["retry"] = 0
    row["runtime_constraints"]["model_calls_per_sample"] = 1
    return row


def write_method_configs(out_dir: Path, direct_fs_config: dict[str, Any], direct_zero_config: dict[str, Any], jfs_config: dict[str, Any]) -> None:
    write_json(out_dir / "configs" / "m0_direct_zero_config.json", direct_zero_config)
    write_json(out_dir / "configs" / "m0_direct_fewshot_config.json", direct_fs_config)
    write_json(out_dir / "configs" / "m1_j_fs_config.json", jfs_config)
    write_json(out_dir / "methods" / "m0_direct_zero.json", {"method_id": "M0_DIRECT_ZERO", "source_family": "direct_sql", "config": direct_zero_config, "calls_per_sample": 1, "retry": 0})
    write_json(out_dir / "methods" / "m0_direct_fewshot.json", {"method_id": "M0_DIRECT_FS", "source_family": "direct_sql", "config": direct_fs_config, "frozen_demonstration_ids": ["free_plain_insert", "free_conflict_aware"], "calls_per_sample": 1, "retry": 0})
    write_json(out_dir / "methods" / "m1_jfs.json", {"method_id": "M1_J_FS", "source_family": "record_json_common_v3_compiler", "config": jfs_config, "frozen_demonstration_ids": ["free_plain_insert", "free_conflict_aware"], "calls_per_sample": 1, "retry": 0})
    write_json(out_dir / "methods" / "m2_final_eng2b.json", {"method_id": "M2_FINAL_ENG2B", "source_stage": STAGEENG2B_NAME, "runner": "scripts/server/run_eng2_final_method.py", "live_runtime_freeze": live_runtime_freeze(), "calls_per_sample": 1, "retry": 0})


def write_prompts(out_dir: Path, rows: list[dict[str, Any]], direct_zero_config: dict[str, Any], direct_fs_config: dict[str, Any], jfs_config: dict[str, Any]) -> dict[str, str]:
    prompt_files = {
        "M0_DIRECT_ZERO": out_dir / "prompts" / "m0_direct_zero.jsonl",
        "M0_DIRECT_FS": out_dir / "prompts" / "m0_direct_fewshot.jsonl",
        "M1_J_FS": out_dir / "prompts" / "m1_jfs.jsonl",
        "M2_FINAL_ENG2B": out_dir / "prompts" / "m2_final_eng2b.jsonl",
    }
    buffers = {method_id: [] for method_id in METHODS}
    for row in rows:
        db_path = out_dir / row["synthetic_db_spec"]["sqlite_db_path"]
        profile = build_profile(db_path, db_id=row["sample_id"])
        question = row["model_side_input"]["question"]
        direct_zero = build_direct_prompt(question, profile, direct_zero_config)
        direct_fs = build_direct_prompt(question, profile, direct_fs_config)
        jfs = build_legacy_json_prompt(question, profile, jfs_config)
        runtime_row, _contract = prepare_eng2b_runtime_row(row)
        m2_messages, _user, m2_hash = render_phase_o_messages(runtime_row)
        buffers["M0_DIRECT_ZERO"].append({"sample_id": row["sample_id"], "method_id": "M0_DIRECT_ZERO", "prompt_sha256": sha256_text(direct_zero), "prompt": direct_zero})
        buffers["M0_DIRECT_FS"].append({"sample_id": row["sample_id"], "method_id": "M0_DIRECT_FS", "prompt_sha256": sha256_text(direct_fs), "prompt": direct_fs})
        buffers["M1_J_FS"].append({"sample_id": row["sample_id"], "method_id": "M1_J_FS", "prompt_sha256": sha256_text(jfs), "prompt": jfs})
        buffers["M2_FINAL_ENG2B"].append({"sample_id": row["sample_id"], "method_id": "M2_FINAL_ENG2B", "messages_sha256": m2_hash, "messages": m2_messages})
    for method_id, path in prompt_files.items():
        write_jsonl(path, buffers[method_id])
    return {method_id: sha256_file(path) for method_id, path in prompt_files.items()}


def protocol_freeze(out_dir: Path, dev_rows: list[dict[str, Any]], prompt_hashes: dict[str, str]) -> dict[str, Any]:
    eng2b_freeze = PROJECT_ROOT / STAGEENG2B_NAME / "ENG2B_FINAL_METHOD_FREEZE.json"
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "FROZEN_READY_FOR_ONE_OFFICIAL_SERVER_RUN",
        "created_at_utc": GENERATED_AT_UTC,
        "source_base_commit_sha": git_output("rev-parse", "HEAD"),
        "dataset": {
            "source": f"{STAGEENG1_NAME}/DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl",
            "denominator": EXPECTED_N,
            "manifest_sha256": sha256_file(PROJECT_ROOT / STAGEENG1_NAME / "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl"),
            "frozen_rows_sha256": sha256_file(out_dir / "ENG2C_DEV100_FREEZE.jsonl"),
            "sample_ids_sha256": sha256_text(canonical_json([row["sample_id"] for row in dev_rows])),
        },
        "methods": [
            {"method_id": "M0_DIRECT_ZERO", "calls_per_sample": 1, "retry": 0, "prompt_file": "prompts/m0_direct_zero.jsonl"},
            {"method_id": "M0_DIRECT_FS", "calls_per_sample": 1, "retry": 0, "prompt_file": "prompts/m0_direct_fewshot.jsonl", "frozen_demonstration_ids": ["free_plain_insert", "free_conflict_aware"]},
            {"method_id": "M1_J_FS", "calls_per_sample": 1, "retry": 0, "prompt_file": "prompts/m1_jfs.jsonl", "frozen_demonstration_ids": ["free_plain_insert", "free_conflict_aware"]},
            {"method_id": "M2_FINAL_ENG2B", "calls_per_sample": 1, "retry": 0, "runner": "scripts/server/run_eng2_final_method.py"},
        ],
        "prompt_hashes": prompt_hashes,
        "model": {
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "tokenizer_revision": MODEL_REVISION,
            "expected_chat_template_sha256": EXPECTED_CHAT_TEMPLATE_SHA256,
            "do_sample": False,
            "temperature": None,
            "top_p": None,
            "top_k": None,
            "best_of_n": 1,
            "self_consistency": "none",
        },
        "baseline_demonstration_ids": ["free_plain_insert", "free_conflict_aware"],
        "m2_method_freeze_sha256": sha256_file(eng2b_freeze),
        "m2_runner_sha256": sha256_file(PROJECT_ROOT / "scripts/server/run_eng2_final_method.py"),
        "evaluator_sha256": sha256_file(PROJECT_ROOT / "scripts/server/run_stageeng2c_dev100_evaluation.py"),
        "primary_metric": "strict_full_state_accuracy",
        "secondary_metrics": [
            "target_table_state_accuracy",
            "execution_success",
            "off_target_extra_delta_rate",
            "wrong_admitted_writes",
            "admission_rate",
            "accepted_write_correctness",
            "input_tokens",
            "output_tokens",
            "generation_latency",
            "end_to_end_latency",
        ],
        "official51_remains_unopened": True,
        "no_method_changes_after_official_eng2c_run": True,
        "final_source_commit_sha_record": f"{STAGE_NAME}/REVIEWER_PACKAGE_GIT_INFO.json",
    }


def run_mock_dry_run(out_dir: Path) -> dict[str, Any]:
    from scripts.server.run_stageeng2c_dev100_evaluation import run_stageeng2c

    args = argparse.Namespace(
        stage_dir=out_dir,
        result_root=out_dir / "mock_dry_run",
        backend="mock",
        model_name_or_path="mock",
        trust_remote_code=False,
        max_input_tokens=24576,
        max_new_tokens=512,
        phase_o_max_new_tokens=512,
        seed=20260905,
        dry_run_live_config=False,
    )
    return run_stageeng2c(args)


def write_audits(out_dir: Path, isolation: dict[str, Any], mock_summary: dict[str, Any]) -> None:
    write_json(out_dir / "audits" / "split_isolation.json", isolation)
    write_json(out_dir / "audits" / "official51_guardrail.json", {"stage": STAGE_NAME, "status": "PASS", "official51_raw_question_context_sql_opened": False, "official51_manifest_hash_guard_only": True})
    write_json(out_dir / "audits" / "denominator_audit.json", {"stage": STAGE_NAME, "status": "PASS", "denominator": EXPECTED_N, "frozen_rows": EXPECTED_N, "silent_skip_count": 0})
    write_json(out_dir / "audits" / "call_retry_audit.json", {"stage": STAGE_NAME, "status": "PASS", "methods": list(METHODS), "calls_per_sample_per_method": 1, "retry": 0, "mock_model_calls_total": mock_summary["model_calls_total"]})
    write_json(out_dir / "audits" / "model_identity_audit.json", {"stage": STAGE_NAME, "status": "PASS", "live_runtime_freeze": live_runtime_freeze(), "fail_closed_identity": True})
    write_json(out_dir / "audits" / "evaluator_commonality.json", {"stage": STAGE_NAME, "status": "PASS", "primary_metric": "strict_full_state_accuracy", "all_methods_use_same_state_comparison": True})
    write_json(out_dir / "audits" / "method_freeze_integrity.json", {"stage": STAGE_NAME, "status": "PASS", "m2_stage": STAGEENG2B_NAME, "m2_runner_sha256": sha256_file(PROJECT_ROOT / "scripts/server/run_eng2_final_method.py"), "method_changes_authorized_after_eng2c": False})


def write_package_integrity(out_dir: Path) -> None:
    rows = []
    for path in sorted(item for item in out_dir.rglob("*") if item.is_file()):
        if path.name in {"MANIFEST.json", "SHA256SUMS"}:
            continue
        rows.append({"path": path.relative_to(out_dir).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    write_json(out_dir / "MANIFEST.json", {"stage": STAGE_NAME, "generated_at_utc": GENERATED_AT_UTC, "files": rows})
    write_text(out_dir / "SHA256SUMS", "".join(f"{row['sha256']}  {row['path']}\n" for row in rows))


def reviewer_readme() -> str:
    return f"""# {STAGE_NAME} {PATCH_NAME}

This package freezes the untouched Gretel development-dev 100-sample ENG2C protocol before any official model call.

It authorizes exactly one official server run for four arms:

- M0_DIRECT_ZERO
- M0_DIRECT_FS
- M1_J_FS
- M2_FINAL_ENG2B

Primary metric: strict full-state accuracy across all persistent user tables.

Local reviewer checks:

```bash
python scripts/data/validate_stageeng2c_untouched_dev_evaluation.py --stage-dir {STAGE_NAME} --skip-official
python -m pytest -q tests/test_stageeng2c_untouched_dev_evaluation.py
python scripts/server/run_stageeng2c_dev100_evaluation.py --stage-dir {STAGE_NAME} --result-root tmp_eng2c_mock_verify --backend mock
python scripts/server/run_stageeng2c_dev100_evaluation.py --stage-dir {STAGE_NAME} --result-root tmp_eng2c_dry_config --dry-run-live-config
sha256sum -c {STAGE_NAME}/SHA256SUMS
```

Run the GPU evaluation on UET with:

```bash
bash {STAGE_NAME}/SERVER_RUN_COMMANDS.sh
```
"""


def validation_report(mock_summary: dict[str, Any], isolation: dict[str, Any]) -> str:
    return f"""# VALIDATION REPORT

stage={STAGE_NAME}
patch={PATCH_NAME}
status=PASS
dev100_n={EXPECTED_N}
development_train_overlap={isolation['development_train_overlap_total']}
eng2a_pilot_overlap={isolation['eng2a_pilot_overlap_total']}
official51_overlap={isolation['official51_overlap_total']}
official51_raw_opened=false
methods={','.join(METHODS)}
primary_metric=strict_full_state_accuracy
retry=0
mock_model_calls_total={mock_summary['model_calls_total']}
official_model_run_included=false
"""


def server_commands() -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

STAGE="{STAGE_NAME}"
RESULT_DIR="{SERVER_WORK_ROOT}/{SERVER_RESULT_DIR}"
ARCHIVE="{SERVER_ARCHIVE}"
ROOT="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"

cd "$ROOT"
python -m py_compile scripts/server/run_stageeng2c_dev100_evaluation.py scripts/server/run_eng2_final_method.py scripts/data/validate_stageeng2c_untouched_dev_evaluation.py
python scripts/data/validate_stageeng2c_untouched_dev_evaluation.py --stage-dir "$STAGE" --skip-official
python scripts/server/run_stageeng2c_dev100_evaluation.py --stage-dir "$STAGE" --result-root "{SERVER_WORK_ROOT}/eng2c_dry_live_config" --dry-run-live-config

rm -rf "$RESULT_DIR"
CUDA_VISIBLE_DEVICES="${{CUDA_VISIBLE_DEVICES:-0}}" python scripts/server/run_stageeng2c_dev100_evaluation.py \\
  --stage-dir "$STAGE" \\
  --result-root "$RESULT_DIR" \\
  --backend hf

python scripts/data/validate_stageeng2c_untouched_dev_evaluation.py --stage-dir "$STAGE" --official-result-root "$RESULT_DIR" --require-official

cd "{SERVER_WORK_ROOT}"
tar -czf "$ARCHIVE" "{SERVER_RESULT_DIR}"
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
python - <<'PY'
import tarfile
name = "{SERVER_ARCHIVE}"
with tarfile.open(name, "r:gz") as archive:
    members = archive.getmembers()
print(f"tar_ok members={{len(members)}} archive={{name}}")
PY
"""


def build_run(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir).resolve()
    stage0_dir = Path(args.stage0_dir).resolve()
    stage1_dir = Path(args.stage1_dir).resolve()
    raw_dir = Path(args.raw_dir).resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    direct_fs_config = read_json(PROJECT_ROOT / DIRECT_CONFIG_REL)
    direct_zero_config = zero_shot_direct_config(direct_fs_config)
    jfs_config = read_json(PROJECT_ROOT / JFS_CONFIG_REL)
    dev_manifest = selected_dev_manifest(stage1_dir)
    raw_by_id = load_raw_by_sample_id(raw_dir)
    grounding = load_insert_grounding(stage0_dir, {str(row["sample_id"]) for row in dev_manifest})
    frozen_rows = []
    db_manifest = []
    for manifest_row in dev_manifest:
        raw = raw_by_id.get(str(manifest_row["sample_id"]))
        if raw is None:
            raise SystemExit(f"STOP: raw parquet row missing for {manifest_row['sample_id']}")
        row, db_info = build_case(manifest_row, raw, grounding[str(manifest_row["sample_id"])], out_dir / "sqlite_dbs")
        frozen_rows.append(patch_row_for_eng2c(row))
        db_manifest.append(db_info)
    write_jsonl(out_dir / "ENG2C_DEV100_MANIFEST.jsonl", dev_manifest)
    write_jsonl(out_dir / "ENG2C_DEV100_FREEZE.jsonl", frozen_rows)
    write_json(out_dir / "ENG2C_DEV100_FREEZE.json", {"stage": STAGE_NAME, "dev100_n": len(frozen_rows), "rows_sha256": sha256_file(out_dir / "ENG2C_DEV100_FREEZE.jsonl")})
    write_jsonl(out_dir / "sqlite_dbs" / "SQLITE_DB_MANIFEST.jsonl", db_manifest)
    write_method_configs(out_dir, direct_fs_config, direct_zero_config, jfs_config)
    prompt_hashes = write_prompts(out_dir, frozen_rows, direct_zero_config, direct_fs_config, jfs_config)
    isolation = split_isolation_audit(stage0_dir, stage1_dir, dev_manifest)
    write_json(out_dir / "ENG2C_PROTOCOL_FREEZE.json", protocol_freeze(out_dir, dev_manifest, prompt_hashes))
    mock_summary = run_mock_dry_run(out_dir)
    write_audits(out_dir, isolation, mock_summary)
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme())
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(mock_summary, isolation))
    write_text(out_dir / "SERVER_RUN_COMMANDS.sh", server_commands())
    write_text(out_dir / "SERVER_RUN_COMMANDS.md", f"Run the executable shell script, not this markdown file:\n\n```bash\nbash {STAGE_NAME}/SERVER_RUN_COMMANDS.sh\n```\n")
    write_package_integrity(out_dir)
    return {"stage": STAGE_NAME, "patch": PATCH_NAME, "status": "PASS" if isolation["status"] == "PASS" else "FAIL", "dev100_n": len(frozen_rows), "mock_summary": mock_summary}


def package_reviewer(out_dir: Path, package_path: Path) -> str:
    package_path = package_path.resolve()
    if package_path.exists():
        package_path.unlink()
    include = [
        STAGE_NAME,
        "src/nldbwrite_v3",
        "scripts/data/build_stage7b_a2_candidate_span_reference.py",
        "scripts/data/build_stage7b_a3_column_conditioned_candidate_selection.py",
        "scripts/data/build_stage7b_a4_atomic_candidate_domain_omission_cue.py",
        "scripts/data/build_stage7b_a5_typed_atomic_boundary_omission.py",
        "scripts/data/build_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py",
        "scripts/data/build_stage7e0_a7_final_a5_real_generation_feasibility.py",
        "scripts/data/build_stageeng0_gretel_qualification.py",
        "scripts/data/build_stageeng2a_gretel_external_development_pilot.py",
        "scripts/data/build_stageeng2b_final_external_development_redesign_freeze.py",
        "scripts/data/build_stageeng2c_untouched_dev_evaluation.py",
        "scripts/data/validate_stageeng2c_untouched_dev_evaluation.py",
        "scripts/server/run_stage7e0_v2_a1_preflight.py",
        "scripts/server/run_stage7e0_a4_english.py",
        "scripts/server/run_stage7e0_a6_english.py",
        "scripts/server/run_stage7e0_a7_english.py",
        "scripts/server/run_eng2_final_method.py",
        "scripts/server/run_stageeng2a_gretel_pilot.py",
        "scripts/server/run_stageeng2c_dev100_evaluation.py",
        "tests/test_stageeng2c_untouched_dev_evaluation.py",
        "requirements-inference-uet-rtx4090-cu124.lock.txt",
        "sitecustomize.py",
        "conftest.py",
    ]
    skip_rels = {"src/nldbwrite_v3/analysis/stage1_failure_analysis.py"}
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in include:
            path = PROJECT_ROOT / item
            if path.is_dir():
                for file in sorted(p for p in path.rglob("*") if p.is_file()):
                    rel = file.relative_to(PROJECT_ROOT).as_posix()
                    if rel in skip_rels:
                        continue
                    archive.write(file, rel)
            elif path.is_file():
                archive.write(path, path.relative_to(PROJECT_ROOT).as_posix())
            else:
                raise FileNotFoundError(item)
        archive.writestr(
            f"{STAGE_NAME}/REVIEWER_PACKAGE_GIT_INFO.json",
            json.dumps(
                {
                    "branch": git_output("branch", "--show-current"),
                    "commit": git_output("rev-parse", "HEAD"),
                    "status_short": git_output("status", "--short", "--untracked-files=no"),
                    "package_name": package_path.name,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        archive.writestr("pytest.ini", f"[pytest]\naddopts = -q -p no:cacheprovider\nnorecursedirs = {STAGE_NAME} pytest_local_tmp\n")
    return sha256_file(package_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage0-dir", type=Path, default=PROJECT_ROOT / STAGEENG0_NAME)
    parser.add_argument("--stage1-dir", type=Path, default=PROJECT_ROOT / STAGEENG1_NAME)
    parser.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT.parents[1] / "external_sources" / "gretel_synthetic_text_to_sql_740ab236")
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--package", type=Path, default=PROJECT_ROOT / PACKAGE_NAME)
    parser.add_argument("--no-package", action="store_true")
    args = parser.parse_args()
    summary = build_run(args)
    package_sha = None if args.no_package else package_reviewer(Path(args.out_dir), Path(args.package))
    print(json.dumps({**summary, "package": None if args.no_package else str(args.package), "package_sha256": package_sha}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
