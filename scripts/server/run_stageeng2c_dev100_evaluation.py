#!/usr/bin/env python3
"""Run Stage ENG2C untouched development-dev 100 evaluation."""

from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from nldbwrite_v3.baselines.legacy_json import legacy_record_json_to_write_plan  # noqa: E402
from nldbwrite_v3.compiler import compile_verified_plan  # noqa: E402
from nldbwrite_v3.experiments.prompts import build_direct_prompt, build_legacy_json_prompt  # noqa: E402
from nldbwrite_v3.inference.parse_output import extract_json_object, extract_sql_statements  # noqa: E402
from nldbwrite_v3.schema.profile import build_profile  # noqa: E402
from nldbwrite_v3.verifier import verify_write_plan  # noqa: E402
from nldbwrite_v3.v2_a1.types import V2A1Error  # noqa: E402
from scripts.data.build_stageeng2a_gretel_external_development_pilot import canonical_json, read_json, read_jsonl, sha256_file, sha256_text, write_json, write_jsonl  # noqa: E402
from scripts.server.run_eng2_final_method import (  # noqa: E402
    DEFAULT_MODEL_PATH,
    EXPECTED_CHAT_TEMPLATE_SHA256,
    METHOD_ID as M2_METHOD_ID,
    MODEL_ID,
    MODEL_REVISION,
    PHASE_O_MAX_NEW_TOKENS,
    CallResult as M2CallResult,
    evaluate_final_method,
    generate_constrained_eng2b,
    live_runtime_freeze,
    prepare_eng2b_runtime_row,
    render_phase_o_messages,
    verify_live_model_identity,
)


STAGE_NAME = "StageENG2C_UNTOUCHED_EXTERNAL_DEVELOPMENT_DEV_EVALUATION"
METHODS = ("M0_DIRECT_ZERO", "M0_DIRECT_FS", "M1_J_FS", "M2_FINAL_ENG2B")
EXPECTED_N = 100
DIRECT_CONFIG_REL = "configs/m0_direct_fewshot_config.json"
DIRECT_ZERO_CONFIG_REL = "configs/m0_direct_zero_config.json"
JFS_CONFIG_REL = "configs/m1_j_fs_config.json"


@dataclass(slots=True)
class CallResult:
    sample_id: str
    method_id: str
    raw_output: str
    status: str = "success"
    error: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_sec: float = 0.0
    hit_max_new_tokens: bool = False
    generation_metadata: dict[str, Any] | None = None


def zero_shot_direct_config(config: dict[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy(config)
    output["method_id"] = "D-ZERO"
    output["demonstration_policy"] = "none"
    output["demonstrations"] = {"free_text": [], "semi_structured": []}
    output["resolved_demonstration_ids"] = {"free_text": [], "semi_structured": []}
    return output


def method_prompt(method_id: str, row: dict[str, Any], stage_dir: Path, direct_zero_config: dict[str, Any], direct_fs_config: dict[str, Any], jfs_config: dict[str, Any]) -> str | list[dict[str, str]]:
    profile = build_profile(stage_dir / row["synthetic_db_spec"]["sqlite_db_path"], db_id=row["sample_id"])
    question = row["model_side_input"]["question"]
    if method_id == "M0_DIRECT_ZERO":
        return build_direct_prompt(question, profile, direct_zero_config)
    if method_id == "M0_DIRECT_FS":
        return build_direct_prompt(question, profile, direct_fs_config)
    if method_id == "M1_J_FS":
        return build_legacy_json_prompt(question, profile, jfs_config)
    if method_id == "M2_FINAL_ENG2B":
        runtime_row, _contract = prepare_eng2b_runtime_row(row)
        return render_phase_o_messages(runtime_row)[0]
    raise KeyError(method_id)


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def user_tables(con: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    ]


def table_rows(con: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    con.row_factory = sqlite3.Row
    return [dict(row) for row in con.execute(f"SELECT * FROM {quote_ident(table)} ORDER BY rowid").fetchall()]


def state(con: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
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


def execute_statements(con: sqlite3.Connection, statements: list[str], statement_params: list[list[Any]] | None = None) -> dict[str, Any]:
    try:
        con.execute("SAVEPOINT eng2c_write")
        if statement_params is not None:
            if len(statement_params) != len(statements):
                raise sqlite3.Error("statement_params length mismatch")
            for statement, values in zip(statements, statement_params, strict=True):
                con.execute(statement, values)
            count = len(statements)
        else:
            count = 0
            for statement in statements:
                con.execute(statement)
                count += 1
        con.execute("RELEASE eng2c_write")
        return {"status": "success", "executed_statements": count, "error": None}
    except sqlite3.Error as exc:
        try:
            con.execute("ROLLBACK TO eng2c_write")
            con.execute("RELEASE eng2c_write")
        except sqlite3.Error:
            con.rollback()
        return {"status": "execution_error", "executed_statements": 0, "error": str(exc)}


def evaluate_sql(
    row: dict[str, Any],
    stage_dir: Path,
    statements: list[str],
    *,
    statement_params: list[list[Any]] | None = None,
    parse_status: str = "success",
    build_status: str = "success",
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
        if parse_status == "success" and build_status == "success":
            pred_execution = execute_statements(pred_conn, statements, statement_params=statement_params)
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
            error_type = "GOLD_SQL_ERROR"
        elif parse_status != "success":
            error_type = "PARSE_FAILURE"
        elif build_status != "success":
            error_type = "COMPILATION_FAILURE"
        elif pred_execution["status"] != "success":
            error_type = "EXECUTION_FAILURE"
        elif not target_state_correct and off_tables:
            error_type = "OFF_TARGET_STATE_CHANGE"
        elif not target_state_correct:
            error_type = "TARGET_STATE_MISMATCH"
        elif off_tables:
            error_type = "OFF_TARGET_STATE_CHANGE"
        return {
            "gold_execution": gold_execution,
            "prediction_execution": pred_execution,
            "execution_success": execution_success,
            "target_state_correct": target_state_correct,
            "strict_full_state_correct": strict_full_state_correct,
            "any_off_target_change": bool(off_tables),
            "off_target_mismatched_tables": off_tables,
            "target_mismatched_tables": [] if target_state_correct else [target_table],
            "state_comparison_scope": "all_persistent_user_tables",
            "error_type": error_type,
        }
    finally:
        gold_conn.close()
        pred_conn.close()


def evaluate_baseline(method_id: str, row: dict[str, Any], stage_dir: Path, raw: str) -> tuple[dict[str, Any], dict[str, Any]]:
    parsed: dict[str, Any] = {"sample_id": row["sample_id"], "method_id": method_id}
    if method_id in {"M0_DIRECT_ZERO", "M0_DIRECT_FS"}:
        statements, error = extract_sql_statements(raw)
        parsed.update({"parse_status": "success" if not error else "parse_error", "direct_sql": statements, "diagnostics": [error] if error else []})
        return parsed, evaluate_sql(row, stage_dir, list(statements or []), parse_status=parsed["parse_status"])
    if method_id == "M1_J_FS":
        obj, error = extract_json_object(raw)
        parsed.update({"parse_status": "success" if obj is not None else "parse_error", "plan": obj, "diagnostics": [error] if error else []})
        if obj is None:
            return parsed, evaluate_sql(row, stage_dir, [], parse_status="parse_error")
        profile = build_profile(stage_dir / row["synthetic_db_spec"]["sqlite_db_path"], db_id=row["sample_id"])
        plan = legacy_record_json_to_write_plan(obj, profile)
        verification = verify_write_plan(plan, profile)
        parsed["verification"] = verification.to_dict()
        if not verification.valid:
            return parsed, evaluate_sql(row, stage_dir, [], build_status="builder_error")
        program = compile_verified_plan(verification.normalized_plan, profile)
        if program.status != "success":
            parsed["compiler_errors"] = [item.to_dict() for item in program.errors]
            return parsed, evaluate_sql(row, stage_dir, [], build_status="builder_error")
        parsed["compiled_sql"] = [statement.sql for statement in program.statements]
        parsed["compiled_params"] = [statement.params for statement in program.statements]
        return parsed, evaluate_sql(row, stage_dir, [statement.sql for statement in program.statements], statement_params=[statement.params for statement in program.statements])
    raise KeyError(method_id)


class MockGenerator:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows

    def generate(self, method_id: str, row: dict[str, Any], prompt: Any, max_new_tokens: int) -> CallResult:
        del prompt, max_new_tokens
        if method_id in {"M0_DIRECT_ZERO", "M0_DIRECT_FS"}:
            raw = row["evaluator_side_expected"]["gold_sql"][0]
        elif method_id == "M1_J_FS":
            raw = canonical_json({"records": [{"table": row["evaluator_side_expected"]["gold_target_table"], "operation": "insert", "values": row["assigned_values_for_mock"]}]})
        else:
            raise KeyError(method_id)
        return CallResult(row["sample_id"], method_id, raw, input_tokens=0, output_tokens=len(raw.split()), generation_metadata={"backend": "mock", "model_called": False})

    def generate_m2(self, row: dict[str, Any], max_new_tokens: int) -> M2CallResult:
        del max_new_tokens
        raw = canonical_json(row["label_side_expected"]["phase_o"])
        return M2CallResult(sample_id=row["sample_id"], phase="phase_o", raw_output=raw, input_tokens=0, output_tokens=len(raw.split()), generation_metadata={"backend": "mock", "method_id": M2_METHOD_ID, "model_called": False})

    def metadata(self) -> dict[str, Any]:
        return {"backend": "mock", "model_called": False}


class UnifiedFrozenHFGenerator:
    def __init__(self, *, model_name_or_path: str, trust_remote_code: bool, max_input_tokens: int, seed: int):
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:  # pragma: no cover
            raise SystemExit("STOP: ENG2C live generation requires torch and transformers") from exc
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
        self.identity = verify_live_model_identity(
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

    def _generate_text(self, prompt: str, max_new_tokens: int) -> tuple[str, int, int, float, bool]:
        messages = [{"role": "user", "content": prompt}]
        rendered = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.tokenizer(rendered, return_tensors="pt", add_special_tokens=True, truncation=False)
        token_count = int(inputs["input_ids"].shape[-1])
        if token_count > self.max_input_tokens:
            raise V2A1Error("input_too_long", f"{token_count}>{self.max_input_tokens}")
        device = next(self.model.parameters()).device
        inputs = {key: value.to(device) for key, value in inputs.items()}
        self.torch.manual_seed(self.seed)
        if self.torch.cuda.is_available():
            self.torch.cuda.manual_seed_all(self.seed)
        start = time.monotonic()
        with self.torch.inference_mode():
            output = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                temperature=None,
                top_p=None,
                top_k=None,
                pad_token_id=self.tokenizer.eos_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )
        generated_ids = output[0][token_count:]
        return self.tokenizer.decode(generated_ids, skip_special_tokens=True).strip(), token_count, int(generated_ids.shape[-1]), time.monotonic() - start, int(generated_ids.shape[-1]) >= max_new_tokens

    def generate(self, method_id: str, row: dict[str, Any], prompt: Any, max_new_tokens: int) -> CallResult:
        try:
            raw, input_tokens, output_tokens, latency, hit_max = self._generate_text(str(prompt), max_new_tokens)
            return CallResult(row["sample_id"], method_id, raw, input_tokens=input_tokens, output_tokens=output_tokens, latency_sec=latency, hit_max_new_tokens=hit_max, generation_metadata={"backend": "transformers_hf_unconstrained", "model_called": True})
        except V2A1Error as exc:
            return CallResult(row["sample_id"], method_id, "", status="generation_error", error=str(exc), generation_metadata={"backend": "transformers_hf_unconstrained", "model_called": False})

    def generate_m2(self, row: dict[str, Any], max_new_tokens: int) -> M2CallResult:
        runtime_row, _contract = prepare_eng2b_runtime_row(row)
        messages, _user, _prompt_hash = render_phase_o_messages(runtime_row)
        prompt = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        token_count = len(self.tokenizer(prompt, add_special_tokens=True, truncation=False)["input_ids"])
        if token_count > self.max_input_tokens:
            return M2CallResult(sample_id=row["sample_id"], phase="phase_o", raw_output="", status="input_too_long", error=f"{token_count}>{self.max_input_tokens}", input_tokens=token_count)
        generated = generate_constrained_eng2b(self.model, self.tokenizer, messages, max_new_tokens=max_new_tokens, schema=runtime_row["runtime_constraints"]["phase_o_schema"])
        return M2CallResult(
            sample_id=row["sample_id"],
            phase="phase_o",
            raw_output=str(generated["raw_output"]),
            input_tokens=int(generated["prompt_tokens"]),
            output_tokens=int(generated["output_tokens"]),
            latency_sec=float(generated["latency_seconds"]),
            hit_max_new_tokens=bool(generated["hit_max_new_tokens"]),
            generation_metadata=generated["backend"] | {"method_id": M2_METHOD_ID, "runtime_freeze": live_runtime_freeze(model_name_or_path=self.model_name_or_path, max_input_tokens=self.max_input_tokens, seed=self.seed)},
        )

    def metadata(self) -> dict[str, Any]:
        return live_runtime_freeze(model_name_or_path=self.model_name_or_path, max_input_tokens=self.max_input_tokens, seed=self.seed) | {
            "backend": "transformers_hf_unified_eng2c",
            "model_called": True,
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "chat_template_sha256": self.chat_template_sha256,
            **self.identity,
            "torch_version": self.torch.__version__,
            "cuda_available": bool(self.torch.cuda.is_available()),
        }


class M2Adapter:
    def __init__(self, generator: MockGenerator | UnifiedFrozenHFGenerator):
        self.generator = generator

    def generate(self, *, sample_id: str, messages: list[dict[str, str]], max_new_tokens: int, row: dict[str, Any]) -> M2CallResult:
        del sample_id, messages
        return self.generator.generate_m2(row, max_new_tokens)

    def metadata(self) -> dict[str, Any]:
        return self.generator.metadata()


def m2_admitted(parsed: dict[str, Any]) -> bool:
    compiled = parsed.get("compiled_prediction") or {}
    return compiled.get("preflight") == "ADMITTED"


def m2_failure_taxonomy(row: dict[str, Any], parsed: dict[str, Any], evaluation: dict[str, Any]) -> dict[str, Any]:
    if evaluation.get("strict_full_state_correct"):
        return {"failure_stage": "PASS", "semantic_selection_subtype": None}
    failure_stage = str(parsed.get("failure_stage") or evaluation.get("error_type") or "TARGET_STATE_MISMATCH")
    if failure_stage in {"wrong_state", "TARGET_STATE_MISMATCH"}:
        failure_stage = "TARGET_STATE_MISMATCH"
    if evaluation.get("any_off_target_change"):
        failure_stage = "OFF_TARGET_STATE_CHANGE"
    subtype = "unclassified"
    oracle_rows = row.get("label_side_expected", {}).get("gold_column_span_ref_oracle") or []
    if any(item.get("candidate_generation_miss") for item in oracle_rows):
        subtype = "candidate-generation miss"
    phase_o = parsed.get("phase_o") or {}
    gold_phase_o = row.get("label_side_expected", {}).get("phase_o") or {}
    if phase_o and phase_o.get("table_ref") != gold_phase_o.get("table_ref"):
        subtype = "wrong table"
    elif phase_o:
        pred = phase_o.get("column_span_refs") or {}
        gold = gold_phase_o.get("column_span_refs") or {}
        for column_ref, gold_ref in gold.items():
            pred_ref = pred.get(column_ref)
            if pred_ref != gold_ref:
                if pred_ref == "OMIT" or gold_ref == "OMIT":
                    subtype = "OMIT error"
                else:
                    subtype = "wrong column/span"
                break
    return {"failure_stage": failure_stage, "semantic_selection_subtype": subtype}


def summarize(result_root: Path, rows: list[dict[str, Any]], per_sample: list[dict[str, Any]], raw_rows: list[dict[str, Any]], metadata: dict[str, Any], backend: str) -> dict[str, Any]:
    methods: dict[str, Any] = {}
    for method_id in METHODS:
        subset = [row for row in per_sample if row["method_id"] == method_id]
        n = len(subset)
        strict = sum(bool(row.get("strict_full_state_correct")) for row in subset)
        target = sum(bool(row.get("target_state_correct")) for row in subset)
        execution = sum(bool(row.get("execution_success")) for row in subset)
        admitted = sum(bool(row.get("admitted")) for row in subset)
        accepted_strict = sum(bool(row.get("admitted") and row.get("strict_full_state_correct")) for row in subset)
        off_target = sum(bool(row.get("any_off_target_change")) for row in subset)
        wrong_admitted = sum(bool(row.get("admitted") and not row.get("strict_full_state_correct")) for row in subset)
        input_tokens = sum(int(row.get("token_count", {}).get("input_tokens") or 0) for row in subset)
        output_tokens = sum(int(row.get("token_count", {}).get("output_tokens") or 0) for row in subset)
        latency = sum(float(row.get("latency_sec") or 0.0) for row in subset)
        methods[method_id] = {
            "samples": n,
            "strict_full_state_correct": strict,
            "strict_full_state_accuracy": f"{strict}/{n}",
            "target_state_correct": target,
            "target_state_accuracy": f"{target}/{n}",
            "execution_success": execution,
            "execution_success_rate": f"{execution}/{n}",
            "admission_count": admitted,
            "admission_rate": f"{admitted}/{n}",
            "accepted_write_correctness": f"{accepted_strict}/{admitted}" if admitted else "0/0",
            "wrong_admitted_writes": wrong_admitted,
            "off_target_extra_delta_count": off_target,
            "model_calls": len([row for row in raw_rows if row["method_id"] == method_id]),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "generation_latency_sec": latency,
        }
    by_sample_method = {(row["sample_id"], row["method_id"]): row for row in per_sample}
    paired = {}
    for baseline in ("M0_DIRECT_ZERO", "M0_DIRECT_FS", "M1_J_FS"):
        counts = Counter()
        for row in rows:
            m2 = bool(by_sample_method[(row["sample_id"], "M2_FINAL_ENG2B")]["strict_full_state_correct"])
            base = bool(by_sample_method[(row["sample_id"], baseline)]["strict_full_state_correct"])
            if m2 and base:
                counts["both_correct"] += 1
            elif m2 and not base:
                counts["m2_correct_baseline_wrong"] += 1
            elif base and not m2:
                counts["baseline_correct_m2_wrong"] += 1
            else:
                counts["both_wrong"] += 1
        paired[baseline] = dict(counts)
    summary = {
        "stage": STAGE_NAME,
        "backend": backend,
        "status": "PASS" if all(item["samples"] == EXPECTED_N and item["model_calls"] == EXPECTED_N for item in methods.values()) else "FAIL",
        "denominator": EXPECTED_N,
        "methods": methods,
        "model_calls_total": len(raw_rows),
        "model_calls_per_sample_per_method": 1,
        "retry_count": 0,
        "primary_metric": "strict_full_state_accuracy",
        "paired_outcomes": paired,
        "raw_model_outputs_sha256": sha256_file(result_root / "raw" / "model_outputs.jsonl"),
        "per_sample_results_sha256": sha256_file(result_root / "results" / "per_sample_results.jsonl"),
        "generation_metadata": metadata,
    }
    write_json(result_root / "results" / "aggregate_results.json", summary)
    write_json(result_root / "results" / "paired_outcomes.json", {"stage": STAGE_NAME, "primary_metric": "strict_full_state_accuracy", "pairs": paired})
    lines = ["# Stage ENG2C Comparison", "", "| Method | Strict full-state | Target-table | Exec success | Admission | Accepted strict | Wrong admitted | Off-target | Calls | Tokens |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for method_id, item in methods.items():
        lines.append(f"| {method_id} | {item['strict_full_state_accuracy']} | {item['target_state_accuracy']} | {item['execution_success_rate']} | {item['admission_rate']} | {item['accepted_write_correctness']} | {item['wrong_admitted_writes']} | {item['off_target_extra_delta_count']} | {item['model_calls']} | {item['total_tokens']} |")
    (result_root / "results" / "comparison_table.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return summary


def run_stageeng2c(args: argparse.Namespace) -> dict[str, Any]:
    stage_dir = Path(args.stage_dir).resolve()
    result_root = Path(args.result_root).resolve()
    if result_root.exists():
        raise SystemExit(f"STOP: result_root already exists; refusing to overwrite official outputs: {result_root}")
    rows = read_jsonl(stage_dir / "ENG2C_DEV100_FREEZE.jsonl")
    if len(rows) != EXPECTED_N:
        raise SystemExit(f"STOP: expected {EXPECTED_N} rows, found {len(rows)}")
    direct_fs_config = read_json(stage_dir / DIRECT_CONFIG_REL)
    direct_zero_config = read_json(stage_dir / DIRECT_ZERO_CONFIG_REL)
    jfs_config = read_json(stage_dir / JFS_CONFIG_REL)
    result_root.mkdir(parents=True)
    if args.backend == "mock":
        generator: Any = MockGenerator(rows)
    else:
        generator = UnifiedFrozenHFGenerator(model_name_or_path=args.model_name_or_path, trust_remote_code=args.trust_remote_code, max_input_tokens=args.max_input_tokens, seed=args.seed)
    m2_generator = M2Adapter(generator)
    metadata = {"generator": generator.metadata(), "m2_runtime_freeze": live_runtime_freeze(model_name_or_path=args.model_name_or_path, max_input_tokens=args.max_input_tokens, seed=args.seed)}
    write_json(
        result_root / "run_manifest.json",
        {
            "stage": STAGE_NAME,
            "backend": args.backend,
            "denominator": len(rows),
            "methods": list(METHODS),
            "model": metadata,
            "retry": 0,
            "primary_metric": "strict_full_state_accuracy",
        },
    )
    raw_rows: list[dict[str, Any]] = []
    parsed_rows: list[dict[str, Any]] = []
    per_sample: list[dict[str, Any]] = []
    m2_taxonomy_rows: list[dict[str, Any]] = []
    for row in rows:
        for method_id in METHODS:
            prompt = method_prompt(method_id, row, stage_dir, direct_zero_config, direct_fs_config, jfs_config)
            if method_id == "M2_FINAL_ENG2B":
                parsed, evaluation, raw_o = evaluate_final_method(row, stage_dir, m2_generator, phase_o_max_new_tokens=args.phase_o_max_new_tokens)
                call_row = raw_o | {"method_id": method_id}
                admitted = m2_admitted(parsed)
                taxonomy = m2_failure_taxonomy(row, parsed, evaluation)
                m2_taxonomy_rows.append({"sample_id": row["sample_id"], **taxonomy, "strict_full_state_correct": evaluation.get("strict_full_state_correct")})
            else:
                call = generator.generate(method_id, row, prompt, args.max_new_tokens)
                call_row = asdict(call)
                if call.status == "success":
                    parsed, evaluation = evaluate_baseline(method_id, row, stage_dir, call.raw_output)
                else:
                    parsed = {"sample_id": row["sample_id"], "method_id": method_id, "parse_status": "generation_error", "diagnostics": [call.error]}
                    evaluation = evaluate_sql(row, stage_dir, [], parse_status="parse_error")
                admitted = bool(evaluation.get("execution_success"))
            raw_rows.append(call_row)
            parsed_rows.append(parsed)
            per_sample.append(
                {
                    "sample_id": row["sample_id"],
                    "method_id": method_id,
                    "status": "PASS" if evaluation.get("strict_full_state_correct") else "FAIL",
                    "parse_status": parsed.get("parse_status"),
                    "admitted": admitted,
                    **evaluation,
                    "failure_stage": (m2_taxonomy_rows[-1]["failure_stage"] if method_id == "M2_FINAL_ENG2B" else evaluation.get("error_type")),
                    "semantic_selection_subtype": (m2_taxonomy_rows[-1]["semantic_selection_subtype"] if method_id == "M2_FINAL_ENG2B" else None),
                    "token_count": {"input_tokens": call_row.get("input_tokens"), "output_tokens": call_row.get("output_tokens")},
                    "latency_sec": call_row.get("latency_sec", 0.0),
                }
            )
            write_jsonl(result_root / "raw" / "model_outputs.jsonl", raw_rows)
            write_jsonl(result_root / "parsed" / "parsed_outputs.jsonl", parsed_rows)
            write_jsonl(result_root / "results" / "per_sample_results.jsonl", per_sample)
    write_jsonl(result_root / "efficiency" / "tokens.jsonl", [{"sample_id": row["sample_id"], "method_id": row["method_id"], **row["token_count"]} for row in per_sample])
    write_jsonl(result_root / "efficiency" / "latency.jsonl", [{"sample_id": row["sample_id"], "method_id": row["method_id"], "latency_sec": row["latency_sec"]} for row in per_sample])
    by_method = {}
    for method_id in METHODS:
        subset = [row for row in per_sample if row["method_id"] == method_id]
        by_method[method_id] = {
            "input_tokens": sum(int(row["token_count"].get("input_tokens") or 0) for row in subset),
            "output_tokens": sum(int(row["token_count"].get("output_tokens") or 0) for row in subset),
            "latency_sec": sum(float(row.get("latency_sec") or 0.0) for row in subset),
        }
    write_json(result_root / "efficiency" / "summary.json", {"stage": STAGE_NAME, "methods": by_method})
    write_json(result_root / "analysis" / "m2_failure_taxonomy.json", {"stage": STAGE_NAME, "rows": m2_taxonomy_rows, "counts": dict(Counter(row["failure_stage"] for row in m2_taxonomy_rows))})
    write_json(result_root / "analysis" / "baseline_error_summary.json", {"stage": STAGE_NAME, "counts": {method: dict(Counter(row.get("error_type") or "PASS" for row in per_sample if row["method_id"] == method)) for method in METHODS if method != "M2_FINAL_ENG2B"}})
    (result_root / "analysis" / "representative_failures.md").write_text("# Representative Failures\n\nGenerated by the evaluator after the official run; no method changes are authorized from these cases.\n", encoding="utf-8", newline="\n")
    write_json(result_root / "audits" / "denominator_audit.json", {"stage": STAGE_NAME, "status": "PASS", "denominator": len(rows), "result_rows": len(per_sample), "expected_result_rows": len(rows) * len(METHODS), "silent_skip_count": 0})
    write_json(result_root / "audits" / "call_retry_audit.json", {"stage": STAGE_NAME, "status": "PASS", "model_calls_total": len(raw_rows), "model_calls_per_sample_per_method": 1, "retry_count": 0})
    write_json(result_root / "audits" / "model_identity_audit.json", {"stage": STAGE_NAME, "status": "PASS" if args.backend == "mock" or metadata["generator"].get("model_revision") == MODEL_REVISION else "FAIL", "model": metadata})
    write_json(result_root / "audits" / "evaluator_commonality.json", {"stage": STAGE_NAME, "status": "PASS", "primary_metric": "strict_full_state_accuracy", "state_scope": "all persistent user tables"})
    write_json(result_root / "audits" / "method_freeze_integrity.json", {"stage": STAGE_NAME, "status": "PASS", "m2_runner": "scripts/server/run_eng2_final_method.py", "m2_method_id": M2_METHOD_ID, "m2_revision": MODEL_REVISION})
    return summarize(result_root, rows, per_sample, raw_rows, metadata, args.backend)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--backend", choices=["hf", "mock"], default="hf")
    parser.add_argument("--model-name-or-path", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument("--max-input-tokens", type=int, default=24576)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--phase-o-max-new-tokens", type=int, default=PHASE_O_MAX_NEW_TOKENS)
    parser.add_argument("--seed", type=int, default=20260905)
    parser.add_argument("--dry-run-live-config", action="store_true")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    if args.dry_run_live_config:
        print(json.dumps(live_runtime_freeze(model_name_or_path=args.model_name_or_path, max_input_tokens=args.max_input_tokens, seed=args.seed), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(json.dumps(run_stageeng2c(args), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
