from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path
from typing import Iterable


RELEASE_ID = "mp_fs_plus_code_and_results_reviewer_v2_20260805"
FIXED_ZIP_TIME = (2026, 8, 5, 0, 0, 0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _included_files(workspace: Path) -> Iterable[Path]:
    roots = (
        workspace / "START_HERE.md",
        workspace / "README.md",
        workspace / "pyproject.toml",
        workspace / "reproduce_paper.py",
        workspace / "build_figures.py",
        workspace / "build_reporting_release.py",
        workspace / "run_backend_swap.py",
        workspace / "run_common_safety_replay.py",
        workspace / "run_exploratory_v2_4.py",
        workspace / "requirements-inference.lock.txt",
        workspace / "requirements-reporting.lock.txt",
        workspace / "release_config.json",
        workspace / "uv.lock",
        workspace / "00_START_HERE",
        workspace / "EXPERIMENT_FREEZE.md",
        workspace / "src",
        workspace / "tests",
        workspace / "scripts",
        workspace / "configs",
        workspace / "artifacts",
        workspace / "schemas",
        workspace / "docs",
        workspace / "archive" / "frozen_inference_source.zip",
        workspace / "archive" / "frozen_inference_source.zip.sha256",
        workspace / "archive" / "README.md",
        workspace / "03_protocol_and_data" / "final_holdout_release",
        workspace / "03_protocol_and_data" / "calibration_evidence",
        workspace / "03_protocol_and_data" / "robustness_extension_20260801",
        workspace / "04_results" / "00_incoming_from_server",
        workspace / "04_results" / "02_paper_ready",
        workspace
        / "04_results"
        / "03_analysis_work"
        / "reporting_v2_3_20260801",
        workspace
        / "04_results"
        / "03_analysis_work"
        / "reporting_v2_4_20260801",
        workspace
        / "04_results"
        / "03_analysis_work"
        / "second_model_qwen14b_20260801",
        workspace
        / "04_results"
        / "03_analysis_work"
        / "cross_family_yi_20260802",
        workspace
        / "04_results"
        / "03_analysis_work"
        / "state_scope_audit_20260805",
        workspace / "07_reproducibility" / "REPRODUCIBILITY_RECORD.md",
        workspace / "07_reproducibility" / "server_final_run",
        workspace / "07_reproducibility" / "exact_v2_source_20260714_rev1",
        workspace / "08_tools",
    )
    excluded_names = {
        "__pycache__",
        ".pytest_cache",
        ".venv",
        "test_tmp",
        "visual_qa",
        "BUNDLE_MANIFEST.json",
    }
    excluded_suffixes = {".aux", ".bbl", ".blg", ".log"}
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            raise ValueError(f"Required release path is missing: {root}")
        paths: list[Path] = []
        if root.is_file():
            paths.append(root)
        else:
            for current, directories, filenames in os.walk(root, topdown=True):
                directories[:] = sorted(
                    directory
                    for directory in directories
                    if directory not in excluded_names
                    and not directory.endswith(".egg-info")
                    and not directory.startswith("_nldb_v24_")
                )
                paths.extend(Path(current) / name for name in sorted(filenames))
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            relative_parts = path.relative_to(workspace).parts
            if any(part in excluded_names for part in relative_parts):
                continue
            if path.suffix.lower() in excluded_suffixes:
                continue
            if (
                path.name.startswith("MP_FS_PLUS_IEEE_ACCESS_OVERLEAF_REVISION_")
                and "V2_4_20260801" not in path.name
            ):
                continue
            if "_backend_swap_holdout" in relative_parts:
                continue
            if (
                "second_model_qwen14b_20260801" in relative_parts
                and "00_extracted" in relative_parts
            ):
                continue
            yield path


def _write_zip_entry(
    archive: zipfile.ZipFile,
    name: str,
    payload: bytes,
) -> None:
    info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, payload)


def build_release(workspace: Path, output_dir: Path) -> dict[str, object]:
    workspace = workspace.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"{RELEASE_ID}.zip"
    checksum_path = output_dir / f"{RELEASE_ID}.zip.sha256"

    files = list(_included_files(workspace))
    manifest_files = {
        path.relative_to(workspace).as_posix(): {
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in files
    }
    manifest = {
        "manifest_version": 2,
        "release_id": RELEASE_ID,
        "release_date_utc": "2026-08-05",
        "status": "internal_release_candidate",
        "paper_result_eligible": True,
        "predictions_modified": False,
        "primary_results_modified": False,
        "requires_gpu_to_reproduce_reporting": False,
        "contains_model_weights": False,
        "contains_extracted_duplicate_result_tree": False,
        "notes": [
            "The verified result archive is included and extracted on demand.",
            "A public license and data-license review are still required before publication.",
            "The completed post-hoc backend-swap analysis and exact hash-verified frozen v2 source are included; no model output was regenerated.",
            "The common transactional-preflight replay is post-hoc and does not modify primary results.",
            "The Qwen2.5-Coder-14B result is included as labeled post-hoc same-family robustness evidence and is not a primary result.",
            "Reporting v2.3 counts off-target changes even when the target state is also wrong and resolves Windows provenance paths portably.",
            "Reporting v2.4 adds a post-hoc J-then-D common-preflight cascade with per-sample rows and no gold-label policy input.",
            "Reporting v2.4 adds a frozen-plan downstream boundary ablation; V3 is anchored to primary MP-FS+ coverage and target-state accuracy.",
            "The common-safety replay resolves the shipped archive from archive_filename and a portable fallback; absolute host paths are provenance only.",
            "A clean-extraction integration validator removes inherited PYTHONPATH, creates no symlinks, and runs the one-command reproduction in a temporary directory.",
            "Wilson intervals, a zero-event upper bound, Holm-corrected 7B--14B paired tests, efficiency quantiles, and a dataset redundancy audit are included.",
            "This reviewer release has one active source tree at the archive root; the original inference source is a read-only ZIP under archive/.",
            "Strict full-state and off-target evaluation compare all persistent user tables; a 1,800-pair audit found no changes to frozen target, strict, or off-target results.",
            "The downstream ablation does not claim to isolate prompt, grounding, evidence-extraction, materialization, or generation effects.",
        ],
        "file_count_excluding_manifest": len(manifest_files),
        "files": manifest_files,
    }
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    with zipfile.ZipFile(archive_path, "w") as archive:
        prefix = f"{RELEASE_ID}/CODE_AND_RESULTS"
        for path in files:
            relative = path.relative_to(workspace).as_posix()
            _write_zip_entry(archive, f"{prefix}/{relative}", path.read_bytes())
        _write_zip_entry(
            archive,
            f"{RELEASE_ID}/RELEASE_MANIFEST.json",
            manifest_payload,
        )

    archive_sha256 = sha256_file(archive_path)
    checksum_path.write_text(
        f"{archive_sha256}  {archive_path.name}\n",
        encoding="ascii",
        newline="\n",
    )
    return {
        "status": "pass",
        "release_id": RELEASE_ID,
        "archive": str(archive_path),
        "archive_sha256": archive_sha256,
        "checksum": str(checksum_path),
        "file_count_excluding_manifest": len(manifest_files),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a deterministic internal reporting release candidate."
    )
    parser.add_argument("--workspace-root")
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    default_workspace = Path(__file__).resolve().parent
    workspace = (
        Path(args.workspace_root).resolve()
        if args.workspace_root
        else default_workspace
    )
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else workspace / "09_release_candidate"
    )
    print(json.dumps(build_release(workspace, output_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
