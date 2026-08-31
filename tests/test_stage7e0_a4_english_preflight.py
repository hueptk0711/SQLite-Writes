from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.data.build_stage7e0_a4_english_preflight import PACKAGE_NAME, STAGE_NAME, build_stage
from scripts.data.validate_stage7e0_a4_english_preflight import validate
from scripts.server.run_stage7e0_a4_english import (
    A4_PROMPT_SPEC_REL,
    CallResult,
    DEFAULT_MODEL_PATH,
    EXPECTED_CHAT_TEMPLATE_SHA256,
    MODEL_ID,
    MODEL_REVISION,
    LabelMockGenerator,
    build_phase_o_span_ref_constraint_grammar,
    canonical_json,
    evaluate_primary_case,
    load_stage7c_a4_rows,
    parse_phase_o_span_ref_output,
    render_phase_o_a4_messages,
    run_stage7e0,
    sha256_file,
    write_json,
    write_jsonl,
)
from scripts.data.validate_stage7e0_a4_server_results import validate as validate_server_result
from nldbwrite_v3.v2_a1.types import V2A1Error


def accepted_commit_for_tests() -> str:
    if (ROOT / ".git").exists():
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    lock_path = ROOT / STAGE_NAME / "STAGE7E0_A4_LOCK.json"
    if lock_path.is_file():
        return read_json(lock_path)["git_commit"]
    return "UNKNOWN"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class OverrideGenerator(LabelMockGenerator):
    def __init__(self, rows: list[dict], phase_o_override: dict | None = None, phase_m_override: dict | None = None):
        super().__init__(rows)
        self.phase_o_override = phase_o_override
        self.phase_m_override = phase_m_override

    def generate(self, **kwargs) -> CallResult:
        phase = kwargs["phase"]
        sample_id = kwargs["sample_id"]
        override = self.phase_o_override if phase == "phase_o" else self.phase_m_override
        if override is None:
            return super().generate(**kwargs)
        raw = canonical_json(override)
        return CallResult(
            sample_id=sample_id,
            phase=phase,
            raw_output=raw,
            input_tokens=0,
            output_tokens=len(raw.split()),
            generation_metadata={
                "backend": "mock",
                "token_level_enforcement": False,
                "fallback_to_unconstrained": False,
                "finite_complete_object_enumeration": False,
                "finite_known_answer_candidates": False,
                "label_side_data_used_for_constraints": True,
                "automatic_repair": False,
                "retry": 0,
            },
        )


def test_stage7e0_a4_uses_exact_candidate_span_prompt() -> None:
    spec = read_json(ROOT / A4_PROMPT_SPEC_REL)
    assert spec["model_id"] == MODEL_ID
    assert spec["model_revision"] == MODEL_REVISION
    rows = load_stage7c_a4_rows(ROOT)
    messages, digest = render_phase_o_a4_messages(rows[0], root=ROOT)
    assert len(digest) == 64
    assert messages[0]["content"] == spec["system_prompt"]
    assert "Candidate span inventory:" in messages[1]["content"]
    assert "SPAN_" in messages[1]["content"]
    assert "start_char" not in messages[1]["content"]
    assert "end_char" not in messages[1]["content"]


def test_phase_o_span_ref_parser_rejects_offsets_unknown_and_duplicates() -> None:
    allowed = ["SPAN_0001", "SPAN_0002"]
    assert parse_phase_o_span_ref_output('{"operation":"INSERT","span_refs":["SPAN_0001"]}', allowed) == {
        "operation": "INSERT",
        "span_refs": ["SPAN_0001"],
    }
    with pytest.raises(V2A1Error, match="only operation and span_refs"):
        parse_phase_o_span_ref_output('{"operation":"INSERT","span_refs":["SPAN_0001"],"value_spans":[]}', allowed)
    with pytest.raises(V2A1Error) as unknown:
        parse_phase_o_span_ref_output('{"operation":"INSERT","span_refs":["SPAN_9999"]}', allowed)
    assert unknown.value.reason_code == "phase_o_unknown_span_refs"
    with pytest.raises(V2A1Error) as duplicate:
        parse_phase_o_span_ref_output('{"operation":"INSERT","span_refs":["SPAN_0001","SPAN_0001"]}', allowed)
    assert duplicate.value.reason_code == "phase_o_duplicate_span_refs"


def test_phase_o_constrained_grammar_uses_exact_dynamic_enum() -> None:
    row = load_stage7c_a4_rows(ROOT)[0]
    grammar = build_phase_o_span_ref_constraint_grammar(row["runtime_constraints"]["phase_o_schema"])
    candidate_refs = [candidate["span_ref"] for candidate in row["runtime_constraints"]["candidate_inventory"]]
    assert grammar.span_ref_choices == candidate_refs
    assert grammar.is_complete('{"operation":"INSERT","span_refs":["SPAN_0010"]}')
    assert not grammar.is_prefix('{"operation":"INSERT","span_refs":["SPAN_9999"]}')
    metadata = grammar.metadata()
    assert metadata["label_side_data_used_for_constraints"] is False
    assert metadata["finite_complete_object_enumeration"] is False
    assert metadata["finite_known_answer_candidates"] is False


def test_label_mock_generator_is_explicitly_non_model_evidence() -> None:
    rows = load_stage7c_a4_rows(ROOT)
    metadata = LabelMockGenerator(rows).metadata()
    assert metadata["backend"] == "mock"
    assert metadata["model_called"] is False
    assert metadata["mock_uses_label_side_expected"] is True


def test_mock_runner_primary_gate_passes_10_of_10(tmp_path: Path) -> None:
    args = type(
        "Args",
        (),
        {
            "accepted_protocol_commit": accepted_commit_for_tests(),
            "result_root": str(tmp_path / "result"),
            "backend": "mock",
            "model_name_or_path": DEFAULT_MODEL_PATH,
            "quantization": "none",
            "phase_o_max_new_tokens": 512,
            "phase_m_max_new_tokens": 8192,
            "max_input_tokens": 28672,
            "seed": 42,
            "trust_remote_code": False,
            "resume": False,
            "skip_git_assertions": True,
            "allow_result_root_inside_git": True,
        },
    )()
    summary = run_stage7e0(args)
    assert summary["status"] == "PASS"
    assert summary["primary_pass_count"] == "10/10"
    assert summary["model_called"] is False
    assert summary["gpu_called"] is False
    assert summary["gretel_pilot_opened"] is False
    assert len(read_jsonl(tmp_path / "result" / "primary_case_results.jsonl")) == 10
    assert len(read_jsonl(tmp_path / "result" / "raw_phase_o_generations.jsonl")) == 10
    assert len(read_jsonl(tmp_path / "result" / "raw_phase_m_generations.jsonl")) == 10


def test_phase_o_correct_refs_reverse_order_passes() -> None:
    row = load_stage7c_a4_rows(ROOT)[0]
    reversed_phase_o = {
        "operation": "INSERT",
        "span_refs": list(reversed(row["label_side_expected"]["phase_o"]["span_refs"])),
    }
    result, _raw_o, _raw_m = evaluate_primary_case(
        row,
        OverrideGenerator([row], phase_o_override=reversed_phase_o),
        phase_o_max_new_tokens=512,
        phase_m_max_new_tokens=8192,
    )
    assert result["status"] == "PASS"
    assert result["checks"]["span_ref_selection_exact"] is True
    assert result["phase_o_canonical_span_refs"] == row["label_side_expected"]["phase_o"]["span_refs"]


def test_phase_o_missing_ref_fails_selection_exact() -> None:
    row = load_stage7c_a4_rows(ROOT)[0]
    missing_one = {
        "operation": "INSERT",
        "span_refs": row["label_side_expected"]["phase_o"]["span_refs"][:-1],
    }
    two_slot_phase_m = {
        **row["label_side_expected"]["phase_m"],
        "assignments": row["label_side_expected"]["phase_m"]["assignments"][:-1],
    }
    result, _raw_o, _raw_m = evaluate_primary_case(
        row,
        OverrideGenerator([row], phase_o_override=missing_one, phase_m_override=two_slot_phase_m),
        phase_o_max_new_tokens=512,
        phase_m_max_new_tokens=8192,
    )
    assert result["status"] == "FAIL"
    assert result["failure_stage"] == "acceptance_gate"
    assert result["checks"]["span_ref_selection_exact"] is False
    assert result["checks"]["no_extra_refs"] is False


def test_phase_m_correct_assignments_reverse_order_passes() -> None:
    row = load_stage7c_a4_rows(ROOT)[0]
    reversed_phase_m = {
        **row["label_side_expected"]["phase_m"],
        "assignments": list(reversed(row["label_side_expected"]["phase_m"]["assignments"])),
    }
    result, _raw_o, _raw_m = evaluate_primary_case(
        row,
        OverrideGenerator([row], phase_m_override=reversed_phase_m),
        phase_o_max_new_tokens=512,
        phase_m_max_new_tokens=8192,
    )
    assert result["status"] == "PASS"
    assert result["checks"]["phase_m_mapping_exact"] is True
    assert result["checks"]["canonical_target_state_exact"] is True


def test_builder_and_validator_pass(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    package_path = tmp_path / PACKAGE_NAME
    summary = build_stage(stage_dir, package_path)
    assert summary["status"] == "PASS_READY_FOR_REAL_A4_CONSTRAINED_PREFLIGHT"
    assert summary["mock_primary_pass_count"] == "10/10"
    report = validate(stage_dir)
    assert report["status"] == "PASS", report["failures"]
    assert package_path.is_file()
    assert package_path.with_suffix(package_path.suffix + ".sha256").is_file()
    with zipfile.ZipFile(package_path) as archive:
        assert archive.testzip() is None


def test_stage_lock_forbids_9_of_10_and_gretel(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, tmp_path / PACKAGE_NAME)
    lock = read_json(stage_dir / "STAGE7E0_A4_LOCK.json")
    policy = read_json(stage_dir / "PRIMARY_ACCEPTANCE_POLICY_A4.json")
    protocol = read_json(stage_dir / "RUNNER_PROTOCOL_A4.json")
    assert lock["primary_acceptance"] == "10/10 required; no average and no 9/10 acceptance"
    assert policy["required_pass_count"] == "10/10"
    assert policy["nine_of_ten_allowed"] is False
    assert policy["averaging_allowed"] is False
    assert lock["gretel_pilot_opened"] is False
    assert lock["development_dev_used"] is False
    assert lock["official_test_used"] is False
    assert protocol["model"]["expected_chat_template_sha256"] == EXPECTED_CHAT_TEMPLATE_SHA256
    assert protocol["model"]["quantization_default"] == "none"
    assert protocol["generation_contract"]["resume_allowed"] is False
    assert protocol["generation_contract"]["backend"] == "incremental_json_schema_grammar"
    assert protocol["prompt_contract"]["phase_o_output_keys"] == ["operation", "span_refs"]


def test_clean_reviewer_zip_validator_passes(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    package_path = tmp_path / PACKAGE_NAME
    build_stage(stage_dir, package_path)
    extract = tmp_path / "extract"
    with zipfile.ZipFile(package_path) as archive:
        archive.extractall(extract)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/data/validate_stage7e0_a4_english_preflight.py",
            "--stage-dir",
            STAGE_NAME,
        ],
        cwd=extract,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _write_server_result(result_dir: Path, *, protocol_valid: bool, pass_count: int) -> None:
    result_dir.mkdir(parents=True, exist_ok=True)
    cases = [
        {"sample_id": f"case_{index:02d}", "status": "PASS" if index <= pass_count else "FAIL", "failure_stage": None if index <= pass_count else "acceptance_gate", "checks": {}}
        for index in range(1, 11)
    ]
    raw_metadata = {
        "backend": "incremental_json_schema_grammar" if protocol_valid else "mock",
        "token_level_enforcement": protocol_valid,
        "fallback_to_unconstrained": False,
        "finite_complete_object_enumeration": False,
        "finite_known_answer_candidates": False,
        "label_side_data_used_for_constraints": False,
        "automatic_repair": False,
        "retry": 0,
    }
    raw_o = [{"sample_id": row["sample_id"], "generation_metadata": raw_metadata} for row in cases]
    raw_m = [{"sample_id": row["sample_id"], "generation_metadata": raw_metadata} for row in cases]
    write_jsonl(result_dir / "primary_case_results.jsonl", cases)
    write_jsonl(result_dir / "raw_phase_o_generations.jsonl", raw_o)
    write_jsonl(result_dir / "raw_phase_m_generations.jsonl", raw_m)
    model = {
        "backend": "incremental_json_schema_grammar" if protocol_valid else "mock",
        "schema_enforcement_mode": "transformers_prefix_allowed_tokens_fn",
        "token_level_enforcement": protocol_valid,
        "fallback_to_unconstrained": False,
        "finite_complete_object_enumeration": False,
        "finite_known_answer_candidates": False,
        "label_side_data_used_for_constraints": False,
        "automatic_repair": False,
        "retry": 0,
        "model_called": True,
        "cuda_available": True,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "chat_template_sha256": EXPECTED_CHAT_TEMPLATE_SHA256,
        "quantization": "none",
        "torch_dtype": "auto",
        "torch_version": "2.6.0+cu124",
        "transformers_version": "5.5.3",
        "tokenizers_version": "0.22.2",
        "accelerate_version": "1.14.0",
        "safetensors_version": "0.5.3",
    }
    write_json(
        result_dir / "run_manifest.json",
        {
            "stage": "Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT",
            "model": model,
            "phase_o_prompt_spec_path": "Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL/PHASE_O_PROMPT_SPEC_A4_ENGLISH.json",
            "retry": 0,
            "repair": "none",
            "phase_o_max_new_tokens": 512,
            "phase_m_max_new_tokens": 8192,
        },
    )
    write_json(
        result_dir / "primary_summary.json",
        {
            "stage": "Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT",
            "status": "PASS" if pass_count == 10 else "FAIL",
            "backend": "constrained_hf",
            "protocol_backend": "incremental_json_schema_grammar" if protocol_valid else "mock",
            "model_called": True,
            "gpu_called": True,
            "phase_o_max_new_tokens": 512,
            "phase_m_max_new_tokens": 8192,
            "primary_pass_count": f"{pass_count}/10",
            "required_pass_count": "10/10",
            "nine_of_ten_allowed": False,
            "diagnostics_run": False,
            "gretel_pilot_opened": False,
            "raw_phase_o_sha256": sha256_file(result_dir / "raw_phase_o_generations.jsonl"),
            "raw_phase_m_sha256": sha256_file(result_dir / "raw_phase_m_generations.jsonl"),
            "primary_case_results_sha256": sha256_file(result_dir / "primary_case_results.jsonl"),
        },
    )


def test_protocol_invalid_server_result_returns_fail_and_cli_nonzero(tmp_path: Path) -> None:
    result_dir = tmp_path / "invalid_result"
    _write_server_result(result_dir, protocol_valid=False, pass_count=10)
    report = validate_server_result(result_dir)
    assert report["evidence_integrity_status"] == "PASS"
    assert report["protocol_compliance_status"] == "FAIL"
    assert report["primary_gate_status"] == "INVALID_NOT_EVALUATED"
    assert report["status"] == "FAIL"
    result = subprocess.run(
        [sys.executable, "scripts/data/validate_stage7e0_a4_server_results.py", "--result-dir", str(result_dir)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode != 0


def test_valid_scientific_failure_server_result_returns_zero_with_primary_fail(tmp_path: Path) -> None:
    result_dir = tmp_path / "valid_scientific_failure"
    _write_server_result(result_dir, protocol_valid=True, pass_count=9)
    report = validate_server_result(result_dir)
    assert report["evidence_integrity_status"] == "PASS"
    assert report["protocol_compliance_status"] == "PASS"
    assert report["primary_gate_status"] == "FAIL"
    assert report["status"] == "PASS"
    result = subprocess.run(
        [sys.executable, "scripts/data/validate_stage7e0_a4_server_results.py", "--result-dir", str(result_dir)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
