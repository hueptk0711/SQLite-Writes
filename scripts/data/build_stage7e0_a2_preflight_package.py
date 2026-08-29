#!/usr/bin/env python3
"""Build reviewer and server packages for Stage7E0-A2 real preflight."""

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
STAGE = "Stage7E0_A2_REAL_GENERATION_PREFLIGHT"

PACKAGE_INPUTS = (
    "pyproject.toml",
    "scripts/data/build_stage7c_a2_phase_o_prompt_amendment.py",
    "scripts/data/validate_stage7c_a2_phase_o_prompt_amendment.py",
    "scripts/data/build_stage7c_a2_prompt_package.py",
    "scripts/data/build_stage7d_v2_a1_implementation.py",
    "scripts/data/validate_stage7d_v2_a1_implementation.py",
    "scripts/data/build_stage7e0_patch_package.py",
    "scripts/data/build_stage7e0_a2_preflight_package.py",
    "scripts/server/RUN_STAGE7E0_V2_A1_PREFLIGHT_ON_SERVER.md",
    "scripts/server/run_stage7e0_v2_a1_preflight.py",
    "scripts/server/run_stage7e0_a2_real_generation_preflight.py",
    "tests/test_stage7c_a2_phase_o_prompt_amendment.py",
    "tests/v2_a1/test_stage7d_v2_a1.py",
    "tests/v2_a1/test_stage7e0_real_generation_preflight.py",
    "tests/v2_a1/test_stage7e0_a2_real_generation_preflight.py",
    "src/nldbwrite_v3",
    "stage7b_a1_free_text_slot_discovery_amendment",
    "stage7b_v2_method_specification",
    "stage7c_a1_v2_development_protocol",
    "stage7c_a2_phase_o_prompt_feasibility_amendment",
    "stage7d_v2_a1_implementation",
)


def packaged_git_value(label: str, default: str) -> str:
    git_info_path = PROJECT_ROOT / "GIT_INFO.md"
    if not git_info_path.exists():
        return default
    prefix = f"{label}:"
    for line in git_info_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip() or default
    return default


def git_output(*args: str, fallback: str | None = None) -> str:
    if not (PROJECT_ROOT / ".git").exists():
        if fallback is not None:
            return fallback
        raise subprocess.CalledProcessError(128, ["git", *args], output="", stderr="not a package git root")
    result = subprocess.run(["git", *args], cwd=PROJECT_ROOT, check=False, capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    if fallback is not None:
        return fallback
    raise subprocess.CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)


def git_branch() -> str:
    return git_output("rev-parse", "--abbrev-ref", "HEAD", fallback=packaged_git_value("Branch", "NO_GIT_BRANCH"))


def git_commit() -> str:
    return git_output("rev-parse", "HEAD", fallback=packaged_git_value("Commit", "NO_GIT_COMMIT"))


def git_short_commit() -> str:
    commit = git_commit()
    if commit.startswith("NO_GIT"):
        return "nogit"
    return git_output("rev-parse", "--short", "HEAD", fallback=commit[:7])


def git_commit_message() -> str:
    return git_output("log", "-1", "--pretty=%s", fallback=packaged_git_value("Commit message", "NO_GIT_COMMIT_MESSAGE"))


def ignore_package_noise(_dir: str, names: list[str]) -> set[str]:
    return {name for name in names if name == "__pycache__" or name == ".pytest_cache" or name.endswith((".pyc", ".pyo"))}


def copy_input(src_rel: str, dest_root: Path) -> None:
    src = PROJECT_ROOT / src_rel
    dest = dest_root / src_rel
    if src.is_dir():
        shutil.copytree(src, dest, ignore=ignore_package_noise)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def server_only_commands(output_dir_name: str, output_zip_name: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

export CUDA_VISIBLE_DEVICES="${{CUDA_VISIBLE_DEVICES:-0}}"
export MODEL_PATH="${{MODEL_PATH:-/home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242}}"
export PYTHONPATH="$PWD:$PWD/src:${{PYTHONPATH:-}}"
PY="${{PY:-/home/uet/miniconda3/envs/spin/bin/python}}"

"$PY" -m py_compile \\
  scripts/server/run_stage7e0_v2_a1_preflight.py \\
  scripts/server/run_stage7e0_a2_real_generation_preflight.py \\
  scripts/data/build_stage7e0_a2_preflight_package.py \\
  tests/v2_a1/test_stage7e0_a2_real_generation_preflight.py
"$PY" scripts/data/validate_stage7c_a2_phase_o_prompt_amendment.py
"$PY" scripts/data/validate_stage7d_v2_a1_implementation.py
"$PY" -m pytest -q \\
  tests/v2_a1/test_stage7d_v2_a1.py \\
  tests/v2_a1/test_stage7e0_real_generation_preflight.py \\
  tests/v2_a1/test_stage7e0_a2_real_generation_preflight.py

runner_status=0
"$PY" scripts/server/run_stage7e0_a2_real_generation_preflight.py \\
  --model-path "$MODEL_PATH" \\
  --output-dir {output_dir_name} || runner_status=$?

zip -r {output_zip_name} {output_dir_name}
sha256sum {output_zip_name} > {output_zip_name}.sha256
exit "$runner_status"
"""


def reviewer_readme(patch: int, reviewer_zip_name: str, server_zip_name: str) -> str:
    return f"""# Stage7E0-A2 PATCH{patch} Reviewer Package

Scope: real GPU/model preflight for the closed Stage7C-A2 Phase O prompt amendment.

Frozen inputs:
- A2 Phase O prompt from `stage7c_a2_phase_o_prompt_feasibility_amendment/PHASE_O_PROMPT_SPEC_A2.json`.
- Stage7E0 PATCH9 incremental constrained backend.
- Qwen/Qwen2.5-Coder-7B-Instruct revision `c03e6d358207e414f1eca0bb1891e29f1db0e242`.
- `temperature=0`, `do_sample=false`, `retry=0`.
- Same Phase M prompt and same Stage7D implementation.

Primary acceptance:
- `stage7c_a2_fresh_en_two_value_0001`
- `stage7c_a2_fresh_zh_two_value_0002`
- `stage7c_a2_fresh_en_three_value_0003`
- `stage7c_a2_fresh_zh_three_value_0004`

Stage PASS requires 4/4 primary fresh cases to pass exact end-to-end:
Phase O exact, Phase M exact, typed materialization, completeness, compile, transactional preflight ADMITTED, and canonical SQLite target-state exact.

Old PATCH9 Alice cases are run as diagnostic regression only and cannot compensate for any primary fresh failure.

This package does not run train/dev generation, the 481 confirmation set, or LiveSQLBench ground truth.

Reviewer ZIP: `{reviewer_zip_name}`

Server run ZIP: `{server_zip_name}`

Branch: {git_branch()}

Commit: {git_commit()}
"""


def validation_report(patch: int) -> str:
    return f"""# Validation Report

Stage: Stage7E0-A2 Real Generation Preflight PATCH{patch}

Commit validated: {git_commit()}

Local validation required before packaging:
- `python -m py_compile scripts/server/run_stage7e0_a2_real_generation_preflight.py scripts/data/build_stage7e0_a2_preflight_package.py tests/v2_a1/test_stage7e0_a2_real_generation_preflight.py`
- `python scripts/data/validate_stage7c_a2_phase_o_prompt_amendment.py`
- `python scripts/data/validate_stage7d_v2_a1_implementation.py`
- `python -m pytest -q tests/v2_a1/test_stage7e0_a2_real_generation_preflight.py tests/v2_a1/test_stage7e0_real_generation_preflight.py tests/v2_a1/test_stage7d_v2_a1.py`
- `python scripts/data/build_stage7e0_a2_preflight_package.py --patch {patch}`
- ZIP integrity checked with Python `zipfile.testzip()`.

Server validation:
- Run `RUN_COMMAND_SERVER_ONLY.sh` inside the unzipped server package on `uet@222.255.250.24`.
- The server script packages output ZIP/SHA even if the real-generation preflight returns FAIL, then exits with the original runner status.

Known local limitation:
- The real model/GPU run is not executed on this Windows workstation.
"""


def upload_and_run(patch: int, server_zip_name: str, run_dir: str) -> str:
    return f"""# Upload and Run Stage7E0-A2 PATCH{patch}

Run from local PowerShell:

```powershell
scp "{server_zip_name}" uet@222.255.250.24:/home/uet/hue_ptk/
scp "{server_zip_name}.sha256" uet@222.255.250.24:/home/uet/hue_ptk/
ssh uet@222.255.250.24
```

Then run on the server after login:

```bash
cd /home/uet/hue_ptk
rm -rf {run_dir}
mkdir -p {run_dir}
unzip -q {server_zip_name} -d {run_dir}
cd {run_dir}
bash RUN_COMMAND_SERVER_ONLY.sh
```

Download server output from local PowerShell:

```powershell
scp "uet@222.255.250.24:/home/uet/hue_ptk/{run_dir}/{STAGE}_PATCH{patch}_SERVER_OUTPUT_{DATE_STAMP}.zip" "D:\\paper kltn\\text to sql\\w\\s6c_exec\\reviewer_packages\\{STAGE}_PATCH{patch}_SERVER_OUTPUT_{DATE_STAMP}.zip"
scp "uet@222.255.250.24:/home/uet/hue_ptk/{run_dir}/{STAGE}_PATCH{patch}_SERVER_OUTPUT_{DATE_STAMP}.zip.sha256" "D:\\paper kltn\\text to sql\\w\\s6c_exec\\reviewer_packages\\{STAGE}_PATCH{patch}_SERVER_OUTPUT_{DATE_STAMP}.zip.sha256"
```
"""


def git_info() -> str:
    return "\n".join(
        [
            "# Git Info",
            "",
            f"Branch: {git_branch()}",
            "",
            f"Commit: {git_commit()}",
            "",
            f"Commit message: {git_commit_message()}",
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
    server_zip_name = f"{STAGE}_PATCH{patch}_SERVER_RUN_PACKAGE_{DATE_STAMP}.zip"
    run_dir = f"stage7e0_a2_preflight_patch{patch}_run_{DATE_STAMP}"
    output_dir_name = f"stage7e0_a2_real_generation_preflight_patch{patch}"
    server_output_zip = f"{STAGE}_PATCH{patch}_SERVER_OUTPUT_{DATE_STAMP}.zip"

    staging = output_root / f"stage7e0_a2_patch{patch}_{git_short_commit()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for src_rel in PACKAGE_INPUTS:
        copy_input(src_rel, staging)

    write_text(staging / "GIT_INFO.md", git_info())
    write_text(staging / "REVIEWER_README.md", reviewer_readme(patch, reviewer_zip_name, server_zip_name))
    write_text(staging / "VALIDATION_REPORT.md", validation_report(patch))
    write_text(staging / "RUN_COMMAND_SERVER_ONLY.sh", server_only_commands(output_dir_name, server_output_zip))
    write_text(staging / "UPLOAD_AND_RUN_FROM_LOCAL.md", upload_and_run(patch, server_zip_name, run_dir))

    reviewer_zip = output_root / reviewer_zip_name
    server_zip = output_root / server_zip_name
    reviewer_sha = zip_dir(staging, reviewer_zip)
    server_sha = zip_dir(staging, server_zip)
    return {
        "reviewer_zip": str(reviewer_zip),
        "reviewer_sha256": reviewer_sha,
        "server_zip": str(server_zip),
        "server_sha256": server_sha,
        "staging_dir": str(staging),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Stage7E0-A2 reviewer and server run packages.")
    parser.add_argument("--patch", type=int, default=0)
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "reviewer_packages")
    args = parser.parse_args()

    result = build_package(args.patch, args.output_root.resolve())
    for key, value in result.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
