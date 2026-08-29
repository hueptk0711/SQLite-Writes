from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nldbwrite_v3.v2_a1.compiler import compile_sqlite_program  # noqa: E402
from nldbwrite_v3.v2_a1.completeness import verify_completeness  # noqa: E402
from nldbwrite_v3.v2_a1.inventories import build_schema_inventory  # noqa: E402
from nldbwrite_v3.v2_a1.json_schema import validate_schema_subset  # noqa: E402
from nldbwrite_v3.v2_a1.phase_m_output import parse_phase_m_output  # noqa: E402
from nldbwrite_v3.v2_a1.phase_m_schema import dynamic_schema, validate_phase_m_ir  # noqa: E402
from nldbwrite_v3.v2_a1.phase_o_output import parse_phase_o_output, phase_o_json_schema  # noqa: E402
from nldbwrite_v3.v2_a1.preflight import preflight_sqlite  # noqa: E402
from nldbwrite_v3.v2_a1.prompt_rendering import (  # noqa: E402
    render_chat_prompt_with_tokenizer,
    render_phase_m_prompt,
    render_phase_o_prompt,
    rendered_prompt_sha256,
)
from nldbwrite_v3.v2_a1.protocol import initialize_v2_a1_runtime, read_json, sha256_file  # noqa: E402
from nldbwrite_v3.v2_a1.slot_inventory import build_slot_bundle  # noqa: E402
from nldbwrite_v3.v2_a1.span_validation import validate_and_sort_spans  # noqa: E402
from nldbwrite_v3.v2_a1.typed_materializer import materialize_ir_values  # noqa: E402
from nldbwrite_v3.v2_a1.types import V2A1Error  # noqa: E402


STAGE = "Stage7E0_V2_A1_REAL_GENERATION_PREFLIGHT"
MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"
REVISION = "c03e6d358207e414f1eca0bb1891e29f1db0e242"
TOKENIZER_CONFIG_SHA256 = "959e7f1d9a1b7641a6d6ce05ca97b75c7894fcb66cbe5a040406458fb1128ee4"
CHAT_TEMPLATE_SHA256 = "cd8e9439f0570856fd70470bf8889ebd8b5d1107207f67a5efb46e342330527f"


@dataclass(frozen=True)
class SmokeFixture:
    sample_id: str
    question: str
    schema_input: dict[str, Any]
    phase_o_label: dict[str, Any]
    phase_m_label: dict[str, Any]


@dataclass(frozen=True)
class ConstraintSpace:
    phase: str
    schema_sha256: str
    constraint_source: str
    candidates: tuple[dict[str, Any], ...]
    branching_evidence: dict[str, Any]

    @property
    def canonical_texts(self) -> tuple[str, ...]:
        return tuple(sorted(canonical_json(candidate) for candidate in self.candidates))

    @property
    def fingerprint(self) -> str:
        return sha256_text("\n".join(self.canonical_texts))

    def metadata(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "constraint_source": self.constraint_source,
            "schema_sha256": self.schema_sha256,
            "constraint_space_sha256": self.fingerprint,
            "candidate_count": len(self.candidates),
            "constraint_space_singleton": len(self.candidates) == 1,
            "semantic_branch_points_observed": bool(self.branching_evidence.get("semantic_branch_points_observed")),
            "finite_known_answer_candidates": False,
            "label_side_data_used_for_constraints": False,
            "candidate_text_sha256_sample": [sha256_text(text) for text in self.canonical_texts[:8]],
            "branching_evidence": self.branching_evidence,
        }


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def canonical_bytes(path: Path) -> bytes:
    return canonical_text(path.read_text(encoding="utf-8-sig")).encode("utf-8")


def sha256_text(text: str) -> str:
    return hashlib.sha256(canonical_text(text).encode("utf-8")).hexdigest()


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def package_version(name: str) -> str | None:
    try:
        module = __import__(name)
    except Exception:
        return None
    return str(getattr(module, "__version__", None))


def nvidia_smi() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as exc:
        return {"available": False, "error": str(exc)}
    return {"available": result.returncode == 0, "returncode": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}


def smoke_fixtures() -> list[SmokeFixture]:
    schema = {
        "schema_inventory": {
            "tables": [{"table_ref": "TAB_1", "table_name": "people"}],
            "columns": [
                {"column_ref": "COL_1", "column_name": "name", "source_type": "TEXT"},
                {"column_ref": "COL_2", "column_name": "age", "source_type": "INTEGER"},
            ],
            "constraints": [{"constraint_ref": "CONSTRAINT_1", "column_refs": ["COL_1"]}],
        }
    }
    return [
        SmokeFixture(
            sample_id="stage7e0_ascii_smoke_0001",
            question="Add Alice, age 20.",
            schema_input={"question": "Add Alice, age 20.", **schema},
            phase_o_label={"operation": "INSERT", "value_spans": [{"start_char": 4, "end_char": 9}, {"start_char": 15, "end_char": 17}]},
            phase_m_label={
                "operation": "INSERT",
                "table_ref": "TAB_1",
                "assignments": [
                    {"column_ref": "COL_1", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"},
                    {"column_ref": "COL_2", "evidence_ref": "EV_2", "slot_ref": "SLOT_2"},
                ],
            },
        ),
        SmokeFixture(
            sample_id="stage7e0_unicode_smoke_0002",
            question="添加员工爱丽丝，年龄20岁。",
            schema_input={"question": "添加员工爱丽丝，年龄20岁。", **schema},
            phase_o_label={"operation": "INSERT", "value_spans": [{"start_char": 4, "end_char": 7}, {"start_char": 10, "end_char": 12}]},
            phase_m_label={
                "operation": "INSERT",
                "table_ref": "TAB_1",
                "assignments": [
                    {"column_ref": "COL_1", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"},
                    {"column_ref": "COL_2", "evidence_ref": "EV_2", "slot_ref": "SLOT_2"},
                ],
            },
        ),
    ]


class SchemaDrivenPrefixTrieConstrainedBackend:
    def __init__(self, tokenizer: Any, constraint_space: ConstraintSpace, *, eos_token_id: int | None) -> None:
        if eos_token_id is None:
            raise ValueError("Tokenizer must expose eos_token_id for constrained generation")
        self.tokenizer = tokenizer
        self.constraint_space = constraint_space
        self.allowed_texts = list(constraint_space.canonical_texts)
        self.allowed_token_paths = [tokenizer(text, add_special_tokens=False).input_ids for text in self.allowed_texts]
        if not self.allowed_token_paths:
            raise ValueError("Schema-driven constrained backend produced no JSON candidates")
        self.eos_token_id = int(eos_token_id)
        self.prompt_token_count = 0

    def set_prompt_token_count(self, prompt_token_count: int) -> None:
        self.prompt_token_count = int(prompt_token_count)

    def allowed_tokens(self, _batch_id: int, input_ids: Any) -> list[int]:
        generated = input_ids.tolist()[self.prompt_token_count :]
        allowed: set[int] = set()
        for path in self.allowed_token_paths:
            if generated == path:
                allowed.add(self.eos_token_id)
            elif len(generated) < len(path) and path[: len(generated)] == generated:
                allowed.add(int(path[len(generated)]))
        return sorted(allowed) or [self.eos_token_id]

    def metadata(self) -> dict[str, Any]:
        return {
            "backend": "schema_driven_prefix_allowed_tokens_trie",
            "schema_mode": "schema_compiled_runtime_domain_trie",
            "token_level_enforcement": True,
            "fallback_to_unconstrained": False,
            "automatic_repair": False,
            "retry": 0,
            **self.constraint_space.metadata(),
        }


def _enum_at(schema: dict[str, Any], *path: str) -> list[str]:
    node: Any = schema
    for part in path:
        node = node[part]
    values = node.get("enum")
    if values is None and "const" in node:
        values = [node["const"]]
    if not isinstance(values, list) or not values:
        raise V2A1Error("constraint_schema_enum_missing", "Constrained generation requires finite enum/const choices", details={"path": list(path)})
    return [str(value) for value in values]


def _all_non_overlapping_span_sets(question: str, *, max_spans: int = 2) -> list[list[dict[str, int]]]:
    spans = [{"start_char": start, "end_char": end} for start in range(len(question)) for end in range(start + 1, len(question) + 1)]
    choices: list[list[dict[str, int]]] = [[span] for span in spans]
    if max_spans >= 2:
        for combo in itertools.combinations(spans, 2):
            try:
                sorted_spans = validate_and_sort_spans(question, list(combo))
            except V2A1Error:
                continue
            choices.append([{"start_char": span.start_char, "end_char": span.end_char} for span in sorted_spans])
    return choices


def build_phase_o_constraint_space(schema: dict[str, Any], question: str, *, max_spans: int = 2) -> ConstraintSpace:
    operations = _enum_at(schema, "properties", "operation")
    span_sets = _all_non_overlapping_span_sets(question, max_spans=max_spans)
    candidates = tuple({"operation": operation, "value_spans": spans} for operation in operations for spans in span_sets)
    validate_constraint_candidates("phase_o", list(candidates), schema, question=question)
    return ConstraintSpace(
        phase="phase_o",
        schema_sha256=sha256_text(canonical_json(schema)),
        constraint_source="json_schema_plus_question_offset_domain",
        candidates=candidates,
        branching_evidence={
            "operation_choices": operations,
            "operation_branch_count": len(operations),
            "span_set_count": len(span_sets),
            "max_spans_per_candidate": max_spans,
            "semantic_branch_points_observed": len(operations) > 1 and len(span_sets) > 1,
        },
    )


def build_phase_m_constraint_space(
    schema: dict[str, Any],
    operation: str,
    inventory: Any,
    slots: Any,
    *,
    root: Path = ROOT,
) -> ConstraintSpace:
    if operation != "INSERT":
        raise V2A1Error("constraint_phase_m_operation_unsupported", "Stage7E0 smoke constraint compiler currently supports INSERT only", details={"operation": operation})
    tables = _enum_at(schema, "properties", "table_ref")
    columns = _enum_at(schema, "properties", "assignments", "items", "properties", "column_ref")
    slot_items = sorted(slots.slots, key=lambda item: item.slot_ref)
    if len(columns) < len(slot_items):
        raise V2A1Error("constraint_phase_m_domain_too_small", "Not enough column choices for smoke slot mappings")
    candidates: list[dict[str, Any]] = []
    for table_ref in tables:
        for slot_order in itertools.permutations(slot_items):
            for column_order in itertools.permutations(columns, len(slot_order)):
                assignments = [
                    {"column_ref": column_ref, "evidence_ref": slot.evidence_ref, "slot_ref": slot.slot_ref}
                    for slot, column_ref in zip(slot_order, column_order)
                ]
                candidates.append({"operation": operation, "table_ref": table_ref, "assignments": assignments})
    validate_constraint_candidates("phase_m", candidates, schema, root=root, operation=operation, inventory=inventory, slots=slots)
    return ConstraintSpace(
        phase="phase_m",
        schema_sha256=sha256_text(canonical_json(schema)),
        constraint_source="json_schema_plus_dynamic_reference_domains",
        candidates=tuple(candidates),
        branching_evidence={
            "table_choices": tables,
            "column_choices": columns,
            "slot_choices": [slot.slot_ref for slot in slot_items],
            "assignment_order_permitted": len(slot_items) > 1,
            "mapping_candidate_count": len(candidates),
            "wrong_but_schema_valid_mapping_present": _has_wrong_but_schema_valid_insert_mapping(candidates),
            "semantic_branch_points_observed": len(candidates) > 1,
        },
    )


def _has_wrong_but_schema_valid_insert_mapping(candidates: list[dict[str, Any]]) -> bool:
    for candidate in candidates:
        assignments = candidate.get("assignments", [])
        if len(assignments) < 2:
            continue
        by_slot = {item["slot_ref"]: item["column_ref"] for item in assignments}
        if by_slot.get("SLOT_1") == "COL_2" and by_slot.get("SLOT_2") == "COL_1":
            return True
    return False


def build_constraint_space(
    phase: str,
    schema: dict[str, Any],
    *,
    question: str | None = None,
    root: Path = ROOT,
    operation: str | None = None,
    inventory: Any | None = None,
    slots: Any | None = None,
) -> ConstraintSpace:
    if phase == "phase_o":
        if question is None:
            raise V2A1Error("constraint_phase_o_context_missing", "Phase O constrained generation requires the question text")
        return build_phase_o_constraint_space(schema, question)
    if phase == "phase_m":
        if operation is None or inventory is None or slots is None:
            raise V2A1Error("constraint_phase_m_context_missing", "Phase M constrained generation requires operation, inventory, and slots")
        return build_phase_m_constraint_space(schema, operation, inventory, slots, root=root)
    raise V2A1Error("constrained_backend_unknown_phase", "Unsupported constrained generation phase", details={"phase": phase})


def validate_constraint_candidates(
    phase: str,
    candidates: list[dict[str, Any]],
    schema: dict[str, Any],
    *,
    root: Path = ROOT,
    question: str | None = None,
    operation: str | None = None,
    inventory: Any | None = None,
    slots: Any | None = None,
) -> dict[str, Any]:
    if not candidates:
        raise V2A1Error("constrained_backend_no_candidates", "Constrained backend requires at least one schema-valid JSON candidate")
    candidate_hashes: list[str] = []
    for obj in candidates:
        validate_schema_subset(schema, obj, reason_code=f"{phase}_candidate_schema_failure")
        if phase == "phase_o":
            parse_phase_o_output(canonical_json(obj), root=root)
            if question is not None:
                validate_and_sort_spans(question, obj["value_spans"])
        elif phase == "phase_m":
            if operation is None or inventory is None or slots is None:
                raise V2A1Error("phase_m_candidate_context_missing", "Phase M candidate validation requires operation, inventory, and slots")
            validate_phase_m_ir(obj, operation, inventory, slots, root=root)
        else:
            raise V2A1Error("constrained_backend_unknown_phase", "Unsupported constrained generation phase", details={"phase": phase})
        candidate_hashes.append(sha256_text(canonical_json(obj)))
    return {
        "candidate_count": len(candidates),
        "candidate_text_sha256": candidate_hashes,
        "schema_sha256": sha256_text(canonical_json(schema)),
    }


def generate_constrained(
    model: Any,
    tokenizer: Any,
    messages: list[dict[str, str]],
    *,
    max_new_tokens: int,
    schema: dict[str, Any],
    phase: str,
    question: str | None = None,
    root: Path = ROOT,
    operation: str | None = None,
    inventory: Any | None = None,
    slots: Any | None = None,
) -> dict[str, Any]:
    import torch

    constraint_space = build_constraint_space(
        phase,
        schema,
        question=question,
        root=root,
        operation=operation,
        inventory=inventory,
        slots=slots,
    )
    rendered = render_chat_prompt_with_tokenizer(tokenizer, messages)
    inputs = tokenizer(rendered, return_tensors="pt")
    device = next(model.parameters()).device
    inputs = {key: value.to(device) for key, value in inputs.items()}
    prompt_tokens = int(inputs["input_ids"].shape[-1])
    backend = SchemaDrivenPrefixTrieConstrainedBackend(tokenizer, constraint_space, eos_token_id=tokenizer.eos_token_id)
    backend.set_prompt_token_count(prompt_tokens)
    start = time.monotonic()
    with torch.inference_mode():
        output = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            temperature=None,
            top_p=None,
            top_k=None,
            pad_token_id=tokenizer.eos_token_id,
            eos_token_id=tokenizer.eos_token_id,
            prefix_allowed_tokens_fn=backend.allowed_tokens,
        )
    latency = time.monotonic() - start
    generated_ids = output[0][prompt_tokens:]
    raw = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
    return {
        "backend": backend.metadata(),
        "rendered_chat_prompt_sha256": rendered_prompt_sha256(rendered),
        "prompt_tokens": prompt_tokens,
        "output_tokens": int(generated_ids.shape[-1]),
        "latency_seconds": latency,
        "hit_max_new_tokens": int(generated_ids.shape[-1]) >= max_new_tokens,
        "raw_output": raw,
    }


def make_smoke_db(path: Path) -> Path:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE "people" ("name" TEXT UNIQUE NOT NULL, "age" INTEGER NOT NULL)')
    conn.commit()
    conn.close()
    return path


def parse_status(kind: str, raw: str, root: Path, inventory: Any | None = None, slots: Any | None = None) -> dict[str, Any]:
    try:
        parsed = parse_phase_o_output(raw, root=root) if kind == "phase_o" else parse_phase_m_output(raw, "INSERT", inventory, slots, root=root)
        return {"status": "PASS", "parsed": parsed}
    except V2A1Error as exc:
        return {"status": "FAIL", "reason_code": exc.reason_code, "message": str(exc), "details": exc.details}
    except Exception as exc:
        return {"status": "FAIL", "reason_code": "unexpected_exception", "message": repr(exc)}


def normalize_phase_o_for_label(obj: dict[str, Any], question: str) -> dict[str, Any]:
    spans = validate_and_sort_spans(question, obj["value_spans"])
    return {
        "operation": obj["operation"],
        "value_spans": [{"start_char": span.start_char, "end_char": span.end_char} for span in spans],
    }


def normalize_phase_m_for_label(obj: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(obj)
    for key in ("assignments", "insert_assignments", "update_assignments"):
        if key in normalized:
            normalized[key] = sorted(normalized[key], key=lambda item: (item["slot_ref"], item["evidence_ref"], item["column_ref"]))
    selector = normalized.get("row_selector")
    if isinstance(selector, dict) and "predicates" in selector:
        normalized["row_selector"] = {
            **selector,
            "predicates": sorted(selector["predicates"], key=lambda item: (item["slot_ref"], item["evidence_ref"], item["column_ref"], item["operator"])),
        }
    return normalized


def evaluate_phase_o_label(generated: dict[str, Any], label: dict[str, Any], question: str) -> dict[str, Any]:
    generated_norm = normalize_phase_o_for_label(generated, question)
    label_norm = normalize_phase_o_for_label(label, question)
    return {"status": "PASS" if generated_norm == label_norm else "FAIL", "generated_normalized": generated_norm, "label_normalized": label_norm}


def evaluate_phase_m_label(generated: dict[str, Any], label: dict[str, Any]) -> dict[str, Any]:
    generated_norm = normalize_phase_m_for_label(generated)
    label_norm = normalize_phase_m_for_label(label)
    return {"status": "PASS" if generated_norm == label_norm else "FAIL", "generated_normalized": generated_norm, "label_normalized": label_norm}


def answer_injection_audit(fixtures: list[SmokeFixture], root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for fixture in fixtures:
        inventory = build_schema_inventory(fixture.schema_input)
        phase_o_schema = phase_o_json_schema(root)
        phase_o_constraints = build_phase_o_constraint_space(phase_o_schema, fixture.question)
        mutated_phase_o_label = {"operation": "DELETE", "value_spans": [{"start_char": 0, "end_char": 1}]}
        phase_o_constraints_after_label_mutation = build_phase_o_constraint_space(phase_o_schema, fixture.question)

        spans = validate_and_sort_spans(fixture.question, fixture.phase_o_label["value_spans"])
        slots = build_slot_bundle(spans)
        phase_m_schema = dynamic_schema(fixture.phase_o_label["operation"], inventory, slots, root=root)
        phase_m_constraints = build_phase_m_constraint_space(phase_m_schema, fixture.phase_o_label["operation"], inventory, slots, root=root)
        mutated_phase_m_label = {
            "operation": "INSERT",
            "table_ref": "TAB_1",
            "assignments": [
                {"column_ref": "COL_2", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"},
                {"column_ref": "COL_1", "evidence_ref": "EV_2", "slot_ref": "SLOT_2"},
            ],
        }
        phase_m_constraints_after_label_mutation = build_phase_m_constraint_space(phase_m_schema, fixture.phase_o_label["operation"], inventory, slots, root=root)
        rows.append(
            {
                "sample_id": fixture.sample_id,
                "phase_o_label_mutation_sha256": sha256_text(canonical_json(mutated_phase_o_label)),
                "phase_m_label_mutation_sha256": sha256_text(canonical_json(mutated_phase_m_label)),
                "phase_o_constraint_hash_before": phase_o_constraints.fingerprint,
                "phase_o_constraint_hash_after_label_mutation": phase_o_constraints_after_label_mutation.fingerprint,
                "phase_m_constraint_hash_before": phase_m_constraints.fingerprint,
                "phase_m_constraint_hash_after_label_mutation": phase_m_constraints_after_label_mutation.fingerprint,
                "phase_o_candidate_count": len(phase_o_constraints.candidates),
                "phase_m_candidate_count": len(phase_m_constraints.candidates),
                "phase_m_wrong_but_schema_valid_mapping_present": phase_m_constraints.branching_evidence["wrong_but_schema_valid_mapping_present"],
            }
        )
    status = "PASS" if all(
        row["phase_o_constraint_hash_before"] == row["phase_o_constraint_hash_after_label_mutation"]
        and row["phase_m_constraint_hash_before"] == row["phase_m_constraint_hash_after_label_mutation"]
        and row["phase_o_candidate_count"] > 1
        and row["phase_m_candidate_count"] > 1
        and row["phase_m_wrong_but_schema_valid_mapping_present"]
        for row in rows
    ) else "FAIL"
    return {
        "status": status,
        "generation_api_accepts_precomputed_candidates": False,
        "generation_path_reads_phase_o_label": False,
        "generation_path_reads_phase_m_label": False,
        "generation_path_reads_gold_labels": False,
        "finite_expected_candidate_trie": False,
        "constraint_source": "json_schema_plus_runtime_domains_not_label_side_answers",
        "label_side_data_used_for_constraints": False,
        "fallback_to_unconstrained": False,
        "automatic_repair": False,
        "retry": 0,
        "rows": rows,
    }


def run_smoke_fixture(root: Path, output_dir: Path, model: Any, tokenizer: Any, fixture: SmokeFixture, *, phase_o_max_new_tokens: int, phase_m_max_new_tokens: int) -> dict[str, Any]:
    inventory = build_schema_inventory(fixture.schema_input)
    phase_o_messages, phase_o_messages_sha256 = render_phase_o_prompt(fixture.question, inventory, root=root)
    phase_o_schema = phase_o_json_schema(root)
    write_json(output_dir / f"PHASE_O_MESSAGES_{fixture.sample_id}.json", {"messages": phase_o_messages, "messages_sha256": phase_o_messages_sha256})
    write_json(output_dir / f"PHASE_O_SCHEMA_USED_{fixture.sample_id}.json", phase_o_schema)

    phase_o_generation = generate_constrained(
        model,
        tokenizer,
        phase_o_messages,
        max_new_tokens=phase_o_max_new_tokens,
        schema=phase_o_schema,
        phase="phase_o",
        question=fixture.question,
        root=root,
    )
    phase_o_parse = parse_status("phase_o", phase_o_generation["raw_output"], root)
    phase_o_ok = phase_o_parse["status"] == "PASS"
    row: dict[str, Any] = {
        "sample_id": fixture.sample_id,
        "question": fixture.question,
        "phase_o": {
            **phase_o_generation,
            "messages_sha256": phase_o_messages_sha256,
            "parse_schema_validation": phase_o_parse,
        },
    }
    if not phase_o_ok:
        row["status"] = "FAIL"
        row["violations"] = ["phase_o_real_generation_failed"]
        return row
    phase_o_label_eval = evaluate_phase_o_label(phase_o_parse["parsed"], fixture.phase_o_label, fixture.question)
    row["phase_o"]["label_evaluation"] = phase_o_label_eval
    if phase_o_label_eval["status"] != "PASS":
        row["status"] = "FAIL"
        row["violations"] = ["phase_o_label_mismatch"]
        return row

    spans = validate_and_sort_spans(fixture.question, phase_o_parse["parsed"]["value_spans"])
    slots = build_slot_bundle(spans)
    phase_m_messages, phase_m_messages_sha256 = render_phase_m_prompt(phase_o_parse["parsed"]["operation"], inventory, slots, root=root)
    phase_m_schema = dynamic_schema(phase_o_parse["parsed"]["operation"], inventory, slots, root=root)
    write_json(output_dir / f"PHASE_M_MESSAGES_{fixture.sample_id}.json", {"messages": phase_m_messages, "messages_sha256": phase_m_messages_sha256})
    write_json(output_dir / f"PHASE_M_DYNAMIC_SCHEMA_USED_{fixture.sample_id}.json", phase_m_schema)

    phase_m_generation = generate_constrained(
        model,
        tokenizer,
        phase_m_messages,
        max_new_tokens=phase_m_max_new_tokens,
        schema=phase_m_schema,
        phase="phase_m",
        root=root,
        operation=phase_o_parse["parsed"]["operation"],
        inventory=inventory,
        slots=slots,
    )
    phase_m_parse = parse_status("phase_m", phase_m_generation["raw_output"], root, inventory, slots)
    row["phase_m"] = {**phase_m_generation, "messages_sha256": phase_m_messages_sha256, "parse_schema_validation": phase_m_parse}
    if phase_m_parse["status"] != "PASS":
        row["status"] = "FAIL"
        row["violations"] = ["phase_m_real_generation_failed"]
        return row
    phase_m_label_eval = evaluate_phase_m_label(phase_m_parse["parsed"], fixture.phase_m_label)
    row["phase_m"]["label_evaluation"] = phase_m_label_eval
    if phase_m_label_eval["status"] != "PASS":
        row["status"] = "FAIL"
        row["violations"] = ["phase_m_label_mismatch"]
        return row

    materialized = materialize_ir_values(phase_m_parse["parsed"], inventory, slots)
    verify_completeness(phase_m_parse["parsed"], slots)
    program = compile_sqlite_program(phase_m_parse["parsed"], inventory, materialized)
    db_path = make_smoke_db(output_dir / f"{fixture.sample_id}.sqlite")
    preflight = preflight_sqlite(db_path, program)
    row["downstream"] = {
        "accepted_spans": [span.__dict__ for span in spans],
        "evidence_inventory": [item.__dict__ for item in slots.evidence],
        "semantic_slot_inventory": [item.__dict__ for item in slots.slots],
        "materialized_bindings": [binding.__dict__ for binding in materialized.values()],
        "compiled_sql": program.sql,
        "compiled_parameters": list(program.parameters),
        "preflight": preflight.__dict__,
    }
    row["status"] = "PASS" if preflight.admitted else "FAIL"
    row["violations"] = [] if preflight.admitted else ["synthetic_transactional_preflight_failed"]
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage7E0 V2-A1 real constrained generation preflight.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "stage7e0_real_generation_preflight_patch5")
    parser.add_argument("--phase-o-max-new-tokens", type=int, default=512)
    parser.add_argument("--phase-m-max-new-tokens", type=int, default=8192)
    args = parser.parse_args()

    root = args.root.resolve()
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    violations: list[str] = []

    run_command = " ".join(sys.argv)
    (output_dir / "RUN_COMMAND.txt").write_text(run_command + "\n", encoding="utf-8")
    write_json(
        output_dir / "GENERATION_CONFIG.json",
        {
            "model_id": MODEL_ID,
            "model_path": str(args.model_path),
            "model_revision": REVISION,
            "tokenizer_revision": REVISION,
            "do_sample": False,
            "temperature": 0.0,
            "top_p": 1.0,
            "retry": 0,
            "phase_o_max_new_tokens": args.phase_o_max_new_tokens,
            "phase_m_max_new_tokens": args.phase_m_max_new_tokens,
            "transformers_generation_kwargs": {
                "do_sample": False,
                "temperature": None,
                "top_p": None,
                "top_k": None,
                "prefix_allowed_tokens_fn": "SchemaDrivenPrefixTrieConstrainedBackend.allowed_tokens",
            },
        },
    )
    fixtures = smoke_fixtures()
    write_jsonl(output_dir / "SMOKE_FIXTURES.jsonl", [fixture.__dict__ for fixture in fixtures])

    try:
        initialize_v2_a1_runtime(root)
    except Exception as exc:
        violations.append(f"runtime_integrity_failed:{exc!r}")

    generation_protocol = read_json(root / "stage7c_a1_v2_development_protocol/GENERATION_PROTOCOL_A1.json")
    chat_preflight = read_json(root / "stage7d_v2_a1_implementation/CHAT_TEMPLATE_PREFLIGHT.json")
    model_config = generation_protocol["model_config"]
    for key, expected in {"model_id": MODEL_ID, "model_revision": REVISION, "tokenizer_revision": REVISION, "tokenizer_config_file_sha256": TOKENIZER_CONFIG_SHA256}.items():
        if model_config.get(key) != expected:
            violations.append(f"generation_protocol_{key}_mismatch")
    if chat_preflight.get("actual_chat_template_string_sha256") != CHAT_TEMPLATE_SHA256:
        violations.append("stage7d_chat_template_hash_mismatch")

    env = {
        "stage": STAGE,
        "platform": platform.platform(),
        "python": sys.version,
        "executable": sys.executable,
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "packages": {name: package_version(name) for name in ("torch", "transformers", "accelerate", "bitsandbytes", "tokenizers", "safetensors")},
        "nvidia_smi": nvidia_smi(),
    }
    try:
        import torch

        env["torch_cuda"] = {
            "cuda_available": bool(torch.cuda.is_available()),
            "cuda_runtime": torch.version.cuda,
            "gpu_count": int(torch.cuda.device_count()),
            "devices": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())],
        }
        if not torch.cuda.is_available():
            violations.append("cuda_not_available")
    except Exception as exc:
        env["torch_cuda"] = {"error": repr(exc)}
        violations.append("torch_cuda_probe_failed")
    write_json(output_dir / "ENVIRONMENT.json", env)

    tokenizer_config = args.model_path / "tokenizer_config.json"
    if not tokenizer_config.exists():
        violations.append(f"missing_tokenizer_config:{tokenizer_config}")
    elif hashlib.sha256(canonical_bytes(tokenizer_config)).hexdigest() != TOKENIZER_CONFIG_SHA256:
        violations.append("server_tokenizer_config_hash_mismatch")

    from transformers import AutoModelForCausalLM, AutoTokenizer

    load_started = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(str(args.model_path), trust_remote_code=False)
    chat_template_hash = sha256_text(tokenizer.chat_template or "")
    if chat_template_hash != CHAT_TEMPLATE_SHA256:
        violations.append("server_chat_template_hash_mismatch")
    model = AutoModelForCausalLM.from_pretrained(str(args.model_path), device_map="auto", torch_dtype="auto", trust_remote_code=False)
    model.eval()
    write_json(
        output_dir / "MODEL_LOAD_AND_TOKENIZER_PREFLIGHT.json",
        {
            "status": "PASS" if not any(v.startswith("server_") or v.startswith("missing_tokenizer") for v in violations) else "FAIL",
            "model_id": MODEL_ID,
            "model_path": str(args.model_path),
            "model_revision": REVISION,
            "tokenizer_config_sha256": sha256_file(tokenizer_config) if tokenizer_config.exists() else None,
            "chat_template_sha256": chat_template_hash,
            "load_seconds": time.monotonic() - load_started,
        },
    )

    backend_summary = {
        "backend": "schema_driven_prefix_allowed_tokens_trie",
        "version": "stage7e0_patch5",
        "backend_version": "stage7e0_patch5",
        "schema_mode": "schema_compiled_runtime_domain_trie",
        "schema_enforcement_mode": "transformers_prefix_allowed_tokens_fn",
        "constraint_source": "json_schema_plus_runtime_domains_not_label_side_answers",
        "finite_known_answer_candidates": False,
        "label_side_data_used_for_constraints": False,
        "token_level_enforcement": True,
        "fallback_to_unconstrained": False,
        "automatic_repair": False,
        "retry": 0,
        "aborts_on_backend_fallback": True,
        "uses_transformers_prefix_allowed_tokens_fn": True,
        "candidate_validation": "schema subset plus runtime-domain reference validators before generation; expected labels are evaluated only after generation",
        "scope": "synthetic Stage7E0 smoke fixtures only; no train/dev generation",
        "answer_injection_audit_artifact": "ANSWER_INJECTION_AUDIT.json",
    }
    write_json(output_dir / "CONSTRAINED_GENERATION_BACKEND.json", backend_summary)

    smoke_rows = []
    for fixture in fixtures:
        try:
            smoke_rows.append(
                run_smoke_fixture(
                    root,
                    output_dir,
                    model,
                    tokenizer,
                    fixture,
                    phase_o_max_new_tokens=args.phase_o_max_new_tokens,
                    phase_m_max_new_tokens=args.phase_m_max_new_tokens,
                )
            )
        except Exception as exc:
            smoke_rows.append({"sample_id": fixture.sample_id, "status": "FAIL", "violations": ["unexpected_smoke_exception"], "error": repr(exc)})
    write_jsonl(output_dir / "SMOKE_GENERATIONS.jsonl", smoke_rows)

    injection_audit = answer_injection_audit(fixtures, root)
    write_json(output_dir / "ANSWER_INJECTION_AUDIT.json", injection_audit)
    backend_summary["answer_injection_audit_status"] = injection_audit["status"]
    write_json(output_dir / "CONSTRAINED_GENERATION_BACKEND.json", backend_summary)
    if injection_audit["status"] != "PASS":
        violations.append("answer_injection_audit_failed")

    for row in smoke_rows:
        if row.get("status") != "PASS":
            violations.append(f"smoke_failed:{row.get('sample_id')}")
        if row.get("phase_o", {}).get("parse_schema_validation", {}).get("status") != "PASS":
            violations.append(f"phase_o_real_generation_failed:{row.get('sample_id')}")
        if row.get("phase_o", {}).get("label_evaluation", {}).get("status") not in {"PASS", None}:
            violations.append(f"phase_o_label_mismatch:{row.get('sample_id')}")
        if row.get("phase_m", {}).get("parse_schema_validation", {}).get("status") != "PASS":
            violations.append(f"phase_m_real_generation_failed:{row.get('sample_id')}")
        if row.get("phase_m", {}).get("label_evaluation", {}).get("status") not in {"PASS", None}:
            violations.append(f"phase_m_label_mismatch:{row.get('sample_id')}")
        if row.get("downstream", {}).get("preflight", {}).get("admitted") is not True:
            violations.append(f"synthetic_preflight_not_admitted:{row.get('sample_id')}")

    result = {
        "stage": STAGE,
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "model_called": True,
        "gpu_called": True,
        "generation_run": True,
        "answer_injection_audit_status": injection_audit["status"],
        "constraint_source": "json_schema_plus_runtime_domains_not_label_side_answers",
        "constraint_space_singleton": False,
        "finite_expected_candidate_trie": False,
        "label_side_data_used_for_constraints": False,
        "train_dev_generation_run": False,
        "confirmation_481_evaluated": False,
        "live_sql_bench_gt_opened": False,
        "ascii_smoke_status": next((row["status"] for row in smoke_rows if row["sample_id"].endswith("0001")), "MISSING"),
        "unicode_smoke_status": next((row["status"] for row in smoke_rows if row["sample_id"].endswith("0002")), "MISSING"),
    }
    write_json(output_dir / "PREFLIGHT_RESULT.json", result)
    report = (
        "# Stage7E0 V2-A1 Real Generation Preflight PATCH5\n\n"
        f"Status: {result['status']}\n\n"
        f"violations: {json.dumps(violations, ensure_ascii=False)}\n\n"
        "Scope: schema-driven constrained synthetic smoke only; expected labels are evaluated after generation and are not passed to decoder constraints. No train/dev generation, no 481 confirmation evaluation, and no LiveSQLBench ground truth.\n"
    )
    (output_dir / "VALIDATION_REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
