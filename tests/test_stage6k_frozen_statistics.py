from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path

import pytest

from scripts.data.build_stage6k_frozen_statistics import build_stage6k
from scripts.data.validate_stage6k_frozen_statistics import (
    BOOTSTRAP_REPLICATES,
    BOOTSTRAP_SEED,
    CLUSTER_KEY,
    FINAL_N,
    cluster_bootstrap_recompute,
    holm_recompute,
    mcnemar_recompute,
    validate,
)

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "stage6_frozen_statistical_analysis"
STAGE6J_DIR = ROOT / "stage6_replay_evaluation"
STAGE6E_DIR = ROOT / "stage6_final_registration_revision"
TEST_TMP_ROOT = ROOT / "test_tmp" / "stage6k_tests"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def workspace_tmp(request) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)
    target = TEST_TMP_ROOT / f"{safe_name}_{uuid.uuid4().hex}"
    target.mkdir(parents=True, exist_ok=False)
    try:
        yield target
    finally:
        resolved = target.resolve()
        if TEST_TMP_ROOT.resolve() in resolved.parents and resolved.exists():
            shutil.rmtree(resolved)


def _copy_stage6k(workspace_tmp: Path) -> Path:
    target = workspace_tmp / "stage6k"
    shutil.copytree(ARTIFACT_DIR, target)
    return target


def _copy_minimal_stage6j(workspace_tmp: Path) -> Path:
    target = workspace_tmp / "stage6j"
    for rel in (
        "STAGE6J_REPLAY_EVALUATION_LOCK.json",
        "REPLAY_ARM_MANIFEST.json",
        "REPLAY_EVALUATION_SUMMARY.json",
        "replay_outcomes/direct.jsonl",
        "replay_outcomes/j_fs.jsonl",
        "replay_outcomes/original_mp_fs_plus.jsonl",
        "replay_outcomes/d_g1_control.jsonl",
        "replay_outcomes/d_f_g1_vnext.jsonl",
    ):
        source = STAGE6J_DIR / rel
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    return target


def _copy_minimal_stage6e(workspace_tmp: Path) -> Path:
    target = workspace_tmp / "stage6e"
    for rel in (
        "STAGE6E_FINAL_REGISTRATION_LOCK.json",
        "artifacts/FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl",
    ):
        source = STAGE6E_DIR / rel
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    return target


def test_stage6k_artifact_validates() -> None:
    report = validate(ARTIFACT_DIR)
    assert report["status"] == "PASS"
    assert report["violations"] == []
    assert report["final_n"] == FINAL_N
    assert report["model_called"] is False
    assert report["gpu_called"] is False


def test_exact_481_ids_pass() -> None:
    rows = _read_jsonl(ARTIFACT_DIR / "PAIRED_OUTCOME_TABLE.jsonl")
    assert len(rows) == 481
    assert len({row["stage6_sample_id"] for row in rows}) == 481
    assert all(row["source_group"] for row in rows)


def test_missing_one_id_fails(workspace_tmp: Path) -> None:
    artifact = _copy_stage6k(workspace_tmp)
    path = artifact / "PAIRED_OUTCOME_TABLE.jsonl"
    rows = _read_jsonl(path)
    _write_jsonl(path, rows[:-1])
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert any(item.startswith("paired_table_row_count:480") for item in report["violations"])


def test_extra_id_fails(workspace_tmp: Path) -> None:
    artifact = _copy_stage6k(workspace_tmp)
    path = artifact / "PAIRED_OUTCOME_TABLE.jsonl"
    rows = _read_jsonl(path)
    extra = dict(rows[0])
    extra["stage6_sample_id"] = "stage6_crudsql_extra"
    rows.append(extra)
    _write_jsonl(path, rows)
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert any(item.startswith("paired_table_row_count:482") for item in report["violations"])


def test_duplicate_id_fails(workspace_tmp: Path) -> None:
    artifact = _copy_stage6k(workspace_tmp)
    path = artifact / "PAIRED_OUTCOME_TABLE.jsonl"
    rows = _read_jsonl(path)
    rows[1]["stage6_sample_id"] = rows[0]["stage6_sample_id"]
    _write_jsonl(path, rows)
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "paired_table_duplicate_ids" in report["violations"]


def test_stage6j_input_hash_changed_fails(workspace_tmp: Path) -> None:
    artifact = _copy_stage6k(workspace_tmp)
    stage6j = _copy_minimal_stage6j(workspace_tmp)
    direct = stage6j / "replay_outcomes" / "direct.jsonl"
    rows = _read_jsonl(direct)
    rows[0]["target_state_correct"] = not rows[0]["target_state_correct"]
    _write_jsonl(direct, rows)
    report = validate(artifact, stage6j, STAGE6E_DIR)
    assert report["status"] == "FAIL"
    assert "manifest_input_hashes_mismatch" in report["violations"]
    assert any(item.startswith("stage6j_arm_manifest_outcome_hash_mismatch:direct") for item in report["violations"])


def test_missing_source_group_fails(workspace_tmp: Path) -> None:
    artifact = _copy_stage6k(workspace_tmp)
    path = artifact / "PAIRED_OUTCOME_TABLE.jsonl"
    rows = _read_jsonl(path)
    rows[0]["source_group"] = ""
    _write_jsonl(path, rows)
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "paired_table_missing_source_group" in report["violations"]


def test_stage6e_missing_source_group_fails(workspace_tmp: Path) -> None:
    artifact = _copy_stage6k(workspace_tmp)
    stage6e = _copy_minimal_stage6e(workspace_tmp)
    manifest = stage6e / "artifacts" / "FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl"
    rows = _read_jsonl(manifest)
    rows[0]["source_group"] = ""
    _write_jsonl(manifest, rows)
    report = validate(artifact, STAGE6J_DIR, stage6e)
    assert report["status"] == "FAIL"
    assert "stage6e_final_manifest_missing_source_group" in report["violations"]
    assert "manifest_input_hashes_mismatch" in report["violations"]


def test_outcome_boolean_inconsistent_with_frozen_replay_fails(workspace_tmp: Path) -> None:
    artifact = _copy_stage6k(workspace_tmp)
    path = artifact / "PAIRED_OUTCOME_TABLE.jsonl"
    rows = _read_jsonl(path)
    rows[0]["direct_correct"] = not rows[0]["direct_correct"]
    _write_jsonl(path, rows)
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "paired_table_recompute_mismatch" in report["violations"]


def test_zero_discordant_mcnemar_returns_one() -> None:
    result = mcnemar_recompute([False, False, False], [False, False, False], "H1", "D+F+G1", "Original MP-FS+")
    assert result["discordant_pairs"] == 0
    assert result["degenerate_no_discordant_pairs"] is True
    assert result["raw_p_value"] == 1.0


def test_holm_h1_h2_p_equals_one() -> None:
    h1 = mcnemar_recompute([False], [False], "H1", "D+F+G1", "Original MP-FS+")
    h2 = mcnemar_recompute([False], [False], "H2", "D+F+G1", "D+G1")
    result = holm_recompute([h1, h2])
    assert result["confirmatory_family"] == ["H1", "H2"]
    assert [row["holm_adjusted_p_value"] for row in result["results"]] == [1.0, 1.0]
    assert [row["reject"] for row in result["results"]] == [False, False]


def test_bootstrap_same_seed_identical_result() -> None:
    rows = [
        {
            "stage6_sample_id": f"s{i}",
            "source_group": f"g{i % 2}",
            "direct_correct": False,
            "j_fs_correct": False,
            "original_mp_fs_plus_correct": False,
            "d_g1_correct": False,
            "d_f_g1_correct": False,
        }
        for i in range(6)
    ]
    assert cluster_bootstrap_recompute(rows) == cluster_bootstrap_recompute(rows)


def test_artifact_modification_lock_validation_fails(workspace_tmp: Path) -> None:
    artifact = _copy_stage6k(workspace_tmp)
    path = artifact / "SECONDARY_RESULTS.json"
    payload = _read_json(path)
    payload["arms"][0]["correct"] += 1
    _write_json(path, payload)
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "stage6k_lock_artifact_hashes_mismatch" in report["violations"]


def test_mcnemar_artifact_modification_recompute_fails_even_if_lock_hash_updated(workspace_tmp: Path) -> None:
    artifact = _copy_stage6k(workspace_tmp)
    path = artifact / "MCNEMAR_H1.json"
    payload = _read_json(path)
    payload["raw_p_value"] = 0.5
    _write_json(path, payload)
    lock_path = artifact / "STAGE6K_STATISTICAL_LOCK.json"
    lock = _read_json(lock_path)
    lock["artifact_hashes"]["MCNEMAR_H1.json"] = _sha256_file(path)
    _write_json(lock_path, lock)
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "mcnemar_h1_recompute_mismatch" in report["violations"]


def test_confirmatory_family_only_h1_h2(workspace_tmp: Path) -> None:
    artifact = _copy_stage6k(workspace_tmp)
    path = artifact / "HOLM_CORRECTION.json"
    payload = _read_json(path)
    payload["confirmatory_family"].append("H3")
    payload["family_size"] = 3
    _write_json(path, payload)
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "confirmatory_family_not_h1_h2_only" in report["violations"]


def test_bootstrap_replicates_must_be_10000(workspace_tmp: Path) -> None:
    artifact = _copy_stage6k(workspace_tmp)
    path = artifact / "CLUSTER_BOOTSTRAP.json"
    payload = _read_json(path)
    payload["bootstrap_replicates"] = 9999
    _write_json(path, payload)
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "bootstrap_replicates_mismatch" in report["violations"]
    assert BOOTSTRAP_REPLICATES == 10000


def test_bootstrap_seed_must_be_240824(workspace_tmp: Path) -> None:
    artifact = _copy_stage6k(workspace_tmp)
    path = artifact / "CLUSTER_BOOTSTRAP.json"
    payload = _read_json(path)
    payload["bootstrap_seed"] = 1
    _write_json(path, payload)
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "bootstrap_seed_mismatch" in report["violations"]
    assert BOOTSTRAP_SEED == 240824


def test_cluster_key_must_be_source_group(workspace_tmp: Path) -> None:
    artifact = _copy_stage6k(workspace_tmp)
    path = artifact / "CLUSTER_BOOTSTRAP.json"
    payload = _read_json(path)
    payload["cluster_key"] = "stage6_sample_id"
    _write_json(path, payload)
    report = validate(artifact)
    assert report["status"] == "FAIL"
    assert "bootstrap_cluster_key_mismatch" in report["violations"]
    assert CLUSTER_KEY == "source_group"


def test_no_model_or_gpu_call_in_stage6k_scripts() -> None:
    combined = "\n".join(
        [
            (ROOT / "scripts" / "data" / "build_stage6k_frozen_statistics.py").read_text(encoding="utf-8").casefold(),
            (ROOT / "scripts" / "data" / "validate_stage6k_frozen_statistics.py").read_text(encoding="utf-8").casefold(),
        ]
    )
    forbidden = ("torch", "cuda", "transformers", "openai", "model.generate")
    assert all(token not in combined for token in forbidden)


def test_builder_does_not_rewrite_stage6j_files(workspace_tmp: Path) -> None:
    tracked = [
        STAGE6J_DIR / "STAGE6J_REPLAY_EVALUATION_LOCK.json",
        STAGE6J_DIR / "REPLAY_ARM_MANIFEST.json",
        STAGE6J_DIR / "REPLAY_EVALUATION_SUMMARY.json",
        STAGE6J_DIR / "replay_outcomes" / "direct.jsonl",
        STAGE6J_DIR / "replay_outcomes" / "j_fs.jsonl",
        STAGE6J_DIR / "replay_outcomes" / "original_mp_fs_plus.jsonl",
        STAGE6J_DIR / "replay_outcomes" / "d_g1_control.jsonl",
        STAGE6J_DIR / "replay_outcomes" / "d_f_g1_vnext.jsonl",
    ]
    before = {path: _sha256_file(path) for path in tracked}
    build_stage6k(STAGE6J_DIR, STAGE6E_DIR, workspace_tmp / "stage6k_generated")
    after = {path: _sha256_file(path) for path in tracked}
    assert after == before
