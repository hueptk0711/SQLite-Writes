#!/usr/bin/env python3
"""Build Stage7E0-A3 English real-generation preflight artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scripts.server.run_stage7e0_a3_english import (  # noqa: E402
    A3_PROMPT_SPEC_REL,
    DEFAULT_MODEL_PATH,
    EXPECTED_CHAT_TEMPLATE_SHA256,
    MODEL_ID,
    MODEL_REVISION,
    STAGE7C_A3_DIR,
    run_stage7e0,
)


STAGE_NAME = "Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT"
PATCH_NAME = "PATCH0"
PACKAGE_NAME = f"{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_20260830.zip"


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(canonical_text(text).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl", ".md", ".py", ".txt", ".toml", ".sh"}:
        data = canonical_text(data.decode("utf-8-sig")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def git_output(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def stage7c_inputs() -> dict[str, Any]:
    rows = read_jsonl(PROJECT_ROOT / STAGE7C_A3_DIR / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl")
    a3_spec = read_json(PROJECT_ROOT / A3_PROMPT_SPEC_REL)
    phase_m = read_json(PROJECT_ROOT / "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json")
    return {
        "stage7c_a3_dir": STAGE7C_A3_DIR,
        "a3_prompt_spec_path": A3_PROMPT_SPEC_REL,
        "a3_prompt_spec_sha256": sha256_file(PROJECT_ROOT / A3_PROMPT_SPEC_REL),
        "a3_phase_o_system_sha256": sha256_text(a3_spec["system_prompt"]),
        "a3_phase_o_user_template_sha256": sha256_text(a3_spec["user_prompt_template"]),
        "phase_m_prompt_spec_path": "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json",
        "phase_m_system_sha256": sha256_text(phase_m["system_prompt"]),
        "phase_m_user_template_sha256": sha256_text(phase_m["user_prompt_template"]),
        "fresh_primary_case_count": len(rows),
        "fresh_primary_case_ids": [row["sample_id"] for row in rows],
        "fresh_primary_smoke_set_sha256": sha256_file(PROJECT_ROOT / STAGE7C_A3_DIR / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl"),
        "stage7c_a3_lock_sha256": sha256_file(PROJECT_ROOT / STAGE7C_A3_DIR / "STAGE7C_A3_LOCK.json"),
    }


def runner_protocol(accepted_commit: str) -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "accepted_protocol_commit": accepted_commit,
        "model": {
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "default_server_model_path": DEFAULT_MODEL_PATH,
            "expected_chat_template_sha256": EXPECTED_CHAT_TEMPLATE_SHA256,
            "quantization_default": "4bit",
            "deterministic_decoding": {"do_sample": False, "seed": 42},
        },
        "prompt_contract": {
            "phase_o_prompt_spec_path": A3_PROMPT_SPEC_REL,
            "phase_o_load_policy": "load exact Stage7C-A3 prompt spec directly before generation",
            "phase_m_prompt_spec_path": "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json",
            "phase_m_changed": False,
            "offset_guide_serializer_changed": False,
        },
        "generation_contract": {
            "primary_cases": "8 fresh Stage7C-A3 English cases",
            "calls_per_successful_case": 2,
            "phase_o_calls": 1,
            "phase_m_calls": 1,
            "zero_shot": True,
            "examples": [],
            "retry": 0,
            "repair": "none",
            "backend": "same PATCH9 incremental parser/materializer/compiler/preflight path",
        },
        "acceptance": {
            "required_pass_count": "8/8",
            "seven_of_eight_allowed": False,
            "averaging_allowed": False,
            "diagnostics_after_primary_freeze_only": ["4 A2 fresh English cases", "2 old PATCH9/Alice diagnostics"],
            "gretel_pilot_gate": "open 100-sample Gretel development-train pilot only after 8/8 primary PASS",
        },
    }


def server_commands(accepted_commit: str, package_name: str) -> str:
    return f"""# Stage7E0-A3 English Server Run Commands

Run these on the Windows machine to upload the package:

```powershell
scp "{package_name}" uet@222.255.250.24:/home/uet/hue_ptk/
scp "{package_name}.sha256" uet@222.255.250.24:/home/uet/hue_ptk/
```

Run these on the GPU server:

```bash
ssh uet@222.255.250.24
cd /home/uet/hue_ptk
sha256sum -c {package_name}.sha256
rm -rf {STAGE_NAME}_{PATCH_NAME}_runner
mkdir -p {STAGE_NAME}_{PATCH_NAME}_runner
unzip -q {package_name} -d {STAGE_NAME}_{PATCH_NAME}_runner
cd {STAGE_NAME}_{PATCH_NAME}_runner
export HF_HOME="$HOME/hue_ptk/hf_cache"
export TRANSFORMERS_OFFLINE=1
/home/uet/hue_ptk/mp_fs_plus_final_gpu_20260731/.venv_gpu/bin/python scripts/server/run_stage7e0_a3_english.py \\
  --accepted-protocol-commit {accepted_commit} \\
  --result-root /home/uet/hue_ptk/stage7e0_a3_english_real_generation_preflight_results \\
  --model-name-or-path {DEFAULT_MODEL_PATH}
```

If the run is interrupted before completion, resume with the same command plus:

```bash
  --resume
```

After completion, copy these result files back for review:

```powershell
scp -r uet@222.255.250.24:/home/uet/hue_ptk/stage7e0_a3_english_real_generation_preflight_results .
```
"""


def validation_report(accepted_commit: str, mock_summary: dict[str, Any]) -> str:
    return f"""# Stage7E0-A3 English Real Generation Preflight PATCH0 Validation Report

Status: PASS_PROTOCOL_READY_FOR_REAL_QWEN_RUN

Validation date: {date.today().isoformat()}

## Scope

This patch prepares the real Stage7E0-A3 runner and server reviewer package. It
does not claim a real model result unless `backend=hf` is run on the GPU server.
The local dry-run uses label-side expected outputs only as a mock infrastructure
test and is marked as non-scientific model evidence.

## Locked Inputs

```text
accepted_protocol_commit={accepted_commit}
phase_o_prompt_spec={A3_PROMPT_SPEC_REL}
model={MODEL_ID}
revision={MODEL_REVISION}
primary_cases=8
acceptance=8/8 required
retry=0
repair=none
diagnostics_run=false
gretel_pilot_opened=false
```

## Local Mock Dry-Run

```text
backend={mock_summary["backend"]}
status={mock_summary["status"]}
primary_pass_count={mock_summary["primary_pass_count"]}
model_called={str(mock_summary["model_called"]).lower()}
gpu_called={str(mock_summary["gpu_called"]).lower()}
mock_uses_label_side_expected={str(mock_summary["mock_uses_label_side_expected"]).lower()}
```

## Validation Commands

```text
python scripts/data/validate_stage7c_a3_english_offset_semantics.py --stage-dir Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT
python scripts/data/validate_stage7e0_a3_english_preflight.py --stage-dir {STAGE_NAME}
python -m pytest -q tests/test_stage7e0_a3_english_preflight.py
python -m zipfile --test {PACKAGE_NAME}
```
"""


def reviewer_readme(package_name: str, accepted_commit: str) -> str:
    return f"""# Stage7E0-A3 English Real Generation Preflight PATCH0

This reviewer package prepares the first stage allowed to call Qwen on the
eight fresh Stage7C-A3 English cases. It wires Phase O to the exact accepted A3
prompt spec and keeps Phase M, V2-A1 materialization, completeness, compiler,
preflight, backend, retry, and repair policy unchanged.

Clean extraction checks:

```bash
python scripts/data/validate_stage7e0_a3_english_preflight.py --stage-dir {STAGE_NAME}
python -m pytest -q tests/test_stage7e0_a3_english_preflight.py
```

Server execution commands are in:

```text
{STAGE_NAME}/SERVER_RUN_COMMANDS.md
```

Package:

```text
{package_name}
```

Accepted commit:

```text
{accepted_commit}
```
"""


def run_mock_dry_run(out_dir: Path, accepted_commit: str) -> dict[str, Any]:
    mock_root = out_dir / "mock_dry_run"
    if mock_root.exists():
        shutil.rmtree(mock_root)
    args = argparse.Namespace(
        accepted_protocol_commit=accepted_commit,
        result_root=str(mock_root),
        backend="mock",
        model_name_or_path=DEFAULT_MODEL_PATH,
        quantization="4bit",
        max_input_tokens=28672,
        seed=42,
        trust_remote_code=False,
        resume=False,
        skip_git_assertions=True,
        allow_result_root_inside_git=True,
    )
    return run_stage7e0(args)


def build_stage(out_dir: Path, package_path: Path | None) -> dict[str, Any]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    accepted_commit = git_output("rev-parse", "HEAD") or "UNKNOWN"
    inputs = stage7c_inputs()
    protocol = runner_protocol(accepted_commit)
    mock_summary = run_mock_dry_run(out_dir, accepted_commit)
    write_json(out_dir / "STAGE7E0_A3_INPUT_MANIFEST.json", inputs)
    write_json(out_dir / "RUNNER_PROTOCOL_A3.json", protocol)
    write_json(out_dir / "PRIMARY_ACCEPTANCE_POLICY_A3.json", protocol["acceptance"])
    write_text(out_dir / "SERVER_RUN_COMMANDS.md", server_commands(accepted_commit, package_path.name if package_path else PACKAGE_NAME))
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(accepted_commit, mock_summary))
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme(package_path.name if package_path else PACKAGE_NAME, accepted_commit))
    artifact_names = [
        "STAGE7E0_A3_INPUT_MANIFEST.json",
        "RUNNER_PROTOCOL_A3.json",
        "PRIMARY_ACCEPTANCE_POLICY_A3.json",
        "SERVER_RUN_COMMANDS.md",
        "VALIDATION_REPORT.md",
        "REVIEWER_README.md",
        "mock_dry_run/run_manifest.json",
        "mock_dry_run/primary_summary.json",
        "mock_dry_run/primary_case_results.jsonl",
        "mock_dry_run/raw_phase_o_generations.jsonl",
        "mock_dry_run/raw_phase_m_generations.jsonl",
    ]
    manifest = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "artifact_count": len(artifact_names),
        "artifacts": [
            {"path": name, "bytes": (out_dir / name).stat().st_size, "sha256": sha256_file(out_dir / name)}
            for name in artifact_names
        ],
    }
    manifest["combined_scientific_artifacts_sha256"] = sha256_text(canonical_json(manifest["artifacts"]))
    write_json(out_dir / "DERIVED_ARTIFACT_MANIFEST.json", manifest)
    lock = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS_PROTOCOL_READY_FOR_REAL_QWEN_RUN",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_branch": git_output("branch", "--show-current"),
        "git_commit": accepted_commit,
        "phase_o_prompt_spec_path": A3_PROMPT_SPEC_REL,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "primary_case_count": 8,
        "primary_acceptance": "8/8 required; no average and no 7/8 acceptance",
        "two_call_architecture": True,
        "zero_shot": True,
        "retry": 0,
        "repair": "none",
        "model_called": False,
        "gpu_called": False,
        "mock_dry_run_only": True,
        "gretel_pilot_opened": False,
        "derived_artifact_manifest_sha256": sha256_file(out_dir / "DERIVED_ARTIFACT_MANIFEST.json"),
    }
    write_json(out_dir / "STAGE7E0_A3_LOCK.json", lock)
    summary = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": lock["status"],
        "accepted_protocol_commit": accepted_commit,
        "mock_primary_pass_count": mock_summary["primary_pass_count"],
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
    }
    if package_path is not None:
        summary["package_sha256"] = package_reviewer(out_dir, package_path)
        summary["package"] = str(package_path)
    return summary


def include_paths(stage_dir: Path) -> list[Path]:
    paths = [path for path in stage_dir.rglob("*") if path.is_file()]
    include_rel = [
        "pyproject.toml",
        "requirements-inference.lock.txt",
        STAGE7C_A3_DIR,
        "stage7b_v2_method_specification",
        "stage7b_a1_free_text_slot_discovery_amendment",
        "stage7c_a1_v2_development_protocol",
        "stage7c_a2_phase_o_prompt_feasibility_amendment",
        "stage7d_v2_a1_implementation",
        "src/nldbwrite_v3/v2_a1",
        "src/nldbwrite_v3/inference/parse_output.py",
        "scripts/server/run_stage7e0_a3_english.py",
        "scripts/data/build_stage7e0_a3_english_preflight.py",
        "scripts/data/validate_stage7c_a3_english_offset_semantics.py",
        "scripts/data/validate_stage7e0_a3_english_preflight.py",
        "tests/test_stage7e0_a3_english_preflight.py",
        "tests/support/windows_py314_pytest_tempdir/sitecustomize.py",
        "tests/support/stage7c_pytest_clean_root/conftest.py",
    ]
    for rel in include_rel:
        path = PROJECT_ROOT / rel
        if path.is_file():
            paths.append(path)
        elif path.is_dir():
            paths.extend(child for child in path.rglob("*") if child.is_file() and "__pycache__" not in child.parts)
    return sorted({path for path in paths if path.is_file()})


def package_reviewer(stage_dir: Path, package_path: Path) -> str:
    if package_path.exists():
        package_path.unlink()
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in include_paths(stage_dir):
            if path.is_relative_to(stage_dir):
                arcname = Path(STAGE_NAME) / path.relative_to(stage_dir)
            elif path.name == "sitecustomize.py" and "windows_py314_pytest_tempdir" in path.parts:
                arcname = Path("sitecustomize.py")
            elif path.name == "conftest.py" and "stage7c_pytest_clean_root" in path.parts:
                arcname = Path("conftest.py")
            else:
                arcname = path.relative_to(PROJECT_ROOT)
            archive.write(path, arcname.as_posix())
    with zipfile.ZipFile(package_path) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP integrity check failed at {bad}")
    digest = sha256_file(package_path)
    package_path.with_suffix(package_path.suffix + ".sha256").write_text(f"{digest}  {package_path.name}\n", encoding="utf-8", newline="\n")
    return digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--package", type=Path, default=PROJECT_ROOT / PACKAGE_NAME)
    args = parser.parse_args()
    print(json.dumps(build_stage(args.out_dir, args.package), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
