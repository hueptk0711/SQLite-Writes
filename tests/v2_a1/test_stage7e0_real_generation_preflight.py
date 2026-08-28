from __future__ import annotations

import importlib.util
import shutil
import sys
import uuid
import zipfile
from pathlib import Path

import pytest

from nldbwrite_v3.v2_a1.inventories import build_schema_inventory
from nldbwrite_v3.v2_a1.phase_m_schema import dynamic_schema
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

    def __call__(self, text: str, *, add_special_tokens: bool = False):
        assert add_special_tokens is False
        return type("Encoded", (), {"input_ids": [ord(ch) for ch in text]})()


def test_stage7e0_smoke_fixture_offsets_include_ascii_and_unicode() -> None:
    runner = load_runner()
    fixtures = runner.smoke_fixtures()

    ascii_fixture = fixtures[0]
    ascii_spans = validate_and_sort_spans(ascii_fixture.question, ascii_fixture.phase_o_expected["value_spans"])
    assert [span.text for span in ascii_spans] == ["Alice", "20"]

    unicode_fixture = fixtures[1]
    unicode_spans = validate_and_sort_spans(unicode_fixture.question, unicode_fixture.phase_o_expected["value_spans"])
    assert [span.text for span in unicode_spans] == ["爱丽丝", "20"]


def test_stage7e0_backend_trie_allows_only_schema_valid_candidate_tokens() -> None:
    runner = load_runner()
    candidate = {"operation": "INSERT", "value_spans": [{"start_char": 4, "end_char": 9}]}
    tokenizer = CharTokenizer()
    backend = runner.PrefixTrieConstrainedBackend(tokenizer, [candidate], eos_token_id=tokenizer.eos_token_id)
    backend.set_prompt_token_count(2)
    candidate_tokens = tokenizer(runner.canonical_json(candidate), add_special_tokens=False).input_ids

    first_allowed = backend.allowed_tokens(0, FakeTokenIds([999, 998]))
    assert first_allowed == [candidate_tokens[0]]
    assert ord("`") not in first_allowed

    assert backend.allowed_tokens(0, FakeTokenIds([999, 998, *candidate_tokens])) == [tokenizer.eos_token_id]
    metadata = backend.metadata()
    assert metadata["token_level_enforcement"] is True
    assert metadata["fallback_to_unconstrained"] is False
    assert metadata["automatic_repair"] is False
    assert metadata["retry"] == 0


def test_stage7e0_candidate_validation_rejects_phase_o_markdown_shape() -> None:
    runner = load_runner()
    schema = phase_o_json_schema(ROOT)
    bad = {"operation": "INSERT", "value_spans": [{"start": 4, "end": 9}], "table_ref": "TAB_1"}

    with pytest.raises(V2A1Error) as exc:
        runner.validate_candidate_json_objects("phase_o", [bad], schema)

    assert exc.value.reason_code == "phase_o_candidate_schema_failure"


def test_stage7e0_candidate_validation_enforces_phase_m_slot_evidence_coherence() -> None:
    runner = load_runner()
    fixture = runner.smoke_fixtures()[0]
    inventory = build_schema_inventory(fixture.schema_input)
    spans = validate_and_sort_spans(fixture.question, fixture.phase_o_expected["value_spans"])
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

    with pytest.raises(V2A1Error) as exc:
        runner.validate_candidate_json_objects("phase_m", [bad], schema, operation="INSERT", inventory=inventory, slots=slots)

    assert exc.value.reason_code == "phase_m_slot_evidence_mismatch"


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
