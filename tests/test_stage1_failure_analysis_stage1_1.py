from __future__ import annotations

import csv
import importlib.util
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = (
    REPO_ROOT
    / "04_results"
    / "mp_fs_plus_failure_analysis_v1"
    / "stage1_mpfsplus_failure_analysis"
    / "analysis_code"
)


def _load_module(name: str, filename: str):
    path = ANALYSIS_DIR / filename
    sys.path.insert(0, str(ANALYSIS_DIR))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v0_recovery_uses_neutral_materialization_label() -> None:
    analysis = _load_module("stage1_failure_analysis_test_labels", "stage1_failure_analysis.py")
    value = analysis.root_cause_for(
        "materialization",
        "VALUE_MISSING",
        {},
        {},
        True,
    )
    assert value == "BYPASS_RECOVERABLE_MATERIALIZATION"
    assert "VERIFIER_OVER_REJECTION" not in value


def test_downstream_bypass_is_summarized_by_first_failure_stage() -> None:
    analysis = _load_module("stage1_failure_analysis_test_bypass", "stage1_failure_analysis.py")
    rows = []
    for stage, total, recoverable in [
        ("reference_resolution", 35, 0),
        ("materialization", 44, 22),
        ("verification", 44, 0),
    ]:
        for index in range(total):
            rows.append(
                {
                    "sample_id": f"{stage}_{index}",
                    "first_failure_stage": stage,
                    "failure_reason_code": "X",
                    "oracle_if_bypassed_correct": 1 if index < recoverable else 0,
                }
            )
    _, summary = analysis.build_downstream_bypass_analysis(rows)
    keyed = {row["First failure stage"]: row for row in summary}
    assert keyed["reference_resolution"]["Bypass-correct"] == 0
    assert keyed["materialization"]["Bypass-correct"] == 22
    assert keyed["verification"]["Bypass-correct"] == 0
    assert all(row["Causal scope"] == "system-level V0 bypass" for row in summary)


def test_state_diff_replay_classifies_wrong_value(tmp_path: Path) -> None:
    state_diff = _load_module("state_diff_audit_test", "state_diff_audit.py")
    database = tmp_path / "demo.sqlite"
    conn = sqlite3.connect(database)
    conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT)")
    conn.commit()
    conn.close()

    holdout_zip = tmp_path / "holdout.zip"
    with zipfile.ZipFile(holdout_zip, "w") as archive:
        archive.write(database, "release/databases/demo/demo.sqlite")

    sample = {
        "id": "sample_1",
        "db_id": "demo",
        "gold_sql": ["INSERT INTO users(id, name) VALUES (1, 'Alice')"],
        "gold_tables": ["users"],
        "conflict_sensitive": False,
    }
    compiled = {
        "status": "success",
        "statements": [
            {
                "sql": "INSERT INTO users(id, name) VALUES (?, ?)",
                "params": [1, "Bob"],
                "group_id": "g1",
                "table": "users",
            }
        ],
    }
    result = state_diff.replay_state_diff(sample, compiled, holdout_zip)
    assert result["primary_class"] == "STATE_WRONG_VALUE"
    assert result["difference"]["users"]["missing_count"] == 1
    assert result["difference"]["users"]["extra_count"] == 1


def test_completed_manual_audit_requires_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analysis = _load_module("stage1_failure_analysis_test_manual", "stage1_failure_analysis.py")
    decisions = tmp_path / "manual.csv"
    with decisions.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "sample_id",
                "manual_review_status",
                "reviewer_root_cause",
                "conflict_ambiguity_gold_label",
                "manual_review_notes",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "sample_id": "s1",
                "manual_review_status": "COMPLETED",
                "reviewer_root_cause": "CONTROL_FIELD_POLICY_ERROR",
                "conflict_ambiguity_gold_label": "",
                "manual_review_notes": "",
            }
        )
    monkeypatch.setattr(analysis, "MANUAL_AUDIT_DECISIONS", decisions)
    with pytest.raises(ValueError, match="requires non-empty manual_review_notes"):
        analysis.load_manual_audit_decisions()


def test_candidate_fix_counts_semantic_and_preflight_by_stage() -> None:
    analysis = _load_module("stage1_failure_analysis_test_candidate", "stage1_failure_analysis.py")
    rows = [
        {
            "target_state_correct": 0,
            "failure_reason_code": "RISK_TRUE_REJECT",
            "first_failure_stage": "semantic_gate",
            "systematic_audit_tags": "",
        },
        {
            "target_state_correct": 0,
            "failure_reason_code": "PREFLIGHT_UNIQUE",
            "first_failure_stage": "preflight",
            "systematic_audit_tags": "",
        },
        {
            "target_state_correct": 0,
            "failure_reason_code": "PREFLIGHT_FK",
            "first_failure_stage": "preflight",
            "systematic_audit_tags": "",
        },
    ]
    text = analysis.build_candidate_fixes(rows)
    section = text.split("## Issue ID: MPF-ERR-005", 1)[1].split("## Issue ID: MPF-ERR-006", 1)[0]
    assert "Affected samples: 3" in section


def test_manual_audit_loader_accepts_utf8_bom(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analysis = _load_module("stage1_failure_analysis_test_bom", "stage1_failure_analysis.py")
    decisions = tmp_path / "manual_bom.csv"
    with decisions.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "sample_id",
                "manual_review_status",
                "reviewer_root_cause",
                "conflict_ambiguity_gold_label",
                "manual_review_notes",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "sample_id": "s1",
                "manual_review_status": "COMPLETED",
                "reviewer_root_cause": "CONTROL_FIELD_POLICY_ERROR",
                "conflict_ambiguity_gold_label": "",
                "manual_review_notes": "Operation is control metadata.",
            }
        )
    monkeypatch.setattr(analysis, "MANUAL_AUDIT_DECISIONS", decisions)
    loaded = analysis.load_manual_audit_decisions()
    assert list(loaded) == ["s1"]
    assert loaded["s1"]["manual_review_status"] == "COMPLETED"


def test_completed_manual_audit_requires_root_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analysis = _load_module(
        "stage1_failure_analysis_test_manual_root",
        "stage1_failure_analysis.py",
    )
    decisions = tmp_path / "manual_missing_root.csv"
    with decisions.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "sample_id",
                "manual_review_status",
                "reviewer_root_cause",
                "conflict_ambiguity_gold_label",
                "manual_review_notes",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "sample_id": "s1",
                "manual_review_status": "COMPLETED",
                "reviewer_root_cause": "",
                "conflict_ambiguity_gold_label": "",
                "manual_review_notes": "Reviewed with evidence.",
            }
        )
    monkeypatch.setattr(analysis, "MANUAL_AUDIT_DECISIONS", decisions)
    with pytest.raises(ValueError, match="requires non-empty reviewer_root_cause"):
        analysis.load_manual_audit_decisions()


def test_reviewed_root_cause_summary_uses_manual_override() -> None:
    analysis = _load_module(
        "stage1_failure_analysis_test_reviewed_root_summary",
        "stage1_failure_analysis.py",
    )
    rows = [
        {
            "target_state_correct": 0,
            "root_cause": "REPRESENTATION_LIMITATION",
            "manual_review_status": "COMPLETED",
            "reviewer_root_cause": "CONFLICT_SEMANTICS_PLANNING_ERROR",
        },
        {
            "target_state_correct": 0,
            "root_cause": "GROUNDING_ERROR",
            "manual_review_status": "NOT_REQUIRED",
            "reviewer_root_cause": "",
        },
        {
            "target_state_correct": 1,
            "root_cause": "NONE",
            "manual_review_status": "NOT_REQUIRED",
            "reviewer_root_cause": "",
        },
    ]
    summary = analysis.build_reviewed_root_cause_summary(rows)
    keyed = {row["Root cause"]: row["N incorrect"] for row in summary}
    assert keyed == {
        "CONFLICT_SEMANTICS_PLANNING_ERROR": 1,
        "GROUNDING_ERROR": 1,
    }

    auto = analysis.build_root_cause_summary_auto(rows)
    auto_keyed = {row["Root cause"]: row["N incorrect"] for row in auto}
    assert auto_keyed == {
        "GROUNDING_ERROR": 1,
        "REPRESENTATION_LIMITATION": 1,
    }


def _trace_row(first_failure_stage: str, failure_reason_code: str) -> dict[str, object]:
    stages = {
        "generation_ok": "1",
        "parse_ok": "1",
        "reference_resolution_ok": "1",
        "materialization_ok": "1",
        "verification_ok": "1",
        "compilation_ok": "1",
        "semantic_gate_ok": "1",
        "preflight_ok": "1",
        "admission_ok": "1",
        "execution_ok": "1",
        "state_correct": "1",
    }
    ordered_failure_columns = {
        "generation": "generation_ok",
        "parse": "parse_ok",
        "reference_resolution": "reference_resolution_ok",
        "materialization": "materialization_ok",
        "verification": "verification_ok",
        "compilation": "compilation_ok",
        "semantic_gate": "semantic_gate_ok",
        "preflight": "preflight_ok",
        "execution": "execution_ok",
        "state_mismatch": "state_correct",
    }
    failure_column = ordered_failure_columns[first_failure_stage]
    stages[failure_column] = "0"

    # Downstream stages are not run for pre-execution failures.
    ordered_columns = list(ordered_failure_columns.values())
    if first_failure_stage != "state_mismatch":
        failure_index = ordered_columns.index(failure_column)
        for column in ordered_columns[failure_index + 1 :]:
            stages[column] = "NA"
        if first_failure_stage not in {"preflight", "execution"}:
            stages["admission_ok"] = "NA"

    return {
        "sample_id": f"sample_{first_failure_stage}",
        **stages,
        "first_failure_stage": first_failure_stage,
        "failure_reason_code": failure_reason_code,
        "admitted": 1 if first_failure_stage == "state_mismatch" else 0,
        "target_state_correct": 0,
    }


def test_parse_trace_has_failure_code() -> None:
    analysis = _load_module(
        "stage1_failure_analysis_test_parse_trace",
        "stage1_failure_analysis.py",
    )
    [trace] = analysis.build_diagnostic_traces(
        [_trace_row("parse", "GEN_UNPARSEABLE_OUTPUT")]
    )
    assert trace["stages"]["parsing"]["status"] == "fail"
    assert trace["stages"]["parsing"]["code"] == "GEN_UNPARSEABLE_OUTPUT"


def test_state_mismatch_trace_has_failure_code() -> None:
    analysis = _load_module(
        "stage1_failure_analysis_test_state_trace",
        "stage1_failure_analysis.py",
    )
    [trace] = analysis.build_diagnostic_traces(
        [_trace_row("state_mismatch", "STATE_WRONG_VALUE")]
    )
    assert trace["stages"]["state_comparison"]["status"] == "fail"
    assert trace["stages"]["state_comparison"]["code"] == "STATE_WRONG_VALUE"
