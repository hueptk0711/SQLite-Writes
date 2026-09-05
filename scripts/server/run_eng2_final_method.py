#!/usr/bin/env python3
"""Canonical ENG2B final proposed-method runner.

M2_FINAL_ENG2B builds gold-blind column-specific runtime domains, generates
against the resulting dynamic schema, parses against the same schema object,
and uses the ENG2B stateful prefix grammar for non-OMIT SPAN uniqueness.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from nldbwrite_v3.v2_a1.compiler import compile_sqlite_program  # noqa: E402
from nldbwrite_v3.v2_a1.completeness import verify_completeness  # noqa: E402
from nldbwrite_v3.v2_a1.eng2b_runtime import build_eng2b_constraint_grammar, prepare_eng2b_runtime_row, schema_branches  # noqa: E402
from nldbwrite_v3.v2_a1.inventories import build_schema_inventory  # noqa: E402
from nldbwrite_v3.v2_a1.preflight import preflight_sqlite  # noqa: E402
from nldbwrite_v3.v2_a1.prompt_rendering import inventory_payload, serialize_prompt_object  # noqa: E402
from nldbwrite_v3.v2_a1.slot_inventory import build_slot_bundle  # noqa: E402
from nldbwrite_v3.v2_a1.typed_materializer import materialize_ir_values  # noqa: E402
from nldbwrite_v3.v2_a1.types import AcceptedSpan, V2A1Error  # noqa: E402


METHOD_ID = "M2_FINAL_ENG2B"
ENG2A_STAGE_NAME = "StageENG2A_GRETEL_EXTERNAL_DEVELOPMENT_PILOT"
MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"
MODEL_REVISION = "c03e6d358207e414f1eca0bb1891e29f1db0e242"
DEFAULT_MODEL_PATH = (
    "/home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/"
    f"snapshots/{MODEL_REVISION}"
)
EXPECTED_CHAT_TEMPLATE_SHA256 = "cd8e9439f0570856fd70470bf8889ebd8b5d1107207f67a5efb46e342330527f"
PHASE_O_MAX_NEW_TOKENS = 512
_FENCE = re.compile(
    r"```(?:sql|json)?[ \t]*\r?\n?(.*?)```",
    re.IGNORECASE | re.DOTALL,
)
PHASE_O_SYSTEM_PROMPT = (
    "You select one source span or OMIT for every SQLite INSERT column. "
    "Return only JSON that matches the provided schema."
)
PHASE_O_USER_PROMPT_TEMPLATE = """Select the literal source span for each target-table column in the INSERT request.

Rules:
- Choose exactly one SPAN reference or OMIT for every column in the selected table branch.
- Use each non-OMIT SPAN reference for at most one column.
- Use OMIT only when the request gives no literal value for that column.
- Choose the smallest complete atomic value span.
- The candidate span inventory has already removed deterministic schema-label/value distractors and omission-cue distractors.
- Do not select field labels, instruction text, table names, column names, or label-plus-value spans.
- For multi-table schemas, choose exactly one table_ref branch from the model-visible schema.
- Do not invent span refs.
- Do not output character offsets, raw values, SLOT refs, Phase M JSON, explanations, or markdown.

Original request:
{question}

Schema inventory:
{schema_inventory}

Candidate span inventory:
{candidate_inventory}
"""


@dataclass(slots=True)
class CallResult:
    sample_id: str
    phase: str
    raw_output: str
    status: str = "success"
    error: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_sec: float = 0.0
    hit_max_new_tokens: bool = False
    generation_metadata: dict[str, Any] | None = None


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return sha256(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")).hexdigest()


def extract_json_object(raw_output: str) -> tuple[dict[str, Any] | None, str | None]:
    candidates = [match.group(1).strip() for match in _FENCE.finditer(raw_output)]
    candidates.append(raw_output.strip())
    decoder = json.JSONDecoder()
    error: str | None = None
    for candidate in candidates:
        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError as exc:
                error = exc.msg
                continue
            if isinstance(value, dict):
                return value, None
    return None, error or "No JSON object found."


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def render_phase_o_messages(row: dict[str, Any]) -> tuple[list[dict[str, str]], str, str]:
    inventory = build_schema_inventory(row["model_side_input"]["schema_inventory"])
    user = PHASE_O_USER_PROMPT_TEMPLATE.format(
        question=row["model_side_input"]["question"],
        schema_inventory=serialize_prompt_object(inventory_payload(inventory)),
        candidate_inventory=row["model_side_input"]["candidate_inventory_text"],
    )
    messages = [{"role": "system", "content": PHASE_O_SYSTEM_PROMPT}, {"role": "user", "content": user}]
    return messages, user, sha256_text(canonical_json(messages))


def branch_for_table(schema: dict[str, Any], table_ref: str) -> dict[str, Any]:
    for branch in schema_branches(schema):
        if branch["table_ref"] == table_ref:
            return branch
    raise V2A1Error("phase_o_unknown_table_ref", "Unknown table_ref for ENG2B schema", details={"table_ref": table_ref})


def parse_phase_o_column_conditioned_output(raw: str, schema: dict[str, Any]) -> dict[str, Any]:
    obj, error = extract_json_object(raw)
    if obj is None:
        raise V2A1Error("phase_o_json_extract", error or "Could not extract JSON object")
    if set(obj) != {"operation", "table_ref", "column_span_refs"}:
        raise V2A1Error("phase_o_schema_failure", "ENG2B output must contain only operation, table_ref, column_span_refs")
    if obj["operation"] != "INSERT":
        raise V2A1Error("phase_o_schema_failure", "ENG2B operation must be INSERT")
    if not isinstance(obj.get("column_span_refs"), dict):
        raise V2A1Error("phase_o_schema_failure", "column_span_refs must be an object")
    branch = branch_for_table(schema, str(obj["table_ref"]))
    decisions = obj["column_span_refs"]
    if set(decisions) != set(branch["columns"]):
        raise V2A1Error("phase_o_schema_failure", "column_span_refs keys must exactly equal selected table required columns")
    ordered: dict[str, str] = {}
    for column in branch["columns"]:
        value = decisions[column]
        if not isinstance(value, str) or value not in branch["column_domains"][column]:
            raise V2A1Error("phase_o_schema_failure", "column decision must be OMIT or an exact current SPAN ref", details={"column_ref": column, "value": value})
        ordered[column] = value
    selected_refs = [span_ref for span_ref in ordered.values() if span_ref != "OMIT"]
    if len(selected_refs) != len(set(selected_refs)):
        raise V2A1Error("phase_o_duplicate_span_ref_reuse", "Each non-OMIT SPAN ref may be used at most once")
    return {"operation": "INSERT", "table_ref": branch["table_ref"], "column_span_refs": ordered}


def deterministic_ir_from_column_spans(
    model_side_input: dict[str, Any],
    runtime_constraints: dict[str, Any],
    phase_o_prediction: dict[str, Any],
) -> tuple[dict[str, Any], tuple[AcceptedSpan, ...], list[dict[str, Any]]]:
    column_decisions = phase_o_prediction["column_span_refs"]
    selected_table_ref_value = phase_o_prediction["table_ref"]
    omitted_required = [
        column
        for column in model_side_input["schema_inventory"]["columns"]
        if column["table_ref"] == selected_table_ref_value
        and column_decisions.get(column["column_ref"]) == "OMIT"
        and column.get("nullable") is False
        and column.get("has_default") is False
    ]
    if omitted_required:
        names = ", ".join(f"{column['column_ref']}:{column['column_name']}" for column in omitted_required)
        raise V2A1Error("required_column_omitted", f"required_column_omitted:{names}")
    selected_refs = [span_ref for _column_ref, span_ref in column_decisions.items() if span_ref != "OMIT"]
    if len(selected_refs) != len(set(selected_refs)):
        raise V2A1Error("duplicate_span_ref", "Duplicate span_refs are forbidden")
    by_ref = {candidate["span_ref"]: candidate for candidate in runtime_constraints["candidate_inventory"]}
    unknown = [span_ref for span_ref in selected_refs if span_ref not in by_ref]
    if unknown:
        raise V2A1Error("unknown_span_ref", f"Unknown span_refs: {unknown}")
    selected = [by_ref[span_ref] for span_ref in selected_refs]
    spans = tuple(AcceptedSpan(start_char=item["start_char"], end_char=item["end_char"], text=item["text"]) for item in selected)
    assignments = []
    resolved = []
    slot_index = 1
    selected_by_ref = {candidate["span_ref"]: candidate for candidate in selected}
    for column_ref_value, span_ref in column_decisions.items():
        if span_ref == "OMIT":
            continue
        candidate = selected_by_ref[span_ref]
        assignments.append({"slot_ref": f"SLOT_{slot_index}", "evidence_ref": f"EV_{slot_index}", "column_ref": column_ref_value})
        resolved.append(
            {
                "column_ref": column_ref_value,
                "candidate_span_ref": span_ref,
                "evidence_ref": f"EV_{slot_index}",
                "slot_ref": f"SLOT_{slot_index}",
                "start_char": candidate["start_char"],
                "end_char": candidate["end_char"],
                "text": candidate["text"],
            }
        )
        slot_index += 1
    return {"operation": "INSERT", "table_ref": selected_table_ref_value, "assignments": assignments}, spans, resolved


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def read_rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(f"SELECT * FROM {quote_ident(table)} ORDER BY rowid").fetchall()
    return [dict(row) for row in rows]


def compile_column_conditioned_prediction(
    *,
    sample_id: str,
    model_side_input: dict[str, Any],
    runtime_constraints: dict[str, Any],
    phase_o_prediction: dict[str, Any],
    db_path: Path,
) -> dict[str, Any]:
    ir, spans, resolved = deterministic_ir_from_column_spans(model_side_input, runtime_constraints, phase_o_prediction)
    slots = build_slot_bundle(spans)
    inventory = build_schema_inventory(model_side_input["schema_inventory"])
    materialized = materialize_ir_values(ir, inventory, slots)
    verify_completeness(ir, slots)
    program = compile_sqlite_program(ir, inventory, materialized)
    preflight = preflight_sqlite(db_path, program)
    return {
        "sample_id": sample_id,
        "phase_o_operation_exact": phase_o_prediction["operation"] == "INSERT",
        "phase_o_output_keys_exact": sorted(phase_o_prediction) == ["column_span_refs", "operation", "table_ref"],
        "phase_m_model_call_removed": True,
        "model_generated_slot_refs": False,
        "model_generated_phase_m": False,
        "selected_table_ref": phase_o_prediction["table_ref"],
        "selected_span_ref_count": len(spans),
        "omit_decision_count": sum(1 for value in phase_o_prediction["column_span_refs"].values() if value == "OMIT"),
        "dynamic_schema_exact": True,
        "resolver": "PASS",
        "deterministic_ir": ir,
        "slot_ev_coherence": "PASS",
        "typed_materialization": "PASS",
        "completeness": "PASS",
        "compilation": "PASS",
        "compiled_sql": program.sql,
        "compiled_parameters": list(program.parameters),
        "preflight": "ADMITTED" if preflight.admitted else "REJECTED",
        "preflight_reason_code": preflight.reason_code,
        "resolved_column_spans": resolved,
    }


def user_tables(con: sqlite3.Connection) -> list[str]:
    return [str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def table_rows(con: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in con.execute(f"SELECT * FROM {quote_ident(table)} ORDER BY rowid").fetchall()]


def state(con: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    con.row_factory = sqlite3.Row
    return {table: table_rows(con, table) for table in user_tables(con)}


def row_counter(rows: list[dict[str, Any]]) -> Counter[str]:
    return Counter(canonical_json(row) for row in rows)


def state_delta(before: dict[str, list[dict[str, Any]]], after: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Counter[str]]]:
    output: dict[str, dict[str, Counter[str]]] = {}
    for table in sorted(set(before) | set(after)):
        b = row_counter(before.get(table, []))
        a = row_counter(after.get(table, []))
        output[table] = {"+": +(a - b), "-": +(b - a)}
    return output


def off_target_extra_tables(gold_delta: dict[str, dict[str, Counter[str]]], pred_delta: dict[str, dict[str, Counter[str]]]) -> list[str]:
    tables = []
    for table in sorted(set(gold_delta) | set(pred_delta)):
        for sign in ("+", "-"):
            pred = pred_delta.get(table, {}).get(sign, Counter())
            gold = gold_delta.get(table, {}).get(sign, Counter())
            if any(count > gold.get(row_key, 0) for row_key, count in pred.items()):
                tables.append(table)
                break
    return tables


def memory_copy(path: Path) -> sqlite3.Connection:
    source = sqlite3.connect(path)
    target = sqlite3.connect(":memory:")
    source.backup(target)
    source.close()
    target.execute("PRAGMA foreign_keys=ON")
    return target


def execute_statements(con: sqlite3.Connection, statements: list[str], params: tuple[Any, ...] | None = None) -> dict[str, Any]:
    try:
        con.execute("SAVEPOINT eng2b_write")
        if params is not None:
            if len(statements) != 1:
                raise sqlite3.Error("parameterized execution expects one statement")
            con.execute(statements[0], params)
            count = 1
        else:
            count = 0
            for statement in statements:
                con.execute(statement)
                count += 1
        con.execute("RELEASE eng2b_write")
        return {"status": "success", "executed_statements": count, "error": None}
    except sqlite3.Error as exc:
        try:
            con.execute("ROLLBACK TO eng2b_write")
            con.execute("RELEASE eng2b_write")
        except sqlite3.Error:
            con.rollback()
        return {"status": "execution_error", "executed_statements": 0, "error": str(exc)}


def evaluate_sql(
    row: dict[str, Any],
    stage_dir: Path,
    statements: list[str],
    *,
    params: tuple[Any, ...] | None = None,
    parse_status: str = "success",
    build_status: str = "success",
    preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    db_path = stage_dir / row["synthetic_db_spec"]["sqlite_db_path"]
    gold_sql = list(row["evaluator_side_expected"]["gold_sql"])
    target_table = str(row["evaluator_side_expected"]["gold_target_table"])
    gold_conn = memory_copy(db_path)
    pred_conn = memory_copy(db_path)
    try:
        before = state(gold_conn)
        gold_execution = execute_statements(gold_conn, gold_sql)
        pred_execution = {"status": "not_run", "executed_statements": 0, "error": None}
        if parse_status == "success" and build_status == "success" and not (preflight and not preflight.get("accepted", True)):
            pred_execution = execute_statements(pred_conn, statements, params=params)
        gold_after = state(gold_conn)
        pred_after = state(pred_conn)
        target_state_correct = pred_after.get(target_table, []) == gold_after.get(target_table, [])
        strict_full_state_correct = pred_after == gold_after
        off_tables = []
        execution_success = gold_execution["status"] == "success" and pred_execution["status"] == "success"
        if execution_success:
            off_tables = off_target_extra_tables(state_delta(before, gold_after), state_delta(before, pred_after))
        error_type = None
        if gold_execution["status"] != "success":
            error_type = "gold_sql_error"
        elif parse_status != "success":
            error_type = "parse_error"
        elif build_status != "success":
            error_type = "builder_error"
        elif preflight and not preflight.get("accepted", True):
            error_type = "preflight_abstention"
        elif pred_execution["status"] != "success":
            error_type = pred_execution["status"]
        elif not target_state_correct and off_tables:
            error_type = "wrong_state_with_off_target_change"
        elif not target_state_correct:
            error_type = "wrong_state"
        elif off_tables:
            error_type = "unintended_side_effect"
        return {
            "gold_execution": gold_execution,
            "prediction_execution": pred_execution,
            "execution_success": execution_success,
            "target_state_correct": target_state_correct,
            "strict_full_state_correct": strict_full_state_correct,
            "any_off_target_change": bool(off_tables),
            "off_target_mismatched_tables": off_tables,
            "target_mismatched_tables": [] if target_state_correct else [target_table],
            "state_comparison_scope": "all_persistent_user_tables_delta",
            "error_type": error_type,
        }
    finally:
        gold_conn.close()
        pred_conn.close()


def failure_stage_from_v2a1_error(exc: V2A1Error) -> str:
    code = exc.reason_code
    details = exc.details or {}
    semantic = str(details.get("semantic_materialization_type") or details.get("reason") or "").upper()
    if code.startswith("phase_o"):
        return "PHASE_O_PARSE_FAILURE"
    if code in {"duplicate_span_ref", "phase_o_duplicate_span_ref_reuse"} or code.startswith("completeness_duplicate"):
        return "DUPLICATE_SPAN"
    if code == "materialization_failure":
        if semantic == "INTEGER":
            return "MATERIALIZATION_INTEGER_FAILURE"
        if semantic == "REAL":
            return "MATERIALIZATION_REAL_FAILURE"
        if semantic == "DATE":
            return "MATERIALIZATION_DATE_FAILURE"
        if semantic in {"DATETIME", "TIMESTAMP"}:
            return "MATERIALIZATION_TIMESTAMP_FAILURE"
        return "TYPED_MATERIALIZATION_FAILURE"
    if code.startswith("completeness"):
        return "COMPLETENESS_REJECT"
    if "preflight" in code:
        return "PREFLIGHT_FAILURE"
    return "V2A1_ERROR"


def live_runtime_freeze(*, model_name_or_path: str = DEFAULT_MODEL_PATH, max_input_tokens: int = 24576, seed: int = 20260904, phase_o_max_new_tokens: int = PHASE_O_MAX_NEW_TOKENS) -> dict[str, Any]:
    return {
        "method_id": METHOD_ID,
        "mode": "live",
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "model_name_or_path": model_name_or_path,
        "tokenizer_id": MODEL_ID,
        "tokenizer_revision": MODEL_REVISION,
        "expected_chat_template_sha256": EXPECTED_CHAT_TEMPLATE_SHA256,
        "identity_fail_closed": {
            "model_snapshot_basename_must_equal_revision": MODEL_REVISION,
            "tokenizer_snapshot_basename_must_equal_revision": MODEL_REVISION,
            "chat_template_sha256_must_equal": EXPECTED_CHAT_TEMPLATE_SHA256,
        },
        "generation_settings": {
            "max_input_tokens": max_input_tokens,
            "phase_o_max_new_tokens": phase_o_max_new_tokens,
            "do_sample": False,
            "temperature": None,
            "top_p": None,
            "top_k": None,
            "retry": 0,
            "repair": "none",
            "calls_per_sample": 1,
            "seed": seed,
        },
    }


def verify_live_model_identity(*, model_name_or_path: str, tokenizer_name_or_path: str, chat_template_sha256: str) -> dict[str, Any]:
    model_path = Path(model_name_or_path)
    tokenizer_path = Path(tokenizer_name_or_path)
    model_revision_observed = model_path.name if model_path.exists() else MODEL_REVISION
    tokenizer_revision_observed = tokenizer_path.name if tokenizer_path.exists() else MODEL_REVISION
    if model_revision_observed != MODEL_REVISION:
        raise V2A1Error(
            "model_revision_mismatch",
            "STOP: ENG2B live model snapshot revision does not match the frozen revision",
            details={"expected": MODEL_REVISION, "observed": model_revision_observed, "model_name_or_path": model_name_or_path},
        )
    if tokenizer_revision_observed != MODEL_REVISION:
        raise V2A1Error(
            "tokenizer_revision_mismatch",
            "STOP: ENG2B live tokenizer snapshot revision does not match the frozen revision",
            details={"expected": MODEL_REVISION, "observed": tokenizer_revision_observed, "tokenizer_name_or_path": tokenizer_name_or_path},
        )
    if chat_template_sha256 != EXPECTED_CHAT_TEMPLATE_SHA256:
        raise V2A1Error(
            "chat_template_hash_mismatch",
            "STOP: ENG2B live tokenizer chat template hash does not match the frozen hash",
            details={"expected": EXPECTED_CHAT_TEMPLATE_SHA256, "observed": chat_template_sha256},
        )
    return {
        "model_revision_verified": True,
        "tokenizer_revision_verified": True,
        "chat_template_hash_verified": True,
    }


def generate_constrained_eng2b(model: Any, tokenizer: Any, messages: list[dict[str, str]], *, max_new_tokens: int, schema: dict[str, Any]) -> dict[str, Any]:
    import torch
    from scripts.server.run_stage7e0_v2_a1_preflight import IncrementalJsonSchemaGrammarBackend

    constraint_grammar = build_eng2b_constraint_grammar(schema)
    rendered = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(rendered, return_tensors="pt")
    device = next(model.parameters()).device
    inputs = {key: value.to(device) for key, value in inputs.items()}
    prompt_tokens = int(inputs["input_ids"].shape[-1])
    backend = IncrementalJsonSchemaGrammarBackend(tokenizer, constraint_grammar, eos_token_id=tokenizer.eos_token_id)
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
    generated_ids = output[0][prompt_tokens:]
    return {
        "backend": backend.metadata(),
        "prompt_tokens": prompt_tokens,
        "output_tokens": int(generated_ids.shape[-1]),
        "latency_seconds": time.monotonic() - start,
        "hit_max_new_tokens": int(generated_ids.shape[-1]) >= max_new_tokens,
        "raw_output": tokenizer.decode(generated_ids, skip_special_tokens=True).strip(),
    }


class Eng2BConstrainedTransformersChatGenerator:
    def __init__(self, *, model_name_or_path: str, trust_remote_code: bool, max_input_tokens: int, seed: int):
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:  # pragma: no cover
            raise SystemExit("STOP: ENG2B live generation requires torch and transformers") from exc
        self.torch = torch
        self.seed = seed
        self.max_input_tokens = max_input_tokens
        kwargs = {"trust_remote_code": trust_remote_code}
        if not Path(model_name_or_path).exists():
            kwargs["revision"] = MODEL_REVISION
        self.tokenizer = AutoTokenizer.from_pretrained(model_name_or_path, **kwargs)
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.tokenizer.padding_side = "left"
        self.chat_template_sha256 = sha256_text(str(getattr(self.tokenizer, "chat_template", "") or ""))
        self.identity_verification = verify_live_model_identity(
            model_name_or_path=model_name_or_path,
            tokenizer_name_or_path=model_name_or_path,
            chat_template_sha256=self.chat_template_sha256,
        )
        model_kwargs = {"trust_remote_code": trust_remote_code, "device_map": "auto", "torch_dtype": "auto"}
        if not Path(model_name_or_path).exists():
            model_kwargs["revision"] = MODEL_REVISION
        self.model = AutoModelForCausalLM.from_pretrained(model_name_or_path, **model_kwargs)
        self.model.eval()
        self.model_name_or_path = model_name_or_path

    def generate(self, *, sample_id: str, messages: list[dict[str, str]], max_new_tokens: int, row: dict[str, Any]) -> CallResult:
        torch = self.torch
        prompt = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        token_count = len(self.tokenizer(prompt, add_special_tokens=True, truncation=False)["input_ids"])
        if token_count > self.max_input_tokens:
            return CallResult(sample_id=sample_id, phase="phase_o", raw_output="", status="input_too_long", error=f"{token_count}>{self.max_input_tokens}", input_tokens=token_count)
        torch.manual_seed(self.seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(self.seed)
        generated = generate_constrained_eng2b(self.model, self.tokenizer, messages, max_new_tokens=max_new_tokens, schema=row["runtime_constraints"]["phase_o_schema"])
        return CallResult(
            sample_id=sample_id,
            phase="phase_o",
            raw_output=str(generated["raw_output"]),
            input_tokens=int(generated["prompt_tokens"]),
            output_tokens=int(generated["output_tokens"]),
            latency_sec=float(generated["latency_seconds"]),
            hit_max_new_tokens=bool(generated["hit_max_new_tokens"]),
            generation_metadata=generated["backend"] | {"method_id": METHOD_ID, "runtime_freeze": self.metadata()},
        )

    def metadata(self) -> dict[str, Any]:
        return live_runtime_freeze(model_name_or_path=self.model_name_or_path, max_input_tokens=self.max_input_tokens, seed=self.seed) | {
            "backend": "transformers_hf_constrained_eng2b",
            "model_called": True,
            "chat_template_sha256": self.chat_template_sha256,
            **self.identity_verification,
            "torch_version": self.torch.__version__,
            "cuda_available": bool(self.torch.cuda.is_available()),
        }


class ReplayGenerator:
    def __init__(self, raw_by_sample_id: dict[str, str]):
        self.raw_by_sample_id = raw_by_sample_id

    def generate(self, *, sample_id: str, messages: list[dict[str, str]], max_new_tokens: int, row: dict[str, Any]) -> CallResult:
        del messages, max_new_tokens, row
        raw = self.raw_by_sample_id[sample_id]
        return CallResult(sample_id=sample_id, phase="phase_o", raw_output=raw, input_tokens=0, output_tokens=len(raw.split()), generation_metadata={"backend": "replay", "method_id": METHOD_ID, "model_called": False})

    def metadata(self) -> dict[str, Any]:
        return {"backend": "replay", "method_id": METHOD_ID, "model_called": False}


def evaluate_final_method(row: dict[str, Any], stage_dir: Path, generator: Any, *, phase_o_max_new_tokens: int = PHASE_O_MAX_NEW_TOKENS) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    runtime_row, runtime_contract = prepare_eng2b_runtime_row(row)
    messages, _user, prompt_hash = render_phase_o_messages(runtime_row)
    call = generator.generate(sample_id=runtime_row["sample_id"], messages=messages, max_new_tokens=phase_o_max_new_tokens, row=runtime_row)
    raw_o = asdict(call) | {"messages_sha256": prompt_hash, "prompt_hash": prompt_hash, "runtime_contract": runtime_contract}
    parsed: dict[str, Any] = {"sample_id": runtime_row["sample_id"], "method_id": METHOD_ID, "runtime_contract": runtime_contract}
    if call.status != "success":
        parsed.update({"parse_status": "generation_error", "diagnostics": [call.error]})
        evaluation = evaluate_sql(runtime_row, stage_dir, [], parse_status="parse_error")
        return parsed, evaluation, raw_o
    try:
        schema = runtime_row["runtime_constraints"]["phase_o_schema"]
        phase_o = parse_phase_o_column_conditioned_output(call.raw_output, schema)
        parsed.update({"parse_status": "success", "phase_o": phase_o})
        compiled = compile_column_conditioned_prediction(
            sample_id=runtime_row["sample_id"],
            model_side_input=runtime_row["model_side_input"],
            runtime_constraints=runtime_row["runtime_constraints"],
            phase_o_prediction=phase_o,
            db_path=stage_dir / runtime_row["synthetic_db_spec"]["sqlite_db_path"],
        )
        parsed["compiled_prediction"] = compiled
        preflight = {"accepted": compiled["preflight"] == "ADMITTED", "error": compiled.get("preflight_reason_code")}
        evaluation = evaluate_sql(runtime_row, stage_dir, [compiled["compiled_sql"]], params=tuple(compiled["compiled_parameters"]), preflight=preflight)
        return parsed, evaluation, raw_o
    except V2A1Error as exc:
        failure_stage = failure_stage_from_v2a1_error(exc)
        parsed.update(
            {
                "parse_status": "parse_error" if failure_stage == "PHASE_O_PARSE_FAILURE" else "success",
                "failure_stage": failure_stage,
                "diagnostics": [{"reason_code": exc.reason_code, "message": str(exc), "details": exc.details}],
            }
        )
        evaluation = evaluate_sql(runtime_row, stage_dir, [], parse_status="parse_error" if failure_stage == "PHASE_O_PARSE_FAILURE" else "success", build_status="builder_error")
        evaluation["error_type"] = failure_stage
        return parsed, evaluation, raw_o
    except sqlite3.Error as exc:
        parsed.update({"parse_status": "success", "failure_stage": "EXECUTION_FAILURE", "diagnostics": [str(exc)]})
        evaluation = evaluate_sql(runtime_row, stage_dir, [], build_status="builder_error")
        evaluation["error_type"] = "EXECUTION_FAILURE"
        return parsed, evaluation, raw_o


def run_rows(rows: list[dict[str, Any]], stage_dir: Path, generator: Any, *, phase_o_max_new_tokens: int) -> list[dict[str, Any]]:
    results = []
    for row in rows:
        parsed, evaluation, raw_o = evaluate_final_method(row, stage_dir, generator, phase_o_max_new_tokens=phase_o_max_new_tokens)
        results.append({"sample_id": row["sample_id"], "parsed": parsed, "evaluation": evaluation, "raw": raw_o})
    return results


def write_run_outputs(output_dir: Path, results: list[dict[str, Any]], generator: Any, mode: str) -> None:
    write_jsonl(output_dir / "raw" / "model_outputs.jsonl", [row["raw"] for row in results])
    write_jsonl(output_dir / "parsed" / "phase_o_outputs.jsonl", [row["parsed"] for row in results])
    write_jsonl(output_dir / "results" / "per_sample_results.jsonl", [row["evaluation"] | {"sample_id": row["sample_id"], "method_id": METHOD_ID} for row in results])
    write_json(
        output_dir / "results" / "summary.json",
        {
            "method_id": METHOD_ID,
            "mode": mode,
            "generator_metadata": generator.metadata(),
            "rows": len(results),
            "model_calls_new": len(results) if mode == "live" else 0,
            "target_state_correct": sum(1 for row in results if row["evaluation"].get("target_state_correct")),
        },
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["replay", "live"], default="replay")
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / ENG2A_STAGE_NAME)
    parser.add_argument("--rows", type=Path, default=PROJECT_ROOT / ENG2A_STAGE_NAME / "ENG2A_PILOT_100_FREEZE.jsonl")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--replay-raw", type=Path, help="JSONL containing sample_id and raw_output for deterministic no-model replay.")
    parser.add_argument("--model-name-or-path", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument("--max-input-tokens", type=int, default=24576)
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--phase-o-max-new-tokens", type=int, default=PHASE_O_MAX_NEW_TOKENS)
    parser.add_argument("--dry-run-live-config", action="store_true", help="Print frozen live runtime configuration without loading a model.")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    if args.mode == "live" and args.dry_run_live_config:
        print(json.dumps(live_runtime_freeze(model_name_or_path=args.model_name_or_path, max_input_tokens=args.max_input_tokens, seed=args.seed, phase_o_max_new_tokens=args.phase_o_max_new_tokens), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    rows = read_jsonl(args.rows)
    if args.mode == "replay":
        if args.replay_raw is None:
            parser.error("--replay-raw is required when --mode replay")
        raw_rows = read_jsonl(args.replay_raw)
        raw_by_id = {str(row["sample_id"]): str(row["raw_output"]) for row in raw_rows}
        generator: Any = ReplayGenerator(raw_by_id)
    else:
        generator = Eng2BConstrainedTransformersChatGenerator(
            model_name_or_path=args.model_name_or_path,
            trust_remote_code=args.trust_remote_code,
            max_input_tokens=args.max_input_tokens,
            seed=args.seed,
        )
    results = run_rows(rows, args.stage_dir, generator, phase_o_max_new_tokens=args.phase_o_max_new_tokens)
    summary = {"method_id": METHOD_ID, "mode": args.mode, "model_calls_new": len(results) if args.mode == "live" else 0, "results": results}
    if args.output_dir is not None:
        write_run_outputs(args.output_dir, results, generator, args.mode)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
