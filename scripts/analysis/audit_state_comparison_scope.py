#!/usr/bin/env python3
"""Audit frozen primary evaluations against all-user-table comparison.

The script never calls a model. It replays only evaluation-successful programs
from locked raw/compiled artifacts and compares the resulting all-table state
with the frozen affected-table result recorded for the same sample and method.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import zipfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from nldbwrite_v3.analysis.common_safety_replay import _program_from_artifact
from nldbwrite_v3.baselines import build_sql_with_v2
from nldbwrite_v3.evaluator import evaluate_candidate_sample, find_database
from nldbwrite_v3.inference.parse_output import (
    extract_json_object,
    extract_sql_statements,
)
from nldbwrite_v3.schema import load_profile


METHODS = (
    ("d_fs_m", "D-FS-M", "direct"),
    ("j_fs_m", "J-FS-M", "compiled"),
    ("s_fs_v2_m", "S-FS-v2-M", "v2"),
    ("mp_fs_m", "MP-FS-M", "compiled"),
    ("mp_fs_plus", "MP-FS+", "compiled"),
    ("gold_mp", "Gold-MP", "compiled"),
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _safe_extract(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(archive) as handle:
        for member in handle.infolist():
            target = (root / member.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"Unsafe ZIP member: {member.filename}")
        handle.extractall(root)


@contextmanager
def _work_directory(parent: Path):
    """Create a normal directory without Python 3.14 TemporaryDirectory ACLs."""
    path = parent / "_state_scope_work"
    if path.exists():
        raise ValueError(f"Audit work directory already exists: {path}")
    path.mkdir(parents=True)
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _discover_run_root(workspace: Path) -> Path:
    candidates = sorted(
        (workspace / "04_results" / "01_extracted_archive").glob(
            "*/experiments/external_holdout/"
            "final300_qwen25_7b_protocol_v2_out8192_20260731"
        )
    )
    if len(candidates) != 1:
        raise ValueError(f"Expected one frozen primary run root, found {candidates}")
    return candidates[0]


def _current_off_target(row: dict[str, Any], targets: set[str]) -> bool:
    return any(
        str(table) not in targets
        for table in row.get("strict_mismatched_tables") or []
    )


def run_audit(workspace: Path, output_dir: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_root = _discover_run_root(workspace)
    holdout_zip = (
        workspace
        / "03_protocol_and_data"
        / "final_holdout_release"
        / "mp_fs_plus_external_holdout_300_20260731.zip"
    )
    v2_source = (
        workspace
        / "07_reproducibility"
        / "exact_v2_source_20260714_rev1"
    )
    rows: list[dict[str, Any]] = []
    anchors: dict[str, int] = {}
    mismatch_counts: Counter[str] = Counter()
    with _work_directory(output_dir) as temp_name:
        extracted = Path(temp_name) / "holdout"
        _safe_extract(holdout_zip, extracted)
        roots = [path.parent for path in extracted.rglob("dataset.final.json")]
        if len(roots) != 1:
            raise ValueError(f"Expected one extracted holdout root, found {roots}")
        holdout = roots[0]
        samples = {
            str(row["id"]): row
            for row in json.loads((holdout / "dataset.final.json").read_text(encoding="utf-8"))
        }
        profiles = {
            path.stem: load_profile(path)
            for path in (holdout / "profiles").glob("*.json")
        }
        db_root = holdout / "databases"
        for slug, method, artifact_kind in METHODS:
            method_root = run_root / slug
            frozen = {
                str(row["sample_id"]): row
                for row in _load_jsonl(method_root / "evaluation.jsonl")
            }
            raw = {
                str(row["sample_id"]): row
                for row in _load_jsonl(method_root / "raw_generations.jsonl")
            }
            compiled = {
                str(row["sample_id"]): row
                for row in _load_jsonl(method_root / "compiled_programs.jsonl")
            }
            all_target_correct = 0
            for sample_id, sample in samples.items():
                old = frozen[sample_id]
                targets = {str(table) for table in sample.get("gold_tables") or []}
                program = None
                direct_sql: list[str] | None = None
                if bool(old.get("execution_success")):
                    if artifact_kind == "direct":
                        direct_sql, error = extract_sql_statements(
                            str(raw[sample_id].get("raw_output") or "")
                        )
                        if error:
                            raise ValueError(f"{method}/{sample_id}: {error}")
                    elif artifact_kind == "v2":
                        payload, error = extract_json_object(
                            str(raw[sample_id].get("raw_output") or "")
                        )
                        if payload is None:
                            raise ValueError(f"{method}/{sample_id}: {error}")
                        status, direct_sql, errors, _ = build_sql_with_v2(
                            payload,
                            profiles[str(sample["db_id"])],
                            v2_source_path=v2_source,
                        )
                        if status != "success":
                            raise ValueError(
                                f"{method}/{sample_id}: v2 replay failed: {errors}"
                            )
                    else:
                        program = _program_from_artifact(compiled[sample_id])
                        if program is None:
                            raise ValueError(
                                f"{method}/{sample_id}: missing compiled program"
                            )
                    all_result = evaluate_candidate_sample(
                        sample,
                        find_database(db_root, str(sample["db_id"])),
                        program=program,
                        direct_sql=direct_sql,
                        state_scope="all_user_tables",
                    )
                else:
                    all_result = {
                        "target_state_correct": False,
                        "strict_full_state_correct": False,
                        "any_off_target_change": False,
                        "strict_mismatched_tables": [],
                        "off_target_mismatched_tables": [],
                    }
                current_target = bool(old.get("target_state_correct"))
                current_strict = bool(old.get("strict_full_state_correct"))
                current_off_target = _current_off_target(old, targets)
                all_target = bool(all_result["target_state_correct"])
                all_strict = bool(all_result["strict_full_state_correct"])
                all_off_target = bool(all_result["any_off_target_change"])
                all_target_correct += int(all_target)
                flags = {
                    "target": current_target != all_target,
                    "strict": current_strict != all_strict,
                    "off_target": current_off_target != all_off_target,
                }
                for key, changed in flags.items():
                    mismatch_counts[key] += int(changed)
                rows.append(
                    {
                        "sample_id": sample_id,
                        "method": method,
                        "db_id": sample.get("db_id"),
                        "execution_success": bool(old.get("execution_success")),
                        "current_target_correct": current_target,
                        "all_table_target_correct": all_target,
                        "current_strict_correct": current_strict,
                        "all_table_strict_correct": all_strict,
                        "current_off_target": current_off_target,
                        "all_table_off_target": all_off_target,
                        "target_mismatch": flags["target"],
                        "strict_mismatch": flags["strict"],
                        "off_target_mismatch": flags["off_target"],
                        "current_mismatch_tables": "|".join(
                            map(str, old.get("strict_mismatched_tables") or [])
                        ),
                        "all_table_mismatch_tables": "|".join(
                            map(str, all_result.get("strict_mismatched_tables") or [])
                        ),
                        "all_table_off_target_tables": "|".join(
                            map(str, all_result.get("off_target_mismatched_tables") or [])
                        ),
                    }
                )
            anchors[method] = all_target_correct
            print(f"[state-scope] {method}: {all_target_correct}/300 target correct", flush=True)
    csv_path = output_dir / "state_scope_audit.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    expected = {
        "D-FS-M": 258,
        "J-FS-M": 258,
        "S-FS-v2-M": 78,
        "MP-FS-M": 34,
        "MP-FS+": 148,
        "Gold-MP": 300,
    }
    report = {
        "status": "pass" if anchors == expected else "fail",
        "pairs": len(rows),
        "all_table_target_correct": anchors,
        "expected_primary_anchors": expected,
        "primary_anchors_unchanged": anchors == expected,
        "mismatch_counts": dict(sorted(mismatch_counts.items())),
        "decision": "use_all_user_tables",
        "model_inference_rerun": False,
        "gpu_required": False,
        "csv": str(csv_path),
    }
    (output_dir / "state_scope_audit_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run_audit(args.workspace_root, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
