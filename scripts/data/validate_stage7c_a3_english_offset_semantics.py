#!/usr/bin/env python3
"""Validate Stage7C A3 English offset-semantics amendment artifacts."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stage7c_a3_english_offset_semantics import (
    MODEL_ID,
    MODEL_REVISION,
    PHASE_O_OFFSET_SEMANTICS_AMENDMENT,
    REQUIRED_PROMPT_SOURCE_SNIPPETS,
    SCIENTIFIC_ARTIFACTS,
    STAGE_NAME,
    canonical_json,
    sha256_file,
    sha256_text,
)


REQUIRED_FILES = [
    *SCIENTIFIC_ARTIFACTS,
    "DERIVED_ARTIFACT_MANIFEST.json",
    "STAGE7C_SMOKE_LOCK.json",
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


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def target_state(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
    return [dict(row) for row in rows]


def validate(stage_dir: Path) -> dict[str, Any]:
    failures: list[str] = []
    for name in REQUIRED_FILES:
        if not (stage_dir / name).is_file():
            failures.append(f"missing_required_file:{name}")
    prompt_source = PROJECT_ROOT / "src" / "nldbwrite_v3" / "planner" / "prompt.py"
    if not prompt_source.is_file():
        failures.append("missing_prompt_source:src/nldbwrite_v3/planner/prompt.py")
    if failures:
        return {"status": "FAIL", "failures": failures}

    amendment = (stage_dir / "PHASE_O_PROMPT_AMENDMENT.md").read_text(encoding="utf-8")
    prompt_audit = read_json(stage_dir / "PHASE_O_PROMPT_AUDIT.json")
    cases = read_jsonl(stage_dir / "FRESH_ENGLISH_SYNTHETIC_CASES.jsonl")
    spans = read_jsonl(stage_dir / "PHASE_O_EXPECTED_SPANS.jsonl")
    mappings = read_jsonl(stage_dir / "PHASE_M_EXPECTED_MAPPINGS.jsonl")
    states = read_jsonl(stage_dir / "TYPED_TARGET_STATES.jsonl")
    db_manifest = read_jsonl(stage_dir / "SYNTHETIC_SQLITE_DB_MANIFEST.jsonl")
    lock = read_json(stage_dir / "STAGE7C_SMOKE_LOCK.json")
    derived_manifest = read_json(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json")
    prompt_text = prompt_source.read_text(encoding="utf-8")

    if amendment.strip() != PHASE_O_OFFSET_SEMANTICS_AMENDMENT.strip():
        failures.append("phase_o_amendment_text_mismatch")
    for required in REQUIRED_PROMPT_SOURCE_SNIPPETS:
        if required not in prompt_text:
            failures.append(f"prompt_source_missing_required_offset_text:{required}")
    if prompt_audit.get("amendment_present_in_prompt_source") is not True:
        failures.append("prompt_audit_does_not_record_amendment_present")
    if prompt_audit.get("required_prompt_source_snippets") != REQUIRED_PROMPT_SOURCE_SNIPPETS:
        failures.append("prompt_audit_required_snippets_mismatch")
    if prompt_audit.get("prompt_source_sha256") != sha256_file(prompt_source):
        failures.append("prompt_source_hash_mismatch")
    if prompt_audit.get("unchanged_protocol", {}).get("model_id") != MODEL_ID:
        failures.append("model_id_changed")
    if prompt_audit.get("unchanged_protocol", {}).get("model_revision") != MODEL_REVISION:
        failures.append("model_revision_changed")
    if prompt_audit.get("unchanged_protocol", {}).get("zero_shot") is not True:
        failures.append("zero_shot_not_locked_true")
    if prompt_audit.get("unchanged_protocol", {}).get("retry") != 0:
        failures.append("retry_not_locked_zero")
    if prompt_audit.get("unchanged_protocol", {}).get("repair") != "none":
        failures.append("repair_not_locked_none")
    if lock.get("model_called") is not False or lock.get("gpu_called") is not False:
        failures.append("model_or_gpu_not_false")
    if lock.get("gretel_pilot_opened") is not False:
        failures.append("gretel_pilot_opened_not_false")

    case_by_id = {row["case_id"]: row for row in cases}
    spans_by_case: dict[str, list[dict[str, Any]]] = {}
    for span in spans:
        spans_by_case.setdefault(str(span["case_id"]), []).append(span)
    mappings_by_case = {row["case_id"]: row for row in mappings}
    states_by_case = {row["case_id"]: row for row in states}
    db_by_case = {row["case_id"]: row for row in db_manifest}
    if len(cases) != 8:
        failures.append("fresh_case_count_mismatch")
    if len(case_by_id) != len(cases):
        failures.append("duplicate_case_id")
    if any(str(case_id).startswith("gretel:") for case_id in case_by_id):
        failures.append("gretel_case_id_present")

    coverage_tags = {tag for row in cases for tag in row.get("coverage_tags", [])}
    missing_tags = sorted(REQUIRED_COVERAGE_TAGS - coverage_tags)
    if missing_tags:
        failures.append(f"coverage_tags_missing:{','.join(missing_tags)}")
    value_counts = sorted({int(row.get("value_count", 0)) for row in cases})
    if value_counts != [2, 3, 4, 5]:
        failures.append("value_count_coverage_mismatch")

    for case in cases:
        case_id = str(case["case_id"])
        request = str(case["request"])
        case_spans = spans_by_case.get(case_id, [])
        if len(case_spans) != int(case.get("value_count", -1)):
            failures.append(f"span_count_mismatch:{case_id}")
        for span in case_spans:
            start = int(span["start_char"])
            end = int(span["end_char"])
            text = str(span["text"])
            if start < 0 or end <= start or end > len(request):
                failures.append(f"invalid_span_bounds:{case_id}:{span['value_id']}")
                continue
            if request[start:end] != text:
                failures.append(f"python_slice_text_mismatch:{case_id}:{span['value_id']}")
            if span.get("selected_text") != text:
                failures.append(f"selected_text_mismatch:{case_id}:{span['value_id']}")
            if text != text.strip():
                failures.append(f"span_contains_surrounding_whitespace:{case_id}:{span['value_id']}")
            if text.endswith((".", ",", ":", ")", "\"", "'")):
                failures.append(f"span_contains_trailing_punctuation:{case_id}:{span['value_id']}")
            if text.startswith(("(", "\"", "'")):
                failures.append(f"span_contains_leading_punctuation:{case_id}:{span['value_id']}")

        mapping = mappings_by_case.get(case_id)
        if not mapping:
            failures.append(f"missing_phase_m_mapping:{case_id}")
        else:
            row = mapping.get("phase_m_expected_mapping", {}).get("write_groups", [{}])[0].get("rows", [{}])[0]
            value_refs = [cell.get("value_from") for cell in row.values() if isinstance(cell, dict)]
            expected_refs = [span["value_id"] for span in case_spans]
            if value_refs != expected_refs:
                failures.append(f"phase_m_value_refs_do_not_match_spans:{case_id}")

        state = states_by_case.get(case_id)
        db_info = db_by_case.get(case_id)
        if not state or not db_info:
            failures.append(f"missing_state_or_db_manifest:{case_id}")
            continue
        db_path = stage_dir / str(db_info["sqlite_db_path"])
        if not db_path.is_file():
            failures.append(f"missing_sqlite_db:{case_id}")
            continue
        if sha256_file(db_path) != db_info.get("sqlite_db_sha256"):
            failures.append(f"sqlite_db_hash_mismatch:{case_id}")
        with sqlite3.connect(db_path) as source, sqlite3.connect(":memory:") as connection:
            source.backup(connection)
            connection.execute(str(db_info["gold_insert_sql"]), db_info.get("gold_insert_params", []))
            connection.commit()
            observed = target_state(connection, str(state["table"]))
        if observed != state.get("typed_target_rows"):
            failures.append(f"typed_target_state_mismatch:{case_id}")
        if sha256_text(canonical_json(observed)) != state.get("target_state_hash"):
            failures.append(f"target_state_hash_mismatch:{case_id}")

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

    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "fresh_english_case_count": len(cases),
        "expected_span_count": len(spans),
        "sqlite_db_count": sum(1 for row in db_manifest if (stage_dir / str(row.get("sqlite_db_path"))).is_file()),
        "model_called": lock.get("model_called"),
        "gpu_called": lock.get("gpu_called"),
        "gretel_pilot_opened": lock.get("gretel_pilot_opened"),
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
