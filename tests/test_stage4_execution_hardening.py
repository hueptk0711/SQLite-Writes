from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.analysis.analyze_stage4_fresh_7b import (
    cluster_bootstrap_accuracy_difference,
    mcnemar_exact_pvalue,
    paired_counts,
)
from scripts.server.run_stage4_fresh_7b import (
    initialize_or_validate_result_root,
    raw_generation_audit,
    verify_raw_complete,
)
from scripts.server.run_stage4_gpu_preflight import (
    EXPECTED_GPU_PYTHON_MAJOR_MINOR,
    assert_environment_versions,
    environment_version_audit,
)


def write_ids(path: Path, ids: list[str]) -> None:
    path.write_text("\n".join(ids) + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )


def write_lock(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "--extra-index-url https://download.pytorch.org/whl/cu124",
                "torch==2.6.0+cu124",
                "transformers==5.5.3",
                "accelerate==1.14.0",
                "bitsandbytes==0.47.0",
                "tokenizers==0.22.2",
                "safetensors==0.5.3",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )


def matching_environment() -> dict[str, object]:
    return {
        "python": EXPECTED_GPU_PYTHON_MAJOR_MINOR + ".1 (stage4 synthetic)",
        "torch_version": "2.6.0+cu124",
        "transformers_version": "5.5.3",
        "accelerate_version": "1.14.0",
        "bitsandbytes_version": "0.47.0",
        "tokenizers_version": "0.22.2",
        "safetensors_version": "0.5.3",
    }


def test_resume_existing_result_root_accepts_matching_execution_lock(tmp_path: Path) -> None:
    result_root = tmp_path / "stage4_results"
    lock = {
        "accepted_protocol_commit": "abc",
        "runner_plan_sha256": "plan",
        "sample_ids_sha256": "ids",
        "inference_config_sha256": "inf",
        "dependency_lock_sha256": "deps",
        "model_identity": {"model": "qwen"},
    }
    initialize_or_validate_result_root(
        result_root=result_root,
        resume=False,
        execution_lock=lock,
    )
    initialize_or_validate_result_root(
        result_root=result_root,
        resume=True,
        execution_lock=lock,
    )


def test_resume_rejects_execution_lock_drift(tmp_path: Path) -> None:
    result_root = tmp_path / "stage4_results"
    lock = {
        "accepted_protocol_commit": "abc",
        "runner_plan_sha256": "plan",
        "sample_ids_sha256": "ids",
        "inference_config_sha256": "inf",
        "dependency_lock_sha256": "deps",
        "model_identity": {"model": "qwen"},
    }
    initialize_or_validate_result_root(result_root=result_root, resume=False, execution_lock=lock)
    drifted = {**lock, "sample_ids_sha256": "changed"}
    with pytest.raises(SystemExit):
        initialize_or_validate_result_root(result_root=result_root, resume=True, execution_lock=drifted)


def test_existing_result_root_without_resume_stops(tmp_path: Path) -> None:
    result_root = tmp_path / "stage4_results"
    result_root.mkdir()
    with pytest.raises(SystemExit):
        initialize_or_validate_result_root(
            result_root=result_root,
            resume=False,
            execution_lock={"accepted_protocol_commit": "abc"},
        )


@pytest.mark.parametrize("status", ["oom", "generation_error", "input_truncation_error"])
def test_non_success_raw_row_is_not_complete(tmp_path: Path, status: str) -> None:
    ids = tmp_path / "ids.txt"
    raw = tmp_path / "raw.jsonl"
    write_ids(ids, ["s1", "s2"])
    write_jsonl(
        raw,
        [
            {"sample_id": "s1", "status": "success", "raw_output": "ok"},
            {"sample_id": "s2", "status": status, "raw_output": ""},
        ],
    )
    audit = raw_generation_audit(raw, ids, require_complete=True)
    assert audit["complete"] is False
    assert audit["non_success_count"] == 1
    with pytest.raises(SystemExit):
        verify_raw_complete(raw, ids)


def test_input_truncated_success_row_is_not_complete(tmp_path: Path) -> None:
    ids = tmp_path / "ids.txt"
    raw = tmp_path / "raw.jsonl"
    write_ids(ids, ["s1"])
    write_jsonl(raw, [{"sample_id": "s1", "status": "success", "input_truncated": True}])
    with pytest.raises(SystemExit):
        verify_raw_complete(raw, ids)


def test_environment_exact_match_passes(tmp_path: Path) -> None:
    lock = tmp_path / "requirements-inference.lock.txt"
    write_lock(lock)
    audit = assert_environment_versions(
        environment=matching_environment(),
        dependency_lock_path=lock,
    )
    assert audit["status"] == "PASS"
    assert all(row["match"] for row in audit["packages"])


def test_environment_mismatch_stops(tmp_path: Path) -> None:
    lock = tmp_path / "requirements-inference.lock.txt"
    write_lock(lock)
    env = matching_environment()
    env["transformers_version"] = "5.5.2"
    audit = environment_version_audit(environment=env, dependency_lock_path=lock)
    assert audit["status"] == "STOP"
    with pytest.raises(SystemExit):
        assert_environment_versions(environment=env, dependency_lock_path=lock)


def test_stage4_gpu_python_expectation_matches_historical_verified_environment() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (
            repo_root
            / "07_reproducibility"
            / "server_final_run"
            / "environment_manifest_final_server.json"
        ).read_text(encoding="utf-8")
    )
    lock_path = repo_root / "requirements-inference.lock.txt"
    lock_sha = hashlib.sha256(lock_path.read_bytes()).hexdigest()

    assert EXPECTED_GPU_PYTHON_MAJOR_MINOR == "3.12"
    assert str(manifest["python"]["version"]).startswith("3.12.")
    assert manifest["dependency_lock"]["sha256"] == lock_sha
    assert manifest["packages"]["torch"] == "2.6.0+cu124"
    assert manifest["packages"]["transformers"] == "5.5.3"
    assert manifest["packages"]["accelerate"] == "1.14.0"
    assert manifest["packages"]["bitsandbytes"] == "0.47.0"
    assert manifest["packages"]["tokenizers"] == "0.22.2"
    assert manifest["packages"]["safetensors"] == "0.5.3"
    assert manifest["torch"]["cuda_runtime"] == "12.4"
    assert manifest["torch"]["cuda_available"] is True


def test_cluster_bootstrap_is_seed_deterministic() -> None:
    baseline = {"s1": True, "s2": False, "s3": False, "s4": True}
    method = {"s1": True, "s2": True, "s3": False, "s4": False}
    source_groups = {"s1": "g1", "s2": "g1", "s3": "g2", "s4": "g3"}
    first = cluster_bootstrap_accuracy_difference(
        baseline=baseline,
        method=method,
        source_groups=source_groups,
        replicates=200,
        seed=240822,
    )
    second = cluster_bootstrap_accuracy_difference(
        baseline=baseline,
        method=method,
        source_groups=source_groups,
        replicates=200,
        seed=240822,
    )
    assert first == second
    assert first["observed_difference"] == 0.0
    assert paired_counts(baseline, method)["baseline_only_correct"] == 1
    assert paired_counts(baseline, method)["method_only_correct"] == 1
    assert mcnemar_exact_pvalue(1, 1) == 1.0
