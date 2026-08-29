from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import uuid
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "server" / "run_stage7e0_a2_real_generation_preflight.py"
PACKAGE_BUILDER_PATH = ROOT / "scripts" / "data" / "build_stage7e0_a2_preflight_package.py"
TEST_TMP_ROOT = ROOT / "test_tmp" / "stage7e0_a2_real_generation_preflight_tests"


def load_runner():
    spec = importlib.util.spec_from_file_location("stage7e0_a2_runner", RUNNER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_package_builder():
    spec = importlib.util.spec_from_file_location("stage7e0_a2_package_builder", PACKAGE_BUILDER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fresh_pass_row(sample_id: str) -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "acceptance_role": "primary_fresh_acceptance",
        "status": "PASS",
        "phase_o": {
            "parse_schema_validation": {"status": "PASS"},
            "label_evaluation": {"status": "PASS"},
        },
        "phase_m": {
            "parse_schema_validation": {"status": "PASS"},
            "label_evaluation": {"status": "PASS"},
        },
        "downstream": {
            "preflight": {"admitted": True},
            "target_state_evaluation": {"status": "PASS"},
        },
    }


def test_stage7e0_a2_loads_exact_four_fresh_acceptance_cases() -> None:
    runner = load_runner()
    fixtures = runner.fresh_acceptance_fixtures(ROOT)

    assert [fixture.sample_id for fixture in fixtures] == list(runner.EXPECTED_FRESH_IDS)
    assert [len(fixture.phase_o_label["value_spans"]) for fixture in fixtures] == [2, 2, 3, 3]
    assert all(fixture.acceptance_role == "primary_fresh_acceptance" for fixture in fixtures)
    assert all(set(fixture.schema_input) == {"question", "schema_inventory"} for fixture in fixtures)
    assert fixtures[0].target_state["rows"] == [["Bob", 5000]]
    assert fixtures[2].target_state["rows"] == [["Carol", 31, "Paris"]]


def test_stage7e0_a2_phase_o_renderer_uses_a2_prompt_not_a1() -> None:
    runner = load_runner()
    fixture = runner.fresh_acceptance_fixtures(ROOT)[0]
    inventory = runner.build_schema_inventory(fixture.schema_input)

    messages, messages_sha = runner.render_phase_o_prompt_a2(fixture.question, inventory, root=ROOT)
    a2_spec = json.loads((ROOT / runner.A2_PHASE_O_SPEC).read_text(encoding="utf-8"))
    a1_spec = json.loads((ROOT / "stage7c_a1_v2_development_protocol" / "PHASE_O_PROMPT_SPEC.json").read_text(encoding="utf-8"))

    assert messages[0]["content"] == a2_spec["system_prompt"]
    assert messages[0]["content"] != a1_spec["system_prompt"]
    assert "smallest contiguous source span" in messages[0]["content"]
    assert messages_sha == runner.sha256_text(runner.serialize_prompt_object(messages))
    assert runner.validate_a2_prompt_hashes(ROOT)["status"] == "PASS"


def test_stage7e0_a2_locked_oracle_path_reaches_typed_target_state() -> None:
    runner = load_runner()
    tmp = TEST_TMP_ROOT / f"oracle_{uuid.uuid4().hex}"
    tmp.mkdir(parents=True, exist_ok=False)

    try:
        for fixture in runner.fresh_acceptance_fixtures(ROOT):
            downstream = runner.downstream_check(ROOT, tmp, fixture, fixture.phase_o_label, fixture.phase_m_label)
            assert downstream["preflight"]["admitted"] is True
            assert downstream["target_state_evaluation"]["status"] == "PASS"
            assert downstream["target_state_evaluation"]["actual"] == fixture.target_state
    finally:
        resolved = tmp.resolve()
        if TEST_TMP_ROOT.resolve() in resolved.parents and resolved.exists():
            shutil.rmtree(resolved)


def test_stage7e0_a2_target_state_mismatch_is_a_primary_failure() -> None:
    runner = load_runner()
    fixture = runner.fresh_acceptance_fixtures(ROOT)[0]
    bad_fixture = runner.A2Fixture(
        sample_id=fixture.sample_id,
        question=fixture.question,
        schema_input=fixture.schema_input,
        phase_o_label=fixture.phase_o_label,
        phase_m_label=fixture.phase_m_label,
        synthetic_db_spec=fixture.synthetic_db_spec,
        target_state={**fixture.target_state, "rows": [["Bob", "5000"]]},
        acceptance_role=fixture.acceptance_role,
    )
    tmp = TEST_TMP_ROOT / f"target_mismatch_{uuid.uuid4().hex}"
    tmp.mkdir(parents=True, exist_ok=False)

    try:
        downstream = runner.downstream_check(ROOT, tmp, bad_fixture, bad_fixture.phase_o_label, bad_fixture.phase_m_label)
    finally:
        resolved = tmp.resolve()
        if TEST_TMP_ROOT.resolve() in resolved.parents and resolved.exists():
            shutil.rmtree(resolved)

    assert downstream["preflight"]["admitted"] is True
    assert downstream["target_state_evaluation"]["status"] == "FAIL"
    assert runner.downstream_violations(downstream) == ["target_state_mismatch"]


def test_stage7e0_a2_primary_acceptance_requires_4_of_4_fresh() -> None:
    runner = load_runner()
    rows = [fresh_pass_row(sample_id) for sample_id in runner.EXPECTED_FRESH_IDS]

    assert runner.primary_acceptance_report(rows)["status"] == "PASS"

    rows[3]["status"] = "FAIL"
    rows.append({"sample_id": "stage7e0_ascii_smoke_0001", "acceptance_role": "old_patch9_regression_diagnostic_only", "status": "PASS"})

    report = runner.primary_acceptance_report(rows)
    assert report["status"] == "FAIL"
    assert report["passed_count"] == 3
    assert report["old_patch9_diagnostics_can_compensate_fresh_failures"] is False


def test_stage7e0_a2_primary_violations_ignore_old_diagnostic_failures() -> None:
    runner = load_runner()
    rows = [fresh_pass_row(sample_id) for sample_id in runner.EXPECTED_FRESH_IDS]
    rows.append(
        {
            "sample_id": "stage7e0_unicode_smoke_0002",
            "acceptance_role": "old_patch9_regression_diagnostic_only",
            "status": "FAIL",
            "phase_o": {"parse_schema_validation": {"status": "FAIL"}},
        }
    )

    assert runner.collect_primary_violations(rows) == []
    assert runner.diagnostic_report([rows[-1]])["status"] == "FAIL"
    assert runner.diagnostic_report([rows[-1]])["not_used_for_primary_acceptance"] is True


def test_stage7e0_a2_old_patch9_cases_are_diagnostic_only() -> None:
    runner = load_runner()
    diagnostics = runner.old_patch9_diagnostic_fixtures()

    assert [fixture.question for fixture in diagnostics] == ["Add Alice, age 20.", "添加员工爱丽丝，年龄20岁。"]
    assert all(fixture.acceptance_role == "old_patch9_regression_diagnostic_only" for fixture in diagnostics)
    assert diagnostics[0].target_state["rows"] == [["Alice", 20]]
    assert diagnostics[1].target_state["rows"] == [["爱丽丝", 20]]


def test_stage7e0_a2_package_builder_includes_a2_runner_and_server_command() -> None:
    builder = load_package_builder()
    package_tmp = TEST_TMP_ROOT / f"package_builder_{uuid.uuid4().hex}"
    package_tmp.mkdir(parents=True, exist_ok=False)

    try:
        result = builder.build_package(0, package_tmp)
        reviewer_zip = Path(result["reviewer_zip"])
        assert reviewer_zip.exists()
        with zipfile.ZipFile(reviewer_zip) as archive:
            names = set(archive.namelist())
            assert archive.testzip() is None
            assert "scripts/server/run_stage7e0_a2_real_generation_preflight.py" in names
            assert "scripts/server/run_stage7e0_v2_a1_preflight.py" in names
            assert "stage7c_a2_phase_o_prompt_feasibility_amendment/PHASE_O_PROMPT_SPEC_A2.json" in names
            assert "stage7c_a2_phase_o_prompt_feasibility_amendment/FRESH_SYNTHETIC_SMOKE_SET.jsonl" in names
            assert "tests/v2_a1/test_stage7e0_a2_real_generation_preflight.py" in names
            command_text = archive.read("RUN_COMMAND_SERVER_ONLY.sh").decode("utf-8")
    finally:
        resolved = package_tmp.resolve()
        if TEST_TMP_ROOT.resolve() in resolved.parents and resolved.exists():
            shutil.rmtree(resolved)

    assert "run_stage7e0_a2_real_generation_preflight.py" in command_text
    assert "validate_stage7c_a2_phase_o_prompt_amendment.py" in command_text
    assert "runner_status=0" in command_text
    assert "|| runner_status=$?" in command_text
    assert 'exit "$runner_status"' in command_text
    assert "Stage7E1" not in command_text
    assert "1760" not in command_text
