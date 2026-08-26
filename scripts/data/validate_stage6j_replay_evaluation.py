from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

import sys

for import_root in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from nldbwrite_v3.common import sha256_file

from scripts.data.create_stage6j_replay_evaluation import (
    EVAL_ARMS,
    EXECUTION_CODE_MANIFEST_SHA256,
    FINAL_CONFIRMATION_N,
    FINAL_GOLD_CORPUS_SHA256,
    FINAL_GOLD_POST_STATE_HASHES_SHA256,
    FINAL_GOLD_PROGRAMS_SHA256,
    FINAL_MANIFEST_SHA256,
    RAW_STREAM_HASHES,
    RUN_STATE_SHA256,
    STAGE6I_ZIP_SHA256,
    read_jsonl,
)

REQUIRED_OUTCOME_FIELDS = {
    "stage6_sample_id",
    "arm",
    "source_raw_generation_sha256",
    "source_raw_generation_row_sha256",
    "parse_status",
    "construction_status",
    "verification_status",
    "admission_status",
    "execution_status",
    "candidate_program",
    "candidate_program_sha256",
    "predicted_post_state_sha256",
    "gold_post_state_sha256",
    "target_state_correct",
    "failure_stage",
    "failure_reason",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(output_dir: Path) -> dict[str, Any]:
    violations: list[str] = []
    lock_path = output_dir / "STAGE6J_REPLAY_EVALUATION_LOCK.json"
    arm_manifest_path = output_dir / "REPLAY_ARM_MANIFEST.json"
    summary_path = output_dir / "REPLAY_EVALUATION_SUMMARY.json"
    h2_path = output_dir / "H2_SHARED_REPLAY_PROVENANCE_AUDIT.json"
    denominator_path = output_dir / "DENOMINATOR_AUDIT.json"
    for path in (lock_path, arm_manifest_path, summary_path, h2_path, denominator_path):
        if not path.is_file():
            violations.append(f"missing_required_artifact:{path.name}")
    if violations:
        return {"status": "FAIL", "violations": violations}

    lock = load_json(lock_path)
    arm_manifest = load_json(arm_manifest_path)
    summary = load_json(summary_path)
    h2 = load_json(h2_path)
    denominator = load_json(denominator_path)

    expected_lock_fields = {
        "stage": "Stage6J_DETERMINISTIC_REPLAY_EVALUATION",
        "status": "PASS_REPLAY_EVALUATION_COMPLETE",
        "final_confirmation_n": FINAL_CONFIRMATION_N,
        "statistics_computed": False,
        "significance_tests_computed": False,
        "model_called": False,
        "gpu_called": False,
    }
    for key, expected in expected_lock_fields.items():
        if lock.get(key) != expected:
            violations.append(f"lock_field_mismatch:{key}")
    expected_hash_fields = {
        "arm_manifest_sha256": arm_manifest_path,
        "summary_sha256": summary_path,
        "h2_shared_replay_audit_sha256": h2_path,
        "denominator_audit_sha256": denominator_path,
    }
    for key, path in expected_hash_fields.items():
        if lock.get(key) != sha256_file(path):
            violations.append(f"lock_hash_mismatch:{key}")

    if arm_manifest.get("source_stage6i_zip_sha256") != STAGE6I_ZIP_SHA256:
        violations.append("stage6i_zip_hash_mismatch")
    if arm_manifest.get("run_state_sha256") != RUN_STATE_SHA256:
        violations.append("run_state_hash_mismatch")
    if arm_manifest.get("execution_code_manifest_sha256") != EXECUTION_CODE_MANIFEST_SHA256:
        violations.append("execution_code_manifest_hash_mismatch")
    final_artifacts = arm_manifest.get("final_stage6e_artifacts") or {}
    expected_final = {
        "final_manifest_sha256": FINAL_MANIFEST_SHA256,
        "final_gold_corpus_sha256": FINAL_GOLD_CORPUS_SHA256,
        "final_gold_programs_sha256": FINAL_GOLD_PROGRAMS_SHA256,
        "final_gold_post_state_hashes_sha256": FINAL_GOLD_POST_STATE_HASHES_SHA256,
    }
    if final_artifacts != expected_final:
        violations.append("final_stage6e_artifact_hashes_mismatch")
    if arm_manifest.get("raw_stream_hashes") != RAW_STREAM_HASHES:
        violations.append("raw_stream_hashes_mismatch")

    expected_ids: set[str] | None = None
    outcomes_by_arm: dict[str, list[dict[str, Any]]] = {}
    for arm, spec in EVAL_ARMS.items():
        outcome_path = output_dir / "replay_outcomes" / f"{arm}.jsonl"
        if not outcome_path.is_file():
            violations.append(f"missing_outcome:{arm}")
            continue
        rows = read_jsonl(outcome_path)
        outcomes_by_arm[arm] = rows
        if len(rows) != FINAL_CONFIRMATION_N:
            violations.append(f"outcome_row_count_mismatch:{arm}:{len(rows)}")
        ids = [str(row.get("stage6_sample_id")) for row in rows]
        if len(set(ids)) != len(ids):
            violations.append(f"duplicate_sample_ids:{arm}")
        if expected_ids is None:
            expected_ids = set(ids)
        elif set(ids) != expected_ids:
            violations.append(f"sample_id_set_mismatch:{arm}")
        for row in rows:
            missing = sorted(REQUIRED_OUTCOME_FIELDS - set(row))
            if missing:
                violations.append(f"outcome_missing_fields:{arm}:{missing}")
                break
            if row.get("arm") != arm:
                violations.append(f"outcome_arm_mismatch:{arm}")
                break
            if row.get("source_raw_generation_sha256") != RAW_STREAM_HASHES[str(spec["raw_stream"])]:
                violations.append(f"outcome_source_raw_hash_mismatch:{arm}")
                break
            if not isinstance(row.get("target_state_correct"), bool):
                violations.append(f"target_state_correct_not_bool:{arm}")
                break
            if row.get("failure_stage") not in {
                "none",
                "generation",
                "parse",
                "construction",
                "verification",
                "admission",
                "execution",
                "state_mismatch",
            }:
                violations.append(f"invalid_failure_stage:{arm}")
                break
    if expected_ids is not None and len(expected_ids) != FINAL_CONFIRMATION_N:
        violations.append("expected_id_set_not_481")

    if h2.get("status") != "PASS" or h2.get("checked_pairs") != FINAL_CONFIRMATION_N or h2.get("mismatch_count") != 0:
        violations.append("h2_shared_replay_audit_failed")
    if "d_g1_control" in outcomes_by_arm and "d_f_g1_vnext" in outcomes_by_arm:
        left = {row["stage6_sample_id"]: row for row in outcomes_by_arm["d_g1_control"]}
        right = {row["stage6_sample_id"]: row for row in outcomes_by_arm["d_f_g1_vnext"]}
        for sample_id, left_row in left.items():
            right_row = right.get(sample_id)
            if right_row is None or left_row.get("shared_raw_generation_row_sha256") != right_row.get("shared_raw_generation_row_sha256"):
                violations.append(f"h2_shared_raw_row_mismatch:{sample_id}")
                break

    if denominator.get("status") != "PASS" or denominator.get("final_confirmation_n") != FINAL_CONFIRMATION_N:
        violations.append("denominator_audit_failed")
    for arm, row in (denominator.get("arms") or {}).items():
        if row.get("row_count") != FINAL_CONFIRMATION_N or row.get("unique_sample_ids") != FINAL_CONFIRMATION_N:
            violations.append(f"denominator_arm_count_mismatch:{arm}")
        if row.get("missing_sample_ids") or row.get("extra_sample_ids"):
            violations.append(f"denominator_arm_id_mismatch:{arm}")

    if summary.get("statistics_computed") is not False or summary.get("significance_tests_computed") is not False:
        violations.append("stage6j_must_not_compute_significance")
    if summary.get("model_called") is not False or summary.get("gpu_called") is not False:
        violations.append("stage6j_must_be_cpu_only")
    if set((summary.get("arms") or {}).keys()) != set(EVAL_ARMS):
        violations.append("summary_arm_set_mismatch")
    for arm, arm_summary in (summary.get("arms") or {}).items():
        if arm_summary.get("n") != FINAL_CONFIRMATION_N:
            violations.append(f"summary_arm_n_mismatch:{arm}")

    return {
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "final_confirmation_n": FINAL_CONFIRMATION_N,
        "arm_count": len(EVAL_ARMS),
        "statistics_computed": False,
        "model_called": False,
        "gpu_called": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage6j-dir", type=Path, default=PROJECT_ROOT / "stage6_replay_evaluation")
    args = parser.parse_args()
    print(json.dumps(validate(args.stage6j_dir), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
