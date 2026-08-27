from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE = "Stage7B_A1_FREE_TEXT_SLOT_DISCOVERY_AMENDMENT"
DATE = "20260827"
HASH_POLICY = "text_sha256_canonical_lf"
LOCK_FILE = "STAGE7B_A1_LOCK.json"
MODEL_CALLED = False
GPU_CALLED = False
V2_IMPLEMENTED = False
EXPERIMENT_RUN = False
LIVESQLBENCH_GT_OPENED = False
PHASE_O_MAX_NEW_TOKENS = 512

STAGE7B_INPUTS = (
    "stage7b_v2_method_specification/STAGE7B_V2_SPECIFICATION_LOCK.json",
    "stage7b_v2_method_specification/V2_ARCHITECTURE_SPEC.json",
    "stage7b_v2_method_specification/OPERATION_CONDITIONING_SPEC.json",
    "stage7b_v2_method_specification/SLOT_GROUNDED_IR_SPEC.json",
    "stage7b_v2_method_specification/COMPLETENESS_VERIFICATION_SPEC.json",
    "stage7b_v2_method_specification/ABLATION_REGISTRATION.json",
    "stage7b_v2_method_specification/DEVELOPMENT_DATA_POLICY.json",
)

STAGE7C_PATCH2_INPUTS = (
    "stage7c_v2_development_data_protocol/STAGE7C_DATA_PROTOCOL_LOCK.json",
    "stage7c_v2_development_data_protocol/STAGE7C_INPUT_MANIFEST.json",
    "stage7c_v2_development_data_protocol/SEMANTIC_SLOT_DERIVATION_AUDIT.json",
    "stage7c_v2_development_data_protocol/SEMANTIC_SLOT_INVENTORY_SPEC.json",
    "stage7c_v2_development_data_protocol/EVIDENCE_INVENTORY_SPEC.json",
    "stage7c_v2_development_data_protocol/GENERATION_PROTOCOL_SPEC.json",
    "stage7c_v2_development_data_protocol/TRAIN_CREATE_MANIFEST.jsonl",
    "stage7c_v2_development_data_protocol/DEV_CREATE_MANIFEST.jsonl",
    "stage7c_v2_development_data_protocol/upstream_crudsql/data/train/crud_train_sql.json",
    "stage7c_v2_development_data_protocol/upstream_crudsql/data/train/crud_train_table.json",
    "stage7c_v2_development_data_protocol/upstream_crudsql/data/dev/crud_dev_sql.json",
    "stage7c_v2_development_data_protocol/upstream_crudsql/data/dev/crud_dev_table.json",
)

ARTIFACTS = (
    "STAGE7B_A1_INPUT_MANIFEST.json",
    "STAGE7B_A1_AMENDMENT_RATIONALE.json",
    "MATERIALIZABLE_SLOT_AUDIT.json",
    "PHASE_O_SEMANTIC_SPAN_SPEC.json",
    "PHASE_O_JSON_SCHEMA.json",
    "SPAN_VALIDATION_SPEC.json",
    "EVIDENCE_VS_SLOT_SEPARATION_SPEC.json",
    "COMPLETENESS_AMENDED_SPEC.json",
    "ABLATION_AMENDMENT.json",
    "ABALATION_AMENDMENT.json",
    "GENERATION_CAPACITY_AMENDMENT.json",
    "VALIDATION_REPORT.md",
    "REVIEWER_README.md",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def reset_output_dir(output_dir: Path, force: bool) -> None:
    default = PROJECT_ROOT / "stage7b_a1_free_text_slot_discovery_amendment"
    if output_dir.exists():
        if not force:
            raise RuntimeError(f"{output_dir} exists; pass --force to rebuild.")
        if default.name != output_dir.name or PROJECT_ROOT not in (output_dir, *output_dir.parents):
            raise RuntimeError(f"Refusing to remove output outside Stage7B A1 path: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)


def input_hashes() -> dict[str, str]:
    hashes = {}
    for rel in STAGE7B_INPUTS + STAGE7C_PATCH2_INPUTS:
        path = PROJECT_ROOT / rel
        if not path.is_file():
            raise FileNotFoundError(f"Missing Stage7B A1 input: {rel}")
        hashes[rel] = sha256_file(path)
    return hashes


def sqlite_affinity(source_type: str) -> str:
    upper = str(source_type).upper()
    if "INT" in upper:
        return "INTEGER"
    if any(token in upper for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    if "BLOB" in upper:
        return "BLOB"
    if any(token in upper for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    return "NUMERIC"


def strict_number(value: str) -> float | None:
    text = str(value).strip()
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def materializes(candidate: str, gold: str, source_type: str) -> bool:
    affinity = sqlite_affinity(source_type)
    if affinity == "TEXT":
        return str(candidate).strip() == str(gold).strip()
    if affinity == "INTEGER":
        cand = str(candidate).strip()
        target = str(gold).strip()
        if not re.fullmatch(r"[+-]?\d+", cand) or not re.fullmatch(r"[+-]?\d+", target):
            return False
        return int(cand) == int(target)
    if affinity in {"REAL", "NUMERIC"}:
        cand_num = strict_number(candidate)
        gold_num = strict_number(gold)
        return cand_num is not None and gold_num is not None and abs(cand_num - gold_num) <= 1e-9
    return False


def split_materializable_audit(split: str) -> dict[str, Any]:
    base = PROJECT_ROOT / "stage7c_v2_development_data_protocol"
    rows = read_jsonl(base / f"{split.upper()}_CREATE_MANIFEST.jsonl")
    raw_rows = read_json(base / "upstream_crudsql" / "data" / split / f"crud_{split}_sql.json")
    tables = {row["id"]: row for row in read_json(base / "upstream_crudsql" / "data" / split / f"crud_{split}_table.json")}
    total = 0
    substring_covered = 0
    materializable_covered = 0
    required_slots = 0
    candidate_slots = 0
    missing_materializable_samples = 0
    examples = []
    for manifest_row in rows:
        raw = raw_rows[manifest_row["source_sql_index"]]
        table = tables[raw["table_id"]]
        evidence = {entry["evidence_ref"]: entry["text"] for entry in manifest_row["model_side_input"]["evidence_inventory"]["evidence"]}
        slots = manifest_row["model_side_input"]["semantic_slot_inventory"]["slots"]
        candidates = [evidence[slot["evidence_ref"]] for slot in slots]
        candidate_slots += len(slots)
        required_slots += sum(1 for slot in slots if slot.get("required") is True)
        sample_materializable = 0
        missing_values = []
        for cond in raw["sql"].get("conds", []):
            column_index = int(cond[0])
            gold = str(cond[2])
            source_type = table["types"][column_index]
            total += 1
            if any(gold and gold in str(candidate) for candidate in candidates):
                substring_covered += 1
            ok = any(materializes(candidate, gold, source_type) for candidate in candidates)
            if ok:
                materializable_covered += 1
                sample_materializable += 1
            else:
                missing_values.append({"column_index": column_index, "gold_value": gold, "source_type": source_type})
        if sample_materializable < len(raw["sql"].get("conds", [])):
            missing_materializable_samples += 1
            if len(examples) < 10:
                examples.append(
                    {
                        "sample_id": manifest_row["sample_id"],
                        "gold_assignment_count": len(raw["sql"].get("conds", [])),
                        "materializable_covered": sample_materializable,
                        "missing_values": missing_values,
                        "candidate_examples": candidates[:12],
                    }
                )
    return {
        "split": split,
        "sample_count": len(rows),
        "gold_assignment_count": total,
        "substring_candidate_coverage_count": substring_covered,
        "substring_candidate_coverage_rate": round(substring_covered / total, 6) if total else 1.0,
        "materializable_candidate_coverage_count": materializable_covered,
        "materializable_candidate_coverage_rate": round(materializable_covered / total, 6) if total else 1.0,
        "samples_missing_materializable_candidate": missing_materializable_samples,
        "candidate_slot_count": candidate_slots,
        "required_slot_count": required_slots,
        "required_slots_per_gold_assignment": round(required_slots / total, 6) if total else 0.0,
        "example_missing_materializable_candidates": examples,
    }


def materializable_slot_audit() -> dict[str, Any]:
    train = split_materializable_audit("train")
    dev = split_materializable_audit("dev")
    return {
        "stage": STAGE,
        "source_artifact": "Stage7C_V2_DEVELOPMENT_DATA_PROTOCOL_PATCH2",
        "audit_contract": {
            "TEXT": "candidate evidence must exactly equal gold text because Stage7B materializer preserves raw TEXT evidence",
            "INTEGER": "candidate evidence must be a strict integer literal equal to the gold value",
            "REAL_OR_NUMERIC": "candidate evidence must be a strict finite numeric literal equal to the gold value",
            "substring_context_not_counted": True,
        },
        "train": train,
        "dev": dev,
        "amendment_trigger": {
            "dev_materializable_coverage_below_viability": dev["materializable_candidate_coverage_rate"] < 0.95,
            "dev_required_slots_near_zero": dev["required_slots_per_gold_assignment"] < 0.01,
            "regex_patch_not_recommended": True,
        },
    }


def phase_o_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Stage7B A1 Phase O operation and semantic span selection",
        "type": "object",
        "additionalProperties": False,
        "required": ["operation", "value_spans"],
        "properties": {
            "operation": {"enum": ["INSERT", "UPDATE", "DELETE", "UPSERT"]},
            "value_spans": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["span_ref", "start_char", "end_char"],
                    "properties": {
                        "span_ref": {"type": "string", "pattern": "^SPAN_[1-9][0-9]*$"},
                        "start_char": {"type": "integer", "minimum": 0},
                        "end_char": {"type": "integer", "minimum": 1},
                    },
                },
            },
        },
        "x-deterministic-validation": [
            "0 <= start_char < end_char <= len(question)",
            "text is derived only as question[start_char:end_char]",
            "duplicate span offsets are rejected",
            "nested or partially overlapping spans are rejected",
            "model-emitted value text is forbidden",
        ],
    }


def static_artifacts(audit: dict[str, Any]) -> dict[str, Any]:
    dev = audit["dev"]
    return {
        "STAGE7B_A1_AMENDMENT_RATIONALE.json": {
            "stage": STAGE,
            "status": "ARCHITECTURE_AMENDMENT_ONLY",
            "amends_stage": "Stage7B_V2_METHOD_SPECIFICATION",
            "empirical_trigger_from_stage7c_patch2": {
                "reported_dev_substring_coverage": dev["substring_candidate_coverage_rate"],
                "materializable_dev_coverage": {
                    "count": dev["materializable_candidate_coverage_count"],
                    "denominator": dev["gold_assignment_count"],
                    "rate": dev["materializable_candidate_coverage_rate"],
                },
                "dev_samples_missing_materializable_candidate": dev["samples_missing_materializable_candidate"],
                "dev_required_slots": dev["required_slot_count"],
                "dev_required_slots_per_gold_assignment": dev["required_slots_per_gold_assignment"],
            },
            "scientific_reason": "deterministic free-text slot discovery produced high substring/context coverage but low materializable atomic-value coverage under the frozen typed materializer, and near-zero required-slot coverage made completeness verification non-viable",
            "decision": "reopen Stage7B by amending Phase O from operation-only to operation plus grounded atomic semantic span selection",
            "not_a_stage7c_regex_patch": True,
            "model_called": False,
            "gpu_called": False,
        },
        "PHASE_O_SEMANTIC_SPAN_SPEC.json": {
            "stage": STAGE,
            "old_phase_o": "operation_enum_only",
            "new_phase_o": "operation_enum_plus_grounded_atomic_semantic_value_spans",
            "model_call_count": 1,
            "input_contract": ["question", "schema_inventory"],
            "forbidden_inputs": ["gold_sql", "sql.conds", "gold_program", "gold_post_state", "dev_metric", "481_test_labels", "LiveSQLBench_ground_truth"],
            "output_contract": ["operation", "value_spans"],
            "value_span_semantics": "each accepted span is the model-selected atomic source text for one semantic write value",
            "text_generation_for_values_allowed": False,
            "offsets_only": True,
            "accepted_spans_to_inventory": "deterministically create EV_i and SLOT_i from question[start_char:end_char]",
            "accepted_slot_requiredness": "all accepted Phase O semantic value spans become required=true",
            "phase_m_receives": ["operation", "schema_inventory", "evidence_inventory", "semantic_slot_inventory"],
        },
        "PHASE_O_JSON_SCHEMA.json": phase_o_schema(),
        "SPAN_VALIDATION_SPEC.json": {
            "stage": STAGE,
            "validation_order": [
                "validate_json_schema",
                "check_offsets_within_question_bounds",
                "derive_text_from_question_offsets",
                "reject_duplicate_span_offsets",
                "reject_nested_or_partially_overlapping_spans",
                "assign_deterministic_EV_and_SLOT_refs",
            ],
            "duplicate_span_policy": "reject",
            "nested_span_policy": "reject",
            "partial_overlap_policy": "reject",
            "same_span_selected_twice_policy": "reject",
            "span_text_source": "question[start_char:end_char] only",
            "model_emitted_text_allowed": False,
        },
        "EVIDENCE_VS_SLOT_SEPARATION_SPEC.json": {
            "stage": STAGE,
            "evidence_definition": "context spans may help the model interpret the request but are not automatically semantic write slots",
            "slot_definition": "atomic value spans selected by Phase O as semantic write values",
            "forbidden_mapping": "do_not_convert_every_context_evidence_span_into_SLOT",
            "broad_context_evidence_allowed": True,
            "broad_context_evidence_required": False,
            "semantic_slots_from_phase_o_only": True,
        },
        "COMPLETENESS_AMENDED_SPEC.json": {
            "stage": STAGE,
            "required_set": "all SLOT_* created from accepted Phase O value_spans",
            "mapped_set": "SLOT_* refs used by Phase M assignments/predicates for the predicted operation",
            "missing": "required_set - mapped_set",
            "extra": "mapped_set - allowed_slot_set",
            "complete_iff": "missing is empty and extra is empty and each required SLOT_* is mapped exactly once unless operation-specific schema explicitly permits reuse",
            "phase_o_span_recall_failure": "reported separately when Phase O misses an atomic write value; not hidden as Phase M completeness success",
        },
        "ABLATION_AMENDMENT.json": ablation_payload(),
        "ABALATION_AMENDMENT.json": ablation_payload(),
        "GENERATION_CAPACITY_AMENDMENT.json": {
            "stage": STAGE,
            "capacity_change": "Phase O output expands from one enum to enum plus span offsets",
            "old_phase_o_max_new_tokens": 32,
            "new_phase_o_max_new_tokens": PHASE_O_MAX_NEW_TOKENS,
            "phase_m_max_new_tokens": 8192,
            "trigger": "pre-experiment schema capacity amendment, not selected from model performance",
            "model_revision_unchanged": "Qwen/Qwen2.5-Coder-7B-Instruct@c03e6d358207e414f1eca0bb1891e29f1db0e242",
            "decoding_unchanged": {"do_sample": False, "temperature": 0.0, "top_p": 1.0, "retry_count": 0},
            "model_called": False,
            "gpu_called": False,
        },
        "REVIEWER_README.md": reviewer_readme_payload(),
    }


def ablation_payload() -> dict[str, Any]:
    return {
        "stage": STAGE,
        "amended_variants": {
            "V2-FULL-A1": "Phase O selects operation plus grounded atomic semantic value spans; Phase M maps required SLOT_* refs to columns/predicates",
            "V2-A_MINUS_OPERATION_CONDITIONING": "remove operation conditioning while preserving Phase O span selection and all other components",
            "V2-B_MINUS_CONSTRAINED_REFERENCES": "remove dynamic inventory membership gates while preserving Phase O span selection and all other components",
            "V2-C_MINUS_DETERMINISTIC_MATERIALIZATION": "replace deterministic typed materializer with the pre-registered counterfactual from Stage7B while preserving Phase O span selection",
            "V2-D_MINUS_COMPLETENESS_VERIFICATION": "bypass required SLOT_* completeness gate while preserving Phase O span selection and all other components",
            "V2-O_MINUS_SPAN_SELECTION": "diagnostic only: retain operation prediction but replace Phase O span inventory with deterministic Stage7C PATCH2 inventory to isolate the free-text slot discovery amendment",
        },
        "everything_else_held_constant": True,
        "hidden_third_model_call_allowed": False,
        "ablation_selection_after_dev_performance_allowed": False,
    }


def reviewer_readme_payload() -> str:
    return """# Stage7B A1 Free-Text Slot Discovery Amendment

This reviewer package amends Stage7B before any V2 implementation or experiment.
It does not call a model or GPU. It uses Stage7C PATCH2 artifacts only as the
empirical trigger showing that deterministic regex slot discovery is not viable
under the frozen typed materialization contract.

Commands:
```bash
python scripts/data/build_stage7b_a1_free_text_slot_discovery_amendment.py --force
python scripts/data/validate_stage7b_a1_free_text_slot_discovery_amendment.py
python -m pytest -q tests/test_stage7b_a1_free_text_slot_discovery_amendment.py
```
"""


def validation_report_text(status: str = "PENDING_VALIDATION", violations: list[str] | None = None) -> str:
    return "# Stage7B A1 Validation Report\n\n" + f"Status: {status}\n\nviolations: {json.dumps(violations or [], ensure_ascii=False, sort_keys=True)}\n"


def artifact_hashes(output_dir: Path) -> dict[str, str]:
    return {rel: sha256_file(output_dir / rel) for rel in ARTIFACTS}


def lock(output_dir: Path, inputs: dict[str, str]) -> dict[str, Any]:
    audit = read_json(output_dir / "MATERIALIZABLE_SLOT_AUDIT.json")
    return {
        "stage": STAGE,
        "status": "BUILT_PENDING_VALIDATION",
        "date": DATE,
        "hash_policy": HASH_POLICY,
        "input_hashes": inputs,
        "artifact_hashes": artifact_hashes(output_dir),
        "amends": "Stage7B_V2_METHOD_SPECIFICATION",
        "dev_materializable_candidate_coverage_rate": audit["dev"]["materializable_candidate_coverage_rate"],
        "dev_required_slots_per_gold_assignment": audit["dev"]["required_slots_per_gold_assignment"],
        "phase_o_model_call_count": 1,
        "phase_m_model_call_count": 1,
        "total_model_call_count": 2,
        "phase_o_max_new_tokens": PHASE_O_MAX_NEW_TOKENS,
        "model_called": MODEL_CALLED,
        "gpu_called": GPU_CALLED,
        "v2_implemented": V2_IMPLEMENTED,
        "experiment_run": EXPERIMENT_RUN,
        "live_sql_bench_gt_opened": LIVESQLBENCH_GT_OPENED,
    }


def build_stage7b_a1(output_dir: Path = PROJECT_ROOT / "stage7b_a1_free_text_slot_discovery_amendment", *, force: bool = False) -> dict[str, Any]:
    reset_output_dir(output_dir, force)
    inputs = input_hashes()
    audit = materializable_slot_audit()
    write_json(output_dir / "STAGE7B_A1_INPUT_MANIFEST.json", {"stage": STAGE, "date": DATE, "hash_policy": HASH_POLICY, "input_hashes": inputs})
    write_json(output_dir / "MATERIALIZABLE_SLOT_AUDIT.json", audit)
    for rel, payload in static_artifacts(audit).items():
        if rel.endswith(".md"):
            (output_dir / rel).write_text(payload, encoding="utf-8")
        else:
            write_json(output_dir / rel, payload)
    (output_dir / "VALIDATION_REPORT.md").write_text(validation_report_text(), encoding="utf-8")
    write_json(output_dir / LOCK_FILE, lock(output_dir, inputs))
    return {
        "stage": STAGE,
        "status": "PASS_BUILT",
        "dev_materializable_candidate_coverage_rate": audit["dev"]["materializable_candidate_coverage_rate"],
        "dev_required_slots_per_gold_assignment": audit["dev"]["required_slots_per_gold_assignment"],
        "model_called": False,
        "gpu_called": False,
        "v2_implemented": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "stage7b_a1_free_text_slot_discovery_amendment")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build_stage7b_a1(args.output_dir, force=args.force), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
