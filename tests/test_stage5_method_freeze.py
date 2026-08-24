from __future__ import annotations

import json
from pathlib import Path

from scripts.analysis.validate_stage5_method_freeze import validate_stage5


ROOT = Path(__file__).resolve().parents[1]


def test_stage5_final_config_freezes_d_f_g1_only() -> None:
    config = json.loads(
        (ROOT / "configs" / "stage5" / "mp_fs_plus_vnext_r1.json").read_text(
            encoding="utf-8"
        )
    )

    assert config["method_id"] == "MP-FS+"
    assert config["method_variant"] == "mp-fs-plus-vnext-r1"
    assert config["method_version"] == "MP-FS+ vNext-R1"
    assert not any(config["stage2_interventions"].values())
    assert config["structured_source_parser"]["enabled"] is True
    assert config["free_text_typed_normalization"]["enabled"] is False
    assert config["constrained_reference_repair"]["enabled"] is True
    assert config["diagnostic_targeted_repair"]["enabled"] is True
    assert config["diagnostic_targeted_repair"]["evidence_span_boundary"] is True
    assert config["diagnostic_targeted_repair"]["evidence_span_selection"] is False


def test_stage5_manifest_records_scientific_lifecycle_boundary() -> None:
    manifest = json.loads(
        (
            ROOT
            / "stage5_method_revision_freeze"
            / "provenance"
            / "freeze_manifest.json"
        ).read_text(encoding="utf-8")
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


def test_stage5_confirmation_protocol_blocks_run_until_new_dataset() -> None:
    lock = json.loads(
        (
            ROOT / "stage5_method_revision_freeze" / "CONFIRMATION_PROTOCOL_LOCK.json"
        ).read_text(encoding="utf-8")
    )

    assert lock["status"] == (
        "blocked_until_new_untouched_dataset_registered_and_reviewer_accepted"
    )
    assert lock["dataset_lock"]["new_untouched_dataset_required"] is True
    assert lock["dataset_lock"]["confirmation_run_allowed_now"] is False
    assert lock["execution_gate"]["confirmation_gpu_run_allowed_before_reviewer_acceptance"] is False
    assert lock["generation_lock"]["max_input_tokens"] == 28672
    assert lock["generation_lock"]["max_new_tokens"] == 4096
    assert lock["generation_lock"]["do_sample"] is False
    assert lock["metric_lock"]["primary_metric"] == "target_state_correct"
    assert lock["statistics_lock"]["paired_test"] == "exact_mcnemar_two_sided"


def test_stage5_validator_passes_checked_in_freeze_package() -> None:
    report = validate_stage5()

    assert report["status"] == "PASS"
    assert report["component_set"] == ["D", "F", "G1"]
    assert report["model_called"] is False
    assert report["gpu_called"] is False
    assert report["confirmation_run_allowed_now"] is False
    assert report["violations"] == []
