from __future__ import annotations

import csv
import json
import shutil
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

from nldbwrite_v3.compiler import preflight_program
from nldbwrite_v3.evaluator import find_database
from nldbwrite_v3.ir import CompiledProgram, CompiledStatement

from .reporting_v2_3 import (
    _discover_extracted_root,
    _discover_run_root,
    load_json,
    portable_filename,
    sha256_file,
)


METHODS = (
    ("d_fs_m", "D-FS-M", "direct_sql"),
    ("j_fs_m", "J-FS-M", "compiled_program"),
)


def _local_archive_path(
    workspace: Path,
    import_report: dict[str, Any],
) -> Path:
    """Resolve an imported result archive without trusting host-specific paths.

    ``archive`` is provenance and may contain an absolute path from the machine
    that assembled the release.  Reproduction always opens the archive shipped
    under ``04_results/00_incoming_from_server``.  ``archive_filename`` is the
    canonical portable field; older reports fall back to a separator-agnostic
    basename.
    """
    archive_name = str(import_report.get("archive_filename") or "")
    if not archive_name:
        archive_name = portable_filename(str(import_report.get("archive") or ""))
    if not archive_name:
        raise ValueError("Import report does not identify a final result archive")
    return (
        workspace
        / "04_results"
        / "00_incoming_from_server"
        / archive_name
    )


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _program_from_artifact(row: dict[str, Any]) -> CompiledProgram | None:
    if row.get("status") != "success":
        return None
    return CompiledProgram(
        status="success",
        strict_atomic=bool(row.get("strict_atomic", True)),
        statements=[
            CompiledStatement(
                sql=str(item["sql"]),
                params=list(item.get("params") or []),
                group_id=str(item.get("group_id") or ""),
                table=str(item.get("table") or ""),
                row_count=int(item.get("row_count") or 0),
                normalizations=list(item.get("normalizations") or []),
            )
            for item in row.get("statements") or []
        ],
    )


def _direct_sql_preflight(db_path: Path, statements: list[str]) -> dict[str, Any]:
    if not statements:
        return {
            "accepted": False,
            "status": "abstained",
            "error_class": "upstream_rejection",
            "error": "No parsed SQL statements were available.",
        }
    unsafe = [
        statement
        for statement in statements
        if not statement.lstrip().upper().startswith(("INSERT ", "REPLACE "))
    ]
    if unsafe:
        return {
            "accepted": False,
            "status": "abstained",
            "error_class": "unsafe_sql",
            "error": "Only INSERT/REPLACE statements are allowed.",
            "executed_statements": 0,
        }
    source = sqlite3.connect(str(db_path))
    target = sqlite3.connect(":memory:")
    try:
        source.backup(target)
    finally:
        source.close()
    target.execute("PRAGMA foreign_keys = ON")
    executed = 0
    try:
        target.execute("SAVEPOINT common_safety_preflight")
        for statement in statements:
            target.execute(statement)
            executed += 1
        target.execute("ROLLBACK TO common_safety_preflight")
        target.execute("RELEASE common_safety_preflight")
        return {
            "accepted": True,
            "status": "accepted",
            "error_class": None,
            "error": None,
            "executed_statements": executed,
        }
    except sqlite3.Error as exc:
        try:
            target.execute("ROLLBACK TO common_safety_preflight")
            target.execute("RELEASE common_safety_preflight")
        except sqlite3.Error:
            target.rollback()
        message = str(exc)
        normalized = message.casefold()
        if "foreign key" in normalized:
            error_class = "foreign_key_violation"
        elif "unique constraint" in normalized:
            error_class = "unique_violation"
        elif "not null" in normalized:
            error_class = "not_null_violation"
        elif "check constraint" in normalized:
            error_class = "check_violation"
        else:
            error_class = "execution_error"
        return {
            "accepted": False,
            "status": "abstained",
            "error_class": error_class,
            "error": message,
            "executed_statements": executed,
        }
    finally:
        target.close()


def _extract_holdout(workspace: Path, work_root: Path) -> Path:
    archive_path = (
        workspace
        / "03_protocol_and_data"
        / "final_holdout_release"
        / "mp_fs_plus_external_holdout_300_20260731.zip"
    )
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        resolved_root = work_root.resolve()
        for member in members:
            target = (resolved_root / member.filename).resolve()
            if target != resolved_root and resolved_root not in target.parents:
                raise ValueError(f"Unsafe holdout archive path: {member.filename}")
        archive.extractall(work_root)
    extracted = work_root / "mp_fs_plus_external_holdout_300_20260731"
    if not (extracted / "dataset.final.json").is_file():
        raise ValueError("Holdout extraction failed")
    return extracted


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    accepted = sum(bool(row["preflight_accepted"]) for row in rows)
    correct = sum(
        bool(row["preflight_accepted"] and row["target_state_correct"])
        for row in rows
    )
    false_accepts = sum(
        bool(row["preflight_accepted"] and not row["target_state_correct"])
        for row in rows
    )
    return {
        "samples": count,
        "transactional_preflight_coverage": accepted / count,
        "target_state_accuracy_after_common_preflight": correct / count,
        "accuracy_conditional_on_common_preflight": (
            correct / accepted if accepted else None
        ),
        "false_accept_rate_conditional_on_common_preflight": (
            false_accepts / accepted if accepted else None
        ),
        "false_accept_count": false_accepts,
    }


def run_common_safety_replay(workspace: Path, output_dir: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    import_report = load_json(
        workspace / "07_reproducibility" / "server_final_run" / "IMPORT_REPORT.json"
    )
    archive_path = _local_archive_path(workspace, import_report)
    if not archive_path.is_file():
        raise FileNotFoundError(f"Final result archive is missing: {archive_path}")
    expected_sha256 = str(import_report.get("archive_sha256") or "")
    if expected_sha256 and sha256_file(archive_path) != expected_sha256:
        raise ValueError("Final result archive checksum mismatch")
    extracted_results = _discover_extracted_root(
        workspace,
        import_report,
        archive_path,
    )
    run_root = _discover_run_root(extracted_results)
    work_root = output_dir / "_common_safety_holdout"
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True)

    all_rows: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}
    try:
        holdout = _extract_holdout(workspace, work_root)
        dataset = {
            str(row["id"]): row
            for row in load_json(holdout / "dataset.final.json")
        }
        db_root = holdout / "databases"

        for slug, method_id, representation in METHODS:
            method_root = run_root / slug
            evaluations = {
                str(row["sample_id"]): row
                for row in _load_jsonl(method_root / "evaluation.jsonl")
            }
            parsed = {
                str(row["sample_id"]): row
                for row in _load_jsonl(method_root / "parsed_mapping_plans.jsonl")
            }
            programs = {
                str(row["sample_id"]): row
                for row in _load_jsonl(method_root / "compiled_programs.jsonl")
            }
            if len(evaluations) != 300 or set(evaluations) != set(dataset):
                raise ValueError(f"Incomplete locked artifacts for {method_id}")

            method_rows: list[dict[str, Any]] = []
            for sample_id in sorted(evaluations):
                evaluation = evaluations[sample_id]
                sample = dataset[sample_id]
                db_path = find_database(db_root, str(sample["db_id"]))
                if not evaluation.get("build_success"):
                    preflight = {
                        "accepted": False,
                        "status": "abstained",
                        "error_class": "upstream_rejection",
                        "error": "Locked prediction did not reach build success.",
                    }
                elif representation == "direct_sql":
                    preflight = _direct_sql_preflight(
                        Path(db_path),
                        list(parsed[sample_id].get("direct_sql") or []),
                    )
                else:
                    program = _program_from_artifact(programs[sample_id])
                    if program is None:
                        preflight = {
                            "accepted": False,
                            "status": "abstained",
                            "error_class": "upstream_rejection",
                            "error": "Locked compiled program was unavailable.",
                        }
                    else:
                        preflight = preflight_program(db_path, program)
                row = {
                    "sample_id": sample_id,
                    "db_id": sample["db_id"],
                    "method_id": method_id,
                    "input_format": evaluation.get("detected_format"),
                    "complexity": (
                        "multi_table"
                        if "multi_table" in (evaluation.get("slice_labels") or [])
                        else "single_table"
                    ),
                    "preflight_accepted": bool(preflight.get("accepted")),
                    "preflight_status": preflight.get("status"),
                    "preflight_error_class": preflight.get("error_class"),
                    "canonical_execution_success": bool(
                        evaluation.get("execution_success")
                    ),
                    "target_state_correct": bool(
                        evaluation.get("target_state_correct")
                    ),
                }
                method_rows.append(row)
                all_rows.append(row)
            summary = _summary(method_rows)
            canonical_execution = sum(
                row["canonical_execution_success"] for row in method_rows
            ) / 300
            if abs(summary["transactional_preflight_coverage"] - canonical_execution) > 1e-12:
                raise ValueError(
                    f"Common-preflight anchor mismatch for {method_id}: "
                    f"preflight={summary['transactional_preflight_coverage']}; "
                    f"execution={canonical_execution}"
                )
            summaries[method_id] = summary

        mp_rows: list[dict[str, Any]] = []
        for evaluation in _load_jsonl(run_root / "mp_fs_plus" / "evaluation.jsonl"):
            row = {
                "sample_id": evaluation["sample_id"],
                "db_id": evaluation["db_id"],
                "method_id": "MP-FS+",
                "input_format": evaluation.get("detected_format"),
                "complexity": (
                    "multi_table"
                    if "multi_table" in (evaluation.get("slice_labels") or [])
                    else "single_table"
                ),
                "preflight_accepted": bool(evaluation.get("preflight_accepted")),
                "preflight_status": (
                    "accepted"
                    if evaluation.get("preflight_accepted")
                    else "abstained"
                ),
                "preflight_error_class": (
                    None
                    if evaluation.get("preflight_accepted")
                    else evaluation.get("error_type")
                ),
                "canonical_execution_success": bool(
                    evaluation.get("execution_success")
                ),
                "target_state_correct": bool(
                    evaluation.get("target_state_correct")
                ),
            }
            mp_rows.append(row)
            all_rows.append(row)
        if len(mp_rows) != 300:
            raise ValueError("Incomplete locked artifacts for MP-FS+")
        summaries["MP-FS+"] = _summary(mp_rows)
    finally:
        if work_root.exists():
            shutil.rmtree(work_root)

    jsonl_path = output_dir / "common_safety_replay.jsonl"
    jsonl_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in all_rows),
        encoding="utf-8",
        newline="\n",
    )
    csv_path = output_dir / "common_safety_summary.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        fields = ["method_id", *next(iter(summaries.values())).keys()]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for method_id, summary in summaries.items():
            writer.writerow({"method_id": method_id, **summary})

    report = {
        "analysis_id": "post_hoc_common_transactional_preflight_v1",
        "status": "pass",
        "analysis_class": "post_hoc_deterministic_common_safety_replay",
        "predictions_modified": False,
        "model_inference_rerun": False,
        "primary_results_modified": False,
        "database_execution_replayed_on_in_memory_copies": True,
        "scope_note": (
            "The transactional dry-run boundary is standardized. The Write Plan "
            "hard verifier is applicable to J-FS-M but not to direct SQL."
        ),
        "methods": summaries,
    }
    (output_dir / "common_safety_results.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    lines = [
        "# Post-hoc common transactional-preflight replay",
        "",
        "Locked predictions are replayed without model inference. Primary results are unchanged.",
        "",
        "| Method | Preflight coverage | Target after gate | Correct given gate | False accepts | False-accept rate |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for method_id, summary in summaries.items():
        lines.append(
            f"| {method_id} | {summary['transactional_preflight_coverage']:.4f} | "
            f"{summary['target_state_accuracy_after_common_preflight']:.4f} | "
            f"{summary['accuracy_conditional_on_common_preflight']:.4f} | "
            f"{summary['false_accept_count']} | "
            f"{summary['false_accept_rate_conditional_on_common_preflight']:.4f} |"
        )
    lines.extend(
        [
            "",
            "The common gate removes execution failures but cannot detect programs that execute successfully into the wrong target state.",
        ]
    )
    (output_dir / "common_safety_summary.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    source_root = Path(__file__).resolve().parents[3]
    holdout_archive = (
        workspace
        / "03_protocol_and_data"
        / "final_holdout_release"
        / "mp_fs_plus_external_holdout_300_20260731.zip"
    )
    source_files = (
        "run_common_safety_replay.py",
        "src/nldbwrite_v3/analysis/common_safety_replay.py",
        "src/nldbwrite_v3/compiler/executor.py",
        "src/nldbwrite_v3/evaluator/state.py",
        "tests/test_common_safety_replay.py",
    )
    output_files = (
        "common_safety_replay.jsonl",
        "common_safety_summary.csv",
        "common_safety_results.json",
        "common_safety_summary.md",
    )
    manifest = {
        "analysis_id": report["analysis_id"],
        "status": "frozen_post_hoc_deterministic_analysis",
        "predictions_modified": False,
        "model_inference_rerun": False,
        "input_sha256": {
            "final_result_archive": sha256_file(archive_path),
            "final_holdout_archive": sha256_file(holdout_archive),
        },
        "source_sha256": {
            name: sha256_file(source_root / name) for name in source_files
        },
        "output_sha256": {
            name: sha256_file(output_dir / name) for name in output_files
        },
    }
    (output_dir / "COMMON_SAFETY_REPLAY_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report
