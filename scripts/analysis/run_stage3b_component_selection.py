#!/usr/bin/env python3
"""Audit prompt surfaces and replay four pre-specified Stage-3B candidates.

This is a deterministic CPU-only analysis. It never calls a model and reuses
the single frozen 300-sample MP-FS+ raw-generation set from Stage 3.
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from nldbwrite_v3.evaluator import find_database
from nldbwrite_v3.experiments.run_method import _prompt_for_sample
from nldbwrite_v3.schema import load_profile

from scripts.analysis.run_stage3_causal_replay import (
    DATASET_ARCHIVE_SHA256,
    FROZEN_G2_COMMIT,
    FROZEN_G2_TAG,
    RESULT_ARCHIVE_SHA256,
    VARIANTS,
    canonical,
    git_output,
    jsonl_bytes,
    load_json,
    load_variant_config,
    read_tar_member_by_suffix,
    replay_variant,
    safe_extract_dataset,
    sha256_bytes,
    sha256_file,
    validate_inputs,
    write_csv,
    write_json,
    write_jsonl,
)


CANDIDATES = [
    ("FULL", "configs/stage3b/full.json"),
    ("NO_C", "configs/stage3b/no_c.json"),
    ("D_ONLY", "configs/stage3b/d_only.json"),
    ("D_G1", "configs/stage3b/d_g1.json"),
]
STAGE3_SAMPLE_RELATIVE = "results/causal_replay_sample_level.csv"
STAGE3_METRICS_RELATIVE = "results/variant_metrics.csv"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def prompt_sha256(prompt: str) -> str:
    return sha256_bytes(prompt.encode("utf-8"))


def build_prompt_audit(
    samples: list[dict[str, Any]],
    profiles: Mapping[str, dict[str, Any]],
    variant_configs: Mapping[str, dict[str, Any]],
    candidate_configs: Mapping[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """Build exact production prompts and adjacent-surface comparisons."""
    variant_ids = [item[0] for item in VARIANTS]
    candidate_ids = [item[0] for item in CANDIDATES]
    rows: list[dict[str, Any]] = []
    set_material: dict[str, list[str]] = {
        identifier: [] for identifier in variant_ids + candidate_ids
    }
    for sample in samples:
        sample_id = str(sample["id"])
        db_id = str(sample["db_id"])
        hashes: dict[str, str] = {}
        modes: dict[str, str] = {}
        for identifier in variant_ids:
            prompt, payload = _prompt_for_sample(
                "MP-FS+", sample, profiles[db_id], variant_configs[identifier]
            )
            hashes[identifier] = prompt_sha256(prompt)
            modes[identifier] = str(payload.mode)
            set_material[identifier].append(hashes[identifier])
        for identifier in candidate_ids:
            prompt, payload = _prompt_for_sample(
                "MP-FS+", sample, profiles[db_id], candidate_configs[identifier]
            )
            hashes[identifier] = prompt_sha256(prompt)
            modes[identifier] = str(payload.mode)
            set_material[identifier].append(hashes[identifier])
        row: dict[str, Any] = {
            "sample_id": sample_id,
            "db_id": db_id,
            "input_type": modes["V0"],
        }
        for identifier in variant_ids + candidate_ids:
            row[f"{identifier}_prompt_sha256"] = hashes[identifier]
        for previous, current in zip(variant_ids, variant_ids[1:]):
            row[f"{previous}_eq_{current}"] = int(hashes[previous] == hashes[current])
        row["V0_to_V3_all_equal"] = int(len({hashes[item] for item in variant_ids[:4]}) == 1)
        row["V4_to_V8_all_equal"] = int(len({hashes[item] for item in variant_ids[4:]}) == 1)
        row["all_candidates_equal_V4"] = int(
            all(hashes[item] == hashes["V4"] for item in candidate_ids)
        )
        rows.append(row)

    summary: list[dict[str, Any]] = []
    for previous, current in zip(variant_ids, variant_ids[1:]):
        for input_type in ("ALL", "free_text", "semi_structured"):
            selected = rows if input_type == "ALL" else [
                row for row in rows if row["input_type"] == input_type
            ]
            changed = [
                str(row["sample_id"])
                for row in selected
                if not bool(row[f"{previous}_eq_{current}"])
            ]
            summary.append(
                {
                    "from_variant": previous,
                    "to_variant": current,
                    "input_type": input_type,
                    "samples": len(selected),
                    "same_prompt": len(selected) - len(changed),
                    "changed_prompt": len(changed),
                    "changed_sample_ids": "|".join(changed),
                }
            )
    prompt_set_hashes = {
        identifier: sha256_bytes(("\n".join(values) + "\n").encode("ascii"))
        for identifier, values in set_material.items()
    }
    return rows, summary, prompt_set_hashes


def candidate_tables(
    samples: list[dict[str, Any]],
    candidate_results: Mapping[str, Mapping[str, dict[str, Any]]],
    stage3_rows: Mapping[str, Mapping[str, str]],
) -> dict[str, list[dict[str, Any]]]:
    sample_rows: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    subgroups: list[dict[str, Any]] = []
    false_accept_rows: list[dict[str, Any]] = []
    trace_rows: list[dict[str, Any]] = []

    for sample in samples:
        sample_id = str(sample["id"])
        frozen = stage3_rows[sample_id]
        row: dict[str, Any] = {
            "sample_id": sample_id,
            "db_id": frozen["db_id"],
            "input_type": frozen["input_type"],
            "operation_type": frozen["operation_type"],
            "V0_correct": int(frozen["V0_correct"]),
            "V0_strict_correct": int(frozen["V0_strict_correct"]),
            "V0_first_failure": frozen["V0_first_failure"],
        }
        per_trace: dict[str, Any] = {}
        for candidate, _ in CANDIDATES:
            result = candidate_results[candidate][sample_id]
            evaluation = result["evaluation"]
            accepted = bool(result["preflight"].get("accepted"))
            correct = bool(evaluation.get("target_state_correct"))
            repair_traces = [
                trace
                for component in ("F", "G1", "G2")
                for trace in result["repair_traces"][component]
            ]
            row[f"{candidate}_correct"] = int(correct)
            row[f"{candidate}_strict_correct"] = int(
                bool(evaluation.get("strict_full_state_correct"))
            )
            row[f"{candidate}_first_failure"] = result["first_failure"]
            row[f"{candidate}_accepted"] = int(accepted)
            row[f"{candidate}_execution_success"] = int(
                bool(evaluation.get("execution_success"))
            )
            row[f"{candidate}_rescued_vs_V0"] = int(
                not bool(int(frozen["V0_correct"])) and correct
            )
            row[f"{candidate}_regressed_vs_V0"] = int(
                bool(int(frozen["V0_correct"])) and not correct
            )
            row[f"{candidate}_false_accept"] = int(accepted and not correct)
            row[f"{candidate}_repair_attempted"] = int(
                any(bool(item.get("repair_attempted")) for item in repair_traces)
            )
            row[f"{candidate}_repair_applied"] = int(
                any(bool(item.get("repair_applied")) for item in repair_traces)
            )
            row[f"{candidate}_repair_succeeded"] = int(
                any(bool(item.get("repair_succeeded")) for item in repair_traces)
            )
            per_trace[candidate] = {
                "pipeline_stage": result["pipeline_stage"],
                "first_failure": result["first_failure"],
                "verification_errors": result["verification_errors"],
                "verification_warnings": result["verification_warnings"],
                "repair_traces": result["repair_traces"],
            }
        sample_rows.append(row)
        trace_rows.append({"sample_id": sample_id, "candidates": per_trace})

    for candidate, _ in CANDIDATES:
        results = list(candidate_results[candidate].values())
        accepted = [item for item in results if item["preflight"].get("accepted")]
        correct = [item for item in results if item["evaluation"].get("target_state_correct")]
        accepted_correct = [
            item for item in accepted if item["evaluation"].get("target_state_correct")
        ]
        false_ids = sorted(
            str(item["sample_id"])
            for item in accepted
            if not item["evaluation"].get("target_state_correct")
        )
        metrics.append(
            {
                "candidate": candidate,
                "samples": len(results),
                "target_state_correct": len(correct),
                "target_state_accuracy": len(correct) / len(results),
                "strict_full_state_correct": sum(
                    bool(item["evaluation"].get("strict_full_state_correct"))
                    for item in results
                ),
                "accepted_output": len(accepted),
                "coverage": len(accepted) / len(results),
                "accepted_output_accuracy": len(accepted_correct) / len(accepted),
                "false_accept": len(false_ids),
                "execution_success": sum(
                    bool(item["evaluation"].get("execution_success")) for item in results
                ),
                "off_target_state_change": sum(
                    bool(item["evaluation"].get("any_off_target_change")) for item in results
                ),
            }
        )
        rescued = [row["sample_id"] for row in sample_rows if row[f"{candidate}_rescued_vs_V0"]]
        regressed = [row["sample_id"] for row in sample_rows if row[f"{candidate}_regressed_vs_V0"]]
        comparisons.append(
            {
                "candidate": candidate,
                "baseline": "V0",
                "rescued": len(rescued),
                "regressed": len(regressed),
                "net_gain": len(rescued) - len(regressed),
                "unchanged_correct": sum(
                    bool(row["V0_correct"]) and bool(row[f"{candidate}_correct"])
                    for row in sample_rows
                ),
                "unchanged_incorrect": sum(
                    not bool(row["V0_correct"]) and not bool(row[f"{candidate}_correct"])
                    for row in sample_rows
                ),
                "rescued_ids": "|".join(rescued),
                "regressed_ids": "|".join(regressed),
            }
        )
        for sample_id in false_ids:
            false_accept_rows.append(
                {
                    "candidate": candidate,
                    "sample_id": sample_id,
                    "input_type": stage3_rows[sample_id]["input_type"],
                    "operation_type": stage3_rows[sample_id]["operation_type"],
                    "db_id": stage3_rows[sample_id]["db_id"],
                    "first_failure": candidate_results[candidate][sample_id]["first_failure"],
                }
            )
        for dimension, field in (
            ("input_type", "input_type"),
            ("operation_type", "operation_type"),
            ("database", "db_id"),
        ):
            values = sorted({str(row[field]) for row in sample_rows})
            for value in values:
                selected = [row for row in sample_rows if str(row[field]) == value]
                accepted_count = sum(bool(row[f"{candidate}_accepted"]) for row in selected)
                correct_count = sum(bool(row[f"{candidate}_correct"]) for row in selected)
                accepted_correct_count = sum(
                    bool(row[f"{candidate}_accepted"]) and bool(row[f"{candidate}_correct"])
                    for row in selected
                )
                subgroups.append(
                    {
                        "candidate": candidate,
                        "dimension": dimension,
                        "value": value,
                        "samples": len(selected),
                        "target_state_correct": correct_count,
                        "target_state_accuracy": correct_count / len(selected),
                        "accepted_output": accepted_count,
                        "coverage": accepted_count / len(selected),
                        "accepted_output_accuracy": (
                            accepted_correct_count / accepted_count if accepted_count else ""
                        ),
                        "false_accept": accepted_count - accepted_correct_count,
                        "rescued_vs_V0": sum(
                            bool(row[f"{candidate}_rescued_vs_V0"]) for row in selected
                        ),
                        "regressed_vs_V0": sum(
                            bool(row[f"{candidate}_regressed_vs_V0"]) for row in selected
                        ),
                    }
                )
    return {
        "sample": sample_rows,
        "metrics": metrics,
        "comparisons": comparisons,
        "subgroups": subgroups,
        "false_accepts": false_accept_rows,
        "traces": trace_rows,
    }


def validate_stage3b(
    prompt_rows: list[dict[str, Any]],
    tables: Mapping[str, list[dict[str, Any]]],
    stage3_rows: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    if len(prompt_rows) != 300 or len(tables["sample"]) != 300:
        violations.append({"rule": "sample_count", "prompt": len(prompt_rows), "candidate": len(tables["sample"])})
    if any(not bool(row["V0_to_V3_all_equal"]) for row in prompt_rows):
        violations.append({"rule": "V0_V3_prompt_equivalence"})
    if any(not bool(row["V4_to_V8_all_equal"]) for row in prompt_rows):
        violations.append({"rule": "V4_V8_prompt_equivalence"})
    if any(not bool(row["all_candidates_equal_V4"]) for row in prompt_rows):
        violations.append({"rule": "candidate_prompt_equivalence_to_V4"})
    for row in tables["sample"]:
        sample_id = str(row["sample_id"])
        frozen = stage3_rows[sample_id]
        for candidate_field, frozen_field in (
            ("FULL_correct", "V8_correct"),
            ("FULL_strict_correct", "V8_strict_correct"),
            ("FULL_first_failure", "V8_first_failure"),
        ):
            if str(row[candidate_field]) != str(frozen[frozen_field]):
                violations.append({"rule": "FULL_equals_V8", "sample_id": sample_id, "field": candidate_field})
    report = {
        "status": "PASS" if not violations else "FAIL",
        "samples": len(prompt_rows),
        "candidates": len(CANDIDATES),
        "candidate_evaluations": len(tables["sample"]) * len(CANDIDATES),
        "prompt_builds": len(prompt_rows) * (len(VARIANTS) + len(CANDIDATES)),
        "full_v8_equivalence_mismatches": sum(
            1 for item in violations if item.get("rule") == "FULL_equals_V8"
        ),
        "violations": violations,
    }
    if violations:
        raise ValueError(f"Stage 3B invariants failed: {violations[:5]}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-archive", required=True)
    parser.add_argument("--result-archive", required=True)
    parser.add_argument("--stage3-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--project-root")
    parser.add_argument("--expected-dataset-sha256", default=DATASET_ARCHIVE_SHA256)
    parser.add_argument("--expected-result-sha256", default=RESULT_ARCHIVE_SHA256)
    args = parser.parse_args()

    started = time.time()
    repo_root = Path(args.project_root).resolve() if args.project_root else PROJECT_ROOT
    dataset_archive = Path(args.dataset_archive).resolve()
    result_archive = Path(args.result_archive).resolve()
    stage3_root = Path(args.stage3_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"Output directory must be absent or empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    input_hashes = validate_inputs(
        dataset_archive, result_archive, args.expected_dataset_sha256, args.expected_result_sha256
    )
    raw_bytes = read_tar_member_by_suffix(result_archive, "/mp_fs_plus/raw_generations.jsonl")
    raw_rows = jsonl_bytes(raw_bytes)
    raw_by_id = {str(row["sample_id"]): row for row in raw_rows}
    if len(raw_by_id) != len(raw_rows):
        raise ValueError("Frozen raw generations contain duplicate sample IDs")
    frozen_rows_list = read_csv(stage3_root / STAGE3_SAMPLE_RELATIVE)
    stage3_rows = {row["sample_id"]: row for row in frozen_rows_list}
    if len(stage3_rows) != len(frozen_rows_list):
        raise ValueError("Stage 3 sample-level artifact contains duplicate sample IDs")

    variant_configs = {
        variant: load_variant_config(repo_root, relative)
        for variant, _, relative in VARIANTS
    }
    candidate_configs = {
        candidate: load_variant_config(repo_root, relative)
        for candidate, relative in CANDIDATES
    }
    candidate_results: dict[str, dict[str, dict[str, Any]]] = {
        candidate: {} for candidate, _ in CANDIDATES
    }
    with tempfile.TemporaryDirectory(prefix="stage3b_", dir=output_dir.parent) as temporary:
        dataset_root = safe_extract_dataset(dataset_archive, Path(temporary) / "dataset")
        samples = load_json(dataset_root / "dataset.final.json")
        profiles = {
            path.stem: load_profile(path)
            for path in (dataset_root / "profiles").glob("*.json")
        }
        expected_ids = [str(sample["id"]) for sample in samples]
        if len(samples) != 300 or len(set(expected_ids)) != 300:
            raise ValueError(f"Expected 300 unique frozen samples, got {len(samples)}")
        if set(expected_ids) != set(raw_by_id) or set(expected_ids) != set(stage3_rows):
            raise ValueError("Dataset, raw generations, and Stage 3 sample IDs differ")
        prompt_rows, prompt_summary, prompt_set_hashes = build_prompt_audit(
            samples, profiles, variant_configs, candidate_configs
        )
        for candidate, _ in CANDIDATES:
            print(f"REPLAY {candidate}: 0/300", flush=True)
            for index, sample in enumerate(samples, start=1):
                sample_id = str(sample["id"])
                candidate_results[candidate][sample_id] = replay_variant(
                    sample,
                    raw_by_id[sample_id],
                    profiles[str(sample["db_id"])],
                    find_database(dataset_root / "databases", str(sample["db_id"])),
                    candidate_configs[candidate],
                )
                if index % 50 == 0:
                    print(f"REPLAY {candidate}: {index}/300", flush=True)

    tables = candidate_tables(samples, candidate_results, stage3_rows)
    invariant_report = validate_stage3b(prompt_rows, tables, stage3_rows)
    prompt_fields = ["sample_id", "db_id", "input_type"]
    prompt_fields += [f"{item[0]}_prompt_sha256" for item in VARIANTS]
    prompt_fields += [f"{item[0]}_prompt_sha256" for item in CANDIDATES]
    prompt_fields += [
        f"{previous[0]}_eq_{current[0]}"
        for previous, current in zip(VARIANTS, VARIANTS[1:])
    ]
    prompt_fields += ["V0_to_V3_all_equal", "V4_to_V8_all_equal", "all_candidates_equal_V4"]
    write_csv(output_dir / "results" / "prompt_equivalence_matrix.csv", prompt_rows, prompt_fields)
    write_csv(output_dir / "results" / "prompt_surface_summary.csv", prompt_summary, list(prompt_summary[0]))
    write_csv(output_dir / "results" / "candidate_sample_level.csv", tables["sample"], list(tables["sample"][0]))
    write_csv(output_dir / "results" / "candidate_metrics.csv", tables["metrics"], list(tables["metrics"][0]))
    write_csv(output_dir / "results" / "candidate_rescue_regression.csv", tables["comparisons"], list(tables["comparisons"][0]))
    write_csv(output_dir / "results" / "candidate_subgroup_metrics.csv", tables["subgroups"], list(tables["subgroups"][0]))
    write_csv(output_dir / "results" / "candidate_false_accept_ids.csv", tables["false_accepts"], list(tables["false_accepts"][0]))
    write_jsonl(output_dir / "traces" / "candidate_intervention_traces.jsonl", tables["traces"])
    write_json(output_dir / "validation" / "stage3b_invariants.json", invariant_report)

    config_hashes: dict[str, Any] = {}
    for candidate, relative in CANDIDATES:
        source = repo_root / relative
        destination = output_dir / "configs" / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        config_hashes[candidate] = {"path": relative, "sha256": sha256_file(source)}
    frozen_tag_commit = git_output(repo_root, "rev-list", "-n", "1", FROZEN_G2_TAG)
    if frozen_tag_commit != FROZEN_G2_COMMIT:
        raise ValueError(f"{FROZEN_G2_TAG} resolves to {frozen_tag_commit}, expected {FROZEN_G2_COMMIT}")
    run_lock = {
        "stage": "Stage3B_FINAL_COMPONENT_SELECTION",
        "interpretation": "fixed-generation conditional replay and prompt-surface audit",
        "model_called": False,
        "gpu_required": False,
        "sample_count": 300,
        "candidate_order": [item[0] for item in CANDIDATES],
        "repository_head": git_output(repo_root, "rev-parse", "HEAD"),
        "frozen_g2_tag": FROZEN_G2_TAG,
        "frozen_g2_commit": FROZEN_G2_COMMIT,
        "frozen_g2_tag_commit_verified": frozen_tag_commit,
        **input_hashes,
        "raw_generations_sha256": sha256_bytes(raw_bytes),
        "stage3_sample_level_sha256": sha256_file(stage3_root / STAGE3_SAMPLE_RELATIVE),
        "stage3_variant_metrics_sha256": sha256_file(stage3_root / STAGE3_METRICS_RELATIVE),
        "sample_ids_sha256": sha256_bytes(("\n".join(str(item["id"]) for item in samples) + "\n").encode("utf-8")),
        "config_hashes": config_hashes,
        "prompt_set_hashes": prompt_set_hashes,
        "full_v8_equivalence_mismatches": invariant_report["full_v8_equivalence_mismatches"],
        "selection_candidates_pre_specified": True,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "sqlite": sqlite3.sqlite_version,
        },
        "elapsed_seconds": time.time() - started,
    }
    write_json(output_dir / "provenance" / "run_lock.json", run_lock)
    manifest_files = {}
    for path in sorted(item for item in output_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(output_dir).as_posix()
        manifest_files[relative] = {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
    write_json(output_dir / "provenance" / "run_manifest.json", {"stage": run_lock["stage"], "files": manifest_files})
    print(json.dumps({"status": "PASS", "metrics": tables["metrics"], "prompt_summary": prompt_summary}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
