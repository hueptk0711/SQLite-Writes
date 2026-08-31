from __future__ import annotations

import json
import os
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

from scripts.data.build_stage7b_a2_candidate_span_reference import (
    EXPECTED_ASSIGNMENT_COUNT,
    EXPECTED_DESIGN_SAMPLE_COUNT,
    PACKAGE_NAME,
    SELECTED_VARIANT,
    STAGE_NAME,
    build_dynamic_phase_o_schema,
    build_stage,
    generate_candidate_inventory,
    package_reviewer,
    resolve_selected_span_refs,
    serialize_candidate_inventory,
)
from scripts.data.validate_stage7b_a2_candidate_span_reference import validate


RAW_DIR = ROOT.parent.parent / "external_sources" / "gretel_synthetic_text_to_sql_740ab236"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def raw_dir_or_skip() -> Path:
    if not (RAW_DIR / "synthetic_text_to_sql_train.snappy.parquet").is_file():
        pytest.skip("local Gretel parquet raw_dir is not available")
    pytest.importorskip("pyarrow")
    return RAW_DIR


def candidate_map(question: str) -> dict[str, str]:
    return {candidate.text: candidate.span_ref for candidate in generate_candidate_inventory(question)}


def test_candidate_generator_covers_literals_and_long_quoted_text() -> None:
    question = (
        "Insert data: (1,1,10,'In Stock'), budget $5000, rate 45%, "
        "price $29.99, email mina.tran@example.com, and note \"new sustainable fabric\"."
    )
    candidates = candidate_map(question)
    for text in ["1", "10", "In Stock", "5000", "45", "29.99", "mina.tran@example.com", "new sustainable fabric"]:
        assert text in candidates


def test_candidate_refs_are_deterministic_offset_backed_and_deduplicated() -> None:
    question = "Add contact name \"Mina Tran\", email mina.tran@example.com, priority 3, priority 3."
    first = generate_candidate_inventory(question)
    second = generate_candidate_inventory(question)
    assert [candidate.span_ref for candidate in first] == [candidate.span_ref for candidate in second]
    assert [candidate.text for candidate in first] == [candidate.text for candidate in second]
    assert len({(candidate.start_char, candidate.end_char) for candidate in first}) == len(first)
    target = next(candidate for candidate in first if candidate.text == "mina.tran@example.com")
    assert question[target.start_char : target.end_char] == target.text


def test_dynamic_phase_o_schema_uses_exact_per_sample_enum() -> None:
    question = "Add contact name \"Mina Tran\", email mina.tran@example.com, priority 3."
    inventory = generate_candidate_inventory(question)
    schema = build_dynamic_phase_o_schema(inventory)
    enum = schema["properties"]["span_refs"]["items"]["enum"]
    assert enum == [candidate.span_ref for candidate in inventory]
    assert "SPAN_9999" not in enum
    assert schema["properties"]["span_refs"]["uniqueItems"] is True
    assert "pattern" not in schema["properties"]["span_refs"]["items"]


def test_resolver_rejects_unknown_and_duplicate_refs_and_sorts() -> None:
    inventory = generate_candidate_inventory("Insert city Boston, code BOS, amount 12.5.")
    later = inventory[-1].span_ref
    earlier = inventory[0].span_ref
    resolved = resolve_selected_span_refs(inventory, [later, earlier])
    assert [item.span_ref for item in resolved] == [earlier, later]
    with pytest.raises(ValueError, match="Unknown span_refs"):
        resolve_selected_span_refs(inventory, ["SPAN_9999"])
    with pytest.raises(ValueError, match="Duplicate span_refs"):
        resolve_selected_span_refs(inventory, [earlier, earlier])


def test_serialization_hides_offsets_and_uses_compact_tags() -> None:
    question = "Insert email mina.tran@example.com, discount 45%, price $29.99, and label \"VIP\"."
    inventory = generate_candidate_inventory(question)
    serialized = serialize_candidate_inventory(inventory)
    assert "start_char" not in serialized
    assert "end_char" not in serialized
    assert "mina.tran@example.com" in serialized
    assert "EMAIL" in serialized
    assert "NUMBER" in serialized
    assert "QUOTED_TEXT" in serialized
    assert all(line.startswith("SPAN_") and " | " in line for line in serialized.splitlines())


def test_build_and_validator_pass_on_728_non_pilot_design_train(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    report = validate(stage_dir)
    assert report["status"] == "PASS", report["failures"]
    coverage = read_json(stage_dir / "ORACLE_CANDIDATE_COVERAGE_AUDIT.json")
    pareto = read_json(stage_dir / "CANDIDATE_GENERATOR_PARETO_AUDIT.json")
    assert coverage["design_sample_count"] == EXPECTED_DESIGN_SAMPLE_COUNT
    assert coverage["assignment_count"] == EXPECTED_ASSIGNMENT_COUNT
    assert coverage["candidate_generator_variant"] == SELECTED_VARIANT
    assert coverage["covered_assignment_count"] >= 2234
    assert coverage["full_sample_covered_count"] >= 721
    assert coverage["assignment_candidate_coverage"] >= 0.99
    assert coverage["full_sample_candidate_coverage"] >= 0.99
    assert coverage["missing_assignment_count"] == coverage["assignment_count"] - coverage["covered_assignment_count"]
    assert pareto["status"] == "PASS"
    assert pareto["selected_variant"] == SELECTED_VARIANT
    selected = next(row for row in pareto["variants"] if row["variant"] == SELECTED_VARIANT)
    passing_keys = [
        (
            row["candidate_inventory_count_stats"]["p95"],
            row["candidate_inventory_count_stats"]["mean"],
            row["candidate_inventory_count_stats"]["max"],
        )
        for row in pareto["variants"]
        if row["passes_hard_requirements"]
    ]
    selected_key = (
        selected["candidate_inventory_count_stats"]["p95"],
        selected["candidate_inventory_count_stats"]["mean"],
        selected["candidate_inventory_count_stats"]["max"],
    )
    assert selected_key == min(passing_keys)


def test_scope_excludes_pilot_development_dev_and_official_test(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    scope = read_json(stage_dir / "DESIGN_TRAIN_SCOPE_AUDIT.json")
    assert scope["design_train_non_pilot_count"] == 728
    assert scope["development_pilot_pool_count"] == 100
    assert scope["development_dev_count"] == 100
    assert scope["official_test_confirmation_count"] == 51
    assert scope["pilot_ids_in_design_train"] == []
    assert scope["development_dev_ids_in_design_train"] == []
    assert scope["official_test_ids_in_design_train"] == []


def test_phase_o_schema_uses_span_refs_not_numeric_offsets(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    schema = read_json(stage_dir / "PHASE_O_SPAN_REFERENCE_BASE_SCHEMA.json")
    assert schema["required"] == ["operation", "span_refs"]
    assert "span_refs" in schema["properties"]
    assert "start_char" not in schema["properties"]
    assert "end_char" not in schema["properties"]
    assert "pattern" not in schema["properties"]["span_refs"]["items"]
    protocol = read_json(stage_dir / "PHASE_O_SPAN_REFERENCE_PROTOCOL.json")
    assert protocol["model_generates_character_offsets"] is False
    assert protocol["pilot_usage_allowed"] is False
    dynamic_spec = read_json(stage_dir / "DYNAMIC_PHASE_O_SCHEMA_SPEC.json")
    assert dynamic_spec["unknown_span_refs_structurally_impossible"] is True


def test_validator_rejects_coverage_tamper(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    coverage_path = stage_dir / "ORACLE_CANDIDATE_COVERAGE_AUDIT.json"
    coverage = read_json(coverage_path)
    coverage["covered_assignment_count"] -= 1
    coverage["assignment_candidate_coverage"] = coverage["covered_assignment_count"] / coverage["assignment_count"]
    coverage_path.write_text(json.dumps(coverage, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = validate(stage_dir)
    assert report["status"] == "FAIL"
    assert "derived_manifest_hash_mismatch:ORACLE_CANDIDATE_COVERAGE_AUDIT.json" in report["failures"]


def test_validator_rejects_source_manifest_tamper(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    source_path = stage_dir / "SOURCE_INPUT_MANIFEST.json"
    source_manifest = read_json(source_path)
    source_manifest["source_files"][0]["sha256"] = "0" * 64
    source_path.write_text(json.dumps(source_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = validate(stage_dir)
    assert report["status"] == "FAIL"
    assert any(failure.startswith("source_manifest_hash_mismatch:") for failure in report["failures"])


def test_reviewer_package_clean_validator_passes(tmp_path: Path) -> None:
    stage_dir = tmp_path / STAGE_NAME
    package_path = tmp_path / PACKAGE_NAME
    build_stage(stage_dir, raw_dir_or_skip())
    digest = package_reviewer(stage_dir, package_path)
    assert len(digest) == 64
    extract = tmp_path / "extract"
    with zipfile.ZipFile(package_path) as archive:
        assert archive.testzip() is None
        archive.extractall(extract)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([".", "tests/support/windows_py314_pytest_tempdir"])
    result = subprocess.run(
        [
            sys.executable,
            "scripts/data/validate_stage7b_a2_candidate_span_reference.py",
            "--stage-dir",
            STAGE_NAME,
        ],
        cwd=extract,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
