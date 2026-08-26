from __future__ import annotations

import json
import hashlib
import shutil
import uuid
from pathlib import Path

from scripts.data.validate_stage6j_replay_evaluation import validate

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "stage6_replay_evaluation"
TEST_TMP_ROOT = ROOT / ".stage6j_test_tmp"


def _copy_artifact() -> Path:
    TEST_TMP_ROOT.mkdir(exist_ok=True)
    target = TEST_TMP_ROOT / f"stage6j_{uuid.uuid4().hex}"
    shutil.copytree(ARTIFACT_DIR, target)
    return target


def _cleanup(path: Path) -> None:
    if path.exists() and TEST_TMP_ROOT.resolve() in path.resolve().parents:
        shutil.rmtree(path)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mutate_first_outcome(
    artifact: Path,
    arm: str,
    mutator,
) -> dict:
    path = artifact / "replay_outcomes" / f"{arm}.jsonl"
    rows = _read_jsonl(path)
    mutator(rows[0])
    _write_jsonl(path, rows)
    return validate(artifact)


def test_stage6j_artifact_validates() -> None:
    report = validate(ARTIFACT_DIR)
    assert report["status"] == "PASS"
    assert report["model_called"] is False
    assert report["gpu_called"] is False


def test_duplicate_sample_id_fails() -> None:
    artifact = _copy_artifact()
    try:
        path = artifact / "replay_outcomes" / "direct.jsonl"
        rows = _read_jsonl(path)
        rows[1]["stage6_sample_id"] = rows[0]["stage6_sample_id"]
        rows[1]["sample_id"] = rows[0]["sample_id"]
        _write_jsonl(path, rows)
        report = validate(artifact)
        assert report["status"] == "FAIL"
        assert any("duplicate_sample_ids:direct" in item for item in report["violations"])
    finally:
        _cleanup(artifact)


def test_h2_shared_raw_row_mismatch_fails() -> None:
    artifact = _copy_artifact()
    try:
        path = artifact / "replay_outcomes" / "d_f_g1_vnext.jsonl"
        rows = _read_jsonl(path)
        rows[0]["shared_raw_generation_row_sha256"] = "0" * 64
        _write_jsonl(path, rows)
        report = validate(artifact)
        assert report["status"] == "FAIL"
        assert any(item.startswith("h2_shared_raw_row_mismatch:") for item in report["violations"])
    finally:
        _cleanup(artifact)


def test_mutate_target_state_correct_fails() -> None:
    artifact = _copy_artifact()
    try:
        report = _mutate_first_outcome(
            artifact,
            "direct",
            lambda row: row.update({"target_state_correct": not row["target_state_correct"]}),
        )
        assert report["status"] == "FAIL"
        assert any(item.startswith("outcome_target_state_correct_mismatch:direct:") for item in report["violations"])
    finally:
        _cleanup(artifact)


def test_mutate_gold_post_state_fails() -> None:
    artifact = _copy_artifact()
    try:
        report = _mutate_first_outcome(
            artifact,
            "direct",
            lambda row: row.update({"gold_post_state_sha256": "0" * 64}),
        )
        assert report["status"] == "FAIL"
        assert any(item.startswith("outcome_gold_post_state_mismatch:direct:") for item in report["violations"])
    finally:
        _cleanup(artifact)


def test_mutate_predicted_post_state_fails() -> None:
    artifact = _copy_artifact()
    try:
        def mutate(row: dict) -> None:
            row["predicted_post_state_sha256"] = row["gold_post_state_sha256"]

        report = _mutate_first_outcome(artifact, "direct", mutate)
        assert report["status"] == "FAIL"
        assert any(item.startswith("outcome_target_state_correct_mismatch:direct:") for item in report["violations"])
    finally:
        _cleanup(artifact)


def test_mutate_candidate_program_without_sha_fails() -> None:
    artifact = _copy_artifact()
    try:
        def mutate(row: dict) -> None:
            row["candidate_program"]["statements"][0] += " -- mutated"

        report = _mutate_first_outcome(artifact, "direct", mutate)
        assert report["status"] == "FAIL"
        assert any(item.startswith("outcome_candidate_program_hash_mismatch:direct:") for item in report["violations"])
    finally:
        _cleanup(artifact)


def test_mutate_source_raw_generation_row_sha_fails() -> None:
    artifact = _copy_artifact()
    try:
        report = _mutate_first_outcome(
            artifact,
            "direct",
            lambda row: row.update({"source_raw_generation_row_sha256": "0" * 64}),
        )
        assert report["status"] == "FAIL"
        assert any(item.startswith("outcome_source_raw_row_hash_mismatch:direct:") for item in report["violations"])
    finally:
        _cleanup(artifact)


def test_mutate_mirrored_stage6i_raw_jsonl_fails() -> None:
    artifact = _copy_artifact()
    try:
        path = artifact / "stage6i_generation_inputs" / "stage6_confirmation_run_outputs" / "raw_generations" / "direct.jsonl"
        rows = _read_jsonl(path)
        rows[0]["raw_output"] = rows[0]["raw_output"] + " -- mutated"
        _write_jsonl(path, rows)
        report = validate(artifact)
        assert report["status"] == "FAIL"
        assert "mirrored_raw_generation_hash_mismatch:direct" in report["violations"]
    finally:
        _cleanup(artifact)


def test_mutate_h2_shared_hash_in_both_arms_fails_against_raw() -> None:
    artifact = _copy_artifact()
    try:
        sample_id = None
        for arm in ("d_g1_control", "d_f_g1_vnext"):
            path = artifact / "replay_outcomes" / f"{arm}.jsonl"
            rows = _read_jsonl(path)
            sample_id = rows[0]["stage6_sample_id"]
            rows[0]["shared_raw_generation_row_sha256"] = "1" * 64
            _write_jsonl(path, rows)
        report = validate(artifact)
        assert report["status"] == "FAIL"
        assert f"h2_shared_raw_row_mismatch:{sample_id}" in report["violations"]
    finally:
        _cleanup(artifact)


def test_significance_outputs_are_forbidden() -> None:
    artifact = _copy_artifact()
    try:
        path = artifact / "REPLAY_EVALUATION_SUMMARY.json"
        summary = _read_json(path)
        summary["significance_tests_computed"] = True
        _write_json(path, summary)
        lock = _read_json(artifact / "STAGE6J_REPLAY_EVALUATION_LOCK.json")
        lock["summary_sha256"] = "0" * 64
        _write_json(artifact / "STAGE6J_REPLAY_EVALUATION_LOCK.json", lock)
        report = validate(artifact)
        assert report["status"] == "FAIL"
        assert "stage6j_must_not_compute_significance" in report["violations"]
    finally:
        _cleanup(artifact)


def test_raw_stream_hash_mutation_fails() -> None:
    artifact = _copy_artifact()
    try:
        path = artifact / "REPLAY_ARM_MANIFEST.json"
        manifest = _read_json(path)
        manifest["raw_stream_hashes"]["direct"] = "0" * 64
        _write_json(path, manifest)
        lock = _read_json(artifact / "STAGE6J_REPLAY_EVALUATION_LOCK.json")
        lock["arm_manifest_sha256"] = "0" * 64
        _write_json(artifact / "STAGE6J_REPLAY_EVALUATION_LOCK.json", lock)
        report = validate(artifact)
        assert report["status"] == "FAIL"
        assert "raw_stream_hashes_mismatch" in report["violations"]
    finally:
        _cleanup(artifact)


def test_mutate_summary_count_fails_even_if_lock_hash_updated() -> None:
    artifact = _copy_artifact()
    try:
        summary_path = artifact / "REPLAY_EVALUATION_SUMMARY.json"
        summary = _read_json(summary_path)
        summary["arms"]["direct"]["target_state_correct"] += 1
        _write_json(summary_path, summary)
        lock = _read_json(artifact / "STAGE6J_REPLAY_EVALUATION_LOCK.json")
        lock["summary_sha256"] = _sha256_file(summary_path)
        _write_json(artifact / "STAGE6J_REPLAY_EVALUATION_LOCK.json", lock)
        report = validate(artifact)
        assert report["status"] == "FAIL"
        assert "summary_recompute_mismatch" in report["violations"]
    finally:
        _cleanup(artifact)


def test_replace_same_sample_id_in_all_arms_fails_against_stage6e_manifest() -> None:
    artifact = _copy_artifact()
    try:
        for arm in ("direct", "j_fs", "original_mp_fs_plus", "d_g1_control", "d_f_g1_vnext"):
            path = artifact / "replay_outcomes" / f"{arm}.jsonl"
            rows = _read_jsonl(path)
            rows[-1]["stage6_sample_id"] = rows[0]["stage6_sample_id"]
            rows[-1]["sample_id"] = rows[0]["sample_id"]
            _write_jsonl(path, rows)
        report = validate(artifact)
        assert report["status"] == "FAIL"
        assert any(item.startswith("duplicate_sample_ids:") for item in report["violations"])
    finally:
        _cleanup(artifact)
