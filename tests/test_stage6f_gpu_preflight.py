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


create_stage6f = load_module("scripts/data/create_stage6f_gpu_preflight.py", "create_stage6f")
validate_stage6f = load_module("scripts/data/validate_stage6f_gpu_preflight.py", "validate_stage6f")


class Args:
    def __init__(self, output_dir: Path):
        self.output_dir = str(output_dir)
        self.execute_gpu_preflight = False
        self.model_name_or_path = None
        self.expected_execution_commit = None
        self.load_model = False
        self.run_synthetic_smoke = False


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fresh_test_dir() -> Path:
    root = PROJECT_ROOT / "stage6f_test_runs"
    root.mkdir(exist_ok=True)
    path = root / f"run_{uuid.uuid4().hex}"
    path.mkdir()
    return path


def create_pending() -> Path:
    out_dir = fresh_test_dir() / "stage6_gpu_preflight"
    lock = create_stage6f.create_preflight(Args(out_dir))
    assert lock["status"] == "PENDING_GPU_EXECUTION"
    return out_dir


def test_pending_preflight_validates_without_gpu():
    out_dir = create_pending()
    report = validate_stage6f.validate(out_dir)
    assert report["status"] == "PASS"
    assert report["gpu_environment_preflight_passed"] is False
    assert report["confirmation_run_allowed_now"] is False
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_require_gpu_pass_fails_for_pending_package():
    out_dir = create_pending()
    report = validate_stage6f.validate(out_dir, require_gpu_pass=True)
    codes = {item["code"] for item in report["violations"]}
    assert report["status"] == "FAIL"
    assert "gpu_preflight_pass_required_but_not_present" in codes
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_mutating_shared_generation_policy_fails():
    out_dir = create_pending()
    path = out_dir / "H2_SHARED_PROMPT_IDENTITY_AUDIT.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["independent_D_F_G1_generation_allowed"] = True
    write_json(path, value)
    report = validate_stage6f.validate(out_dir)
    codes = {item["code"] for item in report["violations"]}
    assert report["status"] == "FAIL"
    assert "independent_dfg1_generation_not_forbidden" in codes
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_mutating_confirmation_run_gate_fails():
    out_dir = create_pending()
    path = out_dir / "STAGE6F_GPU_PREFLIGHT_LOCK.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["confirmation_run_allowed_now"] = True
    write_json(path, value)
    report = validate_stage6f.validate(out_dir)
    codes = {item["code"] for item in report["violations"]}
    assert report["status"] == "FAIL"
    assert "lock_field_mismatch" in codes
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_mutating_generation_stream_path_fails():
    out_dir = create_pending()
    path = out_dir / "CONFIRMATION_RUN_PLAN.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["generation_streams"]["shared_mp_fs_plus_generation"]["raw_generation_path"] = (
        "raw_generations/d_f_g1_independent.jsonl"
    )
    write_json(path, value)
    report = validate_stage6f.validate(out_dir)
    codes = {item["code"] for item in report["violations"]}
    assert report["status"] == "FAIL"
    assert "generation_stream_path_mismatch" in codes
    shutil.rmtree(out_dir.parent, ignore_errors=True)


def test_frozen_artifact_audit_detects_hash_mutation():
    out_dir = create_pending()
    path = out_dir / "FROZEN_ARTIFACT_AUDIT.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["status"] = "FAIL"
    write_json(path, value)
    report = validate_stage6f.validate(out_dir)
    codes = {item["code"] for item in report["violations"]}
    assert report["status"] == "FAIL"
    assert "frozen_artifact_audit_not_pass" in codes
    shutil.rmtree(out_dir.parent, ignore_errors=True)
