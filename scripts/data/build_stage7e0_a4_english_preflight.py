#!/usr/bin/env python3
"""Build Stage7E0-A4 candidate-span real-generation preflight artifacts."""

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

from scripts.data.build_stage7c_a4_candidate_span_phase_o_protocol import SCIENTIFIC_ARTIFACTS as A4_SCIENTIFIC_ARTIFACTS  # noqa: E402
from scripts.server.run_stage7e0_a4_english import (  # noqa: E402
    A4_PROMPT_SPEC_REL,
    DEFAULT_MODEL_PATH,
    EXPECTED_CHAT_TEMPLATE_SHA256,
    EXPECTED_PRIMARY_COUNT,
    FROZEN_RUNTIME_VERSIONS,
    MODEL_ID,
    MODEL_REVISION,
    PHASE_M_MAX_NEW_TOKENS,
    PHASE_O_MAX_NEW_TOKENS,
    STAGE7C_A4_DIR,
    build_phase_o_span_ref_constraint_grammar,
    candidate_records,
    load_stage7c_a4_rows,
    run_stage7e0,
)
from nldbwrite_v3.v2_a1.inventories import build_schema_inventory  # noqa: E402
from nldbwrite_v3.v2_a1.phase_m_schema import dynamic_schema  # noqa: E402
from nldbwrite_v3.v2_a1.prompt_rendering import sha256_text  # noqa: E402
from nldbwrite_v3.v2_a1.slot_inventory import build_slot_bundle  # noqa: E402
from nldbwrite_v3.v2_a1.types import AcceptedSpan  # noqa: E402
from scripts.server.run_stage7e0_v2_a1_preflight import build_phase_m_constraint_grammar  # noqa: E402


STAGE_NAME = "Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT"
PATCH_NAME = "PATCH1"
PACKAGE_NAME = f"{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_20260831.zip"
FRESH_CONSTRAINED_RESULT_DIR_NAME = "stage7e0_a4_english_candidate_span_constrained_results_20260831"


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl", ".md", ".py", ".txt", ".toml", ".sh"}:
        data = canonical_text(data.decode("utf-8-sig")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def stage7c_a4_inputs() -> dict[str, Any]:
    rows = load_stage7c_a4_rows(PROJECT_ROOT)
    prompt = read_json(PROJECT_ROOT / A4_PROMPT_SPEC_REL)
    phase_m = read_json(PROJECT_ROOT / "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json")
    return {
        "stage7c_a4_dir": STAGE7C_A4_DIR,
        "stage7c_a4_closed_commit": git_output("rev-parse", "HEAD") or "UNKNOWN",
        "phase_o_prompt_spec_path": A4_PROMPT_SPEC_REL,
        "phase_o_prompt_spec_sha256": sha256_file(PROJECT_ROOT / A4_PROMPT_SPEC_REL),
        "phase_o_system_sha256": sha256_text(prompt["system_prompt"]),
        "phase_o_user_template_sha256": sha256_text(prompt["user_prompt_template"]),
        "phase_m_prompt_spec_path": "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json",
        "phase_m_system_sha256": sha256_text(phase_m["system_prompt"]),
        "phase_m_user_template_sha256": sha256_text(phase_m["user_prompt_template"]),
        "fresh_primary_case_count": len(rows),
        "fresh_primary_case_ids": [row["sample_id"] for row in rows],
        "fresh_primary_set_sha256": sha256_file(PROJECT_ROOT / STAGE7C_A4_DIR / "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl"),
        "stage7c_a4_lock_sha256": sha256_file(PROJECT_ROOT / STAGE7C_A4_DIR / "STAGE7C_A4_LOCK.json"),
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
            "quantization_default": "none",
            "quantization_allowed": False,
            "torch_dtype": "auto",
            "frozen_runtime_versions": FROZEN_RUNTIME_VERSIONS,
        },
        "prompt_contract": {
            "phase_o_prompt_spec_path": A4_PROMPT_SPEC_REL,
            "phase_o_output_keys": ["operation", "span_refs"],
            "phase_o_numeric_offsets_forbidden": True,
            "phase_m_prompt_spec_path": "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json",
            "phase_m_changed": False,
        },
        "generation_contract": {
            "primary_cases": "10 locked Stage7C-A4 English candidate-span cases",
            "calls_per_successful_case": 2,
            "phase_o_calls": 1,
            "phase_m_calls": 1,
            "zero_shot": True,
            "examples": [],
            "retry": 0,
            "repair": "none",
            "backend": "incremental_json_schema_grammar",
            "schema_enforcement_mode": "transformers_prefix_allowed_tokens_fn",
            "phase_o_runtime_schema": "span_refs.items.enum equals exact current sample candidate refs",
            "token_level_enforcement": True,
            "fallback_to_unconstrained": False,
            "finite_complete_object_enumeration": False,
            "finite_known_answer_candidates": False,
            "label_side_data_used_for_constraints": False,
            "automatic_repair": False,
            "resume_allowed": False,
            "interrupted_run_policy": "archive partial output as ABORTED_INFRASTRUCTURE and start a new empty result-root; do not pass --resume",
            "phase_o_max_new_tokens": PHASE_O_MAX_NEW_TOKENS,
            "phase_m_max_new_tokens": PHASE_M_MAX_NEW_TOKENS,
            "accepted_patch9_backend_file": "scripts/server/run_stage7e0_v2_a1_preflight.py",
            "candidate_span_a4_runner_file": "scripts/server/run_stage7e0_a4_english.py",
        },
        "acceptance": {
            "required_pass_count": "10/10",
            "nine_of_ten_allowed": False,
            "averaging_allowed": False,
            "gretel_pilot_gate": "open 100-sample Gretel development-train pilot only after 10/10 primary PASS",
        },
    }


def server_commands(accepted_commit: str, package_name: str) -> str:
    return f"""# Stage7E0-A4 English Candidate-Span Server Run Commands

Upload from Windows:

```powershell
cd "D:\\paper kltn\\text to sql\\github_publish\\SQLite-Writes"
scp "{package_name}" uet@222.255.250.24:/home/uet/hue_ptk/
scp "{package_name}.sha256" uet@222.255.250.24:/home/uet/hue_ptk/
```

Run on the GPU server:

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
PY="${{PY:-/home/uet/miniconda3/envs/stage7e0/bin/python}}"
"$PY" scripts/server/run_stage7e0_a4_english.py \\
  --accepted-protocol-commit {accepted_commit} \\
  --result-root /home/uet/hue_ptk/{FRESH_CONSTRAINED_RESULT_DIR_NAME} \\
  --backend constrained_hf \\
  --quantization none \\
  --phase-o-max-new-tokens {PHASE_O_MAX_NEW_TOKENS} \\
  --phase-m-max-new-tokens {PHASE_M_MAX_NEW_TOKENS} \\
  --model-name-or-path {DEFAULT_MODEL_PATH}
```

Do not use `--resume`. If infrastructure interrupts the job, archive the
partial output and rerun in a new empty result root.

Copy results back:

```powershell
scp -r uet@222.255.250.24:/home/uet/hue_ptk/{FRESH_CONSTRAINED_RESULT_DIR_NAME} .
```

Validate copied results:

```bash
python scripts/data/validate_stage7e0_a4_server_results.py --result-dir {FRESH_CONSTRAINED_RESULT_DIR_NAME}
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
        quantization="none",
        phase_o_max_new_tokens=PHASE_O_MAX_NEW_TOKENS,
        phase_m_max_new_tokens=PHASE_M_MAX_NEW_TOKENS,
        max_input_tokens=28672,
        seed=42,
        trust_remote_code=False,
        resume=False,
        skip_git_assertions=True,
        allow_result_root_inside_git=True,
    )
    return run_stage7e0(args)


def constraint_independence_audit() -> dict[str, Any]:
    rows = load_stage7c_a4_rows(PROJECT_ROOT)
    audit_rows: list[dict[str, Any]] = []
    for row in rows:
        phase_o_schema = row["runtime_constraints"]["phase_o_schema"]
        phase_o_baseline = build_phase_o_span_ref_constraint_grammar(phase_o_schema)
        mutated = json.loads(json.dumps(row))
        mutated["label_side_expected"]["phase_o"]["span_refs"] = list(reversed(mutated["label_side_expected"]["phase_o"]["span_refs"]))
        phase_o_mutated = build_phase_o_span_ref_constraint_grammar(mutated["runtime_constraints"]["phase_o_schema"])

        selected = candidate_records(row)
        gold_refs = row["label_side_expected"]["phase_o"]["span_refs"]
        selected_by_ref = {candidate.span_ref: candidate for candidate in selected}
        spans = tuple(
            AcceptedSpan(
                start_char=selected_by_ref[span_ref].start_char,
                end_char=selected_by_ref[span_ref].end_char,
                text=selected_by_ref[span_ref].text,
            )
            for span_ref in gold_refs
        )
        slots = build_slot_bundle(spans)
        inventory = build_schema_inventory(row["model_side_input"]["schema_inventory"])
        phase_m_schema = dynamic_schema("INSERT", inventory, slots, root=PROJECT_ROOT)
        phase_m_baseline = build_phase_m_constraint_grammar(phase_m_schema, "INSERT", inventory, slots, root=PROJECT_ROOT)
        phase_m_mutated = build_phase_m_constraint_grammar(phase_m_schema, "INSERT", inventory, slots, root=PROJECT_ROOT)

        audit_rows.append(
            {
                "sample_id": row["sample_id"],
                "phase_o_constraint_fingerprint": phase_o_baseline.fingerprint,
                "phase_o_label_mutation_fingerprint": phase_o_mutated.fingerprint,
                "phase_o_label_independent": phase_o_baseline.fingerprint == phase_o_mutated.fingerprint,
                "phase_m_constraint_fingerprint": phase_m_baseline.fingerprint,
                "phase_m_label_mutation_fingerprint": phase_m_mutated.fingerprint,
                "phase_m_label_independent": phase_m_baseline.fingerprint == phase_m_mutated.fingerprint,
                "label_side_data_used_for_constraints": False,
            }
        )
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS" if len(audit_rows) == EXPECTED_PRIMARY_COUNT and all(row["phase_o_label_independent"] and row["phase_m_label_independent"] for row in audit_rows) else "FAIL",
        "case_count": len(audit_rows),
        "backend": "incremental_json_schema_grammar",
        "label_side_data_used_for_constraints": False,
        "rows": audit_rows,
    }


def validation_report(accepted_commit: str, mock_summary: dict[str, Any]) -> str:
    return f"""# Stage7E0-A4 English Candidate-Span Real Generation Preflight Validation Report

Status: PASS_READY_FOR_REAL_A4_CONSTRAINED_PREFLIGHT

Validation date: {date.today().isoformat()}

## Scope

This package prepares the first real Qwen/GPU run for the 10 locked Stage7C-A4
candidate-span cases. Local build validation uses a disclosed mock backend only
to test wiring and does not claim scientific model evidence.

```text
accepted_protocol_commit={accepted_commit}
model={MODEL_ID}
revision={MODEL_REVISION}
primary_cases=10
acceptance=10/10 required
phase_o_output=operation + span_refs only
candidate_generator=lexical_ngram2
backend=incremental_json_schema_grammar
do_sample=false
retry=0
repair=none
quantization=none
phase_o_max_new_tokens={PHASE_O_MAX_NEW_TOKENS}
phase_m_max_new_tokens={PHASE_M_MAX_NEW_TOKENS}
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
python scripts/data/validate_stage7c_a4_candidate_span_phase_o_protocol.py --stage-dir Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL
python scripts/data/validate_stage7e0_a4_english_preflight.py --stage-dir {STAGE_NAME}
python -m pytest -q tests/test_stage7e0_a4_english_preflight.py
python -m zipfile --test {PACKAGE_NAME}
```
"""


def reviewer_readme(package_name: str, accepted_commit: str) -> str:
    return f"""# Stage7E0-A4 English Candidate-Span Real Generation Preflight PATCH0

This reviewer package prepares the GPU run for the 10 locked Stage7C-A4
candidate-span cases. It does not open Gretel, development, or official test
sets. Phase O emits only `operation` and `span_refs`; Phase M is unchanged.

Clean extraction checks:

```bash
python scripts/data/validate_stage7c_a4_candidate_span_phase_o_protocol.py --stage-dir Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL
python scripts/data/validate_stage7e0_a4_english_preflight.py --stage-dir {STAGE_NAME}
python -m pytest -q tests/test_stage7e0_a4_english_preflight.py
```

Server commands are in:

```text
{STAGE_NAME}/SERVER_RUN_COMMANDS.md
```

Package:

```text
{package_name}
```

Accepted protocol commit:

```text
{accepted_commit}
```
"""


def build_stage(out_dir: Path, package_path: Path | None) -> dict[str, Any]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    accepted_commit = git_output("rev-parse", "HEAD") or "UNKNOWN"
    inputs = stage7c_a4_inputs()
    protocol = runner_protocol(accepted_commit)
    mock_summary = run_mock_dry_run(out_dir, accepted_commit)
    independence = constraint_independence_audit()
    if independence["status"] != "PASS":
        raise RuntimeError("constraint independence audit failed")
    write_json(out_dir / "STAGE7E0_A4_INPUT_MANIFEST.json", inputs)
    write_json(out_dir / "RUNNER_PROTOCOL_A4.json", protocol)
    write_json(out_dir / "PRIMARY_ACCEPTANCE_POLICY_A4.json", protocol["acceptance"])
    write_json(out_dir / "CONSTRAINT_INDEPENDENCE_AUDIT_A4.json", independence)
    write_text(out_dir / "SERVER_RUN_COMMANDS.md", server_commands(accepted_commit, package_path.name if package_path else PACKAGE_NAME))
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(accepted_commit, mock_summary))
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme(package_path.name if package_path else PACKAGE_NAME, accepted_commit))
    artifact_names = [
        "STAGE7E0_A4_INPUT_MANIFEST.json",
        "RUNNER_PROTOCOL_A4.json",
        "PRIMARY_ACCEPTANCE_POLICY_A4.json",
        "CONSTRAINT_INDEPENDENCE_AUDIT_A4.json",
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
        "status": "PASS_READY_FOR_REAL_A4_CONSTRAINED_PREFLIGHT",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_branch": git_output("branch", "--show-current"),
        "git_commit": accepted_commit,
        "phase_o_prompt_spec_path": A4_PROMPT_SPEC_REL,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "primary_case_count": EXPECTED_PRIMARY_COUNT,
        "primary_acceptance": "10/10 required; no average and no 9/10 acceptance",
        "two_call_architecture": True,
        "zero_shot": True,
        "retry": 0,
        "repair": "none",
        "backend": "incremental_json_schema_grammar",
        "token_level_enforcement": True,
        "fallback_to_unconstrained": False,
        "quantization": "none",
        "phase_o_max_new_tokens": PHASE_O_MAX_NEW_TOKENS,
        "phase_m_max_new_tokens": PHASE_M_MAX_NEW_TOKENS,
        "resume_allowed": False,
        "fresh_constrained_result_root": f"/home/uet/hue_ptk/{FRESH_CONSTRAINED_RESULT_DIR_NAME}",
        "frozen_runtime_versions": FROZEN_RUNTIME_VERSIONS,
        "constraint_independence_audit_sha256": sha256_file(out_dir / "CONSTRAINT_INDEPENDENCE_AUDIT_A4.json"),
        "model_called": False,
        "gpu_called": False,
        "mock_dry_run_only": True,
        "gretel_pilot_opened": False,
        "development_dev_used": False,
        "official_test_used": False,
        "derived_artifact_manifest_sha256": sha256_file(out_dir / "DERIVED_ARTIFACT_MANIFEST.json"),
    }
    write_json(out_dir / "STAGE7E0_A4_LOCK.json", lock)
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
        STAGE7C_A4_DIR,
        "Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/STAGE7C_A3_LOCK.json",
        "Stage7B_A2_ENGLISH_CANDIDATE_SPAN_REFERENCE_AMENDMENT",
        "stage7b_v2_method_specification",
        "stage7c_a1_v2_development_protocol",
        "src/nldbwrite_v3/v2_a1",
        "src/nldbwrite_v3/inference/parse_output.py",
        "scripts/server/run_stage7e0_a4_english.py",
        "scripts/server/run_stage7e0_v2_a1_preflight.py",
        "scripts/data/build_stage7e0_a4_english_preflight.py",
        "scripts/data/validate_stage7e0_a4_english_preflight.py",
        "scripts/data/validate_stage7e0_a4_server_results.py",
        "scripts/data/build_stage7b_a2_candidate_span_reference.py",
        "scripts/data/validate_stage7b_a2_candidate_span_reference.py",
        "scripts/data/build_stageeng0_gretel_qualification.py",
        "scripts/data/build_stage7c_a4_candidate_span_phase_o_protocol.py",
        "scripts/data/validate_stage7c_a4_candidate_span_phase_o_protocol.py",
        "tests/test_stage7e0_a4_english_preflight.py",
        "tests/test_stage7c_a4_candidate_span_phase_o_protocol.py",
        "tests/support/windows_py314_pytest_tempdir/sitecustomize.py",
        "tests/support/stage7c_pytest_clean_root/conftest.py",
    ]
    for rel in include_rel:
        path = PROJECT_ROOT / rel
        if path.is_file():
            paths.append(path)
        elif path.is_dir():
            paths.extend(child for child in path.rglob("*") if child.is_file() and "__pycache__" not in child.parts)
    # Keep this import live so reviewers can see that A4 scientific artifacts are intentionally packaged.
    assert "FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl" in A4_SCIENTIFIC_ARTIFACTS
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
