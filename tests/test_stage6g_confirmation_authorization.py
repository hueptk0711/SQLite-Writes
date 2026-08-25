from __future__ import annotations

import importlib.util
import json
import shutil
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_module(relative: str, name: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


create_stage6g = load_module(
    "scripts/data/create_stage6g_confirmation_authorization.py",
    "create_stage6g_confirmation_authorization",
)
validate_stage6g = load_module(
    "scripts/data/validate_stage6g_confirmation_authorization.py",
    "validate_stage6g_confirmation_authorization",
)


def fresh_test_dir() -> Path:
    root = PROJECT_ROOT / "stage6g_test_runs"
    root.mkdir(exist_ok=True)
    path = root / f"run_{uuid.uuid4().hex}"
    path.mkdir()
    return path


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def create_authorization() -> Path:
    out_dir = fresh_test_dir() / "stage6_confirmation_run_authorization"
    create_stage6g.create_authorization(out_dir)
    return out_dir


def test_authorization_lock_validates():
    out_dir = create_authorization()
    report = validate_stage6g.validate(out_dir, repo_root=PROJECT_ROOT)
    assert report["status"] == "PASS"
    assert report["authorization_status"] == "AUTHORIZED_FOR_CONFIRMATORY_GENERATION"
    assert report["confirmation_run_allowed_now"] is True
    assert report["confirmation_predictions_created"] is False
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_mutating_run_gate_fails():
    out_dir = create_authorization()
    lock_path = out_dir / "CONFIRMATION_RUN_AUTHORIZATION_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["confirmation_run_allowed_now"] = False
    write_json(lock_path, lock)
    report = validate_stage6g.validate(out_dir, repo_root=PROJECT_ROOT)
    assert report["status"] == "FAIL"
    assert "authorization_lock_field_mismatch" in {item["code"] for item in report["violations"]}
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_mutating_model_hash_fails():
    out_dir = create_authorization()
    lock_path = out_dir / "CONFIRMATION_RUN_AUTHORIZATION_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["model_sha256"] = "0" * 64
    write_json(lock_path, lock)
    report = validate_stage6g.validate(out_dir, repo_root=PROJECT_ROOT)
    assert report["status"] == "FAIL"
    assert "authorization_lock_field_mismatch" in {item["code"] for item in report["violations"]}
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_mutating_token_budget_fails():
    out_dir = create_authorization()
    lock_path = out_dir / "CONFIRMATION_RUN_AUTHORIZATION_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["generation_parameters"]["max_new_tokens"] = 8192
    write_json(lock_path, lock)
    report = validate_stage6g.validate(out_dir, repo_root=PROJECT_ROOT)
    assert report["status"] == "FAIL"
    assert "authorization_lock_field_mismatch" in {item["code"] for item in report["violations"]}
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_mutating_generation_streams_fails():
    out_dir = create_authorization()
    lock_path = out_dir / "CONFIRMATION_RUN_AUTHORIZATION_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["raw_generation_stream_count"] = 5
    lock["raw_generation_streams"]["d_f_g1_independent"] = {
        "config_arm": "d_f_g1_vnext",
        "raw_generation_path": "raw_generations/d_f_g1_independent.jsonl",
        "role": "forbidden_independent_H2_generation",
    }
    write_json(lock_path, lock)
    report = validate_stage6g.validate(out_dir, repo_root=PROJECT_ROOT)
    codes = {item["code"] for item in report["violations"]}
    assert report["status"] == "FAIL"
    assert "raw_generation_stream_count_mismatch" in codes
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_mutating_shared_replay_arms_fails():
    out_dir = create_authorization()
    lock_path = out_dir / "CONFIRMATION_RUN_AUTHORIZATION_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["raw_generation_streams"]["shared_mp_fs_plus_generation"]["deterministic_replay_arms"] = [
        "d_g1_control"
    ]
    write_json(lock_path, lock)
    report = validate_stage6g.validate(out_dir, repo_root=PROJECT_ROOT)
    codes = {item["code"] for item in report["violations"]}
    assert report["status"] == "FAIL"
    assert "shared_generation_replay_arms_mismatch" in codes
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_external_anchor_hash_mismatch_fails():
    out_dir = create_authorization()
    repo_copy = fresh_test_dir() / "repo"
    anchor = (
        repo_copy
        / "stage6_gpu_preflight_acceptance"
        / "STAGE6F_GPU_PREFLIGHT_ACCEPTANCE_LOCK.json"
    )
    anchor.parent.mkdir(parents=True)
    anchor.write_text("tampered\n", encoding="utf-8")
    for relative, expected_hash in validate_stage6g.EXPECTED_EXTERNAL_HASHES.items():
        path = repo_copy / relative
        if path == anchor:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        source = PROJECT_ROOT / relative
        if source.is_file():
            shutil.copyfile(source, path)
        else:
            path.write_text(expected_hash, encoding="utf-8")
    report = validate_stage6g.validate(out_dir, repo_root=repo_copy)
    assert report["status"] == "FAIL"
    assert "external_anchor_hash_mismatch" in {item["code"] for item in report["violations"]}
    shutil.rmtree(repo_copy.parent, ignore_errors=True)
    shutil.rmtree(out_dir.parent, ignore_errors=True)
