#!/usr/bin/env python3
"""Import Stage7E0-A3 real server results into a reviewer evidence package."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
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

from scripts.data.validate_stage7e0_a3_server_results import classify_result

STAGE_NAME = "Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT"
PATCH_NAME = "PATCH4"
SERVER_RUN_ID = "server_real_run_20260831_patch3_constrained"
PACKAGE_NAME = f"{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_20260831.zip"
RESULT_DIR_NAME = "stage7e0_a3_english_patch3_constrained_results_20260831"
SERVER_TAR_NAME = "stage7e0_a3_english_patch3_constrained_results_20260831.tar.gz"
SERVER_RESULT_CLASSIFICATION_NAME = "SERVER_RESULT_CLASSIFICATION_PATCH4.json"
SERVER_RESULT_VALIDATION_REPORT_NAME = "VALIDATION_REPORT_PATCH4.md"


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


def find_result_dir(result_root: Path) -> Path:
    exact = result_root / RESULT_DIR_NAME
    if exact.is_dir():
        return exact
    candidates = [
        child
        for child in result_root.iterdir()
        if child.is_dir() and (child / "primary_summary.json").is_file()
    ]
    if len(candidates) == 1:
        return candidates[0]
    raise RuntimeError(f"Expected one Stage7E0-A3 result directory inside {result_root}")


def status_from_classification(classification: dict[str, Any]) -> str:
    if classification["evidence_integrity_status"] != "PASS":
        return "REAL_CONSTRAINED_EVIDENCE_INTEGRITY_FAIL_DO_NOT_OPEN_GRETEL"
    if classification["protocol_compliance_status"] != "PASS":
        return "REAL_CONSTRAINED_PROTOCOL_FAIL_INVALID_NOT_EVALUATED_DO_NOT_OPEN_GRETEL"
    if classification["primary_gate_status"] == "PASS":
        return "REAL_CONSTRAINED_PRIMARY_PASS_GRETEL_PILOT_ELIGIBLE"
    if classification["primary_gate_status"] == "FAIL":
        return "REAL_CONSTRAINED_PRIMARY_FAIL_DO_NOT_OPEN_GRETEL"
    return "REAL_CONSTRAINED_INVALID_NOT_EVALUATED_DO_NOT_OPEN_GRETEL"


def decision_from_classification(classification: dict[str, Any]) -> str:
    if classification["evidence_integrity_status"] != "PASS":
        return "Server output is not accepted as evidence; do not open Gretel pilot."
    if classification["protocol_compliance_status"] != "PASS":
        return "Server output is preserved as protocol-invalid evidence; do not use it as a scientific A3 result."
    if classification["primary_gate_status"] == "PASS":
        return "Protocol-compliant constrained A3 output passed 8/8; Gretel pilot eligibility can be reviewed in the next stage."
    return "Protocol-compliant constrained A3 output is a scientific primary failure; do not open Gretel pilot."


def refresh_derived_manifest(stage_dir: Path, result_root: Path) -> None:
    manifest_path = stage_dir / "DERIVED_ARTIFACT_MANIFEST.json"
    if not manifest_path.is_file():
        return
    manifest = read_json(manifest_path)
    rel_paths = {item["path"] for item in manifest.get("artifacts", [])}
    rel_paths.update(
        {
            "SERVER_RESULT_FAILURE_ANALYSIS.md",
            "SERVER_RESULT_IMPORT_REPORT.json",
            "STAGE7E0_A3_SERVER_RESULT_LOCK.json",
            SERVER_RESULT_CLASSIFICATION_NAME,
            SERVER_RESULT_VALIDATION_REPORT_NAME,
        }
    )
    rel_paths.update(
        child.relative_to(stage_dir).as_posix()
        for child in result_root.rglob("*")
        if child.is_file()
    )
    artifacts = [
        {
            "path": rel,
            "sha256": sha256_file(stage_dir / rel),
            "bytes": (stage_dir / rel).stat().st_size,
        }
        for rel in sorted(rel_paths)
        if (stage_dir / rel).is_file()
    ]
    manifest["patch"] = PATCH_NAME
    manifest["artifact_count"] = len(artifacts)
    manifest["artifacts"] = artifacts
    manifest["combined_scientific_artifacts_sha256"] = sha256_text(canonical_json(artifacts))
    write_json(manifest_path, manifest)

    lock_path = stage_dir / "STAGE7E0_A3_LOCK.json"
    if lock_path.is_file():
        lock = read_json(lock_path)
        lock["derived_artifact_manifest_sha256"] = sha256_file(manifest_path)
        write_json(lock_path, lock)


def import_server_results(stage_dir: Path, tar_path: Path) -> dict[str, Any]:
    result_root = stage_dir / SERVER_RUN_ID
    if result_root.exists():
        shutil.rmtree(result_root)
    safe_extract_tar(tar_path, result_root)
    extracted = find_result_dir(result_root)
    summary = read_json(extracted / "primary_summary.json")
    manifest = read_json(extracted / "run_manifest.json")
    cases = read_jsonl(extracted / "primary_case_results.jsonl")
    raw_o = read_jsonl(extracted / "raw_phase_o_generations.jsonl")
    raw_m = read_jsonl(extracted / "raw_phase_m_generations.jsonl")
    classification = classify_result(extracted)
    failures = [row for row in cases if row.get("status") != "PASS"]
    failure_counts: dict[str, int] = {}
    for row in failures:
        stage = str(row.get("failure_stage"))
        failure_counts[stage] = failure_counts.get(stage, 0) + 1
    validation_json = result_root / f"{extracted.name}_validation.json"
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
        "source_validation_report": {
            "path": validation_json.name if validation_json.is_file() else None,
            "sha256": sha256_file(validation_json) if validation_json.is_file() else None,
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
        "server_result_classification": {
            "evidence_integrity_status": classification["evidence_integrity_status"],
            "protocol_compliance_status": classification["protocol_compliance_status"],
            "primary_gate_status": classification["primary_gate_status"],
            "scientific_result_eligible": classification["scientific_result_eligible"],
            "evidence_failures": classification["evidence_failures"],
            "protocol_failures": classification["protocol_failures"],
            "backend": summary.get("backend"),
            "protocol_backend": summary.get("protocol_backend"),
            "quantization": manifest.get("model", {}).get("quantization"),
            "phase_o_max_new_tokens": manifest.get("phase_o_max_new_tokens"),
            "phase_m_max_new_tokens": manifest.get("phase_m_max_new_tokens"),
        },
    }
    write_json(stage_dir / "SERVER_RESULT_IMPORT_REPORT.json", report)
    write_json(stage_dir / SERVER_RESULT_CLASSIFICATION_NAME, report["server_result_classification"])
    write_text(stage_dir / "SERVER_RESULT_FAILURE_ANALYSIS.md", failure_analysis(report, cases, raw_o, raw_m))
    write_text(stage_dir / SERVER_RESULT_VALIDATION_REPORT_NAME, validation_report(report))
    status = status_from_classification(classification)
    lock = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": status,
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
        "evidence_integrity_status": classification["evidence_integrity_status"],
        "protocol_compliance_status": classification["protocol_compliance_status"],
        "primary_gate_status": classification["primary_gate_status"],
        "scientific_result_eligible": classification["scientific_result_eligible"],
        "decision": decision_from_classification(classification),
    }
    write_json(stage_dir / "STAGE7E0_A3_SERVER_RESULT_LOCK.json", lock)
    refresh_derived_manifest(stage_dir, result_root)
    return report


def failure_analysis(report: dict[str, Any], cases: list[dict[str, Any]], raw_o: list[dict[str, Any]], raw_m: list[dict[str, Any]]) -> str:
    raw_o_by_id = {row["sample_id"]: row for row in raw_o}
    raw_m_by_id = {row["sample_id"]: row for row in raw_m}
    lines = [
        "# Stage7E0-A3 English Real Server Result Failure Analysis",
        "",
        f"Status: {status_from_classification(report['server_result_classification'])}",
        "",
        "The Qwen GPU output is preserved as server evidence. The PATCH3",
        "constrained run satisfies evidence and protocol checks, but the primary",
        "gate did not reach the required 8/8 pass count. Diagnostics and the",
        "Gretel development-train pilot remain unopened.",
        "",
        "```text",
        f"backend={report['result']['backend']}",
        f"primary_pass_count={report['result']['primary_pass_count']}",
        f"required_pass_count={report['result']['required_pass_count']}",
        f"evidence_integrity_status={report['server_result_classification']['evidence_integrity_status']}",
        f"protocol_compliance_status={report['server_result_classification']['protocol_compliance_status']}",
        f"primary_gate_status={report['server_result_classification']['primary_gate_status']}",
        f"scientific_result_eligible={str(report['server_result_classification']['scientific_result_eligible']).lower()}",
        f"phase_o_raw_rows={report['result']['phase_o_raw_rows']}",
        f"phase_m_raw_rows={report['result']['phase_m_raw_rows']}",
        f"failure_stage_counts={report['result']['failure_stage_counts']}",
        "```",
        "",
        "## Primary-Failure Case Evidence",
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
    classification = report["server_result_classification"]
    status = status_from_classification(classification)
    decision = decision_from_classification(classification)
    return f"""# Stage7E0-A3 English PATCH4 Server Result Validation Report

Status: {status}

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
evidence_integrity_status={classification["evidence_integrity_status"]}
protocol_compliance_status={classification["protocol_compliance_status"]}
primary_gate_status={classification["primary_gate_status"]}
scientific_result_eligible={str(classification["scientific_result_eligible"]).lower()}
diagnostics_run={str(report["result"]["diagnostics_run"]).lower()}
gretel_pilot_opened={str(report["result"]["gretel_pilot_opened"]).lower()}
```

## Decision

{decision}
"""


def include_paths(stage_dir: Path, tar_path: Path) -> list[Path]:
    rel_paths = [
        "pyproject.toml",
        "requirements-inference.lock.txt",
        "scripts/server/run_stage7e0_a3_english.py",
        "scripts/server/run_stage7e0_v2_a1_preflight.py",
        "scripts/data/build_stage7e0_a3_english_preflight.py",
        "scripts/data/build_stage7c_a3_english_offset_semantics.py",
        "scripts/data/validate_stage7e0_a3_english_preflight.py",
        "scripts/data/import_stage7e0_a3_server_results.py",
        "scripts/data/validate_stage7e0_a3_server_results.py",
        "scripts/data/validate_stage7c_a3_english_offset_semantics.py",
        "tests/test_stage7e0_a3_english_preflight.py",
        "tests/test_stage7e0_a3_server_results.py",
        "tests/test_stage7e0_a3_patch2_constrained_backend.py",
        "tests/test_stage7e0_a3_patch3_protocol_hardening.py",
        "tests/test_stage7c_a3_english_offset_semantics.py",
        "tests/support/windows_py314_pytest_tempdir/sitecustomize.py",
        "tests/support/stage7c_pytest_clean_root/conftest.py",
        "src/nldbwrite_v3/v2_a1",
        "src/nldbwrite_v3/inference/parse_output.py",
        "stage7b_v2_method_specification",
        "stage7b_a1_free_text_slot_discovery_amendment",
        "stage7c_a1_v2_development_protocol",
        "stage7c_a2_phase_o_prompt_feasibility_amendment",
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
        "status": status_from_classification(report["server_result_classification"]),
        "primary_pass_count": report["result"]["primary_pass_count"],
        "model_called": True,
        "gpu_called": True,
        "gretel_pilot_opened": False,
        "evidence_integrity_status": report["server_result_classification"]["evidence_integrity_status"],
        "protocol_compliance_status": report["server_result_classification"]["protocol_compliance_status"],
        "primary_gate_status": report["server_result_classification"]["primary_gate_status"],
        "scientific_result_eligible": report["server_result_classification"]["scientific_result_eligible"],
        "package": str(args.package),
        "package_sha256": digest,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
