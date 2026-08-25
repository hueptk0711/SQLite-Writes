from __future__ import annotations

import importlib.util
import json
import shutil
import uuid
from pathlib import Path
from unittest import mock


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


def create_repo_copy_with_anchors() -> Path:
    repo_copy = fresh_test_dir() / "repo"
    for relative in validate_stage6g.EXPECTED_EXTERNAL_HASHES:
        source = PROJECT_ROOT / relative
        target = repo_copy / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    manifest_source = PROJECT_ROOT / "stage5_method_revision_freeze" / "EXECUTABLE_FREEZE_MANIFEST.json"
    manifest_target = repo_copy / "stage5_method_revision_freeze" / "EXECUTABLE_FREEZE_MANIFEST.json"
    manifest_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(manifest_source, manifest_target)
    manifest = json.loads(manifest_source.read_text(encoding="utf-8"))
    for relative in manifest["method_implementation_files"]:
        source = PROJECT_ROOT / relative
        target = repo_copy / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return repo_copy


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
    lock["generation_lock"]["max_new_tokens"] = 8192
    write_json(lock_path, lock)
    report = validate_stage6g.validate(out_dir, repo_root=PROJECT_ROOT)
    assert report["status"] == "FAIL"
    assert "generation_lock_exact_mismatch" in {item["code"] for item in report["violations"]}
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_mutating_stage5_temperature_null_to_zero_fails():
    out_dir = create_authorization()
    lock_path = out_dir / "CONFIRMATION_RUN_AUTHORIZATION_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["generation_lock"]["temperature"] = 0
    write_json(lock_path, lock)
    report = validate_stage6g.validate(out_dir, repo_root=PROJECT_ROOT)
    codes = {item["code"] for item in report["violations"]}
    assert report["status"] == "FAIL"
    assert "generation_lock_temperature_mismatch" in codes
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
    assert "authorization_lock_field_mismatch" in codes
    assert "raw_generation_streams_mismatch" in codes
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
    assert "raw_generation_streams_mismatch" in codes
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_external_anchor_hash_mismatch_fails():
    out_dir = create_authorization()
    repo_copy = create_repo_copy_with_anchors()
    anchor = (
        repo_copy
        / "stage6_gpu_preflight_acceptance"
        / "STAGE6F_GPU_PREFLIGHT_ACCEPTANCE_LOCK.json"
    )
    anchor.parent.mkdir(parents=True, exist_ok=True)
    anchor.write_text("tampered\n", encoding="utf-8")
    report = validate_stage6g.validate(out_dir, repo_root=repo_copy)
    assert report["status"] == "FAIL"
    assert "external_anchor_hash_mismatch" in {item["code"] for item in report["violations"]}
    shutil.rmtree(repo_copy.parent, ignore_errors=True)
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_mutating_resolved_config_fails():
    out_dir = create_authorization()
    repo_copy = create_repo_copy_with_anchors()
    path = repo_copy / "configs" / "stage5" / "resolved_direct_confirmation.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["family"] = "MUTATED_FAMILY"
    write_json(path, value)
    report = validate_stage6g.validate(out_dir, repo_root=repo_copy)
    assert report["status"] == "FAIL"
    assert "external_anchor_hash_mismatch" in {item["code"] for item in report["violations"]}
    shutil.rmtree(repo_copy.parent, ignore_errors=True)
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_mutating_stage5_protocol_fails():
    out_dir = create_authorization()
    repo_copy = create_repo_copy_with_anchors()
    path = repo_copy / "stage5_method_revision_freeze" / "CONFIRMATION_PROTOCOL_LOCK.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["generation_lock"]["max_new_tokens"] = 8192
    write_json(path, value)
    report = validate_stage6g.validate(out_dir, repo_root=repo_copy)
    assert report["status"] == "FAIL"
    assert "external_anchor_hash_mismatch" in {item["code"] for item in report["violations"]}
    shutil.rmtree(repo_copy.parent, ignore_errors=True)
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_mutating_method_source_tree_fails():
    out_dir = create_authorization()
    repo_copy = create_repo_copy_with_anchors()
    path = repo_copy / "src" / "nldbwrite_v3" / "planner" / "prompt.py"
    path.write_text(path.read_text(encoding="utf-8") + "\n# mutation\n", encoding="utf-8")
    report = validate_stage6g.validate(out_dir, repo_root=repo_copy)
    assert report["status"] == "FAIL"
    assert "method_source_tree_sha256_mismatch" in {item["code"] for item in report["violations"]}
    shutil.rmtree(repo_copy.parent, ignore_errors=True)
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_wrong_git_head_fails():
    out_dir = create_authorization()
    report = validate_stage6g.validate(
        out_dir,
        repo_root=PROJECT_ROOT,
        expected_git_head="0" * 40,
    )
    assert report["status"] == "FAIL"
    assert "git_head_mismatch" in {item["code"] for item in report["violations"]}
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_dirty_worktree_fails_when_required():
    out_dir = create_authorization()

    def fake_git_output(_repo_root: Path, *args: str):
        if args[:2] == ("status", "--porcelain"):
            return " M configs/stage5/resolved_direct_confirmation.json"
        return "86ee2fe0286e08be20bd4d01a175128f73b37ade"

    with mock.patch.object(validate_stage6g, "git_output", side_effect=fake_git_output):
        report = validate_stage6g.validate(
            out_dir,
            repo_root=PROJECT_ROOT,
            require_git_clean=True,
        )
    assert report["status"] == "FAIL"
    assert "git_status_not_clean" in {item["code"] for item in report["violations"]}
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_existing_raw_generation_file_fails():
    out_dir = create_authorization()
    repo_copy = create_repo_copy_with_anchors()
    raw = repo_copy / "raw_generations" / "direct.jsonl"
    raw.parent.mkdir(parents=True)
    raw.write_text('{"stale": true}\n', encoding="utf-8")
    report = validate_stage6g.validate(out_dir, repo_root=repo_copy)
    assert report["status"] == "FAIL"
    assert "preexisting_raw_generation_files" in {item["code"] for item in report["violations"]}
    shutil.rmtree(repo_copy.parent, ignore_errors=True)
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_mutating_runtime_prompt_guard_fails():
    out_dir = create_authorization()
    lock_path = out_dir / "CONFIRMATION_RUN_AUTHORIZATION_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["runtime_prompt_input_identity_guard"]["required_per_row_checks_before_generate"].remove(
        "input_ids_sha256"
    )
    write_json(lock_path, lock)
    report = validate_stage6g.validate(out_dir, repo_root=PROJECT_ROOT)
    assert report["status"] == "FAIL"
    assert "runtime_prompt_guard_fields_mismatch" in {
        item["code"] for item in report["violations"]
    }
    shutil.rmtree(out_dir.parent, ignore_errors=True)
