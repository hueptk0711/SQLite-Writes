#!/usr/bin/env python3
"""Replay one frozen MP-FS+ generation set through cumulative Stage-2 variants.

This runner is deterministic and CPU-only.  It reads the frozen 300-sample
dataset and original MP-FS+ raw generations, calls no model, and evaluates V0
through V8 against isolated in-memory copies of the frozen SQLite databases.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from nldbwrite_v3.compiler import check_semantic_risk_gate, preflight_program
from nldbwrite_v3.evaluator import evaluate_candidate_sample, find_database
from nldbwrite_v3.pipeline import MappingFirstPipeline
from nldbwrite_v3.planner import parse_llm_plan
from nldbwrite_v3.schema import load_profile
from nldbwrite_v3.source_parser import parse_source_payload


FROZEN_G2_COMMIT = "b752867312727e9932dcf48af99c02b4b2af36cf"
FROZEN_G2_TAG = "Stage2-G2-FINAL"
DATASET_ARCHIVE_SHA256 = (
    "525cdd7006ea32a8ab8d81f842332ac9b403dce2472cde608efb4e6962d456df"
)
RESULT_ARCHIVE_SHA256 = (
    "e456037422281d56e03dd7766baf1cc9efa78a95061234444c452f3c04810911"
)
VARIANTS = [
    ("V0", "Original", "configs/stage2/original.json"),
    ("V1", "A", "configs/stage2/v1_control.json"),
    ("V2", "B", "configs/stage2/v2_conflict.json"),
    ("V3", "C", "configs/stage2/v3_update.json"),
    ("V4", "D", "configs/stage2/v4_structured_parser.json"),
    ("V5", "E", "configs/stage2/v5_free_text_typed_normalization.json"),
    ("V6", "F", "configs/stage2/v6_constrained_reference_repair.json"),
    ("V7", "G1", "configs/stage2/v7_diagnostic_targeted_repair_g1.json"),
    ("V8", "G2", "configs/stage2/v8_diagnostic_targeted_repair_g2.json"),
]
COMPONENTS = ["A", "B", "C", "D", "E", "F", "G1", "G2"]
REPAIR_COMPONENTS = {"F", "G1", "G2"}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def jsonl_bytes(value: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in value.decode("utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def git_output(repo_root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=repo_root, text=True, encoding="utf-8"
    ).strip()


def safe_extract_dataset(archive: Path, destination: Path) -> Path:
    """Extract the frozen dataset without accepting unsafe archive paths."""
    with zipfile.ZipFile(archive) as handle:
        files = [item for item in handle.infolist() if not item.is_dir()]
        roots = {PurePosixPath(item.filename).parts[0] for item in files}
        if len(roots) != 1:
            raise ValueError(f"Dataset archive must have one root, got {sorted(roots)}")
        archive_root = next(iter(roots))
        for item in files:
            parts = PurePosixPath(item.filename).parts
            if not parts or parts[0] != archive_root or any(part in {"", ".", ".."} for part in parts):
                raise ValueError(f"Unsafe dataset archive member: {item.filename}")
            relative = Path(*parts[1:])
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(handle.read(item))
    required = [destination / "dataset.final.json", destination / "profiles", destination / "databases"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ValueError(f"Dataset archive is incomplete: {missing}")
    return destination


def read_tar_member_by_suffix(archive: Path, suffix: str) -> bytes:
    with tarfile.open(archive, "r:gz") as handle:
        matches = [member for member in handle.getmembers() if member.isfile() and member.name.endswith(suffix)]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one tar member ending {suffix!r}, got {len(matches)}")
        stream = handle.extractfile(matches[0])
        if stream is None:
            raise ValueError(f"Cannot read tar member: {matches[0].name}")
        return stream.read()


def load_variant_config(repo_root: Path, relative: str) -> dict[str, Any]:
    path = repo_root / relative
    config = load_json(path)
    base_reference = config.get("base_config")
    if base_reference:
        base = load_json(repo_root / str(base_reference))
        config = {**base, **config}
    return config


def upstream_preflight() -> dict[str, Any]:
    return {
        "status": "abstained",
        "accepted": False,
        "action": "abstain",
        "deterministic_repair_applied": False,
        "error_class": "upstream_rejection",
        "error": "Plan did not reach successful compilation.",
        "executed_statements": 0,
        "latency_sec": 0.0,
    }


def blocked_preflight() -> dict[str, Any]:
    return {
        "status": "not_run",
        "accepted": False,
        "action": "abstain",
        "deterministic_repair_applied": False,
        "error_class": "blocked_by_semantic_risk_gate",
        "error": "Candidate was rejected by the semantic-risk gate before transactional preflight.",
        "executed_statements": 0,
        "latency_sec": 0.0,
    }


def first_failure(
    *,
    parse_success: bool,
    pipeline_stage: str,
    build_success: bool,
    semantic_gate: Mapping[str, Any] | None,
    preflight: Mapping[str, Any],
    evaluation: Mapping[str, Any],
) -> str:
    if not parse_success:
        return "parse"
    if not build_success:
        return pipeline_stage or "build"
    if semantic_gate and not semantic_gate.get("accepted"):
        return "semantic_gate"
    if not preflight.get("accepted"):
        return "preflight"
    if not evaluation.get("execution_success"):
        return "execution"
    if not evaluation.get("target_state_correct"):
        return "state_mismatch"
    if not evaluation.get("strict_full_state_correct"):
        return "off_target_state_change"
    return "none"


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from walk_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_dicts(nested)


def unique_dicts(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        key = canonical(row)
        if key not in seen:
            output.append(deepcopy(row))
            seen.add(key)
    return output


def collect_repair_traces(result: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {"F": [], "G1": [], "G2": []}
    candidates = [
        item
        for item in walk_dicts(result)
        if "repair_rule" in item
        and "repair_attempted" in item
        and "repair_applied" in item
        and "repair_succeeded" in item
    ]
    for trace in unique_dicts(candidates):
        intervention = str(trace.get("stage2_intervention") or "")
        if intervention == "G1_evidence_span_boundary_repair":
            output["G1"].append(trace)
        elif intervention == "G2_temporal_evidence_selection_repair":
            output["G2"].append(trace)
        elif "slot_path" in trace and "reference_kind" in trace:
            output["F"].append(trace)
    return output


def source_payload_core(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "mode": payload.get("mode"),
        "instruction_text": payload.get("instruction_text"),
        "collections": [
            {
                "collection_id": item.get("collection_id"),
                "source_path": item.get("source_path"),
                "fields": item.get("fields"),
                "rows": item.get("rows"),
            }
            for item in payload.get("collections") or []
            if isinstance(item, dict)
        ],
    }


def strip_provenance(value: Any) -> Any:
    ignored = {
        "stage2_intervention_trace",
        "normalization_audit",
        "reference_trace",
        "consumed_control_refs",
    }
    if isinstance(value, dict):
        return {key: strip_provenance(item) for key, item in value.items() if key not in ignored}
    if isinstance(value, list):
        return [strip_provenance(item) for item in value]
    return value


def semantic_fingerprint(result: Mapping[str, Any]) -> dict[str, Any]:
    evaluation = result["evaluation"]
    return {
        "source_payload": source_payload_core(result["source_payload"]),
        "parse_success": result["parse_success"],
        "pipeline_stage": result["pipeline_stage"],
        "write_plan": strip_provenance(result.get("write_plan")),
        "verification_errors": [
            (item.get("error_code"), item.get("path"), item.get("group_id"))
            for item in result.get("verification_errors") or []
        ],
        "program": strip_provenance(result.get("compiled_program")),
        "semantic_gate": strip_provenance(result.get("semantic_risk_gate")),
        "preflight": {
            "accepted": result["preflight"].get("accepted"),
            "error_class": result["preflight"].get("error_class"),
        },
        "evaluation": {
            key: evaluation.get(key)
            for key in (
                "execution_success",
                "target_state_correct",
                "strict_full_state_correct",
                "any_off_target_change",
                "error_type",
            )
        },
    }


def component_trace_signals(result: Mapping[str, Any]) -> dict[str, bool]:
    dictionaries = list(walk_dicts(result))
    consumed = [
        item
        for item in dictionaries
        if "consumed_by" in item and "source_field" in item
    ]
    group_traces = [item for item in dictionaries if "request_scope" in item or "explicit_operation" in item]
    audits = [item for item in dictionaries if item.get("stage2_intervention") == "E_free_text_typed_normalization"]
    warning_codes = {
        str(item.get("error_code") or "")
        for item in result.get("verification_warnings") or []
    }
    repairs = result["repair_traces"]
    return {
        "A": any(item.get("consumed_by") == "instruction_semantics.operation" for item in consumed),
        "B": bool(group_traces)
        or bool(warning_codes & {"EXPLICIT_CONFLICT_SEMANTICS_DROPPED"}),
        "C": any(
            item.get("requested_update_column_ids")
            or item.get("excluded_update_column_ids")
            or item.get("planned_update_column_ids_before") != item.get("materialized_update_column_ids")
            for item in group_traces
        )
        or bool(
            warning_codes
            & {"REQUIRED_UPDATE_COLUMNS_DROPPED", "EXCLUDED_UPDATE_COLUMNS_REMOVED"}
        ),
        "D": False,
        "E": any(bool(item.get("intervention_applied")) for item in audits),
        "F": any(bool(item.get("repair_attempted")) for item in repairs["F"]),
        "G1": bool(repairs["G1"]),
        "G2": bool(repairs["G2"]),
    }


def replay_variant(
    sample: dict[str, Any],
    raw: dict[str, Any],
    profile: dict[str, Any],
    db_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    request = str(sample.get("input_text") or "")
    payload = parse_source_payload(request, structured_parser=config.get("structured_source_parser"))
    plan_kind = "mapping" if payload.mode == "semi_structured" else "free_text"
    parsed = parse_llm_plan(
        str(raw.get("raw_output") or ""),
        plan_kind=plan_kind,
        reference_mode=bool(config.get("reference_planning")),
    )
    pipeline_result = None
    program = None
    if parsed.success:
        pipeline_result = MappingFirstPipeline(
            profile,
            normalize_values=bool(config.get("normalize_values")),
            normalization_mode=str(config.get("normalization_mode") or "legacy"),
            reference_planning=bool(config.get("reference_planning")),
            stage2_interventions=config.get("stage2_interventions"),
            structured_source_parser=config.get("structured_source_parser"),
            free_text_typed_normalization=config.get("free_text_typed_normalization"),
            constrained_reference_repair=config.get("constrained_reference_repair"),
            diagnostic_targeted_repair=config.get("diagnostic_targeted_repair"),
        ).run(request, parsed.plan)
        program = pipeline_result.program
    build_success = bool(program is not None and program.status == "success")
    semantic_gate = None
    if build_success and program is not None:
        semantic_gate = check_semantic_risk_gate(program)
        preflight = preflight_program(db_path, program) if semantic_gate["accepted"] else blocked_preflight()
    else:
        preflight = upstream_preflight()
    evaluation = evaluate_candidate_sample(
        sample,
        db_path,
        program=program,
        parse_status=parsed.parse_status,
        build_status="success" if build_success else "error",
        preflight=preflight,
    )
    verification = pipeline_result.verification.to_dict() if pipeline_result and pipeline_result.verification else None
    result = {
        "sample_id": str(sample["id"]),
        "source_payload": payload.to_dict(),
        "parse_success": parsed.success,
        "parse_status": parsed.parse_status,
        "pipeline_stage": pipeline_result.stage if pipeline_result else "parse",
        "build_success": build_success,
        "write_plan": pipeline_result.write_plan if pipeline_result else None,
        "verification_errors": (verification or {}).get("errors") or [],
        "verification_warnings": (verification or {}).get("warnings") or [],
        "compiled_program": program.to_dict() if program else None,
        "semantic_risk_gate": semantic_gate,
        "preflight": preflight,
        "evaluation": evaluation,
    }
    result["repair_traces"] = collect_repair_traces(result)
    result["trace_signals"] = component_trace_signals(result)
    result["first_failure"] = first_failure(
        parse_success=parsed.success,
        pipeline_stage=result["pipeline_stage"],
        build_success=build_success,
        semantic_gate=semantic_gate,
        preflight=preflight,
        evaluation=evaluation,
    )
    return result


def repair_flags(traces: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "attempted": any(bool(item.get("repair_attempted")) for item in traces),
        "applied": any(bool(item.get("repair_applied")) for item in traces),
        "succeeded": any(bool(item.get("repair_succeeded")) for item in traces),
        "rules": sorted({str(item.get("repair_rule") or "") for item in traces}),
    }


def build_tables(
    samples: list[dict[str, Any]],
    all_results: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    sample_rows: list[dict[str, Any]] = []
    trace_rows: list[dict[str, Any]] = []
    activations: dict[str, dict[str, bool]] = {}
    variant_ids = [item[0] for item in VARIANTS]
    for sample in samples:
        sample_id = str(sample["id"])
        per_variant = {variant: all_results[variant][sample_id] for variant in variant_ids}
        sample_activation: dict[str, bool] = {}
        activation_evidence: dict[str, Any] = {}
        for index, component in enumerate(COMPONENTS, start=1):
            previous = per_variant[variant_ids[index - 1]]
            current = per_variant[variant_ids[index]]
            trace_signal = bool(current["trace_signals"].get(component))
            if component == "D":
                trace_signal = canonical(source_payload_core(previous["source_payload"])) != canonical(
                    source_payload_core(current["source_payload"])
                )
            effect_changed = canonical(semantic_fingerprint(previous)) != canonical(semantic_fingerprint(current))
            sample_activation[component] = trace_signal or effect_changed
            activation_evidence[component] = {
                "trace_signal": trace_signal,
                "semantic_effect_changed": effect_changed,
            }
        activations[sample_id] = sample_activation
        final_repairs = {
            component: repair_flags(per_variant[{"F": "V6", "G1": "V7", "G2": "V8"}[component]]["repair_traces"][component])
            for component in REPAIR_COMPONENTS
        }
        row: dict[str, Any] = {
            "sample_id": sample_id,
            "db_id": sample.get("db_id"),
            "input_type": sample.get("input_type") or sample.get("format") or per_variant["V0"]["source_payload"].get("mode"),
            "operation_type": sample.get("operation_type") or sample.get("operation_semantics"),
        }
        for variant in variant_ids:
            result = per_variant[variant]
            row[f"{variant}_correct"] = int(bool(result["evaluation"]["target_state_correct"]))
            row[f"{variant}_strict_correct"] = int(bool(result["evaluation"]["strict_full_state_correct"]))
            row[f"{variant}_first_failure"] = result["first_failure"]
        for component in COMPONENTS:
            row[f"{component}_activated"] = int(sample_activation[component])
        for component in ("F", "G1", "G2"):
            flags = final_repairs[component]
            row[f"{component}_repair_attempted"] = int(flags["attempted"])
            row[f"{component}_repair_applied"] = int(flags["applied"])
            row[f"{component}_repair_succeeded"] = int(flags["succeeded"])
            row[f"{component}_repair_rules"] = "|".join(flags["rules"])
        row["repair_attempted"] = int(any(final_repairs[item]["attempted"] for item in REPAIR_COMPONENTS))
        row["repair_applied"] = int(any(final_repairs[item]["applied"] for item in REPAIR_COMPONENTS))
        row["repair_succeeded"] = int(any(final_repairs[item]["succeeded"] for item in REPAIR_COMPONENTS))
        sample_rows.append(row)
        trace_rows.append(
            {
                "sample_id": sample_id,
                "activation": sample_activation,
                "activation_evidence": activation_evidence,
                "variants": {
                    variant: {
                        "pipeline_stage": per_variant[variant]["pipeline_stage"],
                        "first_failure": per_variant[variant]["first_failure"],
                        "verification_errors": per_variant[variant]["verification_errors"],
                        "verification_warnings": per_variant[variant]["verification_warnings"],
                        "repair_traces": per_variant[variant]["repair_traces"],
                    }
                    for variant in variant_ids
                },
            }
        )

    metrics: list[dict[str, Any]] = []
    for variant, component, _ in VARIANTS:
        results = list(all_results[variant].values())
        accepted = [item for item in results if item["preflight"].get("accepted")]
        accepted_correct = [item for item in accepted if item["evaluation"]["target_state_correct"]]
        metrics.append(
            {
                "variant": variant,
                "cumulative_through": component,
                "samples": len(results),
                "target_state_correct": sum(bool(item["evaluation"]["target_state_correct"]) for item in results),
                "target_state_accuracy": sum(bool(item["evaluation"]["target_state_correct"]) for item in results) / len(results),
                "strict_full_state_correct": sum(bool(item["evaluation"]["strict_full_state_correct"]) for item in results),
                "accepted_output": len(accepted),
                "coverage": len(accepted) / len(results),
                "accepted_output_accuracy": len(accepted_correct) / len(accepted) if accepted else "",
                "false_accept": len(accepted) - len(accepted_correct),
                "execution_success": sum(bool(item["evaluation"]["execution_success"]) for item in results),
                "off_target_state_change": sum(bool(item["evaluation"].get("any_off_target_change")) for item in results),
                "parse_success": sum(bool(item["parse_success"]) for item in results),
                "build_success": sum(bool(item["build_success"]) for item in results),
            }
        )

    rescue: list[dict[str, Any]] = []
    transitions: Counter[tuple[str, str, str, str]] = Counter()
    activation_summary: list[dict[str, Any]] = []
    for index, component in enumerate(COMPONENTS, start=1):
        previous_variant = variant_ids[index - 1]
        current_variant = variant_ids[index]
        previous_correct = {sid: bool(value["evaluation"]["target_state_correct"]) for sid, value in all_results[previous_variant].items()}
        current_correct = {sid: bool(value["evaluation"]["target_state_correct"]) for sid, value in all_results[current_variant].items()}
        rescued = [sid for sid in previous_correct if not previous_correct[sid] and current_correct[sid]]
        regressed = [sid for sid in previous_correct if previous_correct[sid] and not current_correct[sid]]
        rescue.append(
            {
                "intervention": component,
                "from_variant": previous_variant,
                "to_variant": current_variant,
                "rescued": len(rescued),
                "regressed": len(regressed),
                "unchanged_correct": sum(previous_correct[sid] and current_correct[sid] for sid in previous_correct),
                "unchanged_incorrect": sum(not previous_correct[sid] and not current_correct[sid] for sid in previous_correct),
                "net_gain": len(rescued) - len(regressed),
                "rescued_ids": "|".join(sorted(rescued)),
                "regressed_ids": "|".join(sorted(regressed)),
            }
        )
        active_ids = [sid for sid in previous_correct if activations[sid][component]]
        activation_summary.append(
            {
                "intervention": component,
                "from_variant": previous_variant,
                "to_variant": current_variant,
                "activated": len(active_ids),
                "activated_rescued": sum(not previous_correct[sid] and current_correct[sid] for sid in active_ids),
                "activated_regressed": sum(previous_correct[sid] and not current_correct[sid] for sid in active_ids),
                "activated_unchanged": sum(previous_correct[sid] == current_correct[sid] for sid in active_ids),
                "effect_changed": sum(
                    canonical(semantic_fingerprint(all_results[previous_variant][sid]))
                    != canonical(semantic_fingerprint(all_results[current_variant][sid]))
                    for sid in previous_correct
                ),
            }
        )
        for sid in previous_correct:
            transitions[(
                component,
                previous_variant,
                all_results[previous_variant][sid]["first_failure"],
                all_results[current_variant][sid]["first_failure"],
            )] += 1
    transition_rows = [
        {
            "intervention": key[0],
            "from_variant": key[1],
            "to_variant": variant_ids[variant_ids.index(key[1]) + 1],
            "from_first_failure": key[2],
            "to_first_failure": key[3],
            "sample_count": count,
        }
        for key, count in sorted(transitions.items())
    ]

    repair_counter: defaultdict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "trace_count": 0,
            "attempted": 0,
            "applied": 0,
            "succeeded": 0,
            "final_state_correct": 0,
            "final_state_correct_after_applied": 0,
            "final_state_wrong_after_applied": 0,
            "false_repair": 0,
            "sample_ids": [],
        }
    )
    component_variant = {"F": "V6", "G1": "V7", "G2": "V8"}
    for component, variant in component_variant.items():
        for sample_id, result in all_results[variant].items():
            for trace in result["repair_traces"][component]:
                key = (component, str(trace.get("repair_rule") or ""))
                repair_counter[key]["trace_count"] += 1
                repair_counter[key]["attempted"] += int(bool(trace.get("repair_attempted")))
                repair_counter[key]["applied"] += int(bool(trace.get("repair_applied")))
                repair_counter[key]["succeeded"] += int(bool(trace.get("repair_succeeded")))
                final_correct = bool(result["evaluation"]["target_state_correct"])
                applied = bool(trace.get("repair_applied"))
                succeeded = bool(trace.get("repair_succeeded"))
                repair_counter[key]["final_state_correct"] += int(final_correct)
                repair_counter[key]["final_state_correct_after_applied"] += int(
                    applied and final_correct
                )
                repair_counter[key]["final_state_wrong_after_applied"] += int(
                    applied and not final_correct
                )
                repair_counter[key]["false_repair"] += int(
                    applied and succeeded and not final_correct
                )
                repair_counter[key]["sample_ids"].append(sample_id)
    repair_rows = []
    for (component, rule), values in sorted(repair_counter.items()):
        repair_rows.append(
            {
                "intervention": component,
                "repair_rule": rule,
                "trace_count": values["trace_count"],
                "attempted": values["attempted"],
                "applied": values["applied"],
                "succeeded": values["succeeded"],
                "final_state_correct": values["final_state_correct"],
                "final_state_correct_after_applied": values[
                    "final_state_correct_after_applied"
                ],
                "final_state_wrong_after_applied": values[
                    "final_state_wrong_after_applied"
                ],
                "false_repair": values["false_repair"],
                "sample_ids": "|".join(sorted(set(values["sample_ids"]))),
            }
        )
    taxonomy_counter: Counter[tuple[str, str]] = Counter()
    for variant in variant_ids:
        for result in all_results[variant].values():
            taxonomy_counter[(variant, result["first_failure"])] += 1
    taxonomy_rows = [
        {
            "variant": variant,
            "first_failure": failure,
            "sample_count": count,
            "fraction_of_300": count / 300,
        }
        for (variant, failure), count in sorted(taxonomy_counter.items())
    ]
    return {
        "sample_rows": sample_rows,
        "trace_rows": trace_rows,
        "metrics": metrics,
        "rescue": rescue,
        "transitions": transition_rows,
        "activation": activation_summary,
        "repair_rules": repair_rows,
        "taxonomy": taxonomy_rows,
    }


def validate_inputs(
    dataset_archive: Path,
    result_archive: Path,
    expected_dataset_hash: str,
    expected_result_hash: str,
) -> dict[str, str]:
    actual = {
        "dataset_archive_sha256": sha256_file(dataset_archive),
        "result_archive_sha256": sha256_file(result_archive),
    }
    expected = {
        "dataset_archive_sha256": expected_dataset_hash,
        "result_archive_sha256": expected_result_hash,
    }
    mismatches = {key: {"expected": expected[key], "actual": value} for key, value in actual.items() if value != expected[key]}
    if mismatches:
        raise ValueError(f"Frozen input archive hash mismatch: {mismatches}")
    return actual


def validate_replay_results(
    all_results: Mapping[str, Mapping[str, Mapping[str, Any]]],
    expected_ids: set[str],
) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    repair_counts = {component: {"attempted": 0, "applied": 0, "succeeded": 0} for component in REPAIR_COMPONENTS}
    for variant, _, _ in VARIANTS:
        results = all_results.get(variant) or {}
        if set(results) != expected_ids:
            violations.append({"variant": variant, "rule": "sample_identity", "count": len(results)})
        for sample_id, result in results.items():
            repairs = result["repair_traces"]
            if repairs["G1"] and repairs["G2"]:
                violations.append({"variant": variant, "sample_id": sample_id, "rule": "no_g1_g2_chain"})
            for component in REPAIR_COMPONENTS:
                applied_count = 0
                applied_by_slot: Counter[str] = Counter()
                for trace in repairs[component]:
                    attempted = bool(trace.get("repair_attempted"))
                    applied = bool(trace.get("repair_applied"))
                    succeeded = bool(trace.get("repair_succeeded"))
                    repair_counts[component]["attempted"] += int(attempted)
                    repair_counts[component]["applied"] += int(applied)
                    repair_counts[component]["succeeded"] += int(succeeded)
                    applied_count += int(applied)
                    if applied:
                        applied_by_slot[
                            str(
                                trace.get("slot_path")
                                or trace.get("diagnosed_slot")
                                or ""
                            )
                        ] += 1
                    if applied and not attempted:
                        violations.append({"variant": variant, "sample_id": sample_id, "component": component, "rule": "applied_implies_attempted"})
                    if succeeded and not applied:
                        violations.append({"variant": variant, "sample_id": sample_id, "component": component, "rule": "succeeded_implies_applied"})
                    if int(trace.get("revalidation_attempts") or 0) > 1:
                        violations.append({"variant": variant, "sample_id": sample_id, "component": component, "rule": "one_revalidation_max"})
                if component in {"G1", "G2"} and applied_count > 1:
                    violations.append({"variant": variant, "sample_id": sample_id, "component": component, "rule": "one_applied_repair_max"})
                if component == "F" and any(
                    count > 1 for count in applied_by_slot.values()
                ):
                    violations.append({
                        "variant": variant,
                        "sample_id": sample_id,
                        "component": component,
                        "rule": "one_applied_repair_per_slot_max",
                    })
    report = {
        "status": "PASS" if not violations else "FAIL",
        "variants": len(VARIANTS),
        "samples_per_variant": len(expected_ids),
        "evaluations": len(VARIANTS) * len(expected_ids),
        "repair_trace_counts_across_cumulative_variants": repair_counts,
        "violations": violations,
    }
    if violations:
        raise ValueError(f"Replay safety invariants failed: {violations[:5]}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-archive", required=True)
    parser.add_argument("--result-archive", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--project-root")
    parser.add_argument("--expected-dataset-sha256", default=DATASET_ARCHIVE_SHA256)
    parser.add_argument("--expected-result-sha256", default=RESULT_ARCHIVE_SHA256)
    args = parser.parse_args()

    started = time.time()
    repo_root = Path(args.project_root).resolve() if args.project_root else Path(__file__).resolve().parents[2]
    dataset_archive = Path(args.dataset_archive).resolve()
    result_archive = Path(args.result_archive).resolve()
    output_dir = Path(args.output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"Output directory must be absent or empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    input_hashes = validate_inputs(dataset_archive, result_archive, args.expected_dataset_sha256, args.expected_result_sha256)
    raw_bytes = read_tar_member_by_suffix(result_archive, "/mp_fs_plus/raw_generations.jsonl")
    baseline_bytes = read_tar_member_by_suffix(result_archive, "/mp_fs_plus/evaluation.jsonl")
    raw_rows = jsonl_bytes(raw_bytes)
    baseline_rows = jsonl_bytes(baseline_bytes)
    raw_by_id = {str(row["sample_id"]): row for row in raw_rows}
    baseline_by_id = {str(row["sample_id"]): row for row in baseline_rows}
    if len(raw_by_id) != len(raw_rows) or len(baseline_by_id) != len(baseline_rows):
        raise ValueError("Frozen raw/evaluation artifacts contain duplicate sample IDs")

    variant_configs = {
        variant: load_variant_config(repo_root, relative)
        for variant, _, relative in VARIANTS
    }
    all_results: dict[str, dict[str, dict[str, Any]]] = {variant: {} for variant, _, _ in VARIANTS}
    with tempfile.TemporaryDirectory(prefix="stage3_causal_replay_", dir=output_dir.parent) as temp:
        dataset_root = safe_extract_dataset(dataset_archive, Path(temp) / "dataset")
        samples = load_json(dataset_root / "dataset.final.json")
        profiles = {path.stem: load_profile(path) for path in (dataset_root / "profiles").glob("*.json")}
        expected_ids = [str(sample["id"]) for sample in samples]
        if len(samples) != 300 or len(set(expected_ids)) != 300:
            raise ValueError(f"Expected 300 unique frozen samples, got {len(samples)}")
        if set(expected_ids) != set(raw_by_id) or set(expected_ids) != set(baseline_by_id):
            raise ValueError("Dataset, raw generations, and baseline evaluation IDs differ")
        for variant, _, _ in VARIANTS:
            print(f"REPLAY {variant}: 0/300", flush=True)
            for index, sample in enumerate(samples, start=1):
                sample_id = str(sample["id"])
                db_path = find_database(dataset_root / "databases", str(sample["db_id"]))
                all_results[variant][sample_id] = replay_variant(
                    sample,
                    raw_by_id[sample_id],
                    profiles[str(sample["db_id"])],
                    db_path,
                    variant_configs[variant],
                )
                if index % 50 == 0:
                    print(f"REPLAY {variant}: {index}/300", flush=True)

    baseline_mismatches = []
    for sample_id, frozen in baseline_by_id.items():
        replayed = all_results["V0"][sample_id]["evaluation"]
        for field in ("target_state_correct", "strict_full_state_correct", "execution_success"):
            if bool(replayed.get(field)) != bool(frozen.get(field)):
                baseline_mismatches.append({
                    "sample_id": sample_id,
                    "field": field,
                    "frozen": bool(frozen.get(field)),
                    "replayed": bool(replayed.get(field)),
                })
    if baseline_mismatches:
        write_json(output_dir / "baseline_mismatches.json", baseline_mismatches)
        raise ValueError(f"V0 failed frozen baseline equivalence: {len(baseline_mismatches)} field mismatches")

    invariant_report = validate_replay_results(all_results, set(expected_ids))
    tables = build_tables(samples, all_results)
    sample_fields = ["sample_id", "db_id", "input_type", "operation_type"]
    sample_fields += [field for variant, _, _ in VARIANTS for field in (f"{variant}_correct", f"{variant}_strict_correct", f"{variant}_first_failure")]
    sample_fields += [f"{component}_activated" for component in COMPONENTS]
    sample_fields += [field for component in ("F", "G1", "G2") for field in (f"{component}_repair_attempted", f"{component}_repair_applied", f"{component}_repair_succeeded", f"{component}_repair_rules")]
    sample_fields += ["repair_attempted", "repair_applied", "repair_succeeded"]
    write_csv(output_dir / "results" / "causal_replay_sample_level.csv", tables["sample_rows"], sample_fields)
    write_csv(output_dir / "results" / "variant_metrics.csv", tables["metrics"], list(tables["metrics"][0]))
    write_csv(output_dir / "results" / "rescue_regression_matrix.csv", tables["rescue"], list(tables["rescue"][0]))
    write_csv(output_dir / "results" / "failure_stage_transitions.csv", tables["transitions"], list(tables["transitions"][0]))
    write_csv(output_dir / "results" / "intervention_activation_summary.csv", tables["activation"], list(tables["activation"][0]))
    repair_fields = [
        "intervention",
        "repair_rule",
        "trace_count",
        "attempted",
        "applied",
        "succeeded",
        "final_state_correct",
        "final_state_correct_after_applied",
        "final_state_wrong_after_applied",
        "false_repair",
        "sample_ids",
    ]
    write_csv(output_dir / "results" / "repair_rule_summary.csv", tables["repair_rules"], repair_fields)
    write_csv(
        output_dir / "results" / "failure_taxonomy_V0_V8.csv",
        tables["taxonomy"],
        ["variant", "first_failure", "sample_count", "fraction_of_300"],
    )
    write_jsonl(output_dir / "traces" / "A_to_G2_intervention_traces.jsonl", tables["trace_rows"])
    write_json(output_dir / "validation" / "replay_invariants.json", invariant_report)
    write_json(
        output_dir / "validation" / "evaluator_checks.json",
        {
            "status": "PASS",
            "v0_frozen_equivalence_fields": [
                "target_state_correct",
                "strict_full_state_correct",
                "execution_success",
            ],
            "v0_frozen_equivalence_mismatches": 0,
            "state_scope": "all_user_tables",
            "database_execution_isolation": "in-memory copy per evaluation",
        },
    )

    config_hashes = {}
    for variant, _, relative in VARIANTS:
        source = repo_root / relative
        destination = output_dir / "configs" / f"{variant}_{source.name}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        config_hashes[variant] = {"path": relative, "sha256": sha256_file(source)}
    head = git_output(repo_root, "rev-parse", "HEAD")
    frozen_tag_commit = git_output(repo_root, "rev-list", "-n", "1", FROZEN_G2_TAG)
    if frozen_tag_commit != FROZEN_G2_COMMIT:
        raise ValueError(f"{FROZEN_G2_TAG} resolves to {frozen_tag_commit}, expected {FROZEN_G2_COMMIT}")
    run_lock = {
        "stage": "Stage3_FULL_CAUSAL_REPLAY",
        "model_called": False,
        "gpu_required": False,
        "sample_count": 300,
        "variant_order": [variant for variant, _, _ in VARIANTS],
        "component_order": COMPONENTS,
        "repository_head": head,
        "frozen_g2_tag": FROZEN_G2_TAG,
        "frozen_g2_commit": FROZEN_G2_COMMIT,
        "frozen_g2_tag_commit_verified": frozen_tag_commit,
        **input_hashes,
        "raw_generations_sha256": sha256_bytes(raw_bytes),
        "baseline_evaluation_sha256": sha256_bytes(baseline_bytes),
        "sample_ids_sha256": sha256_bytes(("\n".join(str(item["id"]) for item in samples) + "\n").encode("utf-8")),
        "config_hashes": config_hashes,
        "baseline_equivalence": {"fields": ["target_state_correct", "strict_full_state_correct", "execution_success"], "mismatches": 0},
        "activation_definition": "trace signal or observable semantic fingerprint change from the preceding cumulative variant",
        "first_failure_policy": ["parse", "pipeline stage", "semantic_gate", "preflight", "execution", "state_mismatch", "off_target_state_change", "none"],
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
    write_json(
        output_dir / "provenance" / "run_manifest.json",
        {"stage": "Stage3_FULL_CAUSAL_REPLAY", "files": manifest_files},
    )
    print(json.dumps({"status": "PASS", "metrics": tables["metrics"], "baseline_mismatches": 0}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
