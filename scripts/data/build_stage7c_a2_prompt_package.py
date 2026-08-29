#!/usr/bin/env python3
"""Build the Stage7C-A2 prompt-amendment reviewer package."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATE_STAMP = "20260829"
STAGE = "Stage7C_A2_PHASE_O_PROMPT_FEASIBILITY_AMENDMENT"

PACKAGE_INPUTS = (
    "pyproject.toml",
    "stage7c_a2_phase_o_prompt_feasibility_amendment",
    "stage7c_a1_v2_development_protocol/STAGE7C_A1_PROTOCOL_LOCK.json",
    "stage7c_a1_v2_development_protocol/PHASE_O_PROMPT_SPEC.json",
    "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json",
    "stage7c_a1_v2_development_protocol/GENERATION_PROTOCOL_A1.json",
    "stage7c_a1_v2_development_protocol/PROMPT_SERIALIZATION_SPEC.json",
    "stage7c_a1_v2_development_protocol/CHAT_TEMPLATE_RENDERING_SPEC.json",
    "stage7c_a1_v2_development_protocol/QUESTION_OFFSET_GUIDE_SPEC.json",
    "stage7c_a1_v2_development_protocol/PHASE_O_OUTPUT_VALIDATION_SPEC.json",
    "stage7d_v2_a1_implementation/STAGE7D_IMPLEMENTATION_LOCK.json",
    "scripts/data/build_stage7c_a2_phase_o_prompt_amendment.py",
    "scripts/data/validate_stage7c_a2_phase_o_prompt_amendment.py",
    "scripts/data/build_stage7c_a2_prompt_package.py",
    "tests/test_stage7c_a2_phase_o_prompt_amendment.py",
)


def git_output(*args: str) -> str:
    if not (PROJECT_ROOT / ".git").exists():
        raise subprocess.CalledProcessError(128, ["git", *args], output="", stderr="not a git repository")
    result = subprocess.run(["git", *args], cwd=PROJECT_ROOT, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def packaged_git_value(label: str, default: str) -> str:
    git_info_path = PROJECT_ROOT / "GIT_INFO.md"
    prefix = f"{label}:"
    if git_info_path.is_file():
        for line in git_info_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(prefix):
                return line.removeprefix(prefix).strip() or default
    lock_path = PROJECT_ROOT / "stage7c_a2_phase_o_prompt_feasibility_amendment" / "STAGE7C_A2_LOCK.json"
    if lock_path.is_file():
        try:
            source_git = json.loads(lock_path.read_text(encoding="utf-8")).get("source_git", {})
        except json.JSONDecodeError:
            source_git = {}
        key = {"Branch": "branch", "Commit": "commit", "Commit message": "commit_message"}.get(label)
        if key and source_git.get(key):
            return str(source_git[key])
    return default


def git_value(*args: str, label: str, default: str) -> str:
    try:
        return git_output(*args)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return packaged_git_value(label, default)


def git_short_commit() -> str:
    commit = git_value("rev-parse", "HEAD", label="Commit", default="nogit")
    if commit in {"", "not_available_in_clean_package", "nogit"}:
        return "nogit"
    try:
        return git_output("rev-parse", "--short", "HEAD")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return commit[:7]


def ignore_noise(_dir: str, names: list[str]) -> set[str]:
    return {name for name in names if name in {"__pycache__", ".pytest_cache"} or name.endswith((".pyc", ".pyo"))}


def copy_input(rel: str, staging: Path) -> None:
    source = PROJECT_ROOT / rel
    dest = staging / rel
    if source.is_dir():
        shutil.copytree(source, dest, ignore=ignore_noise)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def git_info() -> str:
    return "\n".join(
        [
            "# Git Info",
            "",
            f"Branch: {git_value('rev-parse', '--abbrev-ref', 'HEAD', label='Branch', default='not_available_in_clean_package')}",
            "",
            f"Commit: {git_value('rev-parse', 'HEAD', label='Commit', default='not_available_in_clean_package')}",
            "",
            f"Commit message: {git_value('log', '-1', '--pretty=%s', label='Commit message', default='not_available_in_clean_package')}",
            "",
            "Remote: https://github.com/hueptk0711/SQLite-Writes.git",
            "",
        ]
    )


def zip_dir(src_dir: Path, zip_path: Path) -> str:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(src_dir.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(src_dir).as_posix()
            if "__pycache__/" in rel or rel.endswith((".pyc", ".pyo")):
                continue
            archive.write(path, rel)
    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
    if bad is not None:
        raise RuntimeError(f"Bad ZIP member: {bad}")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest().upper()
    write_text(zip_path.with_suffix(zip_path.suffix + ".sha256"), f"{digest}  {zip_path.name}\n")
    return digest


def build_package(patch: int, output_root: Path) -> dict[str, str]:
    reviewer_zip_name = f"{STAGE}_PATCH{patch}_FINAL_REVIEWER_PACKAGE_{DATE_STAMP}.zip"
    staging = output_root / f"stage7c_a2_patch{patch}_{git_short_commit()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for rel in PACKAGE_INPUTS:
        copy_input(rel, staging)
    write_text(staging / "GIT_INFO.md", git_info())
    reviewer_zip = output_root / reviewer_zip_name
    digest = zip_dir(staging, reviewer_zip)
    return {"reviewer_zip": str(reviewer_zip), "reviewer_sha256": digest, "staging_dir": str(staging)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--patch", type=int, default=0)
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "reviewer_packages")
    args = parser.parse_args()
    result = build_package(args.patch, args.output_root.resolve())
    for key, value in result.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
