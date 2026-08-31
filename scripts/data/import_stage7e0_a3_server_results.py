#!/usr/bin/env python3
"""Import Stage7E0-A3 real server results into a reviewer evidence package."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE_NAME = "Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT"
PATCH_NAME = "PATCH2"
SERVER_RUN_ID = "server_real_run_20260830_220327"
PACKAGE_NAME = f"{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_20260831.zip"
RESULT_DIR_NAME = "stage7e0_a3_english_real_generation_preflight_results"
SERVER_TAR_NAME = "stage7e0_a3_english_real_generation_preflight_results_20260830_220327.tar.gz"


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


def safe_extract_tar(tar_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:gz") as archive:
        for member in archive.getmembers():
            target = (dest / member.name).resolve()
            if not target.is_relative_to(dest.resolve()):
                raise RuntimeError(f"Unsafe tar member path: {member.name}")
        archive.extractall(dest)


def import_server_results(stage_dir: Path, tar_path: Path) -> dict[str, Any]:
    result_root = stage_dir / SERVER_RUN_ID
    if result_root.exists():
        shutil.rmtree(result_root)
    safe_extract_tar(tar_path, result_root)
    extracted = result_root / RESULT_DIR_NAME
    if not extracted.is_dir():
        raise RuntimeError(f"Expected {RESULT_DIR_NAME} inside {tar_path}")
    summary = read_json(extracted / "primary_summary.json")
    cases = read_jsonl(extracted / "primary_case_results.jsonl")
    raw_o = read_jsonl(extracted / "raw_phase_o_generations.jsonl")
    raw_m = read_jsonl(extracted / "raw_phase_m_generations.jsonl")
    failures = [row for row in cases if row.get("status") != "PASS"]
    failure_counts: dict[str, int] = {}
    for row in failures:
        stage = str(row.get("failure_stage"))
        failure_counts[stage] = failure_counts.get(stage, 0) + 1
    report = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "server_run_id": SERVER_RUN_ID,
        "source_tar": {
            "path": tar_path.name,
            "sha256": sha256_file(tar_path),
            "bytes": tar_path.stat().st_size,
        },
        "server_result_files": {
            "primary_summary.json": sha256_file(extracted / "primary_summary.json"),
            "primary_case_results.jsonl": sha256_file(extracted / "primary_case_results.jsonl"),
            "raw_phase_o_generations.jsonl": sha256_file(extracted / "raw_phase_o_generations.jsonl"),
            "raw_phase_m_generations.jsonl": sha256_file(extracted / "raw_phase_m_generations.jsonl"),
            "run_manifest.json": sha256_file(extracted / "run_manifest.json"),
        },
        "result": {
            "status": summary.get("status"),
            "primary_pass_count": summary.get("primary_pass_count"),
            "required_pass_count": summary.get("required_pass_count"),
            "model_called": summary.get("model_called"),
            "gpu_called": summary.get("gpu_called"),
            "backend": summary.get("backend"),
            "diagnostics_run": summary.get("diagnostics_run"),
            "gretel_pilot_opened": summary.get("gretel_pilot_opened"),
            "case_rows": len(cases),
            "phase_o_raw_rows": len(raw_o),
            "phase_m_raw_rows": len(raw_m),
            "failure_count": len(failures),
            "failure_stage_counts": failure_counts,
        },
        "invalid_run_classification": {
            "invalid_run_id": "001",
            "reason": "backend_protocol_violation",
            "evidence_integrity_status": "PASS",
            "protocol_compliance_status": "FAIL",
            "primary_gate_status": "INVALID_NOT_EVALUATED",
            "scientific_result_eligible": False,
            "actual_backend": "plain_hf_unconstrained",
            "reported_backend": summary.get("backend"),
            "required_backend": "patch9_incremental_json_schema_grammar",
            "actual_quantization": read_json(extracted / "run_manifest.json").get("model", {}).get("quantization"),
            "required_quantization": "none",
            "actual_phase_m_max_new_tokens": read_json(extracted / "run_manifest.json").get("phase_m_max_new_tokens"),
            "required_phase_m_max_new_tokens": 8192,
        },
    }
    write_json(stage_dir / "SERVER_RESULT_IMPORT_REPORT.json", report)
    write_json(stage_dir / "INVALID_RUN_001_CLASSIFICATION.json", report["invalid_run_classification"])
    write_text(stage_dir / "SERVER_RESULT_FAILURE_ANALYSIS.md", failure_analysis(report, cases, raw_o, raw_m))
    write_text(stage_dir / "VALIDATION_REPORT_PATCH1.md", validation_report(report))
    lock = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "INVALID_RUN_001_BACKEND_PROTOCOL_VIOLATION_DO_NOT_OPEN_GRETEL",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "server_run_id": SERVER_RUN_ID,
        "server_result_import_report_sha256": sha256_file(stage_dir / "SERVER_RESULT_IMPORT_REPORT.json"),
        "source_tar_sha256": report["source_tar"]["sha256"],
        "primary_pass_count": summary.get("primary_pass_count"),
        "required_pass_count": summary.get("required_pass_count"),
        "model_called": True,
        "gpu_called": True,
        "diagnostics_run": False,
        "gretel_pilot_opened": False,
        "evidence_integrity_status": "PASS",
        "protocol_compliance_status": "FAIL",
        "primary_gate_status": "INVALID_NOT_EVALUATED",
        "scientific_result_eligible": False,
        "decision": "Prior plain-HF output is preserved as invalid-run evidence; do not use it as a scientific A3 failure result.",
    }
    write_json(stage_dir / "STAGE7E0_A3_SERVER_RESULT_LOCK.json", lock)
    return report


def failure_analysis(report: dict[str, Any], cases: list[dict[str, Any]], raw_o: list[dict[str, Any]], raw_m: list[dict[str, Any]]) -> str:
    raw_o_by_id = {row["sample_id"]: row for row in raw_o}
    raw_m_by_id = {row["sample_id"]: row for row in raw_m}
    lines = [
        "# Stage7E0-A3 English Real Server Result Failure Analysis",
        "",
        "Status: INVALID_RUN_001_BACKEND_PROTOCOL_VIOLATION_DO_NOT_OPEN_GRETEL",
        "",
        "The prior Qwen GPU output is preserved as evidence, but it used plain",
        "unconstrained HF generation and is therefore not a scientific A3 primary",
        "result. Diagnostics and the Gretel development-train pilot remain unopened.",
        "",
        "```text",
        f"backend={report['result']['backend']}",
        f"primary_pass_count={report['result']['primary_pass_count']}",
        f"required_pass_count={report['result']['required_pass_count']}",
        "protocol_compliance_status=FAIL",
        "primary_gate_status=INVALID_NOT_EVALUATED",
        "scientific_result_eligible=false",
        f"phase_o_raw_rows={report['result']['phase_o_raw_rows']}",
        f"phase_m_raw_rows={report['result']['phase_m_raw_rows']}",
        f"failure_stage_counts={report['result']['failure_stage_counts']}",
        "```",
        "",
        "## Invalid-Run Case Evidence",
        "",
    ]
    for row in cases:
        if row.get("status") == "PASS":
            continue
        sample_id = row["sample_id"]
        lines.extend(
            [
                f"### {sample_id}",
                "",
                "```text",
                f"failure_stage={row.get('failure_stage')}",
                f"error={row.get('error')}",
                "```",
                "",
                "Phase O raw output:",
                "",
                "```text",
                str(raw_o_by_id.get(sample_id, {}).get("raw_output", "")).strip(),
                "```",
                "",
                "Phase M raw output:",
                "",
                "```text",
                str(raw_m_by_id.get(sample_id, {}).get("raw_output", "")).strip(),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def validation_report(report: dict[str, Any]) -> str:
    return f"""# Stage7E0-A3 English PATCH2 Server Result Validation Report

Status: INVALID_RUN_001_BACKEND_PROTOCOL_VIOLATION_DO_NOT_OPEN_GRETEL

Validation date: {date.today().isoformat()}

## Imported Server Evidence

```text
source_tar={report["source_tar"]["path"]}
source_tar_sha256={report["source_tar"]["sha256"]}
backend={report["result"]["backend"]}
model_called={str(report["result"]["model_called"]).lower()}
gpu_called={str(report["result"]["gpu_called"]).lower()}
primary_pass_count={report["result"]["primary_pass_count"]}
required_pass_count={report["result"]["required_pass_count"]}
diagnostics_run={str(report["result"]["diagnostics_run"]).lower()}
gretel_pilot_opened={str(report["result"]["gretel_pilot_opened"]).lower()}
```

## Decision

The prior server output has evidence integrity, but it used plain unconstrained
HF generation. Its primary gate is invalid/not evaluated, and the Gretel
development-train pilot must remain closed.
"""


def include_paths(stage_dir: Path, tar_path: Path) -> list[Path]:
    rel_paths = [
        "pyproject.toml",
        "requirements-inference.lock.txt",
        "scripts/server/run_stage7e0_a3_english.py",
        "scripts/server/run_stage7e0_v2_a1_preflight.py",
        "scripts/data/build_stage7e0_a3_english_preflight.py",
        "scripts/data/validate_stage7e0_a3_english_preflight.py",
        "scripts/data/import_stage7e0_a3_server_results.py",
        "scripts/data/validate_stage7e0_a3_server_results.py",
        "tests/test_stage7e0_a3_english_preflight.py",
        "tests/test_stage7e0_a3_server_results.py",
        "tests/support/windows_py314_pytest_tempdir/sitecustomize.py",
        "tests/support/stage7c_pytest_clean_root/conftest.py",
        "src/nldbwrite_v3/v2_a1",
        "src/nldbwrite_v3/inference/parse_output.py",
        "Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT",
    ]
    paths = [path for path in stage_dir.rglob("*") if path.is_file()]
    if tar_path.is_file():
        paths.append(tar_path)
    for rel in rel_paths:
        path = PROJECT_ROOT / rel
        if path.is_file():
            paths.append(path)
        elif path.is_dir():
            paths.extend(child for child in path.rglob("*") if child.is_file() and "__pycache__" not in child.parts)
    return sorted({path for path in paths if path.is_file()})


def package_reviewer(stage_dir: Path, tar_path: Path, package_path: Path) -> str:
    if package_path.exists():
        package_path.unlink()
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in include_paths(stage_dir, tar_path):
            if path.is_relative_to(stage_dir):
                arcname = Path(STAGE_NAME) / path.relative_to(stage_dir)
            elif path == tar_path:
                arcname = Path(tar_path.name)
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
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--server-results-tar", type=Path, default=PROJECT_ROOT / SERVER_TAR_NAME)
    parser.add_argument("--package", type=Path, default=PROJECT_ROOT / PACKAGE_NAME)
    args = parser.parse_args()
    report = import_server_results(args.stage_dir, args.server_results_tar)
    digest = package_reviewer(args.stage_dir, args.server_results_tar, args.package)
    summary = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "INVALID_RUN_001_BACKEND_PROTOCOL_VIOLATION_DO_NOT_OPEN_GRETEL",
        "primary_pass_count": report["result"]["primary_pass_count"],
        "model_called": True,
        "gpu_called": True,
        "gretel_pilot_opened": False,
        "package": str(args.package),
        "package_sha256": digest,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
