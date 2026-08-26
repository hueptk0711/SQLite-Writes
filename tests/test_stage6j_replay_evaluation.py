from __future__ import annotations

import json
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
