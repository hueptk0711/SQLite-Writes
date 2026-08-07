from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

from nldbwrite_v3.baselines import (
    build_sql_with_v2,
    legacy_record_json_to_write_plan,
)
from nldbwrite_v3.compiler import compile_verified_plan
from nldbwrite_v3.evaluator import evaluate_candidate_sample, find_database
from nldbwrite_v3.experiments.metrics import summarize_run
from nldbwrite_v3.inference.parse_output import extract_json_object
from nldbwrite_v3.schema import load_profile
from nldbwrite_v3.verifier import verify_write_plan


REPLAYS = (
    ("j_fs_m", "J-FS-M", "common", "J outputs -> common compiler"),
    ("j_fs_m", "J-FS-M", "v2", "J outputs -> legacy v2 builder"),
    ("s_fs_v2_m", "S-FS-v2-M", "common", "S outputs -> common compiler"),
    ("s_fs_v2_m", "S-FS-v2-M", "v2", "S outputs -> legacy v2 builder"),
)


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
        newline="\n",
    )


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def v2_source_tree_sha256(v2_source: Path) -> str:
    """Reproduce the exact v2-source hash used by the frozen run lock."""
    files = {
        path.relative_to(v2_source).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(
            (v2_source / "nldbwrite").rglob("*.py"),
            key=lambda item: item.relative_to(v2_source).as_posix(),
        )
        if "__pycache__" not in path.parts
    }
    return _canonical_sha256(files)


def _first_verifier_error(
    verification: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    errors = (verification or {}).get("errors") or []
    if not errors:
        return None, None
    first = errors[0]
    return str(first.get("error_code") or "VERIFICATION_ERROR"), str(
        first.get("message") or ""
    )


def _evaluate_replay(
    sample: dict[str, Any],
    raw: dict[str, Any],
    profile: dict[str, Any],
    db_root: Path,
    *,
    backend: str,
    v2_source: Path,
    source_evaluation: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    parsed_json, parse_error = extract_json_object(str(raw.get("raw_output") or ""))
    parse_status = "success" if parsed_json is not None else "json_error"
    verification_dict: dict[str, Any] | None = None
    program = None
    direct_sql: list[str] | None = None
    builder_status = "not_applicable"
    builder_errors: list[Any] = []

    if parsed_json is not None:
        try:
            plan = legacy_record_json_to_write_plan(parsed_json, profile)
            if backend == "common":
                verification = verify_write_plan(plan, profile)
                verification_dict = verification.to_dict()
                if verification.valid:
                    program = compile_verified_plan(
                        verification.normalized_plan,
                        profile,
                    )
            elif backend == "v2":
                (
                    builder_status,
                    direct_sql,
                    builder_errors,
                    _builder_metadata,
                ) = build_sql_with_v2(
                    parsed_json,
                    profile,
                    v2_source_path=v2_source,
                )
                if builder_status != "success":
                    direct_sql = []
            else:
                raise ValueError(f"Unknown backend: {backend}")
        except Exception as exc:  # deterministic replay records, then continues
            builder_status = "error"
            builder_errors = [f"{type(exc).__name__}: {exc}"]
            program = None
            direct_sql = []

    build_status = (
        "success"
        if (program is not None and program.status == "success")
        or (direct_sql and parse_status == "success")
        else "error"
    )
    evaluation = evaluate_candidate_sample(
        sample,
        find_database(db_root, str(sample["db_id"])),
        program=program,
        direct_sql=direct_sql,
        parse_status=parse_status,
        build_status=build_status,
        preflight=None,
    )
    first_error, first_message = _first_verifier_error(verification_dict)
    if evaluation.get("error_type") in {None, "builder_error"} and first_error:
        evaluation["error_type"] = first_error
        evaluation["error_message"] = first_message
    elif evaluation.get("error_type") in {None, "builder_error"} and builder_errors:
        evaluation["error_type"] = "builder_error"
        evaluation["error_message"] = str(builder_errors[0])

    evaluation.update(
        {
            "method": label,
            "source_method": source_evaluation.get("method"),
            "replay_backend": backend,
            "parse_success": parse_status == "success",
            "plan_validation_success": (
                bool(
                    verification_dict
                    and verification_dict.get("status") == "valid"
                )
                if backend == "common"
                else None
            ),
            "build_success": build_status == "success",
            "accepted_output": build_status == "success",
            "preflight_accepted": None,
            "generation_status": raw.get("status"),
            "state_changing": source_evaluation.get("state_changing"),
            "conflict_sensitive": source_evaluation.get("conflict_sensitive"),
            "is_original_request": source_evaluation.get("is_original_request"),
            "slice_labels": source_evaluation.get("slice_labels") or [],
            "detected_mode": source_evaluation.get("detected_mode"),
            "detected_format": source_evaluation.get("detected_format"),
            "plan_metrics_available": False,
            "backend_swap_analysis": True,
            "builder_status": builder_status,
            "parse_diagnostic": parse_error,
        }
    )
    return evaluation


def _run_root(workspace: Path) -> Path:
    parent = workspace / "04_results" / "01_extracted_archive"
    extracted = [path for path in parent.iterdir() if path.is_dir()]
    if len(extracted) != 1:
        raise ValueError(f"Expected one extracted archive, found {len(extracted)}")
    runs = [
        path
        for path in (extracted[0] / "experiments" / "external_holdout").iterdir()
        if path.is_dir()
    ]
    if len(runs) != 1:
        raise ValueError(f"Expected one result root, found {len(runs)}")
    return runs[0]


def _auto_v2_source(workspace: Path) -> Path:
    candidate = (
        workspace.parent
        / "99_archive_history_20260731"
        / "legacy_sources_and_results"
        / "server_downloads"
        / "paper_v2_current"
        / "code"
        / "nl_db_write_pipeline"
        / "src"
    )
    if not (candidate / "nldbwrite").is_dir():
        raise ValueError("Could not auto-discover frozen v2 source")
    return candidate


def run_backend_swap(
    workspace: Path,
    output_dir: Path,
    *,
    v2_source: Path | None = None,
) -> dict[str, Any]:
    v2_source = (v2_source or _auto_v2_source(workspace)).resolve()
    if not (v2_source / "nldbwrite").is_dir():
        raise ValueError("v2_source must be the directory containing nldbwrite/")
    holdout_zip = (
        workspace
        / "03_protocol_and_data"
        / "final_holdout_release"
        / "mp_fs_plus_external_holdout_300_20260731.zip"
    )
    canonical = _load_json(
        workspace
        / "04_results"
        / "02_paper_ready"
        / "reports"
        / "final_matrix_results.json"
    )
    run_root = _run_root(workspace)
    s_run_lock = _load_json(run_root / "s_fs_v2_m" / "run_lock.json")
    required_v2_hash = str(s_run_lock["hashes"]["v2_source_tree_sha256"])
    actual_v2_hash = v2_source_tree_sha256(v2_source)
    if actual_v2_hash != required_v2_hash:
        raise ValueError(
            "Frozen v2 source hash mismatch; backend-swap replay refused. "
            f"required={required_v2_hash}; actual={actual_v2_hash}; "
            f"path={v2_source}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    work_root = output_dir / "_backend_swap_holdout"
    extracted = work_root / "mp_fs_plus_external_holdout_300_20260731"
    if not (extracted / "dataset.final.json").is_file():
        work_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(holdout_zip) as archive:
            archive.extractall(work_root)
    if not (extracted / "dataset.final.json").is_file():
        raise ValueError("Holdout archive extraction did not produce dataset.final.json")

    if True:
        dataset = {
            str(row["id"]): row
            for row in _load_json(extracted / "dataset.final.json")
        }
        ids = [
            line.strip()
            for line in (extracted / "final_holdout_ids.txt")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        profiles = {
            db_id: load_profile(extracted / "profiles" / f"{db_id}.json")
            for db_id in sorted({str(dataset[sample_id]["db_id"]) for sample_id in ids})
        }
        db_root = extracted / "databases"

        replay_rows: dict[str, list[dict[str, Any]]] = {}
        summaries: dict[str, dict[str, Any]] = {}
        flat_rows: list[dict[str, Any]] = []
        for source_slug, source_method, backend, label in REPLAYS:
            raw_rows = {
                str(row["sample_id"]): row
                for row in _load_jsonl(run_root / source_slug / "raw_generations.jsonl")
            }
            source_evaluations = {
                str(row["sample_id"]): row
                for row in _load_jsonl(run_root / source_slug / "evaluation.jsonl")
            }
            if set(raw_rows) != set(ids) or set(source_evaluations) != set(ids):
                raise ValueError(f"Incomplete source artifacts for {source_method}")
            rows: list[dict[str, Any]] = []
            for sample_id in ids:
                sample = dataset[sample_id]
                rows.append(
                    _evaluate_replay(
                        sample,
                        raw_rows[sample_id],
                        profiles[str(sample["db_id"])],
                        db_root,
                        backend=backend,
                        v2_source=v2_source,
                        source_evaluation=source_evaluations[sample_id],
                        label=label,
                    )
                )
            replay_rows[label] = rows
            summaries[label] = summarize_run(rows)
            flat_rows.extend(rows)

        anchors = {
            "J outputs -> common compiler": "J-FS-M",
            "S outputs -> legacy v2 builder": "S-FS-v2-M",
        }
        anchor_checks: dict[str, dict[str, Any]] = {}
        for replay_label, canonical_method in anchors.items():
            replay = summaries[replay_label]
            expected = canonical["methods"][canonical_method]
            fields = ("build_success", "execution_success", "target_state_accuracy")
            matches = {
                field: abs(float(replay[field]) - float(expected[field])) <= 1e-12
                for field in fields
            }
            if not all(matches.values()):
                raise ValueError(
                    f"Backend-swap anchor mismatch for {replay_label}: {matches}"
                )
            anchor_checks[replay_label] = {
                "canonical_method": canonical_method,
                "matches": matches,
            }

    summary_rows = [
        {
            "source_outputs": label.split(" outputs", 1)[0],
            "backend": "common_compiler" if "common" in label else "legacy_v2_builder",
            "replay_label": label,
            "parse_coverage": summaries[label]["parse_coverage"],
            "validation_coverage": summaries[label]["validation_coverage"],
            "build_coverage": summaries[label]["build_coverage"],
            "execution_coverage": summaries[label]["execution_coverage"],
            "execution_conditional_accuracy": summaries[label][
                "execution_conditional_accuracy"
            ],
            "target_state_accuracy": summaries[label]["target_state_accuracy"],
        }
        for _slug, _method, _backend, label in REPLAYS
    ]
    csv_path = output_dir / "backend_swap_summary.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        fields = list(summary_rows[0])
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary_rows)
    _write_jsonl(output_dir / "backend_swap_evaluation.jsonl", flat_rows)

    report = {
        "report_version": 1,
        "analysis_id": "deterministic_backend_swap_v1",
        "status": "pass",
        "analysis_class": "post_hoc_deterministic_backend_replay",
        "predictions_modified": False,
        "model_inference_rerun": False,
        "database_execution_replayed_on_temporary_copies": True,
        "primary_results_modified": False,
        "sample_count_per_cell": 300,
        "v2_source": str(v2_source),
        "v2_source_tree_sha256": actual_v2_hash,
        "anchor_checks": anchor_checks,
        "cells": summaries,
    }
    _write_json(output_dir / "backend_swap_results.json", report)
    lines = [
        "# Post-hoc deterministic backend-swap analysis",
        "",
        "The same locked raw Record-JSON output is replayed through both deterministic backends. No model output or prompt is regenerated. This is not a pre-registered primary comparison.",
        "",
        "| Source raw output | Backend | Build | Execution | Correct given execution | Target state |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        lines.append(
            f"| {row['source_outputs']} | {row['backend']} | "
            f"{float(row['build_coverage']):.4f} | "
            f"{float(row['execution_coverage']):.4f} | "
            f"{float(row['execution_conditional_accuracy']):.4f} | "
            f"{float(row['target_state_accuracy']):.4f} |"
        )
    lines.extend(
        [
            "",
            "Anchor checks reproduce the original J/common and S/v2 target, build, and execution metrics exactly.",
        ]
    )
    (output_dir / "backend_swap_summary.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report
