from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.data.build_stage7e0_a3_english_preflight import PACKAGE_NAME, STAGE_NAME, build_stage
from scripts.data.validate_stage7e0_a3_server_results import classify_result
from scripts.server.run_stage7e0_a3_english import (
    FROZEN_RUNTIME_VERSIONS,
    PHASE_M_MAX_NEW_TOKENS,
    PHASE_O_MAX_NEW_TOKENS,
    validate_generation_config,
    validate_runtime_versions,
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _sha256(path: Path) -> str:
    import hashlib

    data = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _valid_model_metadata() -> dict[str, Any]:
    return {
        "backend": "incremental_json_schema_grammar",
        "schema_enforcement_mode": "transformers_prefix_allowed_tokens_fn",
        "token_level_enforcement": True,
        "fallback_to_unconstrained": False,
        "finite_complete_object_enumeration": False,
        "finite_known_answer_candidates": False,
        "label_side_data_used_for_constraints": False,
        "automatic_repair": False,
        "retry": 0,
        "model_called": True,
        "model_id": "Qwen/Qwen2.5-Coder-7B-Instruct",
        "model_revision": "c03e6d358207e414f1eca0bb1891e29f1db0e242",
        "chat_template_sha256": "cd8e9439f0570856fd70470bf8889ebd8b5d1107207f67a5efb46e342330527f",
        "quantization": "none",
        "torch_dtype": "auto",
        "cuda_available": True,
        "torch_version": "2.6.0+cu124",
        "transformers_version": "5.5.3",
        "tokenizers_version": "0.22.2",
        "accelerate_version": "1.14.0",
        "safetensors_version": "0.5.3",
    }


def _make_result_dir(path: Path, *, pass_count: int, backend: str = "constrained_hf", protocol_backend: str = "incremental_json_schema_grammar", model_overrides: dict[str, Any] | None = None) -> Path:
    model = _valid_model_metadata() | (model_overrides or {})
    if backend == "hf":
        model = model | {
            "backend": "hf",
            "token_level_enforcement": False,
            "schema_enforcement_mode": None,
            "fallback_to_unconstrained": True,
        }
    cases = [
        {"sample_id": f"stage7c_fresh_english_{index + 1:03d}", "status": "PASS" if index < pass_count else "FAIL", "failure_stage": None if index < pass_count else "acceptance_gate"}
        for index in range(8)
    ]
    metadata = {
        "backend": "incremental_json_schema_grammar",
        "token_level_enforcement": True,
        "fallback_to_unconstrained": False,
        "finite_complete_object_enumeration": False,
        "finite_known_answer_candidates": False,
        "label_side_data_used_for_constraints": False,
        "automatic_repair": False,
        "retry": 0,
    }
    if backend == "hf":
        metadata = metadata | {"backend": "hf", "token_level_enforcement": False, "fallback_to_unconstrained": True}
    raw_o = [{"sample_id": row["sample_id"], "phase": "phase_o", "status": "success", "raw_output": "{}", "generation_metadata": metadata} for row in cases]
    raw_m = [{"sample_id": row["sample_id"], "phase": "phase_m", "status": "success", "raw_output": "{}", "generation_metadata": metadata} for row in cases]
    _write_jsonl(path / "primary_case_results.jsonl", cases)
    _write_jsonl(path / "raw_phase_o_generations.jsonl", raw_o)
    _write_jsonl(path / "raw_phase_m_generations.jsonl", raw_m)
    summary = {
        "stage": STAGE_NAME,
        "status": "PASS" if pass_count == 8 else "FAIL",
        "backend": backend,
        "protocol_backend": protocol_backend,
        "model_called": True,
        "gpu_called": True,
        "phase_o_max_new_tokens": PHASE_O_MAX_NEW_TOKENS,
        "phase_m_max_new_tokens": PHASE_M_MAX_NEW_TOKENS,
        "primary_pass_count": f"{pass_count}/8",
        "required_pass_count": "8/8",
        "seven_of_eight_allowed": False,
        "diagnostics_run": False,
        "gretel_pilot_opened": False,
    }
    summary["raw_phase_o_sha256"] = _sha256(path / "raw_phase_o_generations.jsonl")
    summary["raw_phase_m_sha256"] = _sha256(path / "raw_phase_m_generations.jsonl")
    summary["primary_case_results_sha256"] = _sha256(path / "primary_case_results.jsonl")
    manifest = {
        "stage": STAGE_NAME,
        "model": model,
        "retry": 0,
        "repair": "none",
        "phase_o_max_new_tokens": PHASE_O_MAX_NEW_TOKENS,
        "phase_m_max_new_tokens": PHASE_M_MAX_NEW_TOKENS,
        "phase_o_prompt_spec_path": "Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/PHASE_O_PROMPT_SPEC_A3_ENGLISH.json",
    }
    _write_json(path / "primary_summary.json", summary)
    _write_json(path / "run_manifest.json", manifest)
    return path


def _args(**overrides: object) -> object:
    values: dict[str, object] = {
        "backend": "constrained_hf",
        "quantization": "none",
        "resume": False,
        "phase_o_max_new_tokens": PHASE_O_MAX_NEW_TOKENS,
        "phase_m_max_new_tokens": PHASE_M_MAX_NEW_TOKENS,
    }
    values.update(overrides)
    return type("Args", (), values)()


def test_generic_result_validator_accepts_constrained_8_of_8(tmp_path: Path) -> None:
    report = classify_result(_make_result_dir(tmp_path / "result", pass_count=8))
    assert report["evidence_integrity_status"] == "PASS"
    assert report["protocol_compliance_status"] == "PASS"
    assert report["primary_gate_status"] == "PASS"
    assert report["scientific_result_eligible"] is True


def test_generic_result_validator_accepts_constrained_6_of_8_as_primary_fail(tmp_path: Path) -> None:
    report = classify_result(_make_result_dir(tmp_path / "result", pass_count=6))
    assert report["evidence_integrity_status"] == "PASS"
    assert report["protocol_compliance_status"] == "PASS"
    assert report["primary_gate_status"] == "FAIL"
    assert report["scientific_result_eligible"] is True


def test_generic_result_validator_classifies_plain_hf_as_invalid_not_evaluated(tmp_path: Path) -> None:
    report = classify_result(_make_result_dir(tmp_path / "result", pass_count=0, backend="hf", protocol_backend="hf"))
    assert report["evidence_integrity_status"] == "PASS"
    assert report["protocol_compliance_status"] == "FAIL"
    assert report["primary_gate_status"] == "INVALID_NOT_EVALUATED"
    assert report["scientific_result_eligible"] is False


def test_generic_result_validator_marks_runtime_drift_protocol_fail(tmp_path: Path) -> None:
    report = classify_result(_make_result_dir(tmp_path / "result", pass_count=8, model_overrides={"tokenizers_version": "0.99.0"}))
    assert report["evidence_integrity_status"] == "PASS"
    assert report["protocol_compliance_status"] == "FAIL"
    assert report["primary_gate_status"] == "INVALID_NOT_EVALUATED"
    assert any("tokenizers_version" in item for item in report["protocol_failures"])


def test_runner_refuses_resume_for_real_constrained_run() -> None:
    with pytest.raises(SystemExit, match="forbids --resume"):
        validate_generation_config(_args(resume=True))


def test_runner_runtime_version_gate() -> None:
    assert validate_runtime_versions(dict(FROZEN_RUNTIME_VERSIONS))["status"] == "PASS"
    bad = dict(FROZEN_RUNTIME_VERSIONS)
    bad["tokenizers"] = "0.99.0"
    with pytest.raises(SystemExit, match="tokenizers"):
        validate_runtime_versions(bad)


def test_constraint_independence_audit_covers_phase_m(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, tmp_path / PACKAGE_NAME)
    audit = json.loads((stage_dir / "CONSTRAINT_INDEPENDENCE_AUDIT_A3.json").read_text(encoding="utf-8"))
    assert audit["status"] == "PASS"
    assert audit["case_count"] == 8
    assert all(row["phase_m_label_independent"] is True for row in audit["rows"])


def test_clean_zip_validators_are_self_contained(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    package_path = tmp_path / PACKAGE_NAME
    build_stage(stage_dir, package_path)
    extract = tmp_path / "extract"
    with zipfile.ZipFile(package_path) as archive:
        archive.extractall(extract)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([".", "tests/support/windows_py314_pytest_tempdir"])
    commands = [
        [sys.executable, "scripts/data/validate_stage7c_a3_english_offset_semantics.py", "--stage-dir", "Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT"],
        [sys.executable, "scripts/data/validate_stage7e0_a3_english_preflight.py", "--stage-dir", STAGE_NAME],
        [sys.executable, "scripts/data/validate_stage7e0_a3_server_results.py", "--stage-dir", STAGE_NAME],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=extract, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
