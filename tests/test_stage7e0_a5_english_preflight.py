from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
import scripts.server.run_stage7e0_a5_english as stage7e0_runner

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nldbwrite_v3.v2_a1.types import V2A1Error
from scripts.data.build_stage7c_a5_column_conditioned_phase_o_protocol import canonical_json
from scripts.data.build_stage7e0_a5_english_preflight import PACKAGE_NAME, STAGE_NAME, build_stage
from scripts.data.validate_stage7e0_a5_english_preflight import validate
from scripts.data.validate_stage7e0_a5_server_results import validate as validate_server_result
from scripts.server.run_stage7e0_a5_english import (
    ALLOWED_FROZEN_RUNTIME_PROFILES,
    CallResult,
    EXPECTED_CHAT_TEMPLATE_SHA256,
    LabelMockGenerator,
    MODEL_ID,
    MODEL_REVISION,
    PRIMARY_RUNTIME_PROFILE_ID,
    build_phase_o_column_conditioned_constraint_grammar,
    evaluate_case,
    load_stage7c_a5_rows,
    parse_phase_o_column_conditioned_output,
    run_stage7e0,
    validate_runtime_versions,
    verify_accepted_protocol_commit_argument,
    write_json,
    write_jsonl,
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def accepted_commit_for_tests() -> str:
    manifest_path = ROOT / STAGE_NAME / "STAGE7E0_A5_INPUT_MANIFEST.json"
    if manifest_path.is_file():
        return read_json(manifest_path)["accepted_stage7c_a5_commit"]
    if (ROOT / ".git").exists():
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    lock_path = ROOT / STAGE_NAME / "STAGE7E0_A5_LOCK.json"
    if lock_path.is_file():
        return read_json(lock_path)["git_commit"]
    return "UNKNOWN"


class OverrideGenerator(LabelMockGenerator):
    def __init__(self, rows: list[dict], phase_o_override: dict | None = None, phase_o_overrides: dict[str, dict] | None = None):
        super().__init__(rows)
        self.phase_o_override = phase_o_override
        self.phase_o_overrides = phase_o_overrides or {}

    def generate(self, **kwargs) -> CallResult:
        sample_id = kwargs["sample_id"]
        override = self.phase_o_overrides.get(sample_id, self.phase_o_override)
        if override is None:
            return super().generate(**kwargs)
        raw = canonical_json(override)
        return CallResult(
            sample_id=sample_id,
            phase="phase_o",
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


def test_stage7e0_a5_uses_exact_column_conditioned_primary_rows() -> None:
    rows = load_stage7c_a5_rows(ROOT)
    assert len(rows) == 12
    assert all(row["sample_id"].startswith("stage7c_a5_primary_english_") for row in rows)
    assert all(sorted(row["label_side_expected"]["phase_o"]) == ["column_span_refs", "operation", "table_ref"] for row in rows)


def test_column_conditioned_grammar_accepts_gold_and_rejects_unknown_refs() -> None:
    row = load_stage7c_a5_rows(ROOT)[0]
    grammar = build_phase_o_column_conditioned_constraint_grammar(row["runtime_constraints"]["phase_o_schema"])
    gold = canonical_json(row["label_side_expected"]["phase_o"])
    assert grammar.is_complete(gold)
    assert grammar.is_prefix(gold[:-1])
    assert not grammar.is_prefix(gold.replace("SPAN_0012", "SPAN_9999"))
    metadata = grammar.metadata()
    assert metadata["label_side_data_used_for_constraints"] is False
    assert metadata["finite_complete_object_enumeration"] is False
    assert metadata["finite_known_answer_candidates"] is False


def test_phase_o_parser_is_order_insensitive_and_rejects_duplicates() -> None:
    row = load_stage7c_a5_rows(ROOT)[0]
    schema = row["runtime_constraints"]["phase_o_schema"]
    phase_o = row["label_side_expected"]["phase_o"]
    reordered = {
        "table_ref": phase_o["table_ref"],
        "operation": phase_o["operation"],
        "column_span_refs": dict(reversed(list(phase_o["column_span_refs"].items()))),
    }
    assert parse_phase_o_column_conditioned_output(json.dumps(reordered), schema) == phase_o
    with pytest.raises(V2A1Error) as unknown:
        parse_phase_o_column_conditioned_output('{"operation":"INSERT","table_ref":"TAB_1","column_span_refs":{"COL_1":"SPAN_9999"}}', schema)
    assert unknown.value.reason_code == "phase_o_schema_failure"
    duplicate = json.loads(canonical_json(phase_o))
    first_two = list(duplicate["column_span_refs"])[:2]
    duplicate["column_span_refs"][first_two[1]] = duplicate["column_span_refs"][first_two[0]]
    with pytest.raises(V2A1Error) as duplicate_error:
        parse_phase_o_column_conditioned_output(canonical_json(duplicate), schema)
    assert duplicate_error.value.reason_code == "phase_o_duplicate_span_ref_reuse"


def test_mock_runner_primary_gate_passes_12_of_12(tmp_path: Path) -> None:
    args = type(
        "Args",
        (),
        {
            "accepted_protocol_commit": accepted_commit_for_tests(),
            "result_root": str(tmp_path / "result"),
            "backend": "mock",
            "model_name_or_path": MODEL_ID,
            "quantization": "none",
            "phase_o_max_new_tokens": 512,
            "max_input_tokens": 28672,
            "seed": 42,
            "trust_remote_code": False,
            "resume": False,
            "run_diagnostics_after_primary_pass": False,
            "skip_git_assertions": True,
            "allow_result_root_inside_git": True,
        },
    )()
    summary = run_stage7e0(args)
    assert summary["status"] == "PASS"
    assert summary["primary_pass_count"] == "12/12"
    assert summary["phase_m_removed"] is True
    assert summary["model_called"] is False
    assert summary["gpu_called"] is False
    assert summary["diagnostics_run"] is False
    assert len(read_jsonl(tmp_path / "result" / "primary_case_results.jsonl")) == 12
    assert len(read_jsonl(tmp_path / "result" / "raw_primary_phase_o_generations.jsonl")) == 12
    assert not (tmp_path / "result" / "raw_phase_m_generations.jsonl").exists()


def test_correct_mapping_reordered_keys_passes() -> None:
    row = load_stage7c_a5_rows(ROOT)[0]
    phase_o = row["label_side_expected"]["phase_o"]
    reordered = {**phase_o, "column_span_refs": dict(reversed(list(phase_o["column_span_refs"].items())))}
    result, _raw = evaluate_case(row, OverrideGenerator([row], phase_o_override=reordered), phase_o_max_new_tokens=512)
    assert result["status"] == "PASS"
    assert result["checks"]["column_span_refs_mapping_exact"] is True


def test_wrong_span_mapping_fails_acceptance_gate() -> None:
    row = load_stage7c_a5_rows(ROOT)[0]
    phase_o = json.loads(canonical_json(row["label_side_expected"]["phase_o"]))
    columns = [column for column, value in phase_o["column_span_refs"].items() if value != "OMIT"]
    phase_o["column_span_refs"][columns[0]], phase_o["column_span_refs"][columns[1]] = phase_o["column_span_refs"][columns[1]], phase_o["column_span_refs"][columns[0]]
    result, _raw = evaluate_case(row, OverrideGenerator([row], phase_o_override=phase_o), phase_o_max_new_tokens=512)
    assert result["status"] == "FAIL"
    assert result["failure_stage"] == "acceptance_gate"
    assert result["checks"]["column_span_refs_mapping_exact"] is False


def test_runtime_lock_accepts_uet_rtx4090_profile() -> None:
    report = validate_runtime_versions(
        {
            "torch": "2.6.0+cu124",
            "torch_cuda": "12.4",
            "transformers": "5.5.3",
            "tokenizers": "0.22.2",
            "accelerate": "1.14.0",
            "safetensors": "0.5.3",
            "cuda_available": True,
            "gpu_count": 1,
            "gpu_devices": ["NVIDIA GeForce RTX 4090"],
        }
    )
    assert report["status"] == "PASS"
    assert report["runtime_profile_id"] == PRIMARY_RUNTIME_PROFILE_ID


def test_runtime_lock_rejects_wrong_runtime() -> None:
    with pytest.raises(SystemExit, match="frozen inference runtime version drift"):
        validate_runtime_versions(
            {
                "torch": "2.10.0",
                "torch_cuda": "12.8",
                "transformers": "5.5.3",
                "tokenizers": "0.22.2",
                "accelerate": "1.14.0",
                "safetensors": "0.5.3",
                "cuda_available": True,
                "gpu_count": 1,
                "gpu_devices": ["NVIDIA GeForce RTX 4090"],
            }
        )


def test_builder_and_validator_pass(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    package_path = tmp_path / PACKAGE_NAME
    summary = build_stage(stage_dir, package_path)
    assert summary["status"] == "PASS_READY_FOR_REAL_A5_CONSTRAINED_PREFLIGHT"
    assert summary["mock_primary_pass_count"] == "12/12"
    report = validate(stage_dir)
    assert report["status"] == "PASS", report["failures"]
    assert package_path.is_file()
    assert package_path.with_suffix(package_path.suffix + ".sha256").is_file()
    with zipfile.ZipFile(package_path) as archive:
        assert archive.testzip() is None


def test_stage_lock_forbids_11_of_12_phase_m_and_gretel(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, tmp_path / PACKAGE_NAME)
    lock = read_json(stage_dir / "STAGE7E0_A5_LOCK.json")
    policy = read_json(stage_dir / "PRIMARY_ACCEPTANCE_POLICY_A5.json")
    protocol = read_json(stage_dir / "RUNNER_PROTOCOL_A5.json")
    assert lock["primary_acceptance"] == "12/12 required; no average and no 11/12 acceptance"
    assert policy["required_pass_count"] == "12/12"
    assert policy["eleven_of_twelve_allowed"] is False
    assert policy["averaging_allowed"] is False
    assert policy["primary_before_diagnostics"] is True
    assert policy["diagnostics_can_compensate_primary_failure"] is False
    assert lock["phase_m_removed"] is True
    assert lock["gretel_pilot_opened"] is False
    assert protocol["model"]["expected_chat_template_sha256"] == EXPECTED_CHAT_TEMPLATE_SHA256
    assert protocol["model"]["allowed_frozen_runtime_profiles"] == ALLOWED_FROZEN_RUNTIME_PROFILES
    assert protocol["prompt_contract"]["phase_o_output_keys"] == ["operation", "table_ref", "column_span_refs"]
    assert protocol["prompt_contract"]["column_span_refs_mapping_equality"] == "order_insensitive_by_object_key"
    assert protocol["generation_contract"]["phase_m_calls"] == 0


def test_clean_reviewer_zip_validator_passes(tmp_path: Path) -> None:
    if os.environ.get("STAGE7E0_A5_SKIP_NESTED_CLEAN_ZIP_FULL_PYTEST") == "1":
        return
    stage_dir = tmp_path / STAGE_NAME
    package_path = tmp_path / PACKAGE_NAME
    build_stage(stage_dir, package_path)
    extract = tmp_path / "extract"
    with zipfile.ZipFile(package_path) as archive:
        archive.extractall(extract)
    result = subprocess.run(
        [sys.executable, "scripts/data/validate_stage7e0_a5_english_preflight.py", "--stage-dir", STAGE_NAME],
        cwd=extract,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    full = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=extract,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=os.environ | {"STAGE7E0_A5_SKIP_NESTED_CLEAN_ZIP_FULL_PYTEST": "1"},
    )
    assert full.returncode == 0, full.stdout + full.stderr


def _expected_input_manifest_for_tests() -> dict:
    path = ROOT / STAGE_NAME / "STAGE7E0_A5_INPUT_MANIFEST.json"
    if path.is_file():
        return read_json(path)
    stage_dir = ROOT / STAGE_NAME
    package_path = ROOT / PACKAGE_NAME
    build_stage(stage_dir, package_path)
    return read_json(path)


def _wrong_phase_o(row: dict) -> dict:
    phase_o = json.loads(canonical_json(row["label_side_expected"]["phase_o"]))
    for column, value in phase_o["column_span_refs"].items():
        if value != "OMIT":
            phase_o["column_span_refs"][column] = "OMIT"
            return phase_o
    first_column = next(iter(phase_o["column_span_refs"]))
    for branch in row["runtime_constraints"]["phase_o_schema"].get("oneOf", [row["runtime_constraints"]["phase_o_schema"]]):
        if branch["properties"]["table_ref"].get("const") == phase_o["table_ref"]:
            domain = branch["properties"]["column_span_refs"]["properties"][first_column]["enum"]
            phase_o["column_span_refs"][first_column] = next(value for value in domain if value != phase_o["column_span_refs"][first_column])
            return phase_o
    raise AssertionError("could not build wrong A5 phase_o")


def _constrained_raw_metadata(row: dict, *, protocol_valid: bool) -> dict:
    grammar = build_phase_o_column_conditioned_constraint_grammar(row["runtime_constraints"]["phase_o_schema"])
    metadata = grammar.metadata() | {
        "backend": "incremental_json_schema_grammar" if protocol_valid else "mock",
        "schema_mode": "incremental_json_schema_grammar" if protocol_valid else "mock",
        "token_level_enforcement": protocol_valid,
        "fallback_to_unconstrained": False,
        "automatic_repair": False,
        "retry": 0,
        "vocab_size": 151936,
        "json_safe_token_count": 2048,
    }
    return metadata


def _server_model_metadata(*, protocol_valid: bool) -> dict:
    return {
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
        "device_map": "auto",
        "max_memory": None,
        "primary_runtime_profile_id": PRIMARY_RUNTIME_PROFILE_ID,
        "runtime_profile_id": PRIMARY_RUNTIME_PROFILE_ID,
        "torch": "2.6.0+cu124",
        "torch_cuda": "12.4",
        "transformers": "5.5.3",
        "tokenizers": "0.22.2",
        "accelerate": "1.14.0",
        "safetensors": "0.5.3",
        "gpu_count": 1,
        "gpu_devices": ["NVIDIA GeForce RTX 4090"],
    }


def _rewrite_summary_hashes(result_dir: Path) -> None:
    summary = read_json(result_dir / "primary_summary.json")
    summary["raw_primary_phase_o_sha256"] = validate_server_result.__globals__["sha256_file"](result_dir / "raw_primary_phase_o_generations.jsonl")
    summary["primary_case_results_sha256"] = validate_server_result.__globals__["sha256_file"](result_dir / "primary_case_results.jsonl")
    write_json(result_dir / "primary_summary.json", summary)


def _write_server_result(result_dir: Path, *, protocol_valid: bool, pass_count: int, accepted_protocol_commit: str | None = None) -> None:
    result_dir.mkdir(parents=True, exist_ok=True)
    rows = load_stage7c_a5_rows(ROOT)
    overrides = {row["sample_id"]: _wrong_phase_o(row) for index, row in enumerate(rows, start=1) if index > pass_count}
    generator = OverrideGenerator(rows, phase_o_overrides=overrides)
    cases = []
    raw_o = []
    for index, row in enumerate(rows, start=1):
        case_result, raw = evaluate_case(row, generator, phase_o_max_new_tokens=512)
        raw["generation_metadata"] = _constrained_raw_metadata(row, protocol_valid=protocol_valid)
        raw["input_tokens"] = 1000 + index
        raw["output_tokens"] = max(1, len(str(raw["raw_output"]).split()))
        raw["latency_sec"] = round(0.1 + index / 1000, 3)
        cases.append(case_result)
        raw_o.append(raw)
    write_jsonl(result_dir / "primary_case_results.jsonl", cases)
    write_jsonl(result_dir / "raw_primary_phase_o_generations.jsonl", raw_o)
    expected_input = _expected_input_manifest_for_tests()
    accepted = accepted_protocol_commit or expected_input["accepted_stage7c_a5_commit"]
    write_json(
        result_dir / "run_manifest.json",
        {
            "stage": STAGE_NAME,
            "accepted_protocol_commit": accepted,
            "git": {
                "accepted_protocol_commit": accepted,
                "execution_commit": None,
                "git_available": False,
                "input_manifest_available": True,
                "frozen_accepted_protocol_commit": expected_input["accepted_stage7c_a5_commit"],
            },
            "stage7c_a5_inputs": {
                "stage7c_a5_lock_sha256": expected_input["stage7c_a5_lock_sha256"],
                "a5_prompt_spec_sha256": expected_input["phase_o_prompt_spec_sha256"],
                "a5_primary_set_sha256": expected_input["primary_set_sha256"],
                "a5_diagnostic_set_sha256": expected_input["diagnostic_set_sha256"],
                "tokenizer_status": expected_input["tokenizer_status"],
                "chat_template_sha256": expected_input["chat_template_sha256"],
            },
            "model": _server_model_metadata(protocol_valid=protocol_valid),
            "primary_case_count": 12,
            "phase_o_prompt_spec_path": "Stage7C_A5_ENGLISH_COLUMN_CONDITIONED_PHASE_O_PROTOCOL_FREEZE/COLUMN_CONDITIONED_PROMPT_SPEC_A5_ENGLISH.json",
            "retry": 0,
            "repair": "none",
            "phase_o_max_new_tokens": 512,
            "phase_m_removed": True,
        },
    )
    write_json(
        result_dir / "primary_summary.json",
        {
            "stage": STAGE_NAME,
            "status": "PASS" if pass_count == 12 else "FAIL",
            "backend": "constrained_hf",
            "protocol_backend": "incremental_json_schema_grammar" if protocol_valid else "mock",
            "model_called": True,
            "gpu_called": True,
            "phase_o_max_new_tokens": 512,
            "phase_m_removed": True,
            "primary_pass_count": f"{pass_count}/12",
            "required_pass_count": "12/12",
            "eleven_of_twelve_allowed": False,
            "diagnostics_run": False,
            "gretel_pilot_opened": False,
            "raw_primary_phase_o_sha256": validate_server_result.__globals__["sha256_file"](result_dir / "raw_primary_phase_o_generations.jsonl"),
            "primary_case_results_sha256": validate_server_result.__globals__["sha256_file"](result_dir / "primary_case_results.jsonl"),
        },
    )


def _run_fake_constrained_primary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, pass_count: int) -> tuple[Path, dict]:
    rows = load_stage7c_a5_rows(ROOT)
    overrides = {row["sample_id"]: _wrong_phase_o(row) for index, row in enumerate(rows, start=1) if index > pass_count}

    class FakeConstrainedGenerator:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def generate(self, *, sample_id: str, messages: list[dict[str, str]], max_new_tokens: int, row: dict) -> CallResult:
            del messages, max_new_tokens
            phase_o = overrides.get(sample_id, row["label_side_expected"]["phase_o"])
            raw = canonical_json(phase_o)
            return CallResult(
                sample_id=sample_id,
                phase="phase_o",
                raw_output=raw,
                input_tokens=1024,
                output_tokens=max(1, len(raw.split())),
                latency_sec=0.01,
                generation_metadata=_constrained_raw_metadata(row, protocol_valid=True),
            )

        def metadata(self) -> dict:
            return _server_model_metadata(protocol_valid=True)

    monkeypatch.setattr(stage7e0_runner, "ConstrainedTransformersChatGenerator", FakeConstrainedGenerator)
    result_dir = tmp_path / f"fake_constrained_{pass_count}"
    args = type(
        "Args",
        (),
        {
            "accepted_protocol_commit": accepted_commit_for_tests(),
            "result_root": str(result_dir),
            "backend": "constrained_hf",
            "model_name_or_path": MODEL_ID,
            "quantization": "none",
            "phase_o_max_new_tokens": 512,
            "max_input_tokens": 28672,
            "seed": 42,
            "trust_remote_code": False,
            "resume": False,
            "run_diagnostics_after_primary_pass": False,
            "skip_git_assertions": True,
            "allow_result_root_inside_git": True,
        },
    )()
    return result_dir, run_stage7e0(args)


def _archive_result_dir(result_dir: Path, archive_path: Path) -> None:
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(result_dir, arcname=result_dir.name)


def test_protocol_invalid_server_result_returns_fail(tmp_path: Path) -> None:
    result_dir = tmp_path / "invalid_result"
    _write_server_result(result_dir, protocol_valid=False, pass_count=12)
    report = validate_server_result(result_dir)
    assert report["evidence_integrity_status"] == "PASS"
    assert report["protocol_compliance_status"] == "FAIL"
    assert report["primary_gate_status"] == "INVALID_NOT_EVALUATED"


def test_valid_scientific_failure_result_is_eligible_but_primary_fail(tmp_path: Path) -> None:
    result_dir = tmp_path / "valid_scientific_failure"
    _write_server_result(result_dir, protocol_valid=True, pass_count=11)
    report = validate_server_result(result_dir)
    assert report["evidence_integrity_status"] == "PASS"
    assert report["protocol_compliance_status"] == "PASS"
    assert report["primary_gate_status"] == "FAIL"
    assert report["status"] == "PASS"


def test_valid_12_of_12_server_result_passes(tmp_path: Path) -> None:
    result_dir = tmp_path / "valid_result"
    _write_server_result(result_dir, protocol_valid=True, pass_count=12)
    report = validate_server_result(result_dir)
    assert report["protocol_compliance_status"] == "PASS"
    assert report["primary_gate_status"] == "PASS"
    assert report["status"] == "PASS"


def test_completed_constrained_primary_12_of_12_validates_and_archives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result_dir, summary = _run_fake_constrained_primary(tmp_path, monkeypatch, pass_count=12)
    assert summary["status"] == "PASS"
    report = validate_server_result(result_dir)
    assert report["protocol_compliance_status"] == "PASS"
    assert report["primary_gate_status"] == "PASS"
    archive_path = tmp_path / "primary_12_of_12.tar.gz"
    _archive_result_dir(result_dir, archive_path)
    assert archive_path.is_file()
    assert archive_path.stat().st_size > 0


def test_completed_constrained_primary_11_of_12_still_validates_and_archives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result_dir, summary = _run_fake_constrained_primary(tmp_path, monkeypatch, pass_count=11)
    assert summary["status"] == "FAIL"
    assert summary["primary_pass_count"] == "11/12"
    assert summary["gretel_pilot_opened"] is False
    report = validate_server_result(result_dir)
    assert report["evidence_integrity_status"] == "PASS"
    assert report["protocol_compliance_status"] == "PASS"
    assert report["primary_gate_status"] == "FAIL"
    assert report["scientific_result_eligible"] is True
    archive_path = tmp_path / "primary_11_of_12.tar.gz"
    _archive_result_dir(result_dir, archive_path)
    assert archive_path.is_file()
    assert archive_path.stat().st_size > 0


def test_server_result_validator_rejects_fake_case_ids(tmp_path: Path) -> None:
    result_dir = tmp_path / "fake_ids"
    _write_server_result(result_dir, protocol_valid=True, pass_count=12)
    cases = read_jsonl(result_dir / "primary_case_results.jsonl")
    raw_o = read_jsonl(result_dir / "raw_primary_phase_o_generations.jsonl")
    for index, row in enumerate(cases, start=1):
        row["sample_id"] = f"case_{index:02d}"
    for index, row in enumerate(raw_o, start=1):
        row["sample_id"] = f"case_{index:02d}"
    write_jsonl(result_dir / "primary_case_results.jsonl", cases)
    write_jsonl(result_dir / "raw_primary_phase_o_generations.jsonl", raw_o)
    _rewrite_summary_hashes(result_dir)
    report = validate_server_result(result_dir)
    assert report["evidence_integrity_status"] == "FAIL"
    assert "primary_case_result_ids_must_equal_locked_a5_primary_ids" in report["evidence_failures"]
    assert "raw_generation_ids_must_equal_locked_a5_primary_ids" in report["evidence_failures"]


def test_server_result_validator_rejects_missing_raw_output(tmp_path: Path) -> None:
    result_dir = tmp_path / "missing_raw_output"
    _write_server_result(result_dir, protocol_valid=True, pass_count=12)
    raw_o = read_jsonl(result_dir / "raw_primary_phase_o_generations.jsonl")
    raw_o[0].pop("raw_output")
    write_jsonl(result_dir / "raw_primary_phase_o_generations.jsonl", raw_o)
    _rewrite_summary_hashes(result_dir)
    report = validate_server_result(result_dir)
    assert report["evidence_integrity_status"] == "FAIL"
    assert any(failure.startswith("raw_generation_missing_required_keys:stage7c_a5_primary_english_001:raw_output") for failure in report["evidence_failures"])


def test_server_result_validator_rejects_fabricated_pass_status(tmp_path: Path) -> None:
    result_dir = tmp_path / "fabricated_pass"
    _write_server_result(result_dir, protocol_valid=True, pass_count=11)
    cases = read_jsonl(result_dir / "primary_case_results.jsonl")
    for row in cases:
        row["status"] = "PASS"
        row["failure_stage"] = None
        if "checks" in row:
            row["checks"] = {key: True for key in row["checks"]}
    write_jsonl(result_dir / "primary_case_results.jsonl", cases)
    summary = read_json(result_dir / "primary_summary.json")
    summary["status"] = "PASS"
    summary["primary_pass_count"] = "12/12"
    write_json(result_dir / "primary_summary.json", summary)
    _rewrite_summary_hashes(result_dir)
    report = validate_server_result(result_dir)
    assert report["evidence_integrity_status"] == "FAIL"
    assert any(failure.startswith("case_result_replay_mismatch:") for failure in report["evidence_failures"])
    assert "primary_pass_count_must_match_replayed_case_rows" in report["evidence_failures"]


def test_server_result_validator_rejects_raw_output_tamper_after_rehashed_summary(tmp_path: Path) -> None:
    result_dir = tmp_path / "tampered_raw_rehashed"
    _write_server_result(result_dir, protocol_valid=True, pass_count=12)
    rows = load_stage7c_a5_rows(ROOT)
    raw_o = read_jsonl(result_dir / "raw_primary_phase_o_generations.jsonl")
    raw_o[0]["raw_output"] = canonical_json(_wrong_phase_o(rows[0]))
    write_jsonl(result_dir / "raw_primary_phase_o_generations.jsonl", raw_o)
    _rewrite_summary_hashes(result_dir)
    report = validate_server_result(result_dir)
    assert report["evidence_integrity_status"] == "FAIL"
    assert "case_result_replay_mismatch:stage7c_a5_primary_english_001" in report["evidence_failures"]
    assert "primary_pass_count_must_match_replayed_case_rows" in report["evidence_failures"]


def test_server_result_validator_rejects_wrong_message_hash(tmp_path: Path) -> None:
    result_dir = tmp_path / "wrong_message_hash"
    _write_server_result(result_dir, protocol_valid=True, pass_count=12)
    raw_o = read_jsonl(result_dir / "raw_primary_phase_o_generations.jsonl")
    raw_o[0]["messages_sha256"] = "0" * 64
    write_jsonl(result_dir / "raw_primary_phase_o_generations.jsonl", raw_o)
    _rewrite_summary_hashes(result_dir)
    report = validate_server_result(result_dir)
    assert report["evidence_integrity_status"] == "FAIL"
    assert "raw_generation_messages_sha256_mismatch:stage7c_a5_primary_english_001" in report["evidence_failures"]


def test_server_result_validator_rejects_wrong_accepted_protocol_commit(tmp_path: Path) -> None:
    result_dir = tmp_path / "wrong_commit"
    _write_server_result(result_dir, protocol_valid=True, pass_count=12, accepted_protocol_commit="WRONG_COMMIT_FOR_PROBE")
    report = validate_server_result(result_dir)
    assert report["evidence_integrity_status"] == "FAIL"
    assert "run_manifest_accepted_protocol_commit_mismatch" in report["evidence_failures"]


def test_runner_rejects_wrong_accepted_protocol_commit_even_when_git_assertions_are_skipped(tmp_path: Path) -> None:
    args = type(
        "Args",
        (),
        {
            "accepted_protocol_commit": "WRONG_COMMIT_FOR_PROBE",
            "result_root": str(tmp_path / "result"),
            "backend": "mock",
            "model_name_or_path": MODEL_ID,
            "quantization": "none",
            "phase_o_max_new_tokens": 512,
            "max_input_tokens": 28672,
            "seed": 42,
            "trust_remote_code": False,
            "resume": False,
            "run_diagnostics_after_primary_pass": False,
            "skip_git_assertions": True,
            "allow_result_root_inside_git": True,
        },
    )()
    with pytest.raises(SystemExit, match="accepted protocol commit WRONG_COMMIT_FOR_PROBE"):
        run_stage7e0(args)


def test_verify_accepted_protocol_commit_argument_accepts_only_frozen_manifest_commit() -> None:
    report = verify_accepted_protocol_commit_argument(accepted_commit_for_tests())
    assert report["input_manifest_available"] is True
    assert report["frozen_accepted_protocol_commit"] == accepted_commit_for_tests()
    with pytest.raises(SystemExit, match="accepted protocol commit WRONG_COMMIT_FOR_PROBE"):
        verify_accepted_protocol_commit_argument("WRONG_COMMIT_FOR_PROBE")
