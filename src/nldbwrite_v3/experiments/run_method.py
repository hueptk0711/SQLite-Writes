from __future__ import annotations

import csv
import hashlib
import json
import platform
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from nldbwrite_v3.baselines import (
    build_sql_with_v2,
    legacy_record_json_to_write_plan,
)
from nldbwrite_v3.common import (
    dump_json,
    iter_jsonl,
    load_json,
    read_ids,
    sha256_file,
    write_jsonl,
)
from nldbwrite_v3.compiler import (
    check_semantic_risk_gate,
    compile_verified_plan,
    preflight_program,
)
from nldbwrite_v3.data.gold_sql import parse_gold_sql
from nldbwrite_v3.evaluator import evaluate_candidate_sample, find_database
from nldbwrite_v3.experiments.metrics import error_taxonomy_row, summarize_run
from nldbwrite_v3.experiments.run_lock import (
    build_run_lock,
    verify_or_create_run_lock,
)
from nldbwrite_v3.experiments.prompts import (
    build_direct_prompt,
    build_legacy_json_prompt,
    build_repair_prompt,
)
from nldbwrite_v3.inference import GenerationRequest, create_generator
from nldbwrite_v3.inference.parse_output import (
    extract_json_object,
    extract_patch_list,
    extract_sql_statements,
)
from nldbwrite_v3.pipeline import MappingFirstPipeline
from nldbwrite_v3.planner import build_planner_prompt, parse_llm_plan
from nldbwrite_v3.repair import (
    apply_plan_patch,
    evaluate_repair_candidate,
)
from nldbwrite_v3.schema import load_profile
from nldbwrite_v3.source_parser import parse_source_payload
from nldbwrite_v3.verifier import verify_write_plan


DIRECT_METHODS = {"D-ZS", "D-FS", "D-FS-M"}
LEGACY_COMMON_METHODS = {
    "J-ZS",
    "J-FS",
    "J-FS-common",
    "J-FS-M",
}
V2_BUILDER_METHODS = {"S-FS-v2", "S-FS-v2-M"}
MAPPING_METHODS = {
    "MP",
    "MP-R",
    "MP-ZS",
    "MP-FS",
    "MP-FS-R-semi",
    "MP-FS-M",
    "MP-FS+",
}
REPAIR_METHODS = {"MP-R", "MP-FS-R-semi"}
PREFLIGHT_METHODS = {"MP-FS+"}
SUPPORTED_METHODS = (
    DIRECT_METHODS
    | LEGACY_COMMON_METHODS
    | V2_BUILDER_METHODS
    | MAPPING_METHODS
    | {"Gold-MP"}
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _database_identity_changes(
    db_root: str | Path,
    expected_hashes: dict[str, str],
) -> dict[str, dict[str, str | None]]:
    changes: dict[str, dict[str, str | None]] = {}
    for db_id, expected in sorted(expected_hashes.items()):
        try:
            actual = sha256_file(find_database(db_root, db_id))
        except FileNotFoundError:
            actual = None
        if actual != expected:
            changes[db_id] = {
                "expected_sha256": expected,
                "actual_sha256": actual,
            }
    return changes


def _load_method_config(
    config_path: str | Path,
) -> tuple[dict[str, Any], Path | None]:
    config = load_json(config_path)
    base_reference = config.get("base_config")
    project_root = Path(__file__).resolve().parents[3]
    base_path: Path | None = None
    if base_reference:
        base_path = Path(str(base_reference))
        if not base_path.is_absolute():
            base_path = project_root / base_path
        if not base_path.exists():
            raise ValueError(f"Base method config not found: {base_path}")
        base = load_json(base_path)
        config = {**base, **config}

    bank_reference = config.get("demonstration_bank")
    representation = config.get("demonstration_representation")
    if bank_reference:
        bank_path = Path(str(bank_reference))
        if not bank_path.is_absolute():
            bank_path = project_root / bank_path
        bank = load_json(bank_path)
        examples = bank.get("examples") or []
        demonstrations: dict[str, list[dict[str, Any]]] = {
            "semi_structured": [],
            "free_text": [],
        }
        for example in examples:
            if not isinstance(example, dict):
                continue
            mode = str(example.get("mode") or "")
            outputs = example.get("outputs") or {}
            output = (
                outputs.get(str(representation))
                if isinstance(outputs, dict)
                else None
            )
            if mode not in demonstrations or output is None:
                continue
            demonstrations[mode].append(
                {
                    "demonstration_id": example.get("demonstration_id"),
                    "input": example.get("input", ""),
                    "output": output,
                }
            )
        expected = {"semi_structured": 4, "free_text": 2}
        actual = {
            mode: len(rows) for mode, rows in demonstrations.items()
        }
        if actual != expected:
            raise ValueError(
                "Matched demonstration bank must resolve to four "
                f"semi-structured and two free-text examples, got {actual}"
            )
        config["demonstrations"] = demonstrations
        config["resolved_demonstration_bank"] = str(bank_path.resolve())
        config["resolved_demonstration_ids"] = {
            mode: [
                row.get("demonstration_id")
                for row in rows
            ]
            for mode, rows in demonstrations.items()
        }
    return config, base_path


def _load_profiles(profile_dir: str | Path) -> dict[str, dict[str, Any]]:
    return {
        path.stem: load_profile(path)
        for path in Path(profile_dir).glob("*.json")
    }


def _prompt_for_sample(
    method: str,
    sample: dict[str, Any],
    profile: dict[str, Any],
    config: dict[str, Any],
) -> tuple[str, Any]:
    request = str(sample.get("input_text") or "")
    payload = parse_source_payload(
        request,
        structured_parser=config.get("structured_source_parser"),
    )
    prompt_config = config
    demonstrations = config.get("demonstrations")
    if isinstance(demonstrations, dict):
        prompt_config = {
            **config,
            "demonstrations": demonstrations.get(payload.mode) or [],
        }
    if method in DIRECT_METHODS:
        return build_direct_prompt(request, profile, prompt_config), payload
    if method in LEGACY_COMMON_METHODS | V2_BUILDER_METHODS:
        return build_legacy_json_prompt(
            request,
            profile,
            prompt_config,
        ), payload
    if method in MAPPING_METHODS:
        return build_planner_prompt(
            request,
            payload,
            profile,
            config,
        ), payload
    return "Gold Plan oracle: no generation", payload


def _plan_metrics(
    sample: dict[str, Any],
    payload: Any,
    predicted_plan: dict[str, Any] | None,
    gold_plan: dict[str, Any],
) -> dict[str, Any]:
    def multiset_f1(left: Counter[Any], right: Counter[Any]) -> float:
        overlap = sum((left & right).values())
        precision = overlap / sum(left.values()) if left else 0.0
        recall = overlap / sum(right.values()) if right else 0.0
        return (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else (1.0 if not left and not right else 0.0)
        )

    def canonical_value(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    source_expected_rows = sample.get("num_records")
    if source_expected_rows is None:
        source_expected_rows = (
            len(sample.get("gold_records") or [])
            if len(sample.get("gold_tables") or []) <= 1
            else None
        )
    source_parse_row_count_exact = (
        len(payload.rows) == int(source_expected_rows)
        if payload.mode == "semi_structured"
        and source_expected_rows is not None
        else None
    )
    gold_groups = gold_plan.get("write_groups") or []
    expected_rows = sum(
        len(group.get("rows") or []) for group in gold_groups
    )
    common = {
        "source_parse_row_count_exact": source_parse_row_count_exact,
        "source_parsed_row_count": (
            len(payload.rows) if payload.mode == "semi_structured" else None
        ),
        "source_expected_row_count": (
            int(source_expected_rows)
            if payload.mode == "semi_structured"
            and source_expected_rows is not None
            else None
        ),
        "expected_row_count": expected_rows,
    }
    if predicted_plan is None:
        return {
            **common,
            "plan_metrics_available": False,
            "row_count_exact": False,
            "row_coverage": 0.0,
            "row_exact_match": False,
            "cell_value_f1": 0.0,
            "payload_copy_integrity": None,
            "conflict_action_correct": False,
            "conflict_target_exact": False,
            "conflict_update_column_f1": 0.0,
            "conflict_full_exact": False,
            "table_exact": False,
            "target_column_f1": 0.0,
        }
    predicted_groups = predicted_plan.get("write_groups") or []
    predicted_rows = [
        row for group in predicted_groups for row in group.get("rows") or []
    ]
    traced = 0
    preserved = 0
    for group in predicted_groups:
        rows = group.get("rows") or []
        provenance = group.get("provenance") or []
        for row, trace in zip(rows, provenance):
            for column, value in row.items():
                source = (trace.get("value_sources") or {}).get(column) or {}
                if source.get("kind") != "source":
                    continue
                traced += 1
                collection = next(
                    (
                        item
                        for item in payload.collections
                        if item.collection_id == source.get("source_collection")
                    ),
                    None,
                )
                index = source.get("source_row_index")
                field = source.get("source_field")
                if (
                    collection is not None
                    and isinstance(index, int)
                    and 0 <= index < len(collection.rows)
                    and field in collection.rows[index]
                    and collection.rows[index][field] == value
                ):
                    preserved += 1
        if payload.mode == "free_text" and group.get("value_evidence"):
            evidence_rows = group.get("value_evidence") or []
            for row_index, row in enumerate(rows):
                evidence = (
                    evidence_rows[row_index]
                    if row_index < len(evidence_rows)
                    else {}
                )
                for column, value in row.items():
                    traced += 1
                    cell_evidence = evidence.get(column) or {}
                    exact_span = str(cell_evidence.get("exact_span") or "")
                    if (
                        cell_evidence.get("source") == "instruction_text"
                        and exact_span
                        and exact_span in str(sample.get("input_text") or "")
                        and str(value) in exact_span
                    ):
                        preserved += 1
    predicted_actions = Counter(
        (
            str(group.get("table")),
            str(group.get("conflict", {}).get("action") or "error"),
        )
        for group in predicted_groups
    )
    gold_actions = Counter(
        (
            str(group.get("table")),
            str(group.get("conflict", {}).get("action") or "error"),
        )
        for group in gold_groups
    )
    predicted_targets = Counter(
        (
            str(group.get("table")),
            tuple(group.get("conflict", {}).get("target") or []),
        )
        for group in predicted_groups
    )
    gold_targets = Counter(
        (
            str(group.get("table")),
            tuple(group.get("conflict", {}).get("target") or []),
        )
        for group in gold_groups
    )
    predicted_updates = Counter(
        (
            str(group.get("table")),
            str(column),
        )
        for group in predicted_groups
        for column in group.get("conflict", {}).get("update_columns") or []
    )
    gold_updates = Counter(
        (
            str(group.get("table")),
            str(column),
        )
        for group in gold_groups
        for column in group.get("conflict", {}).get("update_columns") or []
    )
    predicted_conflicts = Counter(
        (
            str(group.get("table")),
            str(group.get("conflict", {}).get("action") or "error"),
            tuple(group.get("conflict", {}).get("target") or []),
            tuple(sorted(group.get("conflict", {}).get("update_columns") or [])),
        )
        for group in predicted_groups
    )
    gold_conflicts = Counter(
        (
            str(group.get("table")),
            str(group.get("conflict", {}).get("action") or "error"),
            tuple(group.get("conflict", {}).get("target") or []),
            tuple(sorted(group.get("conflict", {}).get("update_columns") or [])),
        )
        for group in gold_groups
    )
    predicted_row_values = Counter(
        (
            str(group.get("table")),
            canonical_value(row),
        )
        for group in predicted_groups
        for row in group.get("rows") or []
    )
    gold_row_values = Counter(
        (
            str(group.get("table")),
            canonical_value(row),
        )
        for group in gold_groups
        for row in group.get("rows") or []
    )
    predicted_cells = Counter(
        (
            str(group.get("table")),
            str(column),
            canonical_value(value),
        )
        for group in predicted_groups
        for row in group.get("rows") or []
        for column, value in row.items()
    )
    gold_cells = Counter(
        (
            str(group.get("table")),
            str(column),
            canonical_value(value),
        )
        for group in gold_groups
        for row in group.get("rows") or []
        for column, value in row.items()
    )
    predicted_tables = {str(group.get("table")) for group in predicted_groups}
    gold_tables = {str(group.get("table")) for group in gold_plan.get("write_groups") or []}
    predicted_columns = {
        f"{group.get('table')}.{column}"
        for group in predicted_groups
        for row in group.get("rows") or []
        for column in row
    }
    gold_columns = {
        f"{group.get('table')}.{column}"
        for group in gold_groups
        for row in group.get("rows") or []
        for column in row
    }
    overlap = len(predicted_columns & gold_columns)
    precision = overlap / len(predicted_columns) if predicted_columns else 0.0
    recall = overlap / len(gold_columns) if gold_columns else 0.0
    target_column_f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else (1.0 if not predicted_columns and not gold_columns else 0.0)
    )
    return {
        **common,
        "plan_metrics_available": True,
        "row_count_exact": len(predicted_rows) == expected_rows,
        "predicted_row_count": len(predicted_rows),
        "row_coverage": (
            min(len(predicted_rows) / expected_rows, 1.0)
            if expected_rows
            else (1.0 if not predicted_rows else 0.0)
        ),
        "row_exact_match": predicted_row_values == gold_row_values,
        "cell_value_f1": multiset_f1(predicted_cells, gold_cells),
        "payload_copy_integrity": (
            preserved / traced if traced else None
        ),
        "conflict_action_correct": predicted_actions == gold_actions,
        "conflict_target_exact": predicted_targets == gold_targets,
        "conflict_update_column_f1": multiset_f1(
            predicted_updates,
            gold_updates,
        ),
        "conflict_full_exact": predicted_conflicts == gold_conflicts,
        "table_exact": predicted_tables == gold_tables,
        "target_column_f1": target_column_f1,
    }


def _first_verifier_error(verification: dict[str, Any] | None) -> tuple[str | None, str | None]:
    errors = (verification or {}).get("errors") or []
    if not errors:
        return None, None
    return errors[0].get("error_code"), errors[0].get("message")


def _write_error_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fields = [
        "sample_id",
        "db_id",
        "method",
        "error_category",
        "error_stage",
        "error_type",
        "error_message",
        "detected_mode",
        "detected_format",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(error_taxonomy_row(row) for row in rows)


def _validate_locked_test_inputs(
    *,
    method: str,
    current_hashes: dict[str, Any],
    locked_config_path: str | Path | None,
    go_decision_path: str | Path | None,
) -> str:
    if locked_config_path is None or go_decision_path is None:
        raise ValueError(
            "locked-test stage requires --locked-config and --go-decision"
        )
    locked_hash = sha256_file(locked_config_path)
    locked = load_json(locked_config_path)
    decision = load_json(go_decision_path)
    if str(decision.get("decision") or "").casefold() != "go":
        raise ValueError("Locked test rejected: go_decision.decision is not 'go'")
    if decision.get("locked_config_sha256") != locked_hash:
        raise ValueError(
            "Locked test rejected: go_decision does not authorize this "
            "locked config hash"
        )
    if not bool(decision.get("manual_error_review_complete")):
        raise ValueError(
            "Locked test rejected: manual error review is not complete"
        )
    if not bool(decision.get("no_silent_truncation")):
        raise ValueError(
            "Locked test rejected: no_silent_truncation is not certified"
        )
    if not bool(decision.get("no_mixed_checkpoint")):
        raise ValueError(
            "Locked test rejected: no_mixed_checkpoint is not certified"
        )
    expected_method = locked.get("method_id")
    if expected_method != method:
        raise ValueError(
            f"Locked config is for {expected_method!r}, not {method!r}"
        )
    expected_hashes = locked.get("hashes") or {}
    required = (
        "method_config_sha256",
        "inference_config_sha256",
        "resolved_config_sha256",
        "dataset_sha256",
        "split_sha256",
    )
    missing = [key for key in required if not expected_hashes.get(key)]
    if missing:
        raise ValueError(
            "Locked config is missing required hashes: " + ", ".join(missing)
        )
    mismatched = [
        key
        for key in required
        if expected_hashes.get(key) != current_hashes.get(key)
    ]
    if mismatched:
        raise ValueError(
            "Locked test rejected because inputs differ from locked config: "
            + ", ".join(mismatched)
        )
    return locked_hash


def _validate_authorized_run_lock(
    run_lock: dict[str, Any],
    locked_config_path: str | Path,
) -> None:
    locked = load_json(locked_config_path)
    expected_hashes = locked.get("authorized_run_hashes")
    if not isinstance(expected_hashes, dict) or not expected_hashes:
        raise ValueError(
            "Locked config does not authorize run-lock hashes; recreate it "
            "with scripts/server/create_locked_config.py"
        )
    actual_hashes = run_lock.get("hashes") or {}
    mismatched_hashes = [
        key
        for key in sorted(expected_hashes)
        if expected_hashes.get(key) != actual_hashes.get(key)
    ]
    if mismatched_hashes:
        raise ValueError(
            "Locked test rejected because runtime assets differ from the "
            "authorized lock: "
            + ", ".join(mismatched_hashes)
        )
    expected_model = locked.get("authorized_model")
    if not isinstance(expected_model, dict) or not expected_model:
        raise ValueError("Locked config does not authorize a model identity")
    actual_model = run_lock.get("model") or {}
    mismatched_model = [
        key
        for key in sorted(expected_model)
        if expected_model.get(key) != actual_model.get(key)
    ]
    if mismatched_model:
        raise ValueError(
            "Locked test rejected because model identity changed: "
            + ", ".join(mismatched_model)
        )


def _validate_final_protocol_inputs(
    *,
    stage: str,
    method: str,
    current_hashes: dict[str, Any],
    protocol_path: str | Path | None,
) -> str:
    if protocol_path is None:
        raise ValueError(
            "external-holdout/second-model stages require --final-protocol"
        )
    protocol = load_json(protocol_path)
    if protocol.get("status") != "frozen":
        raise ValueError("Final protocol status must be 'frozen'.")
    methods = protocol.get("methods") or []
    if method not in methods:
        raise ValueError(
            f"Final protocol does not authorize method {method!r}."
        )
    authorized = protocol.get("authorized_hashes")
    if not isinstance(authorized, dict):
        raise ValueError("Final protocol requires authorized_hashes.")
    required = {
        "dataset_sha256",
        "split_sha256",
        "gold_plans_sha256",
    }
    missing = sorted(
        key for key in required if not authorized.get(key)
    )
    if missing:
        raise ValueError(
            "Final protocol is missing frozen hashes: " + ", ".join(missing)
        )
    mismatched = sorted(
        key
        for key, expected in authorized.items()
        if expected is not None and current_hashes.get(key) != expected
    )
    if mismatched:
        raise ValueError(
            "Final protocol hash mismatch: " + ", ".join(mismatched)
        )
    authorized_runs = protocol.get("authorized_runs")
    if not isinstance(authorized_runs, dict):
        raise ValueError("Final protocol requires authorized_runs.")
    stage_runs = authorized_runs.get(stage)
    if not isinstance(stage_runs, dict):
        raise ValueError(
            f"Final protocol has no authorized_runs entry for {stage!r}."
        )
    method_run = stage_runs.get(method)
    if not isinstance(method_run, dict):
        raise ValueError(
            f"Final protocol has no authorized run for {stage}/{method}."
        )
    run_hash_keys = {
        "resolved_config_sha256",
        "inference_config_sha256",
    }
    missing_run_hashes = sorted(run_hash_keys - set(method_run))
    if missing_run_hashes:
        raise ValueError(
            "Final protocol authorized run is missing: "
            + ", ".join(missing_run_hashes)
        )
    mismatched_run = sorted(
        key
        for key in run_hash_keys
        if method_run.get(key) != current_hashes.get(key)
    )
    if mismatched_run:
        raise ValueError(
            "Final method/model configuration hash mismatch: "
            + ", ".join(mismatched_run)
        )
    return sha256_file(protocol_path)


def run_method(
    config_path: str | Path,
    data_path: str | Path,
    ids_path: str | Path,
    profile_dir: str | Path,
    db_root: str | Path,
    output_dir: str | Path,
    *,
    gold_plans_path: str | Path | None = None,
    inference_config_path: str | Path | None = None,
    resume: bool = True,
    stage: str = "dev",
    dependency_lock_path: str | Path | None = None,
    environment_manifest_path: str | Path | None = None,
    locked_config_path: str | Path | None = None,
    go_decision_path: str | Path | None = None,
    final_protocol_path: str | Path | None = None,
    allow_locked_test_rerun: bool = False,
    v2_source_path: str | Path | None = None,
    reuse_raw_generations_path: str | Path | None = None,
) -> dict[str, Any]:
    started = time.time()
    project_root = Path(__file__).resolve().parents[3]
    config, base_config_path = _load_method_config(config_path)
    if inference_config_path is not None:
        config = {
            **config,
            "inference": load_json(inference_config_path),
        }
    method = str(config.get("method_id") or "")
    method_variant = (
        str(config.get("method_variant"))
        if config.get("method_variant") is not None
        else None
    )
    method_version = (
        str(config.get("method_version"))
        if config.get("method_version") is not None
        else None
    )
    if method not in SUPPORTED_METHODS:
        raise ValueError(
            f"Unsupported method_id {method!r}; expected {sorted(SUPPORTED_METHODS)}"
        )
    if stage not in {
        "dev",
        "calibration",
        "external-holdout",
        "second-model",
        "locked-test",
        "robustness",
    }:
        raise ValueError(
            "stage must be dev, calibration, external-holdout, "
            "second-model, locked-test, or robustness"
        )
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    dump_json(config, target / "config.json")
    current_hashes = {
        "method_config_sha256": sha256_file(config_path),
        "inference_config_sha256": (
            sha256_file(inference_config_path)
            if inference_config_path
            else None
        ),
        "dataset_sha256": sha256_file(data_path),
        "split_sha256": sha256_file(ids_path),
        "gold_plans_sha256": (
            sha256_file(gold_plans_path)
            if gold_plans_path is not None
            else None
        ),
        "base_config_sha256": (
            sha256_file(base_config_path) if base_config_path else None
        ),
        "resolved_config_sha256": _sha256_text(
            json.dumps(
                config,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
    }
    locked_config_sha256 = (
        _validate_locked_test_inputs(
            method=method,
            current_hashes=current_hashes,
            locked_config_path=locked_config_path,
            go_decision_path=go_decision_path,
        )
        if stage == "locked-test"
        else None
    )
    final_protocol_sha256 = (
        _validate_final_protocol_inputs(
            stage=stage,
            method=method,
            current_hashes=current_hashes,
            protocol_path=final_protocol_path,
        )
        if stage in {"external-holdout", "second-model"}
        else None
    )
    consumed_marker = (
        project_root
        / "experiments"
        / "locked_test"
        / "LOCKED_TEST_CONSUMED"
        / f"{locked_config_sha256}.json"
        if locked_config_sha256 is not None
        else None
    )
    final_method_identity = method_variant or method
    final_consumed_marker = (
        project_root
        / "experiments"
        / "external_holdout"
        / "FINAL_RUN_CONSUMED"
        / f"{final_protocol_sha256}_{stage}_{final_method_identity}.json"
        if final_protocol_sha256 is not None
        else None
    )
    if (
        consumed_marker is not None
        and consumed_marker.exists()
        and not allow_locked_test_rerun
    ):
        raise ValueError(
            "Locked test already consumed for this exact configuration: "
            f"{consumed_marker}"
        )
    if final_consumed_marker is not None and final_consumed_marker.exists():
        raise ValueError(
            "Final run already consumed for this protocol, stage, and method: "
            f"{final_consumed_marker}"
        )
    prior_manifest_path = target / "manifest.json"
    if resume and prior_manifest_path.exists():
        prior_hashes = load_json(prior_manifest_path).get("hashes") or {}
        for key, current_value in current_hashes.items():
            prior_value = prior_hashes.get(key)
            if prior_value is not None and prior_value != current_value:
                raise ValueError(
                    f"Resume rejected because {key} changed; use --no-resume"
                )
    all_samples = {str(row["id"]): row for row in load_json(data_path)}
    selected_ids = read_ids(ids_path)
    missing_samples = [sample_id for sample_id in selected_ids if sample_id not in all_samples]
    if missing_samples:
        raise ValueError(f"Split references {len(missing_samples)} missing samples")
    profiles = _load_profiles(profile_dir)
    gold_plans = (
        {
            str(plan["sample_id"]): plan
            for plan in iter_jsonl(gold_plans_path)
        }
        if gold_plans_path
        else {}
    )

    prompt_rows: list[dict[str, Any]] = []
    payloads: dict[str, Any] = {}
    requests: list[GenerationRequest] = []
    for sample_id in selected_ids:
        sample = all_samples[sample_id]
        profile = profiles[str(sample["db_id"])]
        prompt, payload = _prompt_for_sample(method, sample, profile, config)
        payloads[sample_id] = payload
        prompt_rows.append(
            {
                "sample_id": sample_id,
                "method": method,
                "prompt": prompt,
                "prompt_sha256": _sha256_text(prompt),
                "detected_mode": payload.mode,
                "detected_format": payload.source_format,
            }
        )
        if method != "Gold-MP":
            requests.append(GenerationRequest(sample_id, prompt))
    write_jsonl(prompt_rows, target / "prompts.jsonl")
    prompt_hash = _sha256_text(
        "".join(row["prompt_sha256"] for row in prompt_rows)
    )
    prompt_hashes = {
        str(row["sample_id"]): str(row["prompt_sha256"])
        for row in prompt_rows
    }
    generator = None
    generator_metadata: dict[str, Any] = {"backend": "none"}
    inference_config = config.get("inference") or config
    reuse_raw_path = (
        Path(reuse_raw_generations_path)
        if reuse_raw_generations_path is not None
        else None
    )
    if method != "Gold-MP" and reuse_raw_path is not None:
        if not reuse_raw_path.is_file():
            raise ValueError(f"Reusable raw generation file not found: {reuse_raw_path}")
        generator_metadata = {
            "backend": "reused_raw_generation",
            "source_raw_generations": str(reuse_raw_path.resolve()),
            "source_raw_generations_sha256": sha256_file(reuse_raw_path),
            "semantic_retry": False,
            "prompt_hash_match_required": True,
        }
    elif method != "Gold-MP":
        backend = str(inference_config.get("backend") or "hf").casefold()
        if backend in {"hf", "huggingface"}:
            if dependency_lock_path is None:
                raise ValueError(
                    "Hugging Face runs require --dependency-lock"
                )
            if environment_manifest_path is None:
                raise ValueError(
                    "Hugging Face runs require --environment-manifest"
                )
            environment = load_json(environment_manifest_path)
            if environment.get("status") != "gpu_ready":
                raise ValueError(
                    "Environment manifest is not GPU-ready; run the server "
                    "preflight with --require-gpu"
                )
            recorded_lock = (
                environment.get("dependency_lock") or {}
            ).get("sha256")
            current_lock = sha256_file(dependency_lock_path)
            if recorded_lock != current_lock:
                raise ValueError(
                    "Environment manifest was captured with a different "
                    "dependency lock"
                )
        generator = create_generator(inference_config)
        generator_metadata = generator.metadata()
        if generator_metadata.get("model_manifest"):
            dump_json(
                generator_metadata["model_manifest"],
                target / "model_manifest.json",
            )

    run_lock = build_run_lock(
        project_root=project_root,
        stage=stage,
        method_id=method,
        method_variant=method_variant,
        method_version=method_version,
        method_config_path=config_path,
        inference_config_path=inference_config_path,
        base_config_path=base_config_path,
        resolved_config_sha256=current_hashes["resolved_config_sha256"],
        dataset_path=data_path,
        split_path=ids_path,
        gold_plans_path=gold_plans_path,
        profile_dir=profile_dir,
        db_root=db_root,
        selected_db_ids={
            str(all_samples[sample_id]["db_id"])
            for sample_id in selected_ids
        },
        prompt_set_sha256=prompt_hash,
        model_metadata=generator_metadata,
        dependency_lock_path=dependency_lock_path,
        environment_manifest_path=environment_manifest_path,
        v2_source_path=(
            v2_source_path if method in V2_BUILDER_METHODS else None
        ),
        final_protocol_path=final_protocol_path,
    )
    raw_path = target / "raw_generations.jsonl"
    run_lock_path = target / "run_lock.json"
    if stage == "locked-test":
        assert locked_config_path is not None
        _validate_authorized_run_lock(run_lock, locked_config_path)
    if resume and raw_path.exists() and not run_lock_path.exists():
        raise ValueError(
            "Resume rejected: raw checkpoint exists without run_lock.json; "
            "use --no-resume to start a clean run"
        )
    verify_or_create_run_lock(
        run_lock,
        run_lock_path,
        resume=resume,
    )

    # The lock is validated before any checkpoint row is read.
    existing = (
        {str(row["sample_id"]): row for row in iter_jsonl(raw_path)}
        if resume and raw_path.exists()
        else {}
    )
    if method != "Gold-MP" and reuse_raw_path is not None:
        reusable = {str(row["sample_id"]): row for row in iter_jsonl(reuse_raw_path)}
        missing_reusable = [sample_id for sample_id in selected_ids if sample_id not in reusable]
        if missing_reusable:
            raise ValueError(
                "Reusable raw generation file is missing selected samples: "
                f"{missing_reusable[:10]}"
            )
        prompt_mismatches = [
            sample_id
            for sample_id in selected_ids
            if str(reusable[sample_id].get("prompt_sha256") or "")
            != prompt_hashes[sample_id]
        ]
        if prompt_mismatches:
            raise ValueError(
                "Reusable raw generation prompt hashes do not match the "
                f"current method prompts: {prompt_mismatches[:10]}"
            )
        existing = {
            sample_id: {
                **reusable[sample_id],
                "reused_raw_generation": True,
                "reused_raw_generation_source": str(reuse_raw_path.resolve()),
                "semantic_retry": False,
            }
            for sample_id in selected_ids
        }
        write_jsonl(
            [existing[sample_id] for sample_id in selected_ids],
            raw_path,
        )
    elif method != "Gold-MP":
        missing_requests = [
            request for request in requests if request.sample_id not in existing
        ]
        if missing_requests:
            requested_batch_size = int(
                inference_config.get("batch_size")
                or config.get("batch_size")
                or 1
            )
            # Persist each completed generation batch immediately. Rows are
            # keyed and resumed by sample_id, so an interrupted GPU job only
            # repeats its unfinished batch.
            for start in range(
                0,
                len(missing_requests),
                max(requested_batch_size, 1),
            ):
                request_batch = missing_requests[
                    start : start + max(requested_batch_size, 1)
                ]
                generated = generator.generate(
                    request_batch,
                    batch_size=requested_batch_size,
                )
                existing.update(
                    {
                        result.sample_id: {
                            **result.to_dict(),
                            "method": method,
                            "phase": "initial",
                            "prompt_sha256": prompt_hashes[result.sample_id],
                        }
                        for result in generated
                    }
                )
                write_jsonl(
                    [
                        existing[sample_id]
                        for sample_id in selected_ids
                        if sample_id in existing
                    ],
                    raw_path,
                )
        write_jsonl(
            [existing[sample_id] for sample_id in selected_ids],
            raw_path,
        )
    else:
        write_jsonl(
            [
                {
                    "sample_id": sample_id,
                    "method": method,
                    "phase": "oracle",
                    "status": "not_applicable",
                    "raw_output": "",
                    "prompt_sha256": prompt_hashes[sample_id],
                }
                for sample_id in selected_ids
            ],
            raw_path,
        )

    parsed_rows: list[dict[str, Any]] = []
    materialized_rows: list[dict[str, Any]] = []
    verification_rows: list[dict[str, Any]] = []
    compiled_rows: list[dict[str, Any]] = []
    execution_rows: list[dict[str, Any]] = []
    evaluation_rows: list[dict[str, Any]] = []

    for sample_id in selected_ids:
        sample = all_samples[sample_id]
        profile = profiles[str(sample["db_id"])]
        payload = payloads[sample_id]
        gold_plan = gold_plans.get(sample_id) or parse_gold_sql(
            list(sample.get("gold_sql") or []),
            sample_id=sample_id,
            profile=profile,
        )
        raw = existing.get(sample_id, {})
        raw_output = str(raw.get("raw_output") or "")
        parse_status = "success"
        parsed_plan: dict[str, Any] | None = None
        materialized_plan: dict[str, Any] | None = None
        verification_dict: dict[str, Any] | None = None
        program = None
        direct_sql: list[str] | None = None
        repair_artifact: dict[str, Any] | None = None
        semantic_risk_gate_artifact: dict[str, Any] | None = None
        preflight_artifact: dict[str, Any] | None = None

        if method in DIRECT_METHODS:
            direct_sql, sql_error = extract_sql_statements(raw_output)
            parse_status = "success" if not sql_error else "json_error"
            parsed_rows.append(
                {
                    "sample_id": sample_id,
                    "parse_status": parse_status,
                    "direct_sql": direct_sql,
                    "diagnostics": [sql_error] if sql_error else [],
                }
            )
        elif method in LEGACY_COMMON_METHODS:
            legacy_json, json_error = extract_json_object(raw_output)
            parse_status = "success" if legacy_json is not None else "json_error"
            if legacy_json is not None:
                parsed_plan = legacy_record_json_to_write_plan(
                    legacy_json,
                    profile,
                )
                verification = verify_write_plan(parsed_plan, profile)
                verification_dict = verification.to_dict()
                if verification.valid:
                    materialized_plan = verification.normalized_plan
                    program = compile_verified_plan(materialized_plan, profile)
            parsed_rows.append(
                {
                    "sample_id": sample_id,
                    "parse_status": parse_status,
                    "plan": legacy_json,
                    "diagnostics": [json_error] if json_error else [],
                }
            )
        elif method in V2_BUILDER_METHODS:
            legacy_json, json_error = extract_json_object(raw_output)
            parse_status = (
                "success" if legacy_json is not None else "json_error"
            )
            builder_status = "not_available"
            builder_errors: list[Any] = []
            builder_metadata: list[Any] = []
            if legacy_json is not None:
                parsed_plan = legacy_record_json_to_write_plan(
                    legacy_json,
                    profile,
                )
                materialized_plan = parsed_plan
                (
                    builder_status,
                    direct_sql,
                    builder_errors,
                    builder_metadata,
                ) = build_sql_with_v2(
                    legacy_json,
                    profile,
                    v2_source_path=v2_source_path,
                )
                if builder_status != "success":
                    direct_sql = []
            parsed_rows.append(
                {
                    "sample_id": sample_id,
                    "parse_status": parse_status,
                    "plan": legacy_json,
                    "diagnostics": [json_error] if json_error else [],
                    "v2_builder_status": builder_status,
                    "v2_builder_errors": builder_errors,
                    "v2_builder_metadata": builder_metadata,
                }
            )
        elif method in MAPPING_METHODS:
            plan_kind = "mapping" if payload.mode == "semi_structured" else "free_text"
            reference_planning = bool(config.get("reference_planning"))
            parsed = parse_llm_plan(
                raw_output,
                plan_kind=plan_kind,
                reference_mode=reference_planning,
            )
            parse_status = parsed.parse_status
            parsed_plan = parsed.plan
            parsed_rows.append(
                {
                    "sample_id": sample_id,
                    **parsed.to_dict(),
                    "plan_kind": plan_kind,
                }
            )
            if parsed.success:
                pipeline_result = MappingFirstPipeline(
                    profile,
                    normalize_values=bool(config.get("normalize_values")),
                    normalization_mode=str(
                        config.get("normalization_mode") or "legacy"
                    ),
                    reference_planning=reference_planning,
                    stage2_interventions=config.get("stage2_interventions"),
                    structured_source_parser=config.get(
                        "structured_source_parser"
                    ),
                    free_text_typed_normalization=config.get(
                        "free_text_typed_normalization"
                    ),
                    constrained_reference_repair=config.get(
                        "constrained_reference_repair"
                    ),
                    diagnostic_targeted_repair=config.get(
                        "diagnostic_targeted_repair"
                    ),
                ).run(
                    str(sample.get("input_text") or ""),
                    parsed.plan,
                )
                materialized_plan = pipeline_result.write_plan
                verification_dict = (
                    pipeline_result.verification.to_dict()
                    if pipeline_result.verification
                    else None
                )
                program = pipeline_result.program
            if (
                method in REPAIR_METHODS
                and payload.mode == "semi_structured"
                and parsed_plan is not None
                and (program is None or program.status != "success")
                and generator is not None
            ):
                source_metadata = [
                    {
                        "collection_id": collection.collection_id,
                        "source_path": collection.source_path,
                        "row_count": len(collection.rows),
                        "fields": collection.fields,
                    }
                    for collection in payload.collections
                ]
                verifier_errors = (verification_dict or {}).get("errors") or []
                repair_prompt = build_repair_prompt(
                    payload.instruction_text,
                    source_metadata,
                    profile,
                    parsed_plan,
                    verifier_errors,
                )
                repair_prompt_sha256 = _sha256_text(repair_prompt)
                saved_repair = raw.get("repair")
                if saved_repair:
                    if (
                        saved_repair.get("prompt_sha256")
                        != repair_prompt_sha256
                    ):
                        raise ValueError(
                            "Resume rejected because the repair prompt changed "
                            f"for sample {sample_id}"
                        )
                    repair_generation_row = dict(saved_repair)
                else:
                    repair_generation = generator.generate(
                        [
                            GenerationRequest(
                                f"{sample_id}::repair",
                                repair_prompt,
                            )
                        ],
                        batch_size=1,
                    )[0]
                    repair_generation_row = repair_generation.to_dict()
                patches, patch_error = extract_patch_list(
                    str(repair_generation_row.get("raw_output") or "")
                )
                if not patch_error:
                    try:
                        repaired_mapping = apply_plan_patch(parsed_plan, patches)
                        reason = "; ".join(
                            str(patch.get("reason") or "") for patch in patches
                        ).strip("; ")
                        repair_artifact = evaluate_repair_candidate(
                            parsed_plan,
                            repaired_mapping,
                            payload,
                            profile,
                            find_database(db_root, str(sample["db_id"])),
                            repair_reason=reason,
                        )
                        if repair_artifact["accepted"]:
                            materialized_plan = repair_artifact[
                                "repaired_write_plan"
                            ]
                            verification = verify_write_plan(
                                materialized_plan,
                                profile,
                            )
                            verification_dict = verification.to_dict()
                            program = compile_verified_plan(
                                verification.normalized_plan,
                                profile,
                            )
                    except ValueError as exc:
                        patch_error = str(exc)
                if repair_artifact is None:
                    repair_artifact = {
                        "accepted": False,
                        "checks": {},
                        "details": {"error": patch_error},
                    }
                raw["repair"] = {
                    **repair_generation_row,
                    "prompt": repair_prompt,
                    "prompt_sha256": repair_prompt_sha256,
                    "patches": patches,
                    "acceptance": repair_artifact,
                }
                write_jsonl(
                    [
                        existing[completed_id]
                        for completed_id in selected_ids
                        if completed_id in existing
                    ],
                    raw_path,
                )
        else:
            parsed_plan = gold_plan
            verification = verify_write_plan(gold_plan, profile)
            verification_dict = verification.to_dict()
            if verification.valid:
                materialized_plan = verification.normalized_plan
                program = compile_verified_plan(materialized_plan, profile)
            parsed_rows.append(
                {
                    "sample_id": sample_id,
                    "parse_status": "success",
                    "plan": gold_plan,
                    "diagnostics": [],
                }
            )

        materialized_rows.append(
            {
                "sample_id": sample_id,
                "write_plan": materialized_plan,
                "repair": repair_artifact,
            }
        )
        verification_rows.append(
            {
                "sample_id": sample_id,
                **(
                    verification_dict
                    or {
                        "status": "not_available",
                        "normalized_plan": None,
                        "errors": [],
                        "warnings": [],
                    }
                ),
            }
        )
        compiled_rows.append(
            {
                "sample_id": sample_id,
                **(
                    program.to_dict()
                    if program is not None
                    else {
                        "status": "not_available",
                        "statements": [],
                        "errors": [],
                        "warnings": [],
                    }
                ),
            }
        )
        build_status = (
            "success"
            if (program is not None and program.status == "success")
            or (direct_sql and parse_status == "success")
            else "error"
        )
        if method in PREFLIGHT_METHODS:
            if program is not None and program.status == "success":
                semantic_risk_gate_artifact = check_semantic_risk_gate(
                    program
                )
                if semantic_risk_gate_artifact["accepted"]:
                    preflight_artifact = preflight_program(
                        find_database(db_root, str(sample["db_id"])),
                        program,
                    )
                else:
                    preflight_artifact = {
                        "status": "not_run",
                        "accepted": False,
                        "action": "abstain",
                        "deterministic_repair_applied": False,
                        "error_class": "blocked_by_semantic_risk_gate",
                        "error": (
                            "Candidate was rejected by the semantic-risk gate "
                            "before transactional preflight."
                        ),
                        "executed_statements": 0,
                        "latency_sec": 0.0,
                    }
            else:
                preflight_artifact = {
                    "status": "abstained",
                    "accepted": False,
                    "action": "abstain",
                    "deterministic_repair_applied": False,
                    "error_class": "upstream_rejection",
                    "error": "Plan did not reach successful compilation.",
                    "executed_statements": 0,
                    "latency_sec": 0.0,
                }
        evaluation = evaluate_candidate_sample(
            sample,
            find_database(db_root, str(sample["db_id"])),
            program=program,
            direct_sql=direct_sql,
            parse_status=parse_status,
            build_status=build_status,
            preflight=preflight_artifact,
        )
        evaluation["semantic_risk_gate"] = semantic_risk_gate_artifact
        first_error, first_message = _first_verifier_error(verification_dict)
        if evaluation.get("error_type") in {None, "builder_error"} and first_error:
            evaluation["error_type"] = first_error
            evaluation["error_message"] = first_message
        plan_metrics = _plan_metrics(
            sample,
            payload,
            materialized_plan,
            gold_plan,
        )
        repair_generation_row = raw.get("repair") or {}
        original_input_truncated = bool(raw.get("input_truncated"))
        repair_input_truncated = bool(
            repair_generation_row.get("input_truncated")
        )
        original_output_limit_hit = bool(raw.get("hit_max_new_tokens"))
        repair_output_limit_hit = bool(
            repair_generation_row.get("hit_max_new_tokens")
        )
        evaluation.update(
            {
                "method": method,
                "parse_success": parse_status == "success",
                "plan_validation_success": bool(
                    verification_dict
                    and verification_dict.get("status") == "valid"
                )
                if method not in DIRECT_METHODS
                else False,
                "build_success": build_status == "success",
                "accepted_output": (
                    bool(preflight_artifact.get("accepted"))
                    if preflight_artifact is not None
                    else build_status == "success"
                ),
                "preflight_accepted": (
                    bool(preflight_artifact.get("accepted"))
                    if preflight_artifact is not None
                    else None
                ),
                "preflight_latency_sec": (
                    preflight_artifact.get("latency_sec")
                    if preflight_artifact is not None
                    else None
                ),
                "detected_mode": payload.mode,
                "detected_format": payload.source_format,
                "collection_count": len(payload.collections),
                "is_original_request": (
                    sample.get("is_original_request")
                    if sample.get("is_original_request") is not None
                    else sample.get("is_original")
                ),
                "state_changing": sample.get("state_changing"),
                "conflict_sensitive": sample.get("conflict_sensitive"),
                "operation_semantics": sample.get("operation_semantics"),
                "generation_status": raw.get("status"),
                "input_tokens": raw.get("input_tokens"),
                "original_input_tokens": raw.get("original_input_tokens"),
                "used_input_tokens": raw.get("used_input_tokens"),
                "input_truncated": (
                    original_input_truncated or repair_input_truncated
                ),
                "output_tokens": raw.get("output_tokens"),
                "hit_max_new_tokens": (
                    original_output_limit_hit or repair_output_limit_hit
                ),
                "repair_original_input_tokens": repair_generation_row.get(
                    "original_input_tokens"
                ),
                "repair_used_input_tokens": repair_generation_row.get(
                    "used_input_tokens"
                ),
                "repair_input_truncated": repair_input_truncated,
                "repair_output_tokens": repair_generation_row.get(
                    "output_tokens"
                ),
                "repair_hit_max_new_tokens": repair_output_limit_hit,
                "latency_sec": raw.get("latency_sec"),
                "repair_eligible": (
                    method in REPAIR_METHODS
                    and payload.mode == "semi_structured"
                ),
                "repair_attempted": bool(raw.get("repair")),
                "repair_accepted": bool(
                    repair_artifact and repair_artifact.get("accepted")
                ),
                "slice_labels": [
                    payload.mode,
                    f"format:{payload.source_format}",
                    f"db:{sample.get('db_id')}",
                    *(
                        [f"operation:{sample.get('operation_semantics')}"]
                        if sample.get("operation_semantics")
                        else []
                    ),
                    *(
                        [f"input_format:{sample.get('input_format')}"]
                        if sample.get("input_format")
                        else []
                    ),
                    *(
                        ["single_row"]
                        if sum(
                            len(group.get("rows") or [])
                            for group in gold_plan.get("write_groups") or []
                        )
                        == 1
                        else []
                    ),
                    *(
                        ["small_batch"]
                        if 1
                        < sum(
                            len(group.get("rows") or [])
                            for group in gold_plan.get("write_groups") or []
                        )
                        <= 20
                        else []
                    ),
                    *(
                        ["batch_large"]
                        if sum(
                            len(group.get("rows") or [])
                            for group in gold_plan.get("write_groups") or []
                        )
                        > 20
                        else []
                    ),
                    *(
                        ["single_table"]
                        if len(
                            {
                                str(group.get("table"))
                                for group in gold_plan.get("write_groups") or []
                            }
                        )
                        == 1
                        else []
                    ),
                    *(
                        ["multi_table"]
                        if len(
                            {
                                str(group.get("table"))
                                for group in gold_plan.get("write_groups") or []
                            }
                        )
                        > 1
                        else []
                    ),
                    *[
                        f"conflict:{action}"
                        for action in sorted(
                            {
                                str(
                                    group.get("conflict", {}).get("action")
                                    or "error"
                                )
                                for group in gold_plan.get("write_groups") or []
                            }
                        )
                    ],
                ],
                **plan_metrics,
            }
        )
        evaluation_rows.append(evaluation)
        execution_rows.append(
            {
                "sample_id": sample_id,
                "gold_execution": evaluation.get("gold_execution"),
                "prediction_execution": evaluation.get("prediction_execution"),
                "preflight": preflight_artifact,
            }
        )

    database_identity_changes = _database_identity_changes(
        db_root,
        run_lock.get("databases") or {},
    )
    if database_identity_changes:
        dump_json(
            {
                "status": "invalid",
                "reason": "source_database_mutated_during_run",
                "databases": database_identity_changes,
                "run_lock_sha256": run_lock["run_lock_sha256"],
            },
            target / "SOURCE_DATABASE_MUTATION.json",
        )
        raise RuntimeError(
            "Run invalid: source database identity changed during execution; "
            "see SOURCE_DATABASE_MUTATION.json"
        )

    if method in REPAIR_METHODS:
        write_jsonl(
            [existing[sample_id] for sample_id in selected_ids],
            raw_path,
        )

    # Stage-2 experiment identity is attached to sample-level artifacts only
    # when a variant/version is explicitly configured. Historical configs that
    # do not define these fields retain their prior artifact shape.
    sample_method_identity = {
        "method_id": method,
        **({"method_variant": method_variant} if method_variant is not None else {}),
        **({"method_version": method_version} if method_version is not None else {}),
    }
    if method_variant is not None or method_version is not None:
        for rows in (
            parsed_rows,
            materialized_rows,
            verification_rows,
            compiled_rows,
            execution_rows,
            evaluation_rows,
        ):
            for row in rows:
                row.update(sample_method_identity)

    write_jsonl(parsed_rows, target / "parsed_mapping_plans.jsonl")
    write_jsonl(
        materialized_rows,
        target / "materialized_write_plans.jsonl",
    )
    write_jsonl(verification_rows, target / "verification.jsonl")
    write_jsonl(compiled_rows, target / "compiled_programs.jsonl")
    write_jsonl(execution_rows, target / "execution_logs.jsonl")
    write_jsonl(evaluation_rows, target / "evaluation.jsonl")
    metrics = summarize_run(evaluation_rows)
    dump_json(metrics, target / "metrics.json")
    summary_metadata = {
        "method_id": method,
        "method_variant": method_variant,
        "method_version": method_version,
        "stage": stage,
        "sample_count": len(selected_ids),
        "run_lock_sha256": run_lock["run_lock_sha256"],
    }
    dump_json(summary_metadata, target / "summary_metadata.json")
    _write_error_csv(evaluation_rows, target / "error_analysis.csv")

    inference_manifest_metadata = {
        key: value
        for key, value in generator_metadata.items()
        if key != "model_manifest"
    }
    manifest = {
        "method_id": method,
        "method_variant": method_variant,
        "method_version": method_version,
        "stage": stage,
        "created_at_unix": time.time(),
        "elapsed_sec": time.time() - started,
        "sample_count": len(selected_ids),
        "resume_enabled": resume,
        "generation_checkpoint": "completed_batch_rows_keyed_by_sample_id",
        "run_lock_sha256": run_lock["run_lock_sha256"],
        "locked_config_sha256": locked_config_sha256,
        "final_protocol_sha256": final_protocol_sha256,
        "locked_test_rerun_override": bool(allow_locked_test_rerun),
        "hashes": {
            **current_hashes,
            "prompt_set_sha256": prompt_hash,
        },
        "inference": inference_manifest_metadata,
        "generation": {
            key: (config.get("inference") or config).get(key)
            for key in (
                "batch_size",
                "max_input_tokens",
                "max_new_tokens",
                "do_sample",
                "temperature",
                "top_p",
                "seed",
            )
            if key in (config.get("inference") or config)
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
        },
        "artifacts": [
            "config.json",
            "run_lock.json",
            "manifest.json",
            "prompts.jsonl",
            "raw_generations.jsonl",
            "parsed_mapping_plans.jsonl",
            "materialized_write_plans.jsonl",
            "verification.jsonl",
            "compiled_programs.jsonl",
            "execution_logs.jsonl",
            "evaluation.jsonl",
            "metrics.json",
            "summary_metadata.json",
            "error_analysis.csv",
        ],
    }
    if generator_metadata.get("model_manifest"):
        manifest["artifacts"].append("model_manifest.json")
    dump_json(manifest, target / "manifest.json")
    if stage == "locked-test":
        truncation_failures = sum(
            bool(row.get("input_truncated"))
            or bool(row.get("hit_max_new_tokens"))
            for row in evaluation_rows
        )
        if truncation_failures:
            dump_json(
                {
                    "status": "invalid",
                    "reason": "truncation_detected",
                    "affected_samples": truncation_failures,
                    "run_lock_sha256": run_lock["run_lock_sha256"],
                },
                target / "LOCKED_TEST_INVALID",
            )
            raise ValueError(
                "Locked test invalid: input/output truncation was detected; "
                "the consumed marker was not created"
            )
        marker_payload = {
            "status": "consumed",
            "method_id": method,
            "method_variant": method_variant,
            "method_version": method_version,
            "output_dir": str(target.resolve()),
            "run_lock_sha256": run_lock["run_lock_sha256"],
            "locked_config_sha256": locked_config_sha256,
            "rerun_override": bool(allow_locked_test_rerun),
        }
        assert consumed_marker is not None
        dump_json(
            marker_payload,
            consumed_marker,
        )
        dump_json(
            {
                **marker_payload,
                "global_marker": str(consumed_marker.resolve()),
            },
            target / "LOCKED_TEST_CONSUMED",
        )
    if stage in {"external-holdout", "second-model"}:
        truncation_failures = sum(
            bool(row.get("input_truncated"))
            or bool(row.get("hit_max_new_tokens"))
            for row in evaluation_rows
        )
        missing_predictions = sum(
            row.get("generation_status") not in {"success", "not_applicable"}
            for row in evaluation_rows
        )
        if truncation_failures or missing_predictions:
            dump_json(
                {
                    "status": "invalid",
                    "reason": "truncation_or_missing_prediction",
                    "truncation_failures": truncation_failures,
                    "missing_predictions": missing_predictions,
                    "run_lock_sha256": run_lock["run_lock_sha256"],
                    "final_protocol_sha256": final_protocol_sha256,
                },
                target / "FINAL_RUN_INVALID.json",
            )
            raise ValueError(
                "Final run invalid: truncation or missing predictions detected; "
                "no consumed marker was created"
            )
        assert final_consumed_marker is not None
        marker_payload = {
            "status": "consumed",
            "stage": stage,
            "method_id": method,
            "method_variant": method_variant,
            "method_version": method_version,
            "output_dir": str(target.resolve()),
            "run_lock_sha256": run_lock["run_lock_sha256"],
            "final_protocol_sha256": final_protocol_sha256,
        }
        dump_json(marker_payload, final_consumed_marker)
        dump_json(
            {
                **marker_payload,
                "global_marker": str(final_consumed_marker.resolve()),
            },
            target / "FINAL_RUN_CONSUMED.json",
        )
    return metrics
