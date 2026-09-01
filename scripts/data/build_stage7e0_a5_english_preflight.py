#!/usr/bin/env python3
"""Build Stage7E0-A5 one-call column-conditioned preflight artifacts."""

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

from scripts.data.validate_stage7c_a5_column_conditioned_phase_o_protocol import validate as validate_stage7c_a5  # noqa: E402
from scripts.server.run_stage7e0_a5_english import (  # noqa: E402
    A5_DIAGNOSTIC_SET_REL,
    A5_PRIMARY_SET_REL,
    A5_PROMPT_SPEC_REL,
    ALLOWED_FROZEN_RUNTIME_PROFILES,
    CONSTRAINED_BACKEND_ID,
    DEFAULT_MODEL_PATH,
    EXPECTED_CHAT_TEMPLATE_SHA256,
    EXPECTED_DIAGNOSTIC_COUNT,
    EXPECTED_PRIMARY_COUNT,
    FROZEN_RUNTIME_VERSIONS,
    HISTORICAL_RUNTIME_PROFILE_IDS,
    MODEL_ID,
    MODEL_REVISION,
    PHASE_O_MAX_NEW_TOKENS,
    PRIMARY_RUNTIME_PROFILE_ID,
    STAGE7C_A5_DIR,
    build_phase_o_column_conditioned_constraint_grammar,
    load_stage7c_a5_rows,
    run_stage7e0,
    runtime_profile_by_id,
    sha256_file,
)
from scripts.data.build_stage7c_a5_column_conditioned_phase_o_protocol import canonical_json, sha256_text  # noqa: E402


STAGE_NAME = "Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT"
PATCH_NAME = "PATCH2"
PACKAGE_DATE = "20260901"
PACKAGE_NAME = f"{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_{PACKAGE_DATE}.zip"
PRIMARY_RESULT_DIR_NAME = "stage7e0_a5_english_column_conditioned_kaggle_t4x2_primary_results_20260901"
DIAGNOSTIC_RESULT_DIR_NAME = "stage7e0_a5_english_column_conditioned_kaggle_t4x2_diagnostic_results_20260901"
KAGGLE_REQUIREMENTS_LOCK = "requirements-inference-kaggle-t4x2.lock.txt"


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


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


def frozen_accepted_stage7c_a5_commit() -> str:
    existing_manifest = PROJECT_ROOT / STAGE_NAME / "STAGE7E0_A5_INPUT_MANIFEST.json"
    if existing_manifest.is_file():
        value = read_json(existing_manifest).get("accepted_stage7c_a5_commit")
        if isinstance(value, str) and value:
            return value
    committed_manifest = git_output("show", f"HEAD:{STAGE_NAME}/STAGE7E0_A5_INPUT_MANIFEST.json")
    if committed_manifest:
        value = json.loads(committed_manifest).get("accepted_stage7c_a5_commit")
        if isinstance(value, str) and value:
            return value
    return git_output("rev-parse", "HEAD") or "UNKNOWN"


def stage7c_a5_inputs(accepted_commit: str) -> dict[str, Any]:
    upstream = validate_stage7c_a5(PROJECT_ROOT / STAGE7C_A5_DIR)
    if upstream.get("status") != "PASS":
        raise RuntimeError(f"Stage7C-A5 validation must pass before Stage7E0-A5: {upstream.get('failures')}")
    primary_rows = load_stage7c_a5_rows(PROJECT_ROOT)
    diagnostic_rows = load_stage7c_a5_rows(PROJECT_ROOT, diagnostics=True)
    token_audit = read_json(PROJECT_ROOT / STAGE7C_A5_DIR / "FULL_RENDERED_PROMPT_TOKEN_AUDIT.json")
    return {
        "stage7c_a5_dir": STAGE7C_A5_DIR,
        "accepted_stage7c_a5_commit": accepted_commit,
        "phase_o_prompt_spec_path": A5_PROMPT_SPEC_REL,
        "phase_o_prompt_spec_sha256": sha256_file(PROJECT_ROOT / A5_PROMPT_SPEC_REL),
        "primary_case_count": len(primary_rows),
        "primary_case_ids": [row["sample_id"] for row in primary_rows],
        "primary_set_sha256": sha256_file(PROJECT_ROOT / A5_PRIMARY_SET_REL),
        "diagnostic_case_count": len(diagnostic_rows),
        "diagnostic_case_ids": [row["sample_id"] for row in diagnostic_rows],
        "diagnostic_set_sha256": sha256_file(PROJECT_ROOT / A5_DIAGNOSTIC_SET_REL),
        "stage7c_a5_lock_sha256": sha256_file(PROJECT_ROOT / STAGE7C_A5_DIR / "STAGE7C_A5_LOCK.json"),
        "tokenizer_status": token_audit["tokenizer_status"],
        "chat_template_sha256": token_audit["chat_template_sha256"],
    }


def runner_protocol(accepted_commit: str) -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "accepted_protocol_commit": accepted_commit,
        "model": {
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "default_model_path": DEFAULT_MODEL_PATH,
            "expected_chat_template_sha256": EXPECTED_CHAT_TEMPLATE_SHA256,
            "quantization_default": "none",
            "quantization_allowed": False,
            "torch_dtype": "auto",
            "device_map": "auto",
            "max_memory": None,
            "frozen_runtime_versions": FROZEN_RUNTIME_VERSIONS,
            "allowed_frozen_runtime_profiles": ALLOWED_FROZEN_RUNTIME_PROFILES,
            "primary_runtime_profile_id": PRIMARY_RUNTIME_PROFILE_ID,
            "primary_runtime_profile": runtime_profile_by_id(PRIMARY_RUNTIME_PROFILE_ID),
            "historical_runtime_profile_ids": HISTORICAL_RUNTIME_PROFILE_IDS,
            "kaggle_requirements_lock": KAGGLE_REQUIREMENTS_LOCK,
        },
        "prompt_contract": {
            "phase_o_prompt_spec_path": A5_PROMPT_SPEC_REL,
            "phase_o_output_keys": ["operation", "table_ref", "column_span_refs"],
            "column_span_refs_mapping_equality": "order_insensitive_by_object_key",
            "duplicate_non_omit_span_reuse": "method_failure",
            "phase_m_removed": True,
        },
        "generation_contract": {
            "primary_cases": "12 locked Stage7C-A5 primary English column-conditioned cases",
            "diagnostic_cases": "12 Stage7C-A5 A4-derived diagnostics only after primary 12/12",
            "calls_per_primary_case": 1,
            "phase_o_calls": 1,
            "phase_m_calls": 0,
            "zero_shot": True,
            "examples": [],
            "retry": 0,
            "repair": "none",
            "backend": CONSTRAINED_BACKEND_ID,
            "schema_enforcement_mode": "transformers_prefix_allowed_tokens_fn",
            "phase_o_runtime_schema": "table_ref branch plus per-column exact current SPAN refs or OMIT",
            "token_level_enforcement": True,
            "fallback_to_unconstrained": False,
            "finite_complete_object_enumeration": False,
            "finite_known_answer_candidates": False,
            "label_side_data_used_for_constraints": False,
            "automatic_repair": False,
            "resume_allowed": False,
            "interrupted_run_policy": "archive partial output as ABORTED_INFRASTRUCTURE and start a new empty result-root; do not pass --resume",
            "completed_scientific_primary_failure_policy": "runner exits 0 after writing complete evidence; validate, archive, and hash the result before review",
            "phase_o_max_new_tokens": PHASE_O_MAX_NEW_TOKENS,
            "runner_file": "scripts/server/run_stage7e0_a5_english.py",
            "runtime_preflight_file": "scripts/server/preflight_runtime_stage7e0_a5.py",
            "single_primary_runtime_profile": True,
            "runtime_profile_switch_after_completed_generation_allowed": False,
            "gpu_topology_fail_fast_before_model_load": True,
        },
        "acceptance": {
            "required_pass_count": "12/12",
            "eleven_of_twelve_allowed": False,
            "averaging_allowed": False,
            "primary_before_diagnostics": True,
            "diagnostics_can_compensate_primary_failure": False,
            "gretel_pilot_gate": "open 100-sample Gretel development-train pilot only after 12/12 primary PASS",
        },
    }


def server_commands(accepted_commit: str) -> str:
    return f"""# Stage7E0-A5 English Column-Conditioned Kaggle T4x2 Commands

Run the primary set first. Do not run diagnostics before the primary result is
frozen and reviewed. A completed primary result is preserved whether it is
12/12 PASS or a protocol-compliant scientific FAIL below 12/12.

```bash
%%bash
set -euo pipefail
cd /kaggle/working
rm -rf {STAGE_NAME}_{PATCH_NAME}_runner
mkdir -p {STAGE_NAME}_{PATCH_NAME}_runner
PKG_ROOT="$(find /kaggle/input -type d -name '{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_*' -print -quit)"
test -n "$PKG_ROOT"
cp -a "$PKG_ROOT"/. {STAGE_NAME}_{PATCH_NAME}_runner/
cd {STAGE_NAME}_{PATCH_NAME}_runner
export HF_HOME=/kaggle/working/hf_cache
python -m pip install --extra-index-url https://download.pytorch.org/whl/cu130 -r {KAGGLE_REQUIREMENTS_LOCK}
python scripts/data/validate_stage7c_a5_column_conditioned_phase_o_protocol.py --stage-dir {STAGE7C_A5_DIR}
python scripts/data/validate_stage7e0_a5_english_preflight.py --stage-dir {STAGE_NAME}
python scripts/server/preflight_runtime_stage7e0_a5.py --expected-profile {PRIMARY_RUNTIME_PROFILE_ID}
python scripts/server/run_stage7e0_a5_english.py \\
  --accepted-protocol-commit {accepted_commit} \\
  --result-root /kaggle/working/{PRIMARY_RESULT_DIR_NAME} \\
  --backend constrained_hf \\
  --quantization none \\
  --phase-o-max-new-tokens {PHASE_O_MAX_NEW_TOKENS} \\
  --model-name-or-path {MODEL_ID}
python scripts/data/validate_stage7e0_a5_server_results.py --result-dir /kaggle/working/{PRIMARY_RESULT_DIR_NAME}
tar -czf /kaggle/working/{PRIMARY_RESULT_DIR_NAME}.tar.gz -C /kaggle/working {PRIMARY_RESULT_DIR_NAME}
sha256sum /kaggle/working/{PRIMARY_RESULT_DIR_NAME}.tar.gz > /kaggle/working/{PRIMARY_RESULT_DIR_NAME}.tar.gz.sha256
```

Do not use `--resume`. If infrastructure interrupts, archive the partial output
as infrastructure-aborted and rerun in a new empty result root. If the primary
run completes with less than 12/12, keep running the validator, archive, and
sha256 commands above; that is a completed scientific result, not an
infrastructure failure. Diagnostics are not part of this primary preflight
command; run them only after the primary result is frozen and reviewed as 12/12
PASS.
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
        max_input_tokens=28672,
        seed=42,
        trust_remote_code=False,
        resume=False,
        run_diagnostics_after_primary_pass=False,
        skip_git_assertions=True,
        allow_result_root_inside_git=True,
    )
    return run_stage7e0(args)


def constraint_independence_audit() -> dict[str, Any]:
    rows = load_stage7c_a5_rows(PROJECT_ROOT)
    audit_rows = []
    for row in rows:
        baseline = build_phase_o_column_conditioned_constraint_grammar(row["runtime_constraints"]["phase_o_schema"])
        mutated = json.loads(canonical_json(row))
        decisions = mutated["label_side_expected"]["phase_o"]["column_span_refs"]
        mutated["label_side_expected"]["phase_o"]["column_span_refs"] = dict(reversed(list(decisions.items())))
        mutated_grammar = build_phase_o_column_conditioned_constraint_grammar(mutated["runtime_constraints"]["phase_o_schema"])
        audit_rows.append(
            {
                "sample_id": row["sample_id"],
                "constraint_fingerprint": baseline.fingerprint,
                "label_mutation_fingerprint": mutated_grammar.fingerprint,
                "label_independent": baseline.fingerprint == mutated_grammar.fingerprint,
                "label_side_data_used_for_constraints": False,
            }
        )
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS" if len(audit_rows) == EXPECTED_PRIMARY_COUNT and all(row["label_independent"] for row in audit_rows) else "FAIL",
        "case_count": len(audit_rows),
        "backend": CONSTRAINED_BACKEND_ID,
        "label_side_data_used_for_constraints": False,
        "rows": audit_rows,
    }


def validation_report(accepted_commit: str, mock_summary: dict[str, Any]) -> str:
    return f"""# Stage7E0-A5 English Column-Conditioned Real Generation Preflight Validation Report

Status: PASS_READY_FOR_REAL_A5_CONSTRAINED_PREFLIGHT

Validation date: {date.today().isoformat()}

## Scope

This package prepares the first real Qwen/GPU run for the 12 locked Stage7C-A5
primary column-conditioned cases. Local validation uses a disclosed mock backend
only to test wiring and does not claim scientific model evidence.

```text
accepted_protocol_commit={accepted_commit}
model={MODEL_ID}
revision={MODEL_REVISION}
primary_cases=12
diagnostic_cases=12 after primary freeze only
acceptance=12/12 required
phase_o_output=operation + table_ref + column_span_refs
phase_m_removed=true
candidate_generator=lexical_ngram2
backend={CONSTRAINED_BACKEND_ID}
do_sample=false
retry=0
repair=none
quantization=none
phase_o_max_new_tokens={PHASE_O_MAX_NEW_TOKENS}
primary_runtime_profile_id={PRIMARY_RUNTIME_PROFILE_ID}
kaggle_requirements_lock={KAGGLE_REQUIREMENTS_LOCK}
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
"""


def reviewer_readme(package_name: str, accepted_commit: str) -> str:
    return f"""# Stage7E0-A5 English Column-Conditioned Real Generation Preflight {PATCH_NAME}

This reviewer package prepares the Kaggle T4x2 primary GPU run for the 12 locked
Stage7C-A5 primary cases. It does not open Gretel, development-dev, or official
test rows. A5 uses one model call only; Phase M is removed.

Clean extraction checks:

```bash
python scripts/data/validate_stage7c_a5_column_conditioned_phase_o_protocol.py --stage-dir {STAGE7C_A5_DIR}
python scripts/data/validate_stage7e0_a5_english_preflight.py --stage-dir {STAGE_NAME}
python -m pytest -q tests/test_stage7e0_a5_english_preflight.py
```

Kaggle commands are in `{STAGE_NAME}/SERVER_RUN_COMMANDS.md`.

Package: `{package_name}`

Accepted Stage7C-A5 protocol commit: `{accepted_commit}`
"""


def build_stage(out_dir: Path, package_path: Path | None) -> dict[str, Any]:
    accepted_commit = frozen_accepted_stage7c_a5_commit()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    inputs = stage7c_a5_inputs(accepted_commit)
    protocol = runner_protocol(accepted_commit)
    write_json(out_dir / "STAGE7E0_A5_INPUT_MANIFEST.json", inputs)
    mock_summary = run_mock_dry_run(out_dir, accepted_commit)
    independence = constraint_independence_audit()
    if independence["status"] != "PASS":
        raise RuntimeError("constraint independence audit failed")
    write_json(out_dir / "RUNNER_PROTOCOL_A5.json", protocol)
    write_json(out_dir / "PRIMARY_ACCEPTANCE_POLICY_A5.json", protocol["acceptance"])
    write_json(out_dir / "CONSTRAINT_INDEPENDENCE_AUDIT_A5.json", independence)
    write_text(out_dir / "SERVER_RUN_COMMANDS.md", server_commands(accepted_commit))
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(accepted_commit, mock_summary))
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme(package_path.name if package_path else PACKAGE_NAME, accepted_commit))
    artifact_names = [
        "STAGE7E0_A5_INPUT_MANIFEST.json",
        "RUNNER_PROTOCOL_A5.json",
        "PRIMARY_ACCEPTANCE_POLICY_A5.json",
        "CONSTRAINT_INDEPENDENCE_AUDIT_A5.json",
        "SERVER_RUN_COMMANDS.md",
        "VALIDATION_REPORT.md",
        "REVIEWER_README.md",
        "mock_dry_run/run_manifest.json",
        "mock_dry_run/primary_summary.json",
        "mock_dry_run/primary_case_results.jsonl",
        "mock_dry_run/raw_primary_phase_o_generations.jsonl",
    ]
    manifest = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "artifact_count": len(artifact_names),
        "artifacts": [{"path": name, "bytes": (out_dir / name).stat().st_size, "sha256": sha256_file(out_dir / name)} for name in artifact_names],
    }
    manifest["combined_scientific_artifacts_sha256"] = sha256_text(canonical_json(manifest["artifacts"]))
    write_json(out_dir / "DERIVED_ARTIFACT_MANIFEST.json", manifest)
    lock = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS_READY_FOR_REAL_A5_CONSTRAINED_PREFLIGHT",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_branch": git_output("branch", "--show-current"),
        "git_commit": accepted_commit,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "primary_case_count": EXPECTED_PRIMARY_COUNT,
        "diagnostic_case_count": EXPECTED_DIAGNOSTIC_COUNT,
        "primary_acceptance": "12/12 required; no average and no 11/12 acceptance",
        "phase_m_removed": True,
        "calls_per_primary_case": 1,
        "zero_shot": True,
        "retry": 0,
        "repair": "none",
        "backend": CONSTRAINED_BACKEND_ID,
        "token_level_enforcement": True,
        "fallback_to_unconstrained": False,
        "finite_complete_object_enumeration": False,
        "finite_known_answer_candidates": False,
        "label_side_data_used_for_constraints": False,
        "quantization": "none",
        "phase_o_max_new_tokens": PHASE_O_MAX_NEW_TOKENS,
        "resume_allowed": False,
        "primary_result_root": f"/kaggle/working/{PRIMARY_RESULT_DIR_NAME}",
        "diagnostic_result_root": f"/kaggle/working/{DIAGNOSTIC_RESULT_DIR_NAME}",
        "primary_before_diagnostics": True,
        "diagnostics_can_compensate_primary_failure": False,
        "frozen_runtime_versions": FROZEN_RUNTIME_VERSIONS,
        "allowed_frozen_runtime_profiles": ALLOWED_FROZEN_RUNTIME_PROFILES,
        "primary_runtime_profile_id": PRIMARY_RUNTIME_PROFILE_ID,
        "primary_runtime_profile": runtime_profile_by_id(PRIMARY_RUNTIME_PROFILE_ID),
        "historical_runtime_profile_ids": HISTORICAL_RUNTIME_PROFILE_IDS,
        "kaggle_requirements_lock": KAGGLE_REQUIREMENTS_LOCK,
        "single_primary_runtime_profile": True,
        "runtime_profile_switch_after_completed_generation_allowed": False,
        "gpu_topology_fail_fast_before_model_load": True,
        "device_map": "auto",
        "max_memory": None,
        "constraint_independence_audit_sha256": sha256_file(out_dir / "CONSTRAINT_INDEPENDENCE_AUDIT_A5.json"),
        "model_called": False,
        "gpu_called": False,
        "mock_dry_run_only": True,
        "gretel_pilot_opened": False,
        "development_dev_used": False,
        "official_test_used": False,
        "derived_artifact_manifest_sha256": sha256_file(out_dir / "DERIVED_ARTIFACT_MANIFEST.json"),
    }
    write_json(out_dir / "STAGE7E0_A5_LOCK.json", lock)
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
        KAGGLE_REQUIREMENTS_LOCK,
        STAGE7C_A5_DIR,
        "Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL",
        "Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT",
        "Stage7B_A2_ENGLISH_CANDIDATE_SPAN_REFERENCE_AMENDMENT",
        "Stage7B_A3_ENGLISH_COLUMN_CONDITIONED_CANDIDATE_SELECTION_AMENDMENT",
        "src/nldbwrite_v3/v2_a1",
        "src/nldbwrite_v3/inference/parse_output.py",
        "scripts/server/run_stage7e0_a5_english.py",
        "scripts/server/preflight_runtime_stage7e0_a5.py",
        "scripts/server/run_stage7e0_a4_english.py",
        "scripts/server/run_stage7e0_v2_a1_preflight.py",
        "scripts/data/build_stage7e0_a5_english_preflight.py",
        "scripts/data/validate_stage7e0_a5_english_preflight.py",
        "scripts/data/validate_stage7e0_a5_server_results.py",
        "scripts/data/build_stage7c_a5_column_conditioned_phase_o_protocol.py",
        "scripts/data/validate_stage7c_a5_column_conditioned_phase_o_protocol.py",
        "scripts/data/build_stage7b_a2_candidate_span_reference.py",
        "scripts/data/build_stage7b_a3_column_conditioned_candidate_selection.py",
        "scripts/data/build_stageeng0_gretel_qualification.py",
        "tests/conftest.py",
        "tests/test_stage7e0_a5_english_preflight.py",
        "tests/test_stage7c_a5_column_conditioned_phase_o_protocol.py",
        "tests/support/windows_py314_pytest_tempdir/sitecustomize.py",
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
