#!/usr/bin/env python3
"""Validate Stage7C A3 English offset-semantics PATCH1 artifacts."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nldbwrite_v3.v2_a1.inventories import FORBIDDEN_MODEL_SIDE_KEYS, build_schema_inventory
from nldbwrite_v3.v2_a1.prompt_rendering import sha256_text as v2_sha256_text

from scripts.data.build_stage7c_a3_english_offset_semantics import (
    A1_OFFSET_GUIDE_SPEC_PATH,
    A1_PHASE_M_PROMPT_SPEC_PATH,
    A2_PROMPT_SPEC_PATH,
    A3_PROMPT_SPEC_PATH,
    MODEL_ID,
    MODEL_REVISION,
    PHASE_O_OFFSET_SEMANTICS_AMENDMENT,
    SCIENTIFIC_ARTIFACTS,
    STAGE_NAME,
    canonical_json,
    oracle_v2_path,
    read_json,
    sha256_file,
    sha256_text,
)


REQUIRED_FILES = [
    *SCIENTIFIC_ARTIFACTS,
    "DERIVED_ARTIFACT_MANIFEST.json",
    "STAGE7C_A3_LOCK.json",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
]
REQUIRED_COVERAGE_TAGS = {
    "2_values",
    "3_values",
    "4_values",
    "5_values",
    "text",
    "integer",
    "real",
    "comma",
    "colon",
    "quoted_text",
    "parentheses",
    "email",
    "date_like",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def validate_prompt_contract(stage_dir: Path, failures: list[str]) -> None:
    a2 = read_json(PROJECT_ROOT / A2_PROMPT_SPEC_PATH)
    phase_m = read_json(PROJECT_ROOT / A1_PHASE_M_PROMPT_SPEC_PATH)
    offset_guide_spec = read_json(PROJECT_ROOT / A1_OFFSET_GUIDE_SPEC_PATH)
    a3 = read_json(stage_dir / "PHASE_O_PROMPT_SPEC_A3_ENGLISH.json")
    diff = read_json(stage_dir / "PROMPT_CHANGE_DIFF_A2_TO_A3.json")
    amendment = (stage_dir / "PHASE_O_PROMPT_AMENDMENT.md").read_text(encoding="utf-8")
    expected_user = a2["user_prompt_template"].rstrip() + "\n\n" + PHASE_O_OFFSET_SEMANTICS_AMENDMENT.strip()

    if amendment.strip() != PHASE_O_OFFSET_SEMANTICS_AMENDMENT.strip():
        failures.append("phase_o_amendment_text_mismatch")
    if a3.get("parent_prompt_spec_path") != A2_PROMPT_SPEC_PATH:
        failures.append("a3_parent_prompt_spec_path_mismatch")
    if a3.get("changed_component") != "Phase O user prompt template only":
        failures.append("a3_changed_component_not_user_prompt_only")
    if a3.get("system_prompt") != a2.get("system_prompt"):
        failures.append("a3_system_prompt_changed_from_a2")
    if a3.get("user_prompt_template") != expected_user:
        failures.append("a3_user_prompt_is_not_a2_plus_exact_amendment")
    if a3.get("examples") != [] or a3.get("zero_shot") is not True:
        failures.append("a3_zero_shot_or_examples_changed")
    if a3.get("model_id") != MODEL_ID or a3.get("model_revision") != MODEL_REVISION:
        failures.append("a3_model_lock_changed")
    if a3.get("retry") != 0 or a3.get("repair") != "none":
        failures.append("a3_retry_or_repair_changed")
    if a3.get("offset_guide_spec_path") != A1_OFFSET_GUIDE_SPEC_PATH:
        failures.append("a3_offset_guide_spec_path_changed")
    if offset_guide_spec.get("range_convention") != "[start_char, end_char)":
        failures.append("offset_guide_range_convention_changed")

    hashes = a3.get("prompt_hashes", {})
    if hashes.get("phase_o_system_prompt_sha256") != v2_sha256_text(a2["system_prompt"]):
        failures.append("a3_phase_o_system_hash_mismatch")
    if hashes.get("phase_o_user_prompt_template_sha256") != v2_sha256_text(expected_user):
        failures.append("a3_phase_o_user_hash_mismatch")
    if hashes.get("phase_m_system_prompt_sha256") != v2_sha256_text(phase_m["system_prompt"]):
        failures.append("phase_m_system_hash_not_unchanged")
    if hashes.get("phase_m_user_prompt_template_sha256") != v2_sha256_text(phase_m["user_prompt_template"]):
        failures.append("phase_m_user_hash_not_unchanged")

    if diff.get("changed_component") != "Phase O user prompt template only":
        failures.append("prompt_diff_changed_component_mismatch")
    if diff.get("phase_o_system_prompt", {}).get("changed") is not False:
        failures.append("prompt_diff_system_marked_changed")
    if diff.get("phase_o_user_prompt_template", {}).get("changed") is not True:
        failures.append("prompt_diff_user_not_marked_changed")
    if diff.get("phase_m_system_prompt", {}).get("changed") is not False:
        failures.append("prompt_diff_phase_m_system_changed")
    if diff.get("phase_m_user_prompt_template", {}).get("changed") is not False:
        failures.append("prompt_diff_phase_m_user_changed")
    if diff.get("offset_guide_serializer", {}).get("changed") is not False:
        failures.append("prompt_diff_offset_guide_changed")


def validate_smoke_rows(stage_dir: Path, failures: list[str]) -> tuple[int, int, int]:
    rows = read_jsonl(stage_dir / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl")
    oracle_rows = read_jsonl(stage_dir / "ORACLE_V2_PATH_RESULTS.jsonl")
    oracle_by_id = {row["sample_id"]: row for row in oracle_rows}
    if len(rows) != 8:
        failures.append("fresh_case_count_mismatch")
    sample_ids = [row["sample_id"] for row in rows]
    if len(set(sample_ids)) != len(sample_ids):
        failures.append("duplicate_sample_id")
    if any(str(sample_id).startswith("gretel:") for sample_id in sample_ids):
        failures.append("gretel_sample_id_present")

    coverage_tags = {tag for row in rows for tag in row.get("coverage_tags", [])}
    missing_tags = sorted(REQUIRED_COVERAGE_TAGS - coverage_tags)
    if missing_tags:
        failures.append(f"coverage_tags_missing:{','.join(missing_tags)}")
    value_counts = sorted({len(row["label_side_expected"]["phase_o"]["value_spans"]) for row in rows})
    if value_counts != [2, 3, 4, 5]:
        failures.append("value_count_coverage_mismatch")

    span_count = 0
    admitted_count = 0
    for row in rows:
        sample_id = row["sample_id"]
        if set(row.get("model_side_input", {})) != {"question", "schema_inventory"}:
            failures.append(f"model_side_input_keys_mismatch:{sample_id}")
        if FORBIDDEN_MODEL_SIDE_KEYS.intersection(row.get("model_side_input", {})):
            failures.append(f"model_side_gold_leakage:{sample_id}")
        if row["label_side_expected"].get("model_side_visible") is not False:
            failures.append(f"label_side_visible_not_false:{sample_id}")
        if row.get("locked_before_model_run") is not True:
            failures.append(f"not_locked_before_model_run:{sample_id}")

        question = row["model_side_input"]["question"]
        phase_o = row["label_side_expected"]["phase_o"]
        spans = phase_o.get("value_spans", [])
        span_count += len(spans)
        if phase_o.get("operation") != "INSERT":
            failures.append(f"phase_o_operation_not_insert:{sample_id}")
        text_oracle = row["label_side_expected"].get("phase_o_span_text_oracle", [])
        if len(text_oracle) != len(spans):
            failures.append(f"span_text_oracle_count_mismatch:{sample_id}")
        for index, span in enumerate(spans, start=1):
            start = int(span["start_char"])
            end = int(span["end_char"])
            if question[start:end] != text_oracle[index - 1]["text"]:
                failures.append(f"python_slice_text_mismatch:{sample_id}:SPAN_{index}")
            text = question[start:end]
            if text != text.strip():
                failures.append(f"span_contains_surrounding_whitespace:{sample_id}:SPAN_{index}")
            if text.startswith(("(", "\"", "'")) or text.endswith((".", ",", ":", ")", "\"", "'")):
                failures.append(f"span_contains_surrounding_punctuation:{sample_id}:SPAN_{index}")

        schema = row["model_side_input"]["schema_inventory"]
        table_refs = [item.get("table_ref") for item in schema.get("tables", [])]
        column_refs = [item.get("column_ref") for item in schema.get("columns", [])]
        if table_refs != ["TAB_1"]:
            failures.append(f"table_refs_not_v2:{sample_id}")
        if column_refs != [f"COL_{index}" for index in range(1, len(column_refs) + 1)]:
            failures.append(f"column_refs_not_v2:{sample_id}")

        phase_m = row["label_side_expected"]["phase_m"]
        assignments = phase_m.get("assignments", [])
        for index, item in enumerate(assignments, start=1):
            expected = {"slot_ref": f"SLOT_{index}", "evidence_ref": f"EV_{index}", "column_ref": f"COL_{index}"}
            if item != expected:
                failures.append(f"phase_m_assignment_ref_mismatch:{sample_id}:{index}")
        if any(key in phase_m for key in ("write_groups", "plan_kind", "version")):
            failures.append(f"legacy_phase_m_representation_present:{sample_id}")

        db_path = stage_dir / row["synthetic_db_spec"]["sqlite_db_path"]
        if not db_path.is_file():
            failures.append(f"missing_sqlite_db:{sample_id}")
            continue
        if sha256_file(db_path) != row["synthetic_db_spec"]["sqlite_db_sha256"]:
            failures.append(f"sqlite_db_hash_mismatch:{sample_id}")
        try:
            build_schema_inventory(row["model_side_input"])
            oracle = oracle_v2_path(row, db_path)
        except Exception as exc:  # pragma: no cover - failure captured for reviewer
            failures.append(f"v2_oracle_exception:{sample_id}:{exc}")
            continue
        locked = oracle_by_id.get(sample_id)
        if locked != oracle:
            failures.append(f"oracle_result_mismatch:{sample_id}")
        if oracle.get("preflight") == "ADMITTED":
            admitted_count += 1
        for key in (
            "phase_o_operation_exact",
            "phase_o_no_extra_spans",
            "phase_m_mapping_exact",
            "canonical_target_state_exact",
        ):
            if oracle.get(key) is not True:
                failures.append(f"oracle_{key}_not_true:{sample_id}")
        for key in ("deterministic_span_validation", "slot_ev_coherence", "typed_materialization", "completeness", "compilation"):
            if oracle.get(key) != "PASS":
                failures.append(f"oracle_{key}_not_pass:{sample_id}")
        if oracle.get("preflight") != "ADMITTED":
            failures.append(f"oracle_preflight_not_admitted:{sample_id}")
    return len(rows), span_count, admitted_count


def validate_manifests(stage_dir: Path, failures: list[str]) -> None:
    lock = read_json(stage_dir / "STAGE7C_A3_LOCK.json")
    derived_manifest = read_json(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json")
    rendered = read_json(stage_dir / "A3_RENDERED_PHASE_O_PROMPT_SMOKE.json")
    policy = read_json(stage_dir / "ACCEPTANCE_POLICY_A3.json")
    if lock.get("model_called") is not False or lock.get("gpu_called") is not False:
        failures.append("model_or_gpu_not_false")
    if lock.get("gretel_pilot_opened") is not False:
        failures.append("gretel_pilot_opened_not_false")
    if lock.get("phase_o_prompt_spec_path") != A3_PROMPT_SPEC_PATH:
        failures.append("lock_phase_o_prompt_spec_path_mismatch")
    if lock.get("changed_component") != "Phase O user prompt template only":
        failures.append("lock_changed_component_mismatch")
    if policy["primary_stage7e0_a3_acceptance"].get("required_pass_count") != "8/8":
        failures.append("acceptance_not_locked_8_of_8")
    if policy["primary_stage7e0_a3_acceptance"].get("seven_of_eight_allowed") is not False:
        failures.append("acceptance_allows_7_of_8")
    if rendered.get("amendment_present") is not True:
        failures.append("rendered_phase_o_prompt_missing_amendment")

    manifest_by_path = {row["path"]: row for row in derived_manifest.get("artifacts", [])}
    for artifact in derived_manifest.get("artifacts", []):
        path = stage_dir / artifact["path"]
        if not path.exists():
            failures.append(f"derived_artifact_missing:{artifact['path']}")
        elif sha256_file(path) != artifact.get("sha256"):
            failures.append(f"derived_artifact_hash_mismatch:{artifact['path']}")
    for name in SCIENTIFIC_ARTIFACTS:
        if name not in manifest_by_path:
            failures.append(f"derived_manifest_missing_artifact:{name}")
    if lock.get("derived_artifact_manifest_sha256") != sha256_file(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json"):
        failures.append("lock_derived_manifest_hash_mismatch")
    if derived_manifest.get("combined_scientific_artifacts_sha256") != sha256_text(
        canonical_json(derived_manifest.get("artifacts", []))
    ):
        failures.append("combined_scientific_artifacts_hash_mismatch")


def validate(stage_dir: Path) -> dict[str, Any]:
    failures: list[str] = []
    for name in REQUIRED_FILES:
        if not (stage_dir / name).is_file():
            failures.append(f"missing_required_file:{name}")
    for relative in (A2_PROMPT_SPEC_PATH, A1_PHASE_M_PROMPT_SPEC_PATH, A1_OFFSET_GUIDE_SPEC_PATH):
        if not (PROJECT_ROOT / relative).is_file():
            failures.append(f"missing_upstream_file:{relative}")
    if failures:
        return {"status": "FAIL", "failures": failures}

    validate_prompt_contract(stage_dir, failures)
    case_count, span_count, admitted_count = validate_smoke_rows(stage_dir, failures)
    validate_manifests(stage_dir, failures)

    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "fresh_english_case_count": case_count,
        "expected_span_count": span_count,
        "v2_oracle_admitted": admitted_count,
        "sqlite_db_count": sum(1 for row in read_jsonl(stage_dir / "SYNTHETIC_SQLITE_DB_MANIFEST.jsonl") if (stage_dir / row["sqlite_db_path"]).is_file()),
        "model_called": read_json(stage_dir / "STAGE7C_A3_LOCK.json").get("model_called"),
        "gpu_called": read_json(stage_dir / "STAGE7C_A3_LOCK.json").get("gpu_called"),
        "gretel_pilot_opened": read_json(stage_dir / "STAGE7C_A3_LOCK.json").get("gretel_pilot_opened"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    args = parser.parse_args()
    result = validate(args.stage_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
