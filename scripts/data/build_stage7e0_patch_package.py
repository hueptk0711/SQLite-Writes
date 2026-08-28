from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATE_STAMP = "20260828"
STAGE = "Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT"


PACKAGE_INPUTS = (
    "pyproject.toml",
    "scripts/data/build_stage7d_v2_a1_implementation.py",
    "scripts/data/validate_stage7d_v2_a1_implementation.py",
    "scripts/data/build_stage7e0_patch_package.py",
    "scripts/server/RUN_STAGE7E0_V2_A1_PREFLIGHT_ON_SERVER.md",
    "scripts/server/run_stage7e0_v2_a1_preflight.py",
    "tests/v2_a1/test_stage7d_v2_a1.py",
    "tests/v2_a1/test_stage7e0_real_generation_preflight.py",
    "src/nldbwrite_v3",
    "stage7b_a1_free_text_slot_discovery_amendment",
    "stage7b_v2_method_specification",
    "stage7c_a1_v2_development_protocol",
    "stage7d_v2_a1_implementation",
)


def git_output(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def ignore_package_noise(_dir: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name == "__pycache__"
        or name.endswith(".pyc")
        or name.endswith(".pyo")
        or name == ".pytest_cache"
    }


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
export PYTHONPATH="$PWD/src:${{PYTHONPATH:-}}"
PY="${{PY:-/home/uet/miniconda3/envs/spin/bin/python}}"

"$PY" -m py_compile scripts/server/run_stage7e0_v2_a1_preflight.py
"$PY" scripts/data/validate_stage7d_v2_a1_implementation.py
"$PY" -m pytest -q tests/v2_a1/test_stage7d_v2_a1.py tests/v2_a1/test_stage7e0_real_generation_preflight.py

"$PY" scripts/server/run_stage7e0_v2_a1_preflight.py \\
  --model-path "$MODEL_PATH" \\
  --output-dir {output_dir_name}

zip -r {output_zip_name} {output_dir_name}
sha256sum {output_zip_name} > {output_zip_name}.sha256
"""


def reviewer_readme(patch: int, reviewer_zip_name: str, server_zip_name: str) -> str:
    commit = git_output("rev-parse", "HEAD")
    branch = git_output("rev-parse", "--abbrev-ref", "HEAD")
    return f"""# Stage7E0 PATCH{patch} Reviewer Package

Scope: server/reviewer packaging fix for Stage7E0 real-generation preflight.

This patch does not modify the V2-A1 method, prompts, frozen schemas, dataset inputs, gold labels, metrics, historical results, train/dev generation, the 481 confirmation set, or LiveSQLBench ground truth.

Why PATCH{patch} exists:
- PATCH1 server execution failed because the ZIP contained only `src/nldbwrite_v3/v2_a1` plus `src/nldbwrite_v3/__init__.py`; importing `nldbwrite_v3` then required the missing `src/nldbwrite_v3/pipeline.py`.
- The pasted command also executed a nested `ssh` from inside the server because `RUN_COMMAND.txt` mixed local upload/login steps with server-side commands.

PATCH{patch} packaging changes:
- Includes full `src/nldbwrite_v3` so package imports are self-contained.
- Adds `RUN_COMMAND_SERVER_ONLY.sh` for commands to run after SSH login and unzip.
- Keeps upload/login instructions separate in `UPLOAD_AND_RUN_FROM_LOCAL.md`.
- Adds this packaging builder so future reviewer/server packages are reproducible.

Reviewer ZIP: `{reviewer_zip_name}`

Server run ZIP: `{server_zip_name}`

Branch: {branch}

Commit: {commit}
"""


def validation_report(patch: int) -> str:
    commit = git_output("rev-parse", "HEAD")
    return f"""# Validation Report

Stage: Stage7E0 V2-A1 Real Generation Preflight PATCH{patch}

Commit validated: {commit}

Local validation required before packaging:
- `python -m py_compile scripts/server/run_stage7e0_v2_a1_preflight.py tests/v2_a1/test_stage7e0_real_generation_preflight.py scripts/data/build_stage7e0_patch_package.py`
- `python -m pytest -q tests/v2_a1/test_stage7e0_real_generation_preflight.py tests/v2_a1/test_stage7d_v2_a1.py`
- `python scripts/data/validate_stage7d_v2_a1_implementation.py`
- `python scripts/data/build_stage7e0_patch_package.py --patch {patch}`
- ZIP integrity checked with Python `zipfile.testzip()`.

Known local limitation:
- Full repository pytest is not reported as PASS. On this Windows host, full-suite runs hit pytest temporary-directory `PermissionError: [WinError 5] Access is denied` and long-running legacy/out-of-scope failures unrelated to Stage7E0 packaging.

Server validation:
- Real Qwen GPU preflight must be run from `RUN_COMMAND_SERVER_ONLY.sh` inside the unzipped package on `uet@222.255.250.24`.
- No train/dev generation, 481 confirmation evaluation, or LiveSQLBench ground-truth access is performed by this package.
"""


def upload_and_run(patch: int, server_zip_name: str, run_dir: str) -> str:
    return f"""# Upload and Run Stage7E0 PATCH{patch}

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
"""


def git_info() -> str:
    return "\n".join(
        [
            "# Git Info",
            "",
            f"Branch: {git_output('rev-parse', '--abbrev-ref', 'HEAD')}",
            "",
            f"Commit: {git_output('rev-parse', 'HEAD')}",
            "",
            f"Commit message: {git_output('log', '-1', '--pretty=%s')}",
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
            if "__pycache__/" in rel or rel.endswith(".pyc") or rel.endswith(".pyo"):
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
    run_dir = f"stage7e0_v2_a1_preflight_patch{patch}_run_{DATE_STAMP}"
    output_dir_name = f"stage7e0_real_generation_preflight_patch{patch}"
    server_output_zip = f"{STAGE}_PATCH{patch}_SERVER_OUTPUT_{DATE_STAMP}.zip"

    staging = output_root / f"stage7e0_patch{patch}_{git_output('rev-parse', '--short', 'HEAD')}"
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
    parser = argparse.ArgumentParser(description="Build Stage7E0 reviewer and server run packages.")
    parser.add_argument("--patch", type=int, default=2)
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "reviewer_packages")
    args = parser.parse_args()

    result = build_package(args.patch, args.output_root.resolve())
    for key, value in result.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
