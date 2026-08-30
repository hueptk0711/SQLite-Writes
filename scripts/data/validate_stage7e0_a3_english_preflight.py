#!/usr/bin/env python3
"""Validate Stage7E0-A3 English real-generation preflight artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
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
    load_stage7c_a3_rows,
    render_phase_o_a3_messages,
)


STAGE_NAME = "Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT"
REQUIRED_FILES = {
    "STAGE7E0_A3_INPUT_MANIFEST.json",
    "RUNNER_PROTOCOL_A3.json",
    "PRIMARY_ACCEPTANCE_POLICY_A3.json",
    "SERVER_RUN_COMMANDS.md",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
    "DERIVED_ARTIFACT_MANIFEST.json",
    "STAGE7E0_A3_LOCK.json",
    "mock_dry_run/run_manifest.json",
    "mock_dry_run/primary_summary.json",
    "mock_dry_run/primary_case_results.jsonl",
    "mock_dry_run/raw_phase_o_generations.jsonl",
    "mock_dry_run/raw_phase_m_generations.jsonl",
}


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl", ".md", ".py", ".txt", ".toml", ".sh"}:
        data = canonical_text(data.decode("utf-8-sig")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate(stage_dir: Path) -> dict[str, Any]:
    failures: list[str] = []
    for rel in sorted(REQUIRED_FILES):
        if not (stage_dir / rel).is_file():
            failures.append(f"missing required artifact: {rel}")

    protocol = read_json(stage_dir / "RUNNER_PROTOCOL_A3.json")
    inputs = read_json(stage_dir / "STAGE7E0_A3_INPUT_MANIFEST.json")
    policy = read_json(stage_dir / "PRIMARY_ACCEPTANCE_POLICY_A3.json")
    lock = read_json(stage_dir / "STAGE7E0_A3_LOCK.json")
    mock_summary = read_json(stage_dir / "mock_dry_run" / "primary_summary.json")
    mock_cases = read_jsonl(stage_dir / "mock_dry_run" / "primary_case_results.jsonl")
    raw_o = read_jsonl(stage_dir / "mock_dry_run" / "raw_phase_o_generations.jsonl")
    raw_m = read_jsonl(stage_dir / "mock_dry_run" / "raw_phase_m_generations.jsonl")

    if protocol.get("model", {}).get("model_id") != MODEL_ID:
        failures.append("runner protocol model_id drifted")
    if protocol.get("model", {}).get("model_revision") != MODEL_REVISION:
        failures.append("runner protocol model_revision drifted")
    if protocol.get("model", {}).get("default_server_model_path") != DEFAULT_MODEL_PATH:
        failures.append("runner protocol server model path drifted")
    if protocol.get("model", {}).get("expected_chat_template_sha256") != EXPECTED_CHAT_TEMPLATE_SHA256:
        failures.append("runner protocol chat template hash drifted")
    if protocol.get("prompt_contract", {}).get("phase_o_prompt_spec_path") != A3_PROMPT_SPEC_REL:
        failures.append("runner does not lock exact Stage7C-A3 Phase O prompt spec")
    if protocol.get("prompt_contract", {}).get("phase_m_changed") is not False:
        failures.append("Phase M must remain unchanged")
    if protocol.get("generation_contract", {}).get("phase_o_calls") != 1:
        failures.append("Phase O call count must be one")
    if protocol.get("generation_contract", {}).get("phase_m_calls") != 1:
        failures.append("Phase M call count must be one")
    if protocol.get("generation_contract", {}).get("retry") != 0:
        failures.append("retry must be 0")
    if protocol.get("generation_contract", {}).get("repair") != "none":
        failures.append("repair must be none")

    if policy.get("required_pass_count") != "8/8":
        failures.append("primary acceptance must require 8/8")
    if policy.get("seven_of_eight_allowed") is not False:
        failures.append("7/8 acceptance must be forbidden")
    if policy.get("averaging_allowed") is not False:
        failures.append("averaging acceptance must be forbidden")

    if inputs.get("a3_prompt_spec_path") != A3_PROMPT_SPEC_REL:
        failures.append("input manifest does not point to A3 prompt spec")
    if inputs.get("fresh_primary_case_count") != 8:
        failures.append("input manifest must lock 8 primary cases")
    if inputs.get("a3_prompt_spec_sha256") != sha256_file(PROJECT_ROOT / A3_PROMPT_SPEC_REL):
        failures.append("A3 prompt spec hash mismatch")
    if inputs.get("fresh_primary_smoke_set_sha256") != sha256_file(PROJECT_ROOT / STAGE7C_A3_DIR / "FRESH_ENGLISH_A3_SMOKE_SET.jsonl"):
        failures.append("A3 smoke set hash mismatch")

    rows = load_stage7c_a3_rows(PROJECT_ROOT)
    if [row["sample_id"] for row in rows] != inputs.get("fresh_primary_case_ids"):
        failures.append("primary case order drifted")
    messages, _ = render_phase_o_a3_messages(rows[0]["model_side_input"]["question"], rows[0]["model_side_input"], root=PROJECT_ROOT)
    if "Offsets follow Python slicing exactly." not in messages[1]["content"]:
        failures.append("rendered Phase O prompt does not contain A3 offset semantics")

    if mock_summary.get("backend") != "mock":
        failures.append("local dry-run must be mock backend")
    if mock_summary.get("status") != "PASS":
        failures.append("mock dry-run must pass")
    if mock_summary.get("primary_pass_count") != "8/8":
        failures.append("mock dry-run must prove 8/8 wiring")
    if mock_summary.get("model_called") is not False or mock_summary.get("gpu_called") is not False:
        failures.append("package build must not call model or GPU")
    if mock_summary.get("mock_uses_label_side_expected") is not True:
        failures.append("mock dry-run must disclose label-side use")
    if len(mock_cases) != 8 or any(row.get("status") != "PASS" for row in mock_cases):
        failures.append("mock case results must contain 8 PASS rows")
    if len(raw_o) != 8 or len(raw_m) != 8:
        failures.append("mock raw generation files must contain 8 Phase O and 8 Phase M rows")

    if lock.get("status") != "PASS_PROTOCOL_READY_FOR_REAL_QWEN_RUN":
        failures.append("stage lock status must be protocol-ready")
    if lock.get("model_called") is not False or lock.get("gpu_called") is not False:
        failures.append("stage lock must record no local model/GPU call")
    if lock.get("gretel_pilot_opened") is not False:
        failures.append("Gretel pilot must remain unopened")

    commands = (stage_dir / "SERVER_RUN_COMMANDS.md").read_text(encoding="utf-8")
    for needle in ("uet@222.255.250.24", "/home/uet/hue_ptk", "run_stage7e0_a3_english.py", DEFAULT_MODEL_PATH):
        if needle not in commands:
            failures.append(f"server command missing {needle}")

    manifest = read_json(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json")
    for item in manifest.get("artifacts", []):
        rel = item["path"]
        path = stage_dir / rel
        if not path.is_file():
            failures.append(f"manifested artifact missing: {rel}")
        elif item.get("sha256") != sha256_file(path):
            failures.append(f"manifest hash mismatch: {rel}")
    if lock.get("derived_artifact_manifest_sha256") != sha256_file(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json"):
        failures.append("stage lock derived manifest hash mismatch")

    return {
        "stage": STAGE_NAME,
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "primary_case_count": len(rows),
        "mock_primary_pass_count": mock_summary.get("primary_pass_count"),
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    args = parser.parse_args()
    report = validate(args.stage_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
