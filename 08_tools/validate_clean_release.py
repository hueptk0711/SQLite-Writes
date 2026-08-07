from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
            "cascade_accuracy_0_94": abs(
                float(record.get("exploratory_v2_4", {}).get("cascade_accuracy", -1))
                - 0.94
            )
            < 1e-12,
            "downstream_variants_4": record.get("exploratory_v2_4", {}).get(
                "downstream_ablation_variants"
            )
            == 4,
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
