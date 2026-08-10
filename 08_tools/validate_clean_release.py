from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath


EXPECTED_CORRECTED_MATRIX_SHA256 = (
    "d3e3f2b9a8358de4b2c79190d69aadc97d9a6b3a2d087e80b8b5caf1ad101f60"
)
EXPECTED_OFF_TARGET_AUDIT_SHA256 = (
    "ca15c4e9a55cd7bc2c36e42a2e669f369911b3c48bb11ccb33e3399df1e9a3ba"
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def contains_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        if key in value:
            return True
        return any(contains_key(nested, key) for nested in value.values())
    if isinstance(value, list):
        return any(contains_key(nested, key) for nested in value)
    return False


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    for member in members:
        path = PurePosixPath(member.filename)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Unsafe release archive path: {member.filename}")
    return members


def _find_workspace(extracted_root: Path) -> Path:
    candidates = [
        path.parent
        for path in extracted_root.rglob("07_reproducibility/server_final_run/IMPORT_REPORT.json")
        if path.is_file()
    ]
    workspaces = sorted({path.parents[1] for path in candidates})
    if len(workspaces) != 1:
        raise ValueError(
            f"Expected exactly one released workspace, found {len(workspaces)}"
        )
    return workspaces[0]


@contextmanager
def _temporary_work_root(parent: Path):
    """Create a private validation directory without platform-specific temp ACLs."""
    base = (parent / "clean_release_validation_tmp").resolve()
    base.mkdir(parents=True, exist_ok=True)
    root = (base / f"run_{uuid.uuid4().hex}").resolve()
    if root.parent != base or not root.name.startswith("run_"):
        raise ValueError(f"Unsafe validation work directory: {root}")
    root.mkdir()
    try:
        yield root
    finally:
        if root.exists() and root.parent == base and root.name.startswith("run_"):
            last_error: OSError | None = None
            for attempt in range(3):
                try:
                    shutil.rmtree(root)
                    last_error = None
                    break
                except OSError as error:
                    last_error = error
                    if attempt < 2:
                        time.sleep(0.2 * (attempt + 1))
            if last_error is not None:
                raise last_error


def validate_clean_release(archive_path: Path) -> dict[str, object]:
    archive_path = archive_path.resolve()
    python_executable = Path(sys.executable).resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(archive_path)

    stages: dict[str, dict[str, object]] = {}

    def stage(number: int, name: str, message: str) -> float:
        print(f"[{number}/7] {message}", flush=True)
        return time.perf_counter()

    started = stage(1, "checksum", "Verifying release archive...")
    with zipfile.ZipFile(archive_path) as archive:
        members = _safe_members(archive)
    stages["checksum"] = {
        "status": "pass",
        "seconds": round(time.perf_counter() - started, 6),
        "members": len(members),
        "archive_sha256": _sha256_file(archive_path),
    }
    result: dict[str, object]
    cleanup_started = 0.0
    with _temporary_work_root(archive_path.parent) as temp_root:
        extraction_root = temp_root / "extracted"
        output_root = temp_root / "outputs"
        extraction_root.mkdir()
        started = stage(2, "extract", "Extracting archive...")
        with zipfile.ZipFile(archive_path) as archive:
            members = _safe_members(archive)
            archive.extractall(extraction_root, members=members)
        stages["extract"] = {
            "status": "pass",
            "seconds": round(time.perf_counter() - started, 6),
        }

        started = stage(3, "manifests", "Locating released workspace...")
        workspace = _find_workspace(extraction_root)
        source = workspace
        if not source.is_dir():
            raise FileNotFoundError(
                f"Released reporting source is missing: {source}; workspace={workspace}"
            )
        stages["manifests"] = {
            "status": "pass",
            "seconds": round(time.perf_counter() - started, 6),
            "workspace": str(workspace),
        }
        command = [
            str(python_executable),
            str(source / "reproduce_paper.py"),
            "--artifact",
            "final_release",
            "--workspace-root",
            str(workspace),
            "--output-root",
            str(output_root),
        ]
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        started = stage(4, "reproduction", "Running deterministic reproduction...")
        completed = subprocess.run(
            command,
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
            check=False,
        )
        stages["reproduction"] = {
            "status": "pass" if completed.returncode == 0 else "fail",
            "seconds": round(time.perf_counter() - started, 6),
            "output_path": str(output_root),
        }
        if completed.returncode != 0:
            raise RuntimeError(
                "Clean-extraction reproduction failed\n"
                f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
            )

        record_path = (
            output_root
            / "reporting_v2_4_20260801"
            / "REPRODUCTION_V2_4_PASS.json"
        )
        record = json.loads(record_path.read_text(encoding="utf-8"))
        corrected_matrix_path = (
            output_root
            / "reporting_v2_3_20260801"
            / "final_matrix_results_corrected.json"
        )
        if not corrected_matrix_path.is_file():
            raise FileNotFoundError(corrected_matrix_path)
        corrected_matrix = json.loads(
            corrected_matrix_path.read_text(encoding="utf-8")
        )
        downstream_path = (
            output_root
            / "reporting_v2_4_20260801"
            / "downstream_ablation_results.json"
        )
        if not downstream_path.is_file():
            raise FileNotFoundError(downstream_path)
        downstream = json.loads(
            downstream_path.read_text(encoding="utf-8")
        )
        variants = {
            row["variant"]: row
            for row in downstream.get("variants", [])
        }

        def variant_matches(
            name: str,
            *,
            admitted: int,
            correct: int,
            false_accepts: int,
        ) -> bool:
            row = variants.get(name)
            if row is None:
                return False
            samples = int(row.get("samples", 0))
            if samples != 300:
                return False
            return (
                math.isclose(
                    float(row.get("coverage", -1)),
                    admitted / 300,
                    abs_tol=1e-12,
                )
                and math.isclose(
                    float(row.get("target_state_accuracy", -1)),
                    correct / 300,
                    abs_tol=1e-12,
                )
                and int(row.get("false_accept_count", -1))
                == false_accepts
            )

        def corrected_method_matches(
            method_id: str,
            *,
            correct: int,
            off_target: int,
        ) -> bool:
            methods = corrected_matrix.get("methods", {})
            row = methods.get(method_id)
            if not isinstance(row, dict):
                return False
            return (
                int(row.get("target_state_correct_count", -1)) == correct
                and int(row.get("off_target_event_count", -1)) == off_target
                and math.isclose(
                    float(row.get("off_target_modification_rate", -1)),
                    off_target / 300,
                    abs_tol=1e-12,
                )
            )

        started = stage(5, "anchors", "Checking primary anchors...")
        checks = {
            "exit_code_zero": completed.returncode == 0,
            "status_pass": record.get("status") == "pass",
            "samples_300": record.get("corrective_v2_3", {}).get(
                "verified_samples"
            )
            == 300,
            "methods_6": record.get("corrective_v2_3", {}).get(
                "verified_methods"
            )
            == 6,
            "primary_results_unmodified": (
                record.get("primary_results_modified") is False
                and record.get("corrective_v2_3", {}).get(
                    "verified_primary_results_unchanged"
                )
                is True
            ),
            "no_gpu": record.get("gpu_required") is False,
            "no_model_inference": record.get("model_inference_rerun") is False,
            "corrected_matrix_sha256": (
                _sha256_file(corrected_matrix_path)
                == EXPECTED_CORRECTED_MATRIX_SHA256
            ),
            "corrected_matrix_status_pass": (
                corrected_matrix.get("status") == "pass"
            ),
            "corrected_matrix_canonical": (
                corrected_matrix.get("canonical_for_off_target_reporting")
                is True
            ),
            "corrected_matrix_primary_unchanged": (
                corrected_matrix.get("primary_target_state_metrics_modified")
                is False
            ),
            "corrected_matrix_no_db_rerun": (
                corrected_matrix.get("database_execution_repeated") is False
            ),
            "corrected_matrix_no_side_effect_rate": (
                not contains_key(corrected_matrix, "side_effect_rate")
            ),
            "corrected_matrix_audit_provenance": (
                corrected_matrix
                .get("off_target_correction_source", {})
                .get("sha256")
                == EXPECTED_OFF_TARGET_AUDIT_SHA256
            ),
            "corrected_d_fs_m_anchor": corrected_method_matches(
                "D-FS-M",
                correct=258,
                off_target=1,
            ),
            "corrected_j_fs_m_anchor": corrected_method_matches(
                "J-FS-M",
                correct=258,
                off_target=0,
            ),
            "corrected_s_fs_v2_m_anchor": corrected_method_matches(
                "S-FS-v2-M",
                correct=78,
                off_target=0,
            ),
            "corrected_mp_fs_m_anchor": corrected_method_matches(
                "MP-FS-M",
                correct=34,
                off_target=1,
            ),
            "corrected_mp_fs_plus_anchor": corrected_method_matches(
                "MP-FS+",
                correct=148,
                off_target=0,
            ),
            "corrected_gold_mp_anchor": corrected_method_matches(
                "Gold-MP",
                correct=300,
                off_target=0,
            ),
            "cascade_accuracy_0_94": abs(
                float(record.get("exploratory_v2_4", {}).get("cascade_accuracy", -1))
                - 0.94
            )
            < 1e-12,
            "downstream_variants_5": record.get("exploratory_v2_4", {}).get(
                "downstream_ablation_variants"
            )
            == 5,
            "downstream_v0_anchor": variant_matches(
                "V0_no_verifier_no_provenance_no_semantic_gate_no_preflight",
                admitted=217,
                correct=170,
                false_accepts=47,
            ),
            "downstream_v1_anchor": variant_matches(
                "V1_hard_verifier_only",
                admitted=196,
                correct=170,
                false_accepts=26,
            ),
            "downstream_v2_anchor": variant_matches(
                "V2_hard_verifier_plus_provenance",
                admitted=171,
                correct=148,
                false_accepts=23,
            ),
            "downstream_v2_5_anchor": variant_matches(
                "V2_5_plus_semantic_risk_gate",
                admitted=170,
                correct=148,
                false_accepts=22,
            ),
            "downstream_v3_anchor": variant_matches(
                "V3_full_with_transactional_preflight",
                admitted=164,
                correct=148,
                false_accepts=16,
            ),
        }
        if not all(checks.values()):
            raise ValueError(f"Clean-release anchors failed: {checks}")
        stages["anchors"] = {
            "status": "pass",
            "seconds": round(time.perf_counter() - started, 6),
        }
        started = stage(6, "outputs", "Checking generated files...")
        if not record_path.is_file():
            raise FileNotFoundError(record_path)
        stages["outputs"] = {
            "status": "pass",
            "seconds": round(time.perf_counter() - started, 6),
            "record": str(record_path),
        }
        cleanup_started = stage(7, "cleanup", "Cleaning temporary files...")
        result = {
            "status": "pass",
            "archive": str(archive_path),
            "archive_sha256": stages["checksum"]["archive_sha256"],
            "clean_extraction": True,
            "symlinks_created": False,
            "inherited_pythonpath_removed": True,
            "checks": checks,
            "stages": stages,
        }
    stages["cleanup"] = {
        "status": "pass",
        "seconds": round(time.perf_counter() - cleanup_started, 6),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract a release to a new temporary directory and reproduce it."
    )
    parser.add_argument("archive", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = validate_clean_release(args.archive)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload, encoding="utf-8", newline="\n")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
