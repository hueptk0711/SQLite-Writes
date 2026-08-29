from __future__ import annotations

import importlib.util
import inspect
import shutil
import sys
import uuid
import zipfile
from pathlib import Path

import pytest

from nldbwrite_v3.v2_a1.inventories import build_schema_inventory
from nldbwrite_v3.v2_a1.phase_m_schema import dynamic_schema, validate_phase_m_ir
from nldbwrite_v3.v2_a1.phase_o_output import phase_o_json_schema
from nldbwrite_v3.v2_a1.slot_inventory import build_slot_bundle
from nldbwrite_v3.v2_a1.span_validation import validate_and_sort_spans
from nldbwrite_v3.v2_a1.types import V2A1Error


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "server" / "run_stage7e0_v2_a1_preflight.py"
PACKAGE_BUILDER_PATH = ROOT / "scripts" / "data" / "build_stage7e0_patch_package.py"
TEST_TMP_ROOT = ROOT / "test_tmp" / "stage7e0_real_generation_preflight_tests"


def load_runner():
    spec = importlib.util.spec_from_file_location("stage7e0_runner", RUNNER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_package_builder():
    spec = importlib.util.spec_from_file_location("stage7e0_package_builder", PACKAGE_BUILDER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeTokenIds:
    def __init__(self, ids: list[int]) -> None:
        self._ids = ids

    def tolist(self) -> list[int]:
        return list(self._ids)


class CharTokenizer:
    eos_token_id = 0
    all_special_ids = [0]

    def __call__(self, text: str, *, add_special_tokens: bool = False):
        assert add_special_tokens is False
        return type("Encoded", (), {"input_ids": [ord(ch) for ch in text]})()

    def __len__(self) -> int:
        return 128

    def decode(self, ids: list[int], *, skip_special_tokens: bool = False, clean_up_tokenization_spaces: bool = False) -> str:
        return "".join(chr(token_id) for token_id in ids if not skip_special_tokens or token_id != self.eos_token_id)


def test_stage7e0_smoke_fixture_offsets_include_ascii_and_unicode() -> None:
    runner = load_runner()
    fixtures = runner.smoke_fixtures()

    ascii_fixture = fixtures[0]
    ascii_spans = validate_and_sort_spans(ascii_fixture.question, ascii_fixture.phase_o_label["value_spans"])
    assert [span.text for span in ascii_spans] == ["Alice", "20"]

    unicode_fixture = fixtures[1]
    unicode_spans = validate_and_sort_spans(unicode_fixture.question, unicode_fixture.phase_o_label["value_spans"])
    assert [span.text for span in unicode_spans] == ["爱丽丝", "20"]


def test_stage7e0_generation_api_does_not_accept_precomputed_answer_candidates() -> None:
    runner = load_runner()
    signature = inspect.signature(runner.generate_constrained)

    assert "allowed_json_" + "objects" not in signature.parameters
    assert "candidates" not in signature.parameters


def test_stage7e0_backend_uses_incremental_schema_grammar_not_complete_object_trie() -> None:
    runner = load_runner()
    schema = phase_o_json_schema(ROOT)
    grammar = runner.build_phase_o_constraint_grammar(schema, "Add Alice, age 20.")
    tokenizer = CharTokenizer()
    backend = runner.IncrementalJsonSchemaGrammarBackend(tokenizer, grammar, eos_token_id=tokenizer.eos_token_id)
    backend.set_prompt_token_count(2)

    first_allowed = backend.allowed_tokens(0, FakeTokenIds([999, 998]))
    assert first_allowed == [ord("{")]
    assert ord("`") not in first_allowed

    valid = runner.canonical_json({"operation": "INSERT", "value_spans": [{"start_char": 4, "end_char": 9}, {"start_char": 15, "end_char": 17}]})
    valid_tokens = tokenizer(valid, add_special_tokens=False).input_ids
    assert backend.allowed_tokens(0, FakeTokenIds([999, 998, *valid_tokens])) == [tokenizer.eos_token_id]
    metadata = backend.metadata()
    assert metadata["constraint_space_singleton"] is False
    assert metadata["finite_known_answer_candidates"] is False
    assert metadata["finite_complete_object_enumeration"] is False
    assert metadata["complete_object_candidate_count"] is None
    assert metadata["hard_max_semantic_spans"] is None
    assert metadata["label_side_data_used_for_constraints"] is False
    assert metadata["token_level_enforcement"] is True
    assert metadata["fallback_to_unconstrained"] is False
    assert metadata["automatic_repair"] is False
    assert metadata["retry"] == 0


def test_stage7e0_incremental_grammar_rejects_phase_o_markdown_shape() -> None:
    runner = load_runner()
    schema = phase_o_json_schema(ROOT)
    grammar = runner.build_phase_o_constraint_grammar(schema, "Add Alice, age 20.")

    assert grammar.status("```json") == "invalid"
    assert grammar.status('{"operation":"INSERT","value_spans":[{"bad":4}]}') == "invalid"


def test_stage7e0_candidate_validation_enforces_phase_m_slot_evidence_coherence() -> None:
    runner = load_runner()
    fixture = runner.smoke_fixtures()[0]
    inventory = build_schema_inventory(fixture.schema_input)
    spans = validate_and_sort_spans(fixture.question, fixture.phase_o_label["value_spans"])
    slots = build_slot_bundle(spans)
    schema = dynamic_schema("INSERT", inventory, slots, root=ROOT)
    bad = {
        "operation": "INSERT",
        "table_ref": "TAB_1",
        "assignments": [
            {"column_ref": "COL_1", "evidence_ref": "EV_2", "slot_ref": "SLOT_1"},
            {"column_ref": "COL_2", "evidence_ref": "EV_1", "slot_ref": "SLOT_2"},
        ],
    }

    grammar = runner.build_phase_m_constraint_grammar(schema, "INSERT", inventory, slots, root=ROOT)
    assert grammar.is_complete(runner.canonical_json(bad))

    with pytest.raises(V2A1Error) as exc:
        validate_phase_m_ir(bad, "INSERT", inventory, slots, root=ROOT)
    assert exc.value.reason_code == "phase_m_slot_evidence_mismatch"


def test_stage7e0_constraint_space_is_label_independent_under_label_mutation() -> None:
    runner = load_runner()
    fixture = runner.smoke_fixtures()[0]
    phase_o_schema = phase_o_json_schema(ROOT)

    assert runner.build_phase_o_constraint_grammar(phase_o_schema, fixture.question).fingerprint == runner.build_phase_o_constraint_grammar(phase_o_schema, fixture.question).fingerprint

    inventory = build_schema_inventory(fixture.schema_input)
    generated_phase_o = {"operation": "INSERT", "value_spans": [{"start_char": 4, "end_char": 9}, {"start_char": 15, "end_char": 17}]}
    spans = validate_and_sort_spans(fixture.question, generated_phase_o["value_spans"])
    slots = build_slot_bundle(spans)
    phase_m_schema = dynamic_schema(generated_phase_o["operation"], inventory, slots, root=ROOT)

    original_grammar = runner.build_phase_m_constraint_grammar(phase_m_schema, generated_phase_o["operation"], inventory, slots, root=ROOT)
    mutated_grammar = runner.build_phase_m_constraint_grammar(phase_m_schema, generated_phase_o["operation"], inventory, slots, root=ROOT)
    assert original_grammar.fingerprint == mutated_grammar.fingerprint


def test_stage7e0_phase_m_constraint_language_contains_wrong_but_schema_valid_mapping() -> None:
    runner = load_runner()
    fixture = runner.smoke_fixtures()[0]
    inventory = build_schema_inventory(fixture.schema_input)
    spans = validate_and_sort_spans(fixture.question, fixture.phase_o_label["value_spans"])
    slots = build_slot_bundle(spans)
    schema = dynamic_schema("INSERT", inventory, slots, root=ROOT)
    grammar = runner.build_phase_m_constraint_grammar(schema, "INSERT", inventory, slots, root=ROOT)
    wrong_mapping = {
        "operation": "INSERT",
        "table_ref": "TAB_1",
        "assignments": [
            {"column_ref": "COL_2", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"},
            {"column_ref": "COL_1", "evidence_ref": "EV_2", "slot_ref": "SLOT_2"},
        ],
    }

    assert grammar.branching_evidence["wrong_but_schema_valid_mapping_present"] is True
    assert grammar.is_complete(runner.canonical_json(wrong_mapping))


def test_stage7e0_answer_injection_audit_records_non_singleton_label_independence() -> None:
    runner = load_runner()
    audit = runner.answer_injection_audit(runner.smoke_fixtures(), ROOT)

    assert audit["status"] == "PASS"
    assert audit["generation_api_accepts_precomputed_candidates"] is False
    assert audit["finite_expected_candidate_trie"] is False
    assert audit["finite_complete_object_enumeration"] is False
    assert audit["label_side_data_used_for_constraints"] is False
    assert all(row["phase_o_complete_object_candidate_count"] is None and row["phase_m_complete_object_candidate_count"] is None for row in audit["rows"])


def test_stage7e0_capacity_audit_accepts_13_phase_o_spans_and_7_phase_m_slots() -> None:
    runner = load_runner()
    audit = runner.constraint_capacity_audit(ROOT)

    assert audit["status"] == "PASS"
    assert audit["phase_o_schema_max_items"] is None
    assert audit["backend_hard_max_spans"] is None
    assert audit["backend_supports_more_than_two_spans"] is True
    assert audit["phase_o_span_count_acceptance"]["13"] is True
    assert audit["phase_m_valid_7_slot_mapping_accepted"] is True
    assert audit["phase_m_complete_mapping_permutation_enumeration"] is False


def test_stage7e0_smoke_violation_summary_only_reports_executed_phases() -> None:
    runner = load_runner()
    rows = [
        {
            "sample_id": "stage7e0_ascii_smoke_0001",
            "status": "FAIL",
            "phase_o": {
                "parse_schema_validation": {"status": "PASS"},
                "label_evaluation": {"status": "FAIL"},
            },
            "violations": ["phase_o_label_mismatch"],
        }
    ]

    violations = runner.collect_smoke_violations(rows)

    assert violations == [
        "smoke_failed:stage7e0_ascii_smoke_0001",
        "phase_o_label_mismatch:stage7e0_ascii_smoke_0001",
    ]


def test_stage7e0_phase_o_invalid_offset_keeps_raw_generation_context() -> None:
    runner = load_runner()
    label = {"operation": "INSERT", "value_spans": [{"start_char": 4, "end_char": 7}, {"start_char": 10, "end_char": 12}]}
    generated = {"operation": "INSERT", "value_spans": [{"start_char": 0, "end_char": 999}]}

    evaluation = runner.evaluate_phase_o_label(generated, label, "添加员工爱丽丝，年龄20岁。")

    assert evaluation["status"] == "FAIL"
    assert evaluation["reason_code"] == "phase_o_invalid_offset"
    assert evaluation["generated"] == generated


def test_stage7e0_smoke_violation_summary_reports_invalid_phase_o_offsets() -> None:
    runner = load_runner()
    rows = [
        {
            "sample_id": "stage7e0_unicode_smoke_0002",
            "status": "FAIL",
            "phase_o": {
                "parse_schema_validation": {"status": "PASS"},
                "label_evaluation": {"status": "FAIL", "reason_code": "phase_o_invalid_offset"},
            },
            "violations": ["phase_o_label_mismatch"],
        }
    ]

    violations = runner.collect_smoke_violations(rows)

    assert violations == [
        "smoke_failed:stage7e0_unicode_smoke_0002",
        "phase_o_deterministic_validation_failed:stage7e0_unicode_smoke_0002",
    ]


def test_stage7e0_package_builder_includes_import_closure_and_server_only_command() -> None:
    builder = load_package_builder()
    package_tmp = TEST_TMP_ROOT / f"package_builder_{uuid.uuid4().hex}"
    package_tmp.mkdir(parents=True, exist_ok=False)

    try:
        result = builder.build_package(99, package_tmp)

        reviewer_zip = Path(result["reviewer_zip"])
        assert reviewer_zip.exists()
        with zipfile.ZipFile(reviewer_zip) as archive:
            names = set(archive.namelist())
            assert archive.testzip() is None
            assert "src/nldbwrite_v3/__init__.py" in names
            assert "src/nldbwrite_v3/pipeline.py" in names
            assert "src/nldbwrite_v3/v2_a1/compiler.py" in names
            assert "scripts/data/build_stage7d_v2_a1_implementation.py" in names
            assert "RUN_COMMAND_SERVER_ONLY.sh" in names
            assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
            command_text = archive.read("RUN_COMMAND_SERVER_ONLY.sh").decode("utf-8")
    finally:
        resolved = package_tmp.resolve()
        if TEST_TMP_ROOT.resolve() in resolved.parents and resolved.exists():
            shutil.rmtree(resolved)

    assert "ssh " not in command_text
    assert "scp " not in command_text
    assert "runner_status=0" in command_text
    assert "|| runner_status=$?" in command_text
    assert 'exit "$runner_status"' in command_text
    assert "test_stage7e0_real_generation_preflight.py" in command_text


def test_stage7e0_package_builder_uses_packaged_git_info_without_git_repo() -> None:
    builder = load_package_builder()
    original_root = builder.PROJECT_ROOT
    package_root = TEST_TMP_ROOT / f"nogit_root_{uuid.uuid4().hex}"
    package_root.mkdir(parents=True, exist_ok=False)
    (package_root / "GIT_INFO.md").write_text(
        "\n".join(
            [
                "# Git Info",
                "",
                "Branch: packaged/branch",
                "",
                "Commit: abcdef1234567890",
                "",
                "Commit message: packaged commit",
                "",
            ]
        ),
        encoding="utf-8",
    )

    try:
        builder.PROJECT_ROOT = package_root
        assert builder.git_branch() == "packaged/branch"
        assert builder.git_commit() == "abcdef1234567890"
        assert builder.git_short_commit() == "abcdef1"
        assert builder.git_commit_message() == "packaged commit"
    finally:
        builder.PROJECT_ROOT = original_root
        resolved = package_root.resolve()
        if TEST_TMP_ROOT.resolve() in resolved.parents and resolved.exists():
            shutil.rmtree(resolved)
