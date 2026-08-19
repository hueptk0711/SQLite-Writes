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
