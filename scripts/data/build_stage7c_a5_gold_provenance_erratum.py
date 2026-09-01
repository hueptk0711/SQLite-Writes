#!/usr/bin/env python3
"""Build Stage7C-A5 gold-provenance erratum and corrected UET replay package."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import zipfile
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scripts.data.build_stage7c_a5_column_conditioned_phase_o_protocol import (  # noqa: E402
    STAGE_NAME as STAGE7C_A5_STAGE,
    canonical_json,
    literal_occurrences,
    oracle_column_conditioned_path,
)
from scripts.data.build_stage7e0_a5_english_preflight import include_paths as preflight_include_paths  # noqa: E402
from scripts.data.validate_stage7e0_a5_server_results import classify_result  # noqa: E402
from scripts.server.run_stage7e0_a5_english import CallResult, PHASE_O_MAX_NEW_TOKENS, evaluate_case  # noqa: E402


STAGE_NAME = "Stage7C_A5_PRIMARY_GOLD_PROVENANCE_ERRATUM_PATCH0"
PACKAGE_DATE = "20260901"
PACKAGE_NAME = f"{STAGE_NAME}_FINAL_REVIEWER_PACKAGE_{PACKAGE_DATE}.zip"
RESULT_DIR_NAME = "stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901"
SERVER_TAR_NAME = f"{RESULT_DIR_NAME}.tar.gz"
SERVER_TAR_SHA256 = "f275b7990c06f6d2130b6ebf0cdb33d29d8a51b83281fdc0beb83cdc2f34c035"
SERVER_RUN_ID = "uet_p4_raw"
CORRECTIONS = [
    {
        "split": "primary",
        "sample_id": "stage7c_a5_primary_english_003",
        "column_ref": "COL_4",
        "column_name": "passed",
        "value": "7",
        "old_candidate_span_ref": "SPAN_0013",
        "old_start_char": 34,
        "old_end_char": 35,
        "new_candidate_span_ref": "SPAN_0030",
        "new_start_char": 75,
        "new_end_char": 76,
    },
    {
        "split": "primary",
        "sample_id": "stage7c_a5_primary_english_011",
        "column_ref": "COL_2",
        "column_name": "artist_name",
        "value": "Glass",
        "old_candidate_span_ref": "SPAN_0009",
        "old_start_char": 19,
        "old_end_char": 24,
        "new_candidate_span_ref": "SPAN_0019",
        "new_start_char": 42,
        "new_end_char": 47,
    },
    {
        "split": "diagnostic",
        "sample_id": "stage7c_a5_fresh_english_011",
        "column_ref": "COL_2",
        "column_name": "state_name",
        "value": "New York",
        "old_candidate_span_ref": "SPAN_0008",
        "old_start_char": 10,
        "old_end_char": 18,
        "new_candidate_span_ref": "SPAN_0021",
        "new_start_char": 33,
        "new_end_char": 41,
    },
]
ERRATUM_ARTIFACTS = [
    "GOLD_PROVENANCE_ERRATUM.json",
    "DUPLICATE_LITERAL_GOLD_AUDIT.json",
    "CORRECTED_FRESH_ENGLISH_A5_PRIMARY_FEASIBILITY_SET.jsonl",
    "CORRECTED_A4_DERIVED_REGRESSION_DIAGNOSTICS_A5.jsonl",
    "OFFLINE_REPLAY_CORRECTED_UET_PRIMARY_RESULTS.jsonl",
    "OFFLINE_REPLAY_CORRECTED_UET_PRIMARY_SUMMARY.json",
    "SERVER_RESULT_RECLASSIFICATION_PATCH0.json",
    "ERRATUM_REPORT.md",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
    "ERRATUM_LOCK.json",
]


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl", ".md", ".py", ".txt", ".toml", ".sh"}:
        data = canonical_text(data.decode("utf-8-sig")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(canonical_text(text).encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


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


def find_result_dir(root: Path) -> Path:
    exact = root / RESULT_DIR_NAME
    if exact.is_dir():
        return exact
    candidates = [child for child in root.iterdir() if child.is_dir() and (child / "primary_summary.json").is_file()]
    if len(candidates) == 1:
        return candidates[0]
    raise RuntimeError(f"Expected one result directory inside {root}")


def correction_by_row(split: str) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (item["sample_id"], item["column_ref"]): item
        for item in CORRECTIONS
        if item["split"] == split
    }


def apply_corrections(rows: list[dict[str, Any]], *, split: str) -> list[dict[str, Any]]:
    corrections = correction_by_row(split)
    corrected = json.loads(canonical_json(rows))
    for row in corrected:
        sample_id = row["sample_id"]
        phase_o = row["label_side_expected"]["phase_o"]["column_span_refs"]
        by_ref = {candidate["span_ref"]: candidate for candidate in row["runtime_constraints"]["candidate_inventory"]}
        for column_ref, correction in corrections.items():
            if column_ref[0] != sample_id:
                continue
            if phase_o.get(correction["column_ref"]) != correction["old_candidate_span_ref"]:
                raise RuntimeError(f"Old gold ref mismatch for {sample_id}:{correction['column_ref']}")
            candidate = by_ref.get(correction["new_candidate_span_ref"])
            if candidate is None:
                raise RuntimeError(f"Missing new candidate {correction['new_candidate_span_ref']} for {sample_id}")
            if candidate["start_char"] != correction["new_start_char"] or candidate["end_char"] != correction["new_end_char"] or candidate["text"] != correction["value"]:
                raise RuntimeError(f"New candidate coordinates mismatch for {sample_id}:{correction['column_ref']}")
            question = row["model_side_input"]["question"]
            if question[correction["new_start_char"] : correction["new_end_char"]] != correction["value"]:
                raise RuntimeError(f"Question slice mismatch for {sample_id}:{correction['column_ref']}")
            phase_o[correction["column_ref"]] = correction["new_candidate_span_ref"]
            for oracle_row in row["label_side_expected"]["gold_column_span_ref_oracle"]:
                if oracle_row["column_ref"] == correction["column_ref"]:
                    oracle_row.update(
                        {
                            "start_char": correction["new_start_char"],
                            "end_char": correction["new_end_char"],
                            "text": correction["value"],
                            "candidate_span_ref": correction["new_candidate_span_ref"],
                            "candidate_tags": list(candidate["tags"]),
                            "occurrence_count": len(literal_occurrences(question, correction["value"])),
                            "explicitly_disambiguated": True,
                            "erratum_correction": True,
                        }
                    )
                    break
            else:
                raise RuntimeError(f"Missing oracle row for {sample_id}:{correction['column_ref']}")
            db_path = PROJECT_ROOT / STAGE7C_A5_STAGE / row["synthetic_db_spec"]["sqlite_db_path"]
            oracle = oracle_column_conditioned_path(row, db_path)
            row["label_side_expected"]["resolved_column_span_oracle"] = oracle["resolved_column_spans"]
            row["label_side_expected"]["deterministic_ir_oracle"] = oracle["deterministic_ir"]
            row["label_side_expected"]["target_state"]["compiler_observed_target_state_hash"] = oracle["observed_target_state_hash"]
    return corrected


def duplicate_literal_audit(primary_rows: list[dict[str, Any]], diagnostic_rows: list[dict[str, Any]]) -> dict[str, Any]:
    correction_keys = {(item["split"], item["sample_id"], item["column_ref"]) for item in CORRECTIONS}
    rows = []
    for split, split_rows in (("primary", primary_rows), ("diagnostic", diagnostic_rows)):
        for row in split_rows:
            question = row["model_side_input"]["question"]
            by_ref = {candidate["span_ref"]: candidate for candidate in row["runtime_constraints"]["candidate_inventory"]}
            for oracle_row in row["label_side_expected"]["gold_column_span_ref_oracle"]:
                value = oracle_row["text"]
                occurrences = literal_occurrences(question, value)
                selected = by_ref[oracle_row["candidate_span_ref"]]
                duplicate = len(occurrences) > 1
                corrected = (split, row["sample_id"], oracle_row["column_ref"]) in correction_keys
                rows.append(
                    {
                        "split": split,
                        "sample_id": row["sample_id"],
                        "column_ref": oracle_row["column_ref"],
                        "column_name": oracle_row["column_name"],
                        "value": value,
                        "occurrence_count": len(occurrences),
                        "occurrences": occurrences,
                        "gold_start_char": oracle_row["start_char"],
                        "gold_end_char": oracle_row["end_char"],
                        "candidate_span_ref": oracle_row["candidate_span_ref"],
                        "candidate_text": selected["text"],
                        "duplicate_literal": duplicate,
                        "explicitly_disambiguated": corrected if duplicate else False,
                        "implicit_first_occurrence_forbidden": duplicate and not corrected,
                    }
                )
    duplicate_rows = [row for row in rows if row["duplicate_literal"]]
    forbidden = [row for row in duplicate_rows if row["implicit_first_occurrence_forbidden"]]
    return {
        "stage": STAGE_NAME,
        "status": "PASS" if len(duplicate_rows) == 3 and not forbidden else "FAIL",
        "primary_case_count": len(primary_rows),
        "diagnostic_case_count": len(diagnostic_rows),
        "duplicate_literal_count": len(duplicate_rows),
        "primary_duplicate_literal_count": sum(1 for row in duplicate_rows if row["split"] == "primary"),
        "diagnostic_duplicate_literal_count": sum(1 for row in duplicate_rows if row["split"] == "diagnostic"),
        "implicit_first_occurrence_forbidden_count": len(forbidden),
        "rows": rows,
    }


class RawReplayGenerator:
    def __init__(self, raw_by_id: dict[str, dict[str, Any]]):
        self.raw_by_id = raw_by_id

    def generate(self, *, sample_id: str, messages: list[dict[str, str]], max_new_tokens: int, row: dict[str, Any]) -> CallResult:
        del messages, max_new_tokens, row
        raw = self.raw_by_id[sample_id]
        return CallResult(
            sample_id=sample_id,
            phase=str(raw.get("phase")),
            raw_output=str(raw.get("raw_output")),
            status=str(raw.get("status")),
            error=raw.get("error"),
            input_tokens=raw.get("input_tokens"),
            output_tokens=raw.get("output_tokens"),
            latency_sec=float(raw.get("latency_sec", 0.0)),
            hit_max_new_tokens=bool(raw.get("hit_max_new_tokens", False)),
            generation_metadata=raw.get("generation_metadata"),
        )

    def metadata(self) -> dict[str, Any]:
        return {"backend": "raw_uet_replay"}


def corrected_replay(corrected_primary_rows: list[dict[str, Any]], source_result_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_rows = read_jsonl(source_result_dir / "raw_primary_phase_o_generations.jsonl")
    raw_by_id = {row["sample_id"]: row for row in raw_rows}
    generator = RawReplayGenerator(raw_by_id)
    results = []
    for row in corrected_primary_rows:
        result, _raw = evaluate_case(row, generator, phase_o_max_new_tokens=PHASE_O_MAX_NEW_TOKENS)
        results.append(result)
    pass_count = sum(1 for row in results if row["status"] == "PASS")
    failure_counts: dict[str, int] = {}
    for row in results:
        key = str(row.get("failure_stage"))
        failure_counts[key] = failure_counts.get(key, 0) + 1
    summary = {
        "stage": STAGE_NAME,
        "source_stage": "Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT",
        "status": "REAL_CONSTRAINED_PRIMARY_FAIL_DO_NOT_OPEN_GRETEL",
        "old_gold_primary_pass_count": "1/12",
        "corrected_primary_pass_count": f"{pass_count}/12",
        "required_pass_count": "12/12",
        "original_classification_superseded": "SUPERSEDED_BY_GOLD_PROVENANCE_ERRATUM",
        "model_called": False,
        "gpu_called": False,
        "raw_uet_outputs_reused": True,
        "raw_primary_phase_o_sha256": sha256_file(source_result_dir / "raw_primary_phase_o_generations.jsonl"),
        "failure_stage_counts": failure_counts,
        "corrected_pass_case_ids": [row["sample_id"] for row in results if row["status"] == "PASS"],
    }
    return results, summary


def erratum_report(summary: dict[str, Any], audit: dict[str, Any]) -> str:
    return f"""# Stage7C-A5 Gold Provenance Erratum PATCH0

Status: {summary["status"]}

The Stage7E0-A5 UET raw generations are reused byte-for-byte. No model, GPU,
Gretel pilot, development-dev, or official test rows are opened in this
erratum. The previous old-gold `1/12` classification is superseded by corrected
offline replay.

```text
source_tar={SERVER_TAR_NAME}
source_tar_sha256={SERVER_TAR_SHA256}
old_gold_primary_pass_count={summary["old_gold_primary_pass_count"]}
corrected_primary_pass_count={summary["corrected_primary_pass_count"]}
required_pass_count={summary["required_pass_count"]}
duplicate_literal_count={audit["duplicate_literal_count"]}
primary_duplicate_literal_count={audit["primary_duplicate_literal_count"]}
diagnostic_duplicate_literal_count={audit["diagnostic_duplicate_literal_count"]}
implicit_first_occurrence_forbidden_count={audit["implicit_first_occurrence_forbidden_count"]}
corrected_pass_case_ids={summary["corrected_pass_case_ids"]}
```
"""


def validation_report(summary: dict[str, Any], old_classification: dict[str, Any]) -> str:
    return f"""# Stage7C-A5 Gold Provenance Erratum PATCH0 Validation Report

Status: {summary["status"]}

Validation date: {date.today().isoformat()}

```text
old_gold_evidence_integrity_status={old_classification["evidence_integrity_status"]}
old_gold_protocol_compliance_status={old_classification["protocol_compliance_status"]}
old_gold_primary_pass_count={old_classification["primary_pass_count"]}
old_gold_primary_gate_status={old_classification["primary_gate_status"]}
corrected_primary_pass_count={summary["corrected_primary_pass_count"]}
corrected_primary_gate_status=FAIL
scientific_result_eligible=true
gretel_pilot_opened=false
diagnostics_run=false
```
"""


def reviewer_readme(summary: dict[str, Any]) -> str:
    return f"""# Stage7C-A5 Primary Gold Provenance Erratum PATCH0

This package corrects three source-span provenance labels generated by the old
implicit first-occurrence lookup. It then replays the same frozen UET raw model
outputs offline. The corrected result is still a protocol-compliant scientific
primary failure: `{summary["corrected_primary_pass_count"]}` with `12/12`
required.

Review these first:

```text
{STAGE_NAME}/GOLD_PROVENANCE_ERRATUM.json
{STAGE_NAME}/DUPLICATE_LITERAL_GOLD_AUDIT.json
{STAGE_NAME}/OFFLINE_REPLAY_CORRECTED_UET_PRIMARY_SUMMARY.json
{STAGE_NAME}/SERVER_RESULT_RECLASSIFICATION_PATCH0.json
{STAGE_NAME}/VALIDATION_REPORT.md
```

No GPU rerun is required. Gretel and diagnostics remain closed.
"""


def build_manifest(stage_dir: Path) -> dict[str, Any]:
    artifacts = [
        {"path": rel, "bytes": (stage_dir / rel).stat().st_size, "sha256": sha256_file(stage_dir / rel)}
        for rel in sorted(ERRATUM_ARTIFACTS)
        if (stage_dir / rel).is_file()
    ]
    return {
        "stage": STAGE_NAME,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "combined_scientific_artifacts_sha256": sha256_text(canonical_json(artifacts)),
    }


def build_stage(stage_dir: Path, tar_path: Path) -> dict[str, Any]:
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)
    tar_path = tar_path.resolve()
    if sha256_file(tar_path) != SERVER_TAR_SHA256:
        raise RuntimeError(f"Server tar SHA mismatch for {tar_path}")
    source_root = stage_dir / SERVER_RUN_ID
    safe_extract_tar(tar_path, source_root)
    source_result_dir = find_result_dir(source_root)
    old_classification = classify_result(source_result_dir)
    primary_rows = read_jsonl(PROJECT_ROOT / STAGE7C_A5_STAGE / "FRESH_ENGLISH_A5_PRIMARY_FEASIBILITY_SET.jsonl")
    diagnostic_rows = read_jsonl(PROJECT_ROOT / STAGE7C_A5_STAGE / "A4_DERIVED_REGRESSION_DIAGNOSTICS_A5.jsonl")
    corrected_primary = apply_corrections(primary_rows, split="primary")
    corrected_diagnostic = apply_corrections(diagnostic_rows, split="diagnostic")
    audit = duplicate_literal_audit(corrected_primary, corrected_diagnostic)
    if audit["status"] != "PASS":
        raise RuntimeError("Duplicate literal gold audit failed")
    replay_rows, replay_summary = corrected_replay(corrected_primary, source_result_dir)
    reclassification = {
        "stage": STAGE_NAME,
        "status": replay_summary["status"],
        "source_tar_sha256": SERVER_TAR_SHA256,
        "old_gold_primary_pass_count": old_classification["primary_pass_count"],
        "old_gold_primary_gate_status": old_classification["primary_gate_status"],
        "old_gold_classification": "SUPERSEDED_BY_GOLD_PROVENANCE_ERRATUM",
        "corrected_primary_pass_count": replay_summary["corrected_primary_pass_count"],
        "corrected_primary_gate_status": "FAIL",
        "evidence_integrity_status": old_classification["evidence_integrity_status"],
        "protocol_compliance_status": old_classification["protocol_compliance_status"],
        "scientific_result_eligible": True,
        "gretel_pilot_opened": False,
        "diagnostics_run": False,
    }
    write_json(stage_dir / "GOLD_PROVENANCE_ERRATUM.json", {"stage": STAGE_NAME, "status": "PASS", "corrections": CORRECTIONS})
    write_json(stage_dir / "DUPLICATE_LITERAL_GOLD_AUDIT.json", audit)
    write_jsonl(stage_dir / "CORRECTED_FRESH_ENGLISH_A5_PRIMARY_FEASIBILITY_SET.jsonl", corrected_primary)
    write_jsonl(stage_dir / "CORRECTED_A4_DERIVED_REGRESSION_DIAGNOSTICS_A5.jsonl", corrected_diagnostic)
    write_jsonl(stage_dir / "OFFLINE_REPLAY_CORRECTED_UET_PRIMARY_RESULTS.jsonl", replay_rows)
    write_json(stage_dir / "OFFLINE_REPLAY_CORRECTED_UET_PRIMARY_SUMMARY.json", replay_summary)
    write_json(stage_dir / "SERVER_RESULT_RECLASSIFICATION_PATCH0.json", reclassification)
    write_text(stage_dir / "ERRATUM_REPORT.md", erratum_report(replay_summary, audit))
    write_text(stage_dir / "VALIDATION_REPORT.md", validation_report(replay_summary, old_classification))
    write_text(stage_dir / "REVIEWER_README.md", reviewer_readme(replay_summary))
    lock = {
        "stage": STAGE_NAME,
        "status": replay_summary["status"],
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_tar_sha256": SERVER_TAR_SHA256,
        "old_gold_primary_pass_count": old_classification["primary_pass_count"],
        "old_gold_classification": "SUPERSEDED_BY_GOLD_PROVENANCE_ERRATUM",
        "corrected_primary_pass_count": replay_summary["corrected_primary_pass_count"],
        "required_pass_count": "12/12",
        "model_called": False,
        "gpu_called": False,
        "raw_uet_outputs_reused": True,
        "gretel_pilot_opened": False,
        "diagnostics_run": False,
        "correction_count": len(CORRECTIONS),
        "duplicate_literal_gold_audit_sha256": sha256_file(stage_dir / "DUPLICATE_LITERAL_GOLD_AUDIT.json"),
    }
    write_json(stage_dir / "ERRATUM_LOCK.json", lock)
    write_json(stage_dir / "DERIVED_ARTIFACT_MANIFEST.json", build_manifest(stage_dir))
    return {
        "stage": STAGE_NAME,
        "status": replay_summary["status"],
        "old_gold_primary_pass_count": old_classification["primary_pass_count"],
        "corrected_primary_pass_count": replay_summary["corrected_primary_pass_count"],
        "correction_count": len(CORRECTIONS),
        "source_tar_sha256": SERVER_TAR_SHA256,
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
    }


def include_paths(stage_dir: Path, tar_path: Path) -> list[Path]:
    paths = preflight_include_paths(PROJECT_ROOT / "Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT")
    paths.extend(path for path in stage_dir.rglob("*") if path.is_file())
    paths.extend(
        PROJECT_ROOT / rel
        for rel in (
            "scripts/data/build_stage7c_a5_gold_provenance_erratum.py",
            "scripts/data/validate_stage7c_a5_gold_provenance_erratum.py",
            "tests/test_stage7c_a5_gold_provenance_erratum.py",
            "stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901.tar.gz",
            "stage7e0_a5_english_column_conditioned_uet_rtx4090_primary_results_20260901.tar.gz.sha256",
        )
        if (PROJECT_ROOT / rel).is_file()
    )
    if tar_path.is_file():
        paths.append(tar_path.resolve())
    tar_sha = Path(f"{tar_path}.sha256")
    if tar_sha.is_file():
        paths.append(tar_sha.resolve())
    return sorted({path for path in paths if path.is_file()})


def package_reviewer(stage_dir: Path, tar_path: Path, package_path: Path) -> str:
    if package_path.exists():
        package_path.unlink()
    tar_path = tar_path.resolve()
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in include_paths(stage_dir, tar_path):
            if path.is_relative_to(stage_dir):
                arcname = Path(STAGE_NAME) / path.relative_to(stage_dir)
            elif path == tar_path or path == Path(f"{tar_path}.sha256"):
                arcname = Path(path.name)
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
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--server-results-tar", type=Path, default=PROJECT_ROOT / SERVER_TAR_NAME)
    parser.add_argument("--package", type=Path, default=PROJECT_ROOT / PACKAGE_NAME)
    args = parser.parse_args()
    summary = build_stage(args.stage_dir, args.server_results_tar)
    digest = package_reviewer(args.stage_dir, args.server_results_tar, args.package)
    summary["package"] = str(args.package)
    summary["package_sha256"] = digest
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
