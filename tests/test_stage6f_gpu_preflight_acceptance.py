from __future__ import annotations

import importlib.util
import json
import shutil
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_DIR = PROJECT_ROOT / "stage6_gpu_preflight_acceptance"


def load_module(relative: str, name: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


validator = load_module(
    "scripts/data/validate_stage6f_gpu_preflight_acceptance.py",
    "validate_stage6f_gpu_preflight_acceptance",
)


def copy_acceptance() -> Path:
    root = PROJECT_ROOT / "stage6f_acceptance_test_runs"
    root.mkdir(exist_ok=True)
    target = root / f"run_{uuid.uuid4().hex}"
    shutil.copytree(ACCEPTANCE_DIR, target)
    return target


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_stage6f_gpu_preflight_acceptance_passes():
    report = validator.validate(ACCEPTANCE_DIR)
    assert report["status"] == "PASS"
    assert report["server_preflight_status"] == "PASS_GPU_PREFLIGHT_COMPLETE"
    assert report["prompt_token_rows"] == 2405
    assert report["confirmation_run_allowed_now"] is False


def test_mutating_acceptance_run_gate_fails():
    target = copy_acceptance()
    path = target / "STAGE6F_GPU_PREFLIGHT_ACCEPTANCE_LOCK.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["confirmation_run_allowed_now"] = True
    write_json(path, value)
    report = validator.validate(target)
    assert report["status"] == "FAIL"
    assert "acceptance_lock_field_mismatch" in {item["code"] for item in report["violations"]}
    shutil.rmtree(target, ignore_errors=True)


def test_mutating_server_preflight_status_fails():
    target = copy_acceptance()
    path = (
        target
        / validator.SERVER_RELATIVE
        / "STAGE6F_GPU_PREFLIGHT_LOCK.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    value["status"] = "PENDING_GPU_EXECUTION"
    write_json(path, value)
    report = validator.validate(target)
    assert report["status"] == "FAIL"
    assert "server_file_manifest_mismatch" in {item["code"] for item in report["violations"]}
    assert "server_preflight_status_not_pass" in {item["code"] for item in report["violations"]}
    shutil.rmtree(target, ignore_errors=True)


def test_mutating_h2_shared_generation_policy_fails():
    target = copy_acceptance()
    path = (
        target
        / validator.SERVER_RELATIVE
        / "H2_SHARED_PROMPT_IDENTITY_AUDIT.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    value["independent_D_F_G1_generation_allowed"] = True
    write_json(path, value)
    report = validator.validate(target)
    assert report["status"] == "FAIL"
    assert "server_file_manifest_mismatch" in {item["code"] for item in report["violations"]}
    assert "independent_dfg1_generation_allowed" in {item["code"] for item in report["violations"]}
    shutil.rmtree(target, ignore_errors=True)


def test_mutating_model_tokenizer_asset_identity_fails():
    target = copy_acceptance()
    path = target / validator.SERVER_RELATIVE / "MODEL_TOKENIZER_ASSET_AUDIT.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["actual_tokenizer_sha256"] = "deadbeef"
    value["tokenizer_match"] = False
    write_json(path, value)
    report = validator.validate(target)
    codes = {item["code"] for item in report["violations"]}
    assert report["status"] == "FAIL"
    assert "server_file_manifest_mismatch" in codes
    assert "extracted_server_artifacts_do_not_match_zip" in codes
    assert "model_tokenizer_asset_field_mismatch" in codes
    assert "model_tokenizer_asset_match_not_true" in codes
    shutil.rmtree(target, ignore_errors=True)


def test_mutating_synthetic_smoke_confirmation_usage_fails():
    target = copy_acceptance()
    path = target / validator.SERVER_RELATIVE / "SYNTHETIC_GPU_SMOKE_REPORT.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["confirmation_samples_used"] = 1
    write_json(path, value)
    report = validator.validate(target)
    codes = {item["code"] for item in report["violations"]}
    assert report["status"] == "FAIL"
    assert "synthetic_smoke_used_confirmation_samples" in codes
    shutil.rmtree(target, ignore_errors=True)


def test_mutating_nested_server_zip_fails():
    target = copy_acceptance()
    zip_path = target / "server_output_zip" / "Stage6F_GPU_PREFLIGHT_PATCH2_SERVER_OUTPUT_20260825.zip"
    with zip_path.open("ab") as handle:
        handle.write(b"mutation")
    report = validator.validate(target)
    codes = {item["code"] for item in report["violations"]}
    assert report["status"] == "FAIL"
    assert "nested_server_zip_actual_sha256_mismatch" in codes
    shutil.rmtree(target, ignore_errors=True)


def test_mutating_h2_input_id_identity_fails():
    target = copy_acceptance()
    path = target / validator.SERVER_RELATIVE / "PROMPT_TOKEN_AUDIT.jsonl"
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in rows:
        if row["arm"] == "d_f_g1_vnext":
            row["input_ids_sha256"] = "deadbeef"
            break
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    report = validator.validate(target)
    codes = {item["code"] for item in report["violations"]}
    assert report["status"] == "FAIL"
    assert "h2_input_identity_mismatch" in codes
    shutil.rmtree(target, ignore_errors=True)
