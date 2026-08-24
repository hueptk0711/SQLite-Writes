from __future__ import annotations

import json
import shutil
from pathlib import Path

import scripts.analysis.validate_stage5_method_freeze as stage5_validator
from scripts.analysis.validate_stage5_method_freeze import validate_stage5


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def stage5_copy(tmp_path: Path, monkeypatch) -> Path:
    target = tmp_path / "project"
    for relative in [
        "configs/stage5/mp_fs_plus_vnext_r1.json",
        "configs/stage5/resolved_mp_fs_plus_vnext_r1.json",
        "configs/final/mp_fs_plus.json",
        "configs/proposed/mp_fs.json",
        "configs/demonstrations/matched_semantic_bank.json",
        "stage5_method_revision_freeze/CONFIRMATION_PROTOCOL_LOCK.json",
        "stage5_method_revision_freeze/EXECUTABLE_FREEZE_MANIFEST.json",
        "stage5_method_revision_freeze/provenance/freeze_manifest.json",
        "scripts/analysis/validate_stage5_method_freeze.py",
    ]:
        source = ROOT / relative
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    manifest = read_json(target / "stage5_method_revision_freeze" / "EXECUTABLE_FREEZE_MANIFEST.json")
    for relative in manifest["method_implementation_files"]:
        source = ROOT / str(relative)
        destination = target / str(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    monkeypatch.setattr(stage5_validator, "PROJECT_ROOT", target)
    return target


def validate_copy(project: Path) -> dict[str, object]:
    return validate_stage5(
        stage5_root=project / "stage5_method_revision_freeze",
        config_path=project / "configs" / "stage5" / "mp_fs_plus_vnext_r1.json",
        resolved_config_path=project
        / "configs"
        / "stage5"
        / "resolved_mp_fs_plus_vnext_r1.json",
        executable_manifest_path=project
        / "stage5_method_revision_freeze"
        / "EXECUTABLE_FREEZE_MANIFEST.json",
    )


def test_stage5_final_config_freezes_exact_d_f_g1_parameters() -> None:
    config = read_json(ROOT / "configs" / "stage5" / "mp_fs_plus_vnext_r1.json")

    assert config["method_id"] == "MP-FS+"
    assert config["method_variant"] == "mp-fs-plus-vnext-r1"
    assert config["method_version"] == "MP-FS+ vNext-R1"
    assert not any(config["stage2_interventions"].values())
    assert config["structured_source_parser"] == {
        "enabled": True,
        "null_literal_policy": "explicit_only",
        "emit_value_provenance": True,
    }
    assert config["free_text_typed_normalization"] == {"enabled": False}
    assert config["constrained_reference_repair"] == {
        "enabled": True,
        "exact_name": True,
        "singleton": True,
        "max_revalidation_attempts": 1,
        "emit_repair_provenance": True,
    }
    assert config["diagnostic_targeted_repair"]["require_unique_candidate"] is True
    assert config["diagnostic_targeted_repair"]["preserve_other_semantics"] is True
    assert config["diagnostic_targeted_repair"]["evidence_span_selection"] is False


def test_stage5_resolved_config_removes_dynamic_inheritance() -> None:
    resolved = read_json(
        ROOT / "configs" / "stage5" / "resolved_mp_fs_plus_vnext_r1.json"
    )

    assert "base_config" not in resolved
    assert "demonstration_bank" not in resolved
    assert resolved["freeze_usage"] == (
        "confirmation runs must use this resolved config directly; "
        "do not resolve base_config or demonstration_bank dynamically"
    )
    assert len(resolved["demonstrations"]["semi_structured"]) == 4
    assert len(resolved["demonstrations"]["free_text"]) == 2


def test_stage5_manifest_records_executable_freeze_boundary() -> None:
    manifest = read_json(
        ROOT / "stage5_method_revision_freeze" / "provenance" / "freeze_manifest.json"
    )

    assert manifest["model_called"] is False
    assert manifest["gpu_called"] is False
    assert manifest["new_evaluation_results"] is False
    assert manifest["final_method"]["component_set"] == ["D", "F", "G1"]
    assert (
        manifest["source_evidence"]["stage4_interpretation"]
        == "diagnostic_used_not_confirmatory_for_D_F_G1"
    )
    assert "post-hoc Stage 4" in manifest["source_evidence"]["F"]
    assert manifest["executable_freeze_manifest"].endswith(
        "EXECUTABLE_FREEZE_MANIFEST.json"
    )


def test_stage5_confirmation_protocol_locks_arms_hypotheses_and_truncation() -> None:
    lock = read_json(
        ROOT / "stage5_method_revision_freeze" / "CONFIRMATION_PROTOCOL_LOCK.json"
    )

    assert lock["status"] == (
        "blocked_until_new_untouched_dataset_registered_and_reviewer_accepted"
    )
    assert lock["dataset_lock"]["new_untouched_dataset_required"] is True
    assert lock["dataset_lock"]["confirmation_run_allowed_now"] is False
    assert lock["confirmation_arms"]["d_g1_included"] is True
    assert lock["confirmation_arms"]["d_f_g1_included"] is True
    assert lock["confirmation_arms"]["full_included"] is False
    assert (
        lock["confirmation_arms"]["generation_strategy"]["d_f_g1_shared_mp_fs_plus"][
            "shares_raw_generation_with"
        ]
        == "d_g1_shared_mp_fs_plus"
    )
    assert lock["hypotheses"]["H1_method_level_confirmation"]["comparison"] == (
        "D_F_G1_vs_original_mp_fs_plus"
    )
    assert lock["hypotheses"]["H2_F_incremental_confirmation"]["comparison"] == (
        "D_F_G1_vs_D_G1"
    )
    assert lock["generation_lock"]["max_input_tokens"] == 28672
    assert lock["generation_lock"]["max_new_tokens"] == 4096
    assert lock["generation_lock"]["do_sample"] is False
    assert lock["generation_lock"]["output_max_new_tokens_policy"] == (
        "record_hit_max_new_tokens_continue_evaluation_score_invalid_or_wrong_state"
        "_as_false_keep_sample_in_denominator"
    )
    assert lock["metric_lock"]["output_max_token_policy"] == (
        "sample_remains_in_denominator_and_is_scored_by_deterministic_pipeline"
    )
    assert lock["execution_gate"]["method_edits_allowed_after_stage5_acceptance"] is False


def test_stage5_validator_passes_checked_in_freeze_package() -> None:
    report = validate_stage5()

    assert report["status"] == "PASS"
    assert report["component_set"] == ["D", "F", "G1"]
    assert report["model_called"] is False
    assert report["gpu_called"] is False
    assert report["confirmation_run_allowed_now"] is False
    assert report["violations"] == []


def test_stage5_validator_rejects_mutated_method_parameters(tmp_path: Path, monkeypatch) -> None:
    project = stage5_copy(tmp_path, monkeypatch)
    config_path = project / "configs" / "stage5" / "mp_fs_plus_vnext_r1.json"
    config = read_json(config_path)
    config["structured_source_parser"]["null_literal_policy"] = "permissive"
    config["constrained_reference_repair"]["exact_name"] = False
    config["constrained_reference_repair"]["singleton"] = False
    config["constrained_reference_repair"]["max_revalidation_attempts"] = 99
    config["diagnostic_targeted_repair"]["require_unique_candidate"] = False
    config["diagnostic_targeted_repair"]["preserve_other_semantics"] = False
    write_json(config_path, config)

    report = validate_copy(project)

    assert report["status"] == "FAIL"
    rules = {row["rule"] for row in report["violations"]}
    assert "config_exact_structured_source_parser" in rules
    assert "config_exact_constrained_reference_repair" in rules
    assert "config_exact_diagnostic_targeted_repair" in rules
    assert "frozen_file_hash_mismatch" in rules


def test_stage5_validator_rejects_mutated_protocol_lock(tmp_path: Path, monkeypatch) -> None:
    project = stage5_copy(tmp_path, monkeypatch)
    lock_path = project / "stage5_method_revision_freeze" / "CONFIRMATION_PROTOCOL_LOCK.json"
    lock = read_json(lock_path)
    lock["model_lock"]["primary_model"]["model_id"] = "changed/model"
    lock["generation_lock"]["context_length"] = 8192
    lock["generation_lock"]["quantization"] = "none"
    lock["prompt_lock"]["prompt_builder"] = "changed.builder"
    lock["statistics_lock"]["cluster_key"] = "db_id"
    lock["statistics_lock"]["bootstrap_seed"] = 123
    lock["execution_gate"]["method_edits_allowed_after_stage5_acceptance"] = True
    write_json(lock_path, lock)

    report = validate_copy(project)

    assert report["status"] == "FAIL"
    rules = {row["rule"] for row in report["violations"]}
    assert "model_lock_exact" in rules
    assert "generation_lock_exact" in rules
    assert "prompt_builder_exact" in rules
    assert "cluster_key" in rules
    assert "bootstrap_seed" in rules
    assert "execution_gate_method_edits_allowed_after_stage5_acceptance" in rules
    assert "frozen_file_hash_mismatch" in rules
