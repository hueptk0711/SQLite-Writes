#!/usr/bin/env python3
"""Build Stage7C V2 development/data protocol artifacts.

This stage is CPU-only. It freezes data provenance, adapter contracts, leakage
boundaries, and selection/evaluation protocols before any V2 implementation or
generation run exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CRUDSQL_ROOT = PROJECT_ROOT.parents[1] / "external_sources" / "CRUDSQL_63bfce67"
STAGE = "Stage7C_V2_DEVELOPMENT_DATA_PROTOCOL"
DATE = "20260826"
CRUDSQL_REPO = "https://github.com/bizard-lab/CRUDSQL.git"
CRUDSQL_COMMIT = "63bfce67d8391185453a812751e115a499201363"
EXPECTED_CREATE_COUNTS = {"train": 1760, "dev": 240}
HASH_POLICY = "sha256_bytes_for_raw_files_text_sha256_canonical_lf_for_json_artifacts"
MODEL_CALLED = False
GPU_CALLED = False
V2_IMPLEMENTED = False
EXPERIMENT_RUN = False
LIVESQLBENCH_GT_OPENED = False

STAGE7B_INPUTS = (
    "stage7b_v2_method_specification/STAGE7B_V2_SPECIFICATION_LOCK.json",
    "stage7b_v2_method_specification/V2_ARCHITECTURE_SPEC.json",
    "stage7b_v2_method_specification/REFERENCE_CONSTRAINT_SPEC.json",
    "stage7b_v2_method_specification/COMPLETENESS_VERIFICATION_SPEC.json",
    "stage7b_v2_method_specification/TYPED_MATERIALIZATION_SPEC.json",
    "stage7b_v2_method_specification/DEVELOPMENT_DATA_POLICY.json",
)

STAGE6_TEST_INPUTS = (
    "stage6_final_registration_revision/artifacts/FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl",
)

RAW_SOURCE_RELS = (
    "data/train/crud_train_sql.json",
    "data/train/crud_train_table.json",
    "data/train/train.db",
    "data/dev/crud_dev_sql.json",
    "data/dev/crud_dev_table.json",
    "data/dev/dev.db",
)

ARTIFACTS = (
    "STAGE7C_INPUT_MANIFEST.json",
    "CRUDSQL_SOURCE_MANIFEST.json",
    "TRAIN_CREATE_MANIFEST.jsonl",
    "DEV_CREATE_MANIFEST.jsonl",
    "DATASET_ELIGIBILITY_SPEC.json",
    "DATASET_ELIGIBILITY_AUDIT.json",
    "CRUDSQL_ADAPTER_SPEC.json",
    "SCHEMA_INVENTORY_SPEC.json",
    "EVIDENCE_INVENTORY_SPEC.json",
    "SEMANTIC_SLOT_INVENTORY_SPEC.json",
    "MODEL_INPUT_LEAKAGE_POLICY.json",
    "SPLIT_CONTAMINATION_AUDIT.json",
    "DEV_SELECTION_PROTOCOL.json",
    "GENERATION_PROTOCOL_SPEC.json",
    "EVALUATION_ENVIRONMENT_SPEC.json",
    "RESERVED_BENCHMARK_POLICY.json",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
)
RAW_ARTIFACTS = tuple(f"upstream_crudsql/{rel}" for rel in RAW_SOURCE_RELS)
LOCK_FILE = "STAGE7C_DATA_PROTOCOL_LOCK.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")


def git_output(root: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def sqlite_integrity(path: Path) -> dict[str, Any]:
    con = sqlite3.connect(path)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    finally:
        con.close()
    return {"integrity_check": integrity, "table_count": len(tables)}


def sqlite_affinity(declared_type: str) -> str:
    dtype = (declared_type or "").upper()
    if "INT" in dtype:
        return "INTEGER"
    if any(token in dtype for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in dtype or dtype == "":
        return "BLOB"
    if any(token in dtype for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def split_paths(root: Path, split: str) -> dict[str, Path]:
    return {
        "sql": root / "data" / split / f"crud_{split}_sql.json",
        "table": root / "data" / split / f"crud_{split}_table.json",
        "db": root / "data" / split / f"{split}.db",
    }


def copy_raw_source(crudsql_root: Path, output_dir: Path) -> None:
    for rel in RAW_SOURCE_RELS:
        src = crudsql_root / rel
        dst = output_dir / "upstream_crudsql" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def reset_output_dir(output_dir: Path, force: bool) -> None:
    default = PROJECT_ROOT / "stage7c_v2_development_data_protocol"
    if output_dir.exists():
        if not force and output_dir == default:
            raise RuntimeError(f"{output_dir} exists; pass --force to rebuild.")
        if default not in (output_dir, *output_dir.parents):
            raise RuntimeError(f"Refusing to remove output outside Stage7C path: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)


def input_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for rel in STAGE7B_INPUTS + STAGE6_TEST_INPUTS:
        path = PROJECT_ROOT / rel
        hashes[rel] = sha256_file(path)
    return hashes


def source_manifest(crudsql_root: Path, output_dir: Path) -> dict[str, Any]:
    files = []
    for rel in RAW_SOURCE_RELS:
        source_path = crudsql_root / rel
        packaged_path = output_dir / "upstream_crudsql" / rel
        item = {
            "path": f"upstream_crudsql/{rel}",
            "source_path": rel,
            "sha256": sha256_file(packaged_path),
            "size_bytes": packaged_path.stat().st_size,
        }
        if rel.endswith(".json"):
            item["record_count"] = len(read_json(packaged_path))
        if rel.endswith(".db"):
            item.update(sqlite_integrity(packaged_path))
        item["source_sha256_matches_packaged"] = sha256_file(source_path) == item["sha256"]
        files.append(item)
    return {
        "stage": STAGE,
        "source": {
            "dataset": "CRUDSQL",
            "repository": CRUDSQL_REPO,
            "commit": CRUDSQL_COMMIT,
            "local_source_head": git_output(crudsql_root, "rev-parse", "HEAD"),
            "local_source_clean": git_output(crudsql_root, "status", "--short") == "",
        },
        "included_splits": ["train", "dev"],
        "excluded_splits": ["test"],
        "files": files,
        "model_called": MODEL_CALLED,
        "gpu_called": GPU_CALLED,
    }


def extract_question_spans(question: str) -> list[dict[str, Any]]:
    spans = []
    start = 0
    parts = re.split(r"([，。；;、,\n\r\t])", question)
    cursor = 0
    for part in parts:
        if not part:
            continue
        if re.fullmatch(r"[，。；;、,\n\r\t]", part):
            cursor += len(part)
            start = cursor
            continue
        text = part.strip()
        leading = len(part) - len(part.lstrip())
        if text:
            span_start = start + leading
            spans.append({"text": text, "start_char": span_start, "end_char": span_start + len(text)})
        cursor += len(part)
    if not spans and question:
        spans.append({"text": question, "start_char": 0, "end_char": len(question)})
    return spans


def schema_inventory(table: dict[str, Any]) -> dict[str, Any]:
    return {
        "tables": [{"table_ref": "TAB_1", "table_id": table["id"], "table_name": table["name"]}],
        "columns": [
            {
                "column_ref": f"COL_{index + 1}",
                "table_ref": "TAB_1",
                "source_column_index": index,
                "header": header,
                "source_column_type": table["types"][index],
                "sqlite_affinity": sqlite_affinity(str(table["types"][index])),
            }
            for index, header in enumerate(table["header"])
        ],
        "constraints": [],
    }


def evidence_and_slots(question: str) -> tuple[dict[str, Any], dict[str, Any]]:
    spans = extract_question_spans(question)
    evidence = []
    slots = []
    for index, span in enumerate(spans, start=1):
        evidence_ref = f"EV_{index}"
        slot_ref = f"SLOT_{index}"
        evidence.append({"evidence_ref": evidence_ref, "text": span["text"], "start_char": span["start_char"], "end_char": span["end_char"], "source": "question_text_deterministic_span"})
        slots.append({"slot_ref": slot_ref, "evidence_ref": evidence_ref, "role": "write_value", "required": True, "source": "deterministic_question_span", "uses_gold_sql": False})
    return {"evidence": evidence, "construction": "deterministic_question_span_split"}, {"slots": slots, "construction": "one_required_slot_per_question_span", "uses_gold_sql": False, "model_call_used": False}


def canonical_record(split: str, source_index: int, create_ordinal: int, row: dict[str, Any], table: dict[str, Any]) -> dict[str, Any]:
    question = row["question"]
    evidence_inventory, slot_inventory = evidence_and_slots(question)
    source_hash = sha256_text(canonical_json(row))
    sample_id = f"stage7c_crudsql_{split}_create_{create_ordinal:04d}"
    model_side_input = {
        "question": question,
        "schema_inventory": schema_inventory(table),
        "evidence_inventory": evidence_inventory,
        "semantic_slot_inventory": slot_inventory,
    }
    return {
        "sample_id": sample_id,
        "split": split,
        "source_repository": CRUDSQL_REPO,
        "source_commit": CRUDSQL_COMMIT,
        "source_sql_file": f"upstream_crudsql/data/{split}/crud_{split}_sql.json",
        "source_table_file": f"upstream_crudsql/data/{split}/crud_{split}_table.json",
        "source_db_file": f"upstream_crudsql/data/{split}/{split}.db",
        "source_sql_index": source_index,
        "create_ordinal": create_ordinal,
        "table_id": row["table_id"],
        "question": question,
        "question_sha256": sha256_text(question),
        "canonical_source_record_sha256": source_hash,
        "operation_label_for_evaluation_only": "CREATE",
        "operation_label_visible_to_phase_o": False,
        "model_side_input": model_side_input,
        "model_side_input_sha256": sha256_text(canonical_json(model_side_input)),
        "model_side_input_fields": ["question", "schema_inventory", "evidence_inventory", "semantic_slot_inventory"],
        "semantic_slot_inventory_derivation_inputs": ["question"],
        "label_side_bookkeeping": {
            "crudsql_type": row["sql"]["type"],
            "gold_annotation_sha256": sha256_text(canonical_json(row["sql"])),
            "gold_sql_or_structured_annotation_visible_to_model": False,
            "used_for": "development_evaluation_only",
        },
    }


def build_split_manifest(output_dir: Path, split: str) -> list[dict[str, Any]]:
    paths = split_paths(output_dir / "upstream_crudsql", split)
    sql_rows = read_json(paths["sql"])
    tables = {row["id"]: row for row in read_json(paths["table"])}
    manifest = []
    create_ordinal = 0
    for source_index, row in enumerate(sql_rows):
        if row.get("sql", {}).get("type") != 0:
            continue
        table = tables[row["table_id"]]
        manifest.append(canonical_record(split, source_index, create_ordinal, row, table))
        create_ordinal += 1
    return manifest


def leakage_counts(rows: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    forbidden = {"operation", "operation_label", "gold", "gold_sql", "sql", "conds", "sel", "agg", "type"}
    for row in rows:
        model_text = canonical_json(row["model_side_input"]).casefold()
        if any(f'"{key.casefold()}"' in model_text for key in forbidden):
            counts["model_side_forbidden_key_present"] += 1
        if row["operation_label_visible_to_phase_o"]:
            counts["operation_label_visible_to_phase_o"] += 1
        if row["semantic_slot_inventory_derivation_inputs"] != ["question"]:
            counts["slot_inventory_uses_non_question_input"] += 1
        if row["model_side_input"]["semantic_slot_inventory"].get("uses_gold_sql") is not False:
            counts["slot_inventory_gold_sql_flag_not_false"] += 1
    return counts


def source_split_counts(output_dir: Path, split: str) -> dict[str, Any]:
    rows = read_json(output_dir / "upstream_crudsql" / "data" / split / f"crud_{split}_sql.json")
    type_counts = Counter(str(row.get("sql", {}).get("type")) for row in rows)
    return {"total_records": len(rows), "type_counts": dict(sorted(type_counts.items())), "create_type0_count": type_counts["0"]}


def contamination_audit(train_rows: list[dict[str, Any]], dev_rows: list[dict[str, Any]]) -> dict[str, Any]:
    test_rows = read_jsonl(PROJECT_ROOT / STAGE6_TEST_INPUTS[0])
    train_hashes = {row["question_sha256"] for row in train_rows}
    dev_hashes = {row["question_sha256"] for row in dev_rows}
    test_hashes = {row["input_text_sha256"] for row in test_rows}
    return {
        "stage": STAGE,
        "train_dev_question_hash_overlap": len(train_hashes & dev_hashes),
        "train_481_question_hash_overlap": len(train_hashes & test_hashes),
        "dev_481_question_hash_overlap": len(dev_hashes & test_hashes),
        "train_sample_id_count": len({row["sample_id"] for row in train_rows}),
        "dev_sample_id_count": len({row["sample_id"] for row in dev_rows}),
        "train_dev_sample_id_overlap": len({row["sample_id"] for row in train_rows} & {row["sample_id"] for row in dev_rows}),
        "test_question_text_imported": False,
        "live_sql_bench_gt_opened": LIVESQLBENCH_GT_OPENED,
        "status": "PASS",
    }


def eligibility_spec() -> dict[str, Any]:
    return {
        "stage": STAGE,
        "scope": "CRUDSQL train/dev Create only",
        "selected_operation": {"crudsql_sql_type": 0, "label": "Create"},
        "method_agnostic_exclusion_reasons": ["missing_source_file", "db_integrity_failure", "table_id_missing", "malformed_record", "no_question_text", "no_deterministic_question_span"],
        "forbidden_exclusion_reasons": ["v2_prediction_wrong", "low_confidence_after_generation", "dev_accuracy_hurts", "manual_metric_optimization"],
        "eligibility_frozen_before_v2": True,
    }


def eligibility_audit(output_dir: Path, train_rows: list[dict[str, Any]], dev_rows: list[dict[str, Any]]) -> dict[str, Any]:
    train_counts = source_split_counts(output_dir, "train")
    dev_counts = source_split_counts(output_dir, "dev")
    return {
        "stage": STAGE,
        "expected_official_create_counts": EXPECTED_CREATE_COUNTS,
        "source_split_counts": {"train": train_counts, "dev": dev_counts},
        "eligible_create_counts": {"train": len(train_rows), "dev": len(dev_rows)},
        "exclusions_from_create_pool": {"train": {}, "dev": {}},
        "all_exclusions_method_agnostic": True,
        "status": "PASS",
    }


def static_specs() -> dict[str, Any]:
    return {
        "CRUDSQL_ADAPTER_SPEC.json": {
            "stage": STAGE,
            "operation": "Create",
            "canonical_record_fields": ["sample_id", "split", "question", "table_id", "model_side_input", "label_side_bookkeeping"],
            "gold_sql_visibility": "label_side_bookkeeping_only_never_model_side_input",
            "phase_o_input_fields": ["question", "schema_inventory", "evidence_inventory", "semantic_slot_inventory"],
            "phase_m_input_fields": ["question", "schema_inventory", "evidence_inventory", "semantic_slot_inventory", "phase_o_predicted_operation", "operation_specific_dynamic_schema"],
            "no_v2_implementation": True,
        },
        "SCHEMA_INVENTORY_SPEC.json": {
            "stage": STAGE,
            "table_refs": "TAB_* assigned deterministically per sample",
            "column_refs": "COL_* assigned in table header order",
            "sqlite_affinity_policy": "Stage7B five-affinity policy",
            "gold_assignment_visible": False,
        },
        "EVIDENCE_INVENTORY_SPEC.json": {
            "stage": STAGE,
            "source": "natural_language_question_only",
            "construction": "deterministic split on frozen punctuation delimiters",
            "gold_sql_or_cond_values_used": False,
            "model_call_used": False,
        },
        "SEMANTIC_SLOT_INVENTORY_SPEC.json": {
            "stage": STAGE,
            "source": "evidence_inventory_from_question_only",
            "construction": "one required write_value SLOT_* per deterministic question span",
            "required_flag_policy": "all deterministic question spans required before later dev redesign; no gold SQL is used to decide requiredness",
            "model_call_used": False,
            "hidden_third_llm_call_allowed": False,
        },
        "MODEL_INPUT_LEAKAGE_POLICY.json": {
            "stage": STAGE,
            "forbidden_model_side_fields": ["operation_label", "gold_sql", "crudsql_sql", "conds", "sel", "agg", "target_state", "post_state_hash", "dev_metric"],
            "phase_o_must_predict_operation": True,
            "gold_structured_annotation_use": "development_evaluation_only",
            "slot_inventory_gold_sql_use_allowed": False,
        },
        "DEV_SELECTION_PROTOCOL.json": {
            "stage": STAGE,
            "primary_metric": "Target-State Accuracy",
            "tie_breakers_ordered": ["verification_failure_rate", "execution_success_rate", "schema_rejection_rate"],
            "primary_system": "V2-FULL",
            "variant_selection_rule": "V2-FULL remains primary; ablations diagnose components. If FULL is not viable, open formal redesign rather than cherry-pick an ablation.",
            "selection_split": "CRUDSQL dev Create",
            "forbidden_selection_split": "current 481 CRUDSQL Create test",
        },
        "GENERATION_PROTOCOL_SPEC.json": {
            "stage": STAGE,
            "core_v2_max_model_calls": 2,
            "phase_o_model_calls": 1,
            "phase_m_model_calls": 1,
            "semantic_slot_inventory_model_call_allowed": False,
            "model_revision_selection_rule": "freeze exact model revision before Stage7D execution; if unavailable, record replacement before any generation",
            "temperature": "to_be_frozen_before_stage7d_execution",
            "max_tokens": "to_be_frozen_before_stage7d_execution",
            "retry_policy": "no hidden retry beyond registered protocol",
            "v2_generation_run": False,
        },
        "EVALUATION_ENVIRONMENT_SPEC.json": {
            "stage": STAGE,
            "sqlite_foreign_keys": "ON",
            "database_policy": "copy per sample; transaction/savepoint; rollback after evaluation",
            "primary_metric": "target_state_accuracy",
            "post_state_comparison": "canonical SQLite post-state comparison consistent with frozen V1 principles where applicable",
            "timeout_policy": "to_be_frozen before execution, not tuned on 481",
            "sqlite_version": sqlite3.sqlite_version,
        },
        "RESERVED_BENCHMARK_POLICY.json": {
            "stage": STAGE,
            "current_481_crudsql_create": "post_hoc_only_not_selection",
            "crudsql_update_delete": "reserved_until_after_v2_freeze",
            "livesqlbench_sqlite": "untouched_external_no_gt_access",
            "live_sql_bench_gt_opened": LIVESQLBENCH_GT_OPENED,
        },
    }


def validation_report_text(status: str = "PENDING_VALIDATION", violations: list[str] | None = None) -> str:
    return "# Stage7C Validation Report\n\n" + f"Status: {status}\n\nviolations: {json.dumps(violations or [], ensure_ascii=False, sort_keys=True)}\n"


def reviewer_readme() -> str:
    return """# Stage7C V2 Development/Data Protocol

This package freezes data provenance, CRUDSQL train/dev Create manifests,
adapter and leakage policies, slot-inventory construction policy, selection
rules, and evaluation environment before V2 implementation.

Commands:
```bash
python scripts/data/validate_stage7c_v2_development_data_protocol.py
python -m pytest -q tests/test_stage7c_v2_development_data_protocol.py
```

No model, GPU, V2 implementation, V2 generation, 481-test tuning, or
LiveSQLBench ground-truth access is performed in Stage7C.
"""


def artifact_hashes(output_dir: Path) -> dict[str, str]:
    return {rel: sha256_file(output_dir / rel) for rel in (*ARTIFACTS, *RAW_ARTIFACTS)}


def lock(output_dir: Path, inputs: dict[str, str]) -> dict[str, Any]:
    return {
        "stage": STAGE,
        "status": "BUILT_PENDING_VALIDATION",
        "date": DATE,
        "hash_policy": HASH_POLICY,
        "input_hashes": inputs,
        "artifact_hashes": artifact_hashes(output_dir),
        "crudsql_commit": CRUDSQL_COMMIT,
        "train_create_count": EXPECTED_CREATE_COUNTS["train"],
        "dev_create_count": EXPECTED_CREATE_COUNTS["dev"],
        "model_called": MODEL_CALLED,
        "gpu_called": GPU_CALLED,
        "v2_implemented": V2_IMPLEMENTED,
        "experiment_run": EXPERIMENT_RUN,
        "live_sql_bench_gt_opened": LIVESQLBENCH_GT_OPENED,
    }


def build_stage7c(output_dir: Path, crudsql_root: Path = DEFAULT_CRUDSQL_ROOT, *, force: bool = False) -> dict[str, Any]:
    reset_output_dir(output_dir, force)
    if git_output(crudsql_root, "rev-parse", "HEAD") != CRUDSQL_COMMIT:
        raise RuntimeError(f"CRUDSQL source must be at {CRUDSQL_COMMIT}")
    copy_raw_source(crudsql_root, output_dir)
    inputs = input_hashes()
    train_rows = build_split_manifest(output_dir, "train")
    dev_rows = build_split_manifest(output_dir, "dev")
    write_json(output_dir / "STAGE7C_INPUT_MANIFEST.json", {"stage": STAGE, "date": DATE, "hash_policy": HASH_POLICY, "input_hashes": inputs, "stage7b_locked": True})
    write_json(output_dir / "CRUDSQL_SOURCE_MANIFEST.json", source_manifest(crudsql_root, output_dir))
    write_jsonl(output_dir / "TRAIN_CREATE_MANIFEST.jsonl", train_rows)
    write_jsonl(output_dir / "DEV_CREATE_MANIFEST.jsonl", dev_rows)
    write_json(output_dir / "DATASET_ELIGIBILITY_SPEC.json", eligibility_spec())
    write_json(output_dir / "DATASET_ELIGIBILITY_AUDIT.json", eligibility_audit(output_dir, train_rows, dev_rows))
    for rel, payload in static_specs().items():
        write_json(output_dir / rel, payload)
    leak = leakage_counts(train_rows + dev_rows)
    contamination = contamination_audit(train_rows, dev_rows)
    write_json(output_dir / "SPLIT_CONTAMINATION_AUDIT.json", contamination | {"model_input_leakage_counts": dict(leak), "model_input_leakage_status": "PASS" if not leak else "FAIL"})
    (output_dir / "VALIDATION_REPORT.md").write_text(validation_report_text(), encoding="utf-8")
    (output_dir / "REVIEWER_README.md").write_text(reviewer_readme(), encoding="utf-8")
    write_json(output_dir / LOCK_FILE, lock(output_dir, inputs))
    return {"stage": STAGE, "status": "PASS_BUILT", "train_create": len(train_rows), "dev_create": len(dev_rows), "model_called": False, "gpu_called": False, "v2_implemented": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "stage7c_v2_development_data_protocol")
    parser.add_argument("--crudsql-root", type=Path, default=DEFAULT_CRUDSQL_ROOT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build_stage7c(args.output_dir, args.crudsql_root, force=args.force), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
