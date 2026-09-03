#!/usr/bin/env python3
"""Run Stage ENG2A Gretel development-pilot evaluation."""

from __future__ import annotations

import argparse
import json
import shutil
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
from scripts.data.build_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze import (  # noqa: E402
    canonical_json,
    oracle_column_conditioned_path,
    render_phase_o_messages,
)
from scripts.data.build_stage7e0_a7_final_a5_real_generation_feasibility import (  # noqa: E402
    read_jsonl,
    write_json,
    write_jsonl,
)
from scripts.data.build_stageeng2a_gretel_external_development_pilot import (  # noqa: E402
    EXPECTED_PILOT_N,
    JFS_CONFIG_REL,
    DIRECT_CONFIG_REL,
    SERVER_RESULT_DIR,
    STAGE_NAME,
    sha256_file,
    sha256_text,
)
from scripts.server.run_stage7e0_a4_english import DEFAULT_MODEL_PATH, runtime_versions  # noqa: E402
from scripts.server.run_stage7e0_a6_english import (  # noqa: E402
    CONSTRAINED_BACKEND_ID,
    ConstrainedTransformersChatGenerator,
    parse_phase_o_column_conditioned_output,
    validate_runtime_versions,
)


METHODS = ("M0_DIRECT_SQL", "M1_J_FS", "M2_FROZEN_A7")
FAILURE_TAXONOMY = ("E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9", "E10")


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


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def user_tables(con: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    ]


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


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


def execute_statements(
    con: sqlite3.Connection,
    statements: list[str],
    params: tuple[Any, ...] | None = None,
    statement_params: list[list[Any]] | None = None,
) -> dict[str, Any]:
    try:
        con.execute("SAVEPOINT eng2a_write")
        if params is not None:
            if len(statements) != 1:
                raise sqlite3.Error("parameterized execution expects one statement")
            con.execute(statements[0], params)
            count = 1
        elif statement_params is not None:
            if len(statement_params) != len(statements):
                raise sqlite3.Error("statement_params length mismatch")
            count = 0
            for statement, values in zip(statements, statement_params, strict=True):
                con.execute(statement, values)
                count += 1
        else:
            count = 0
            for statement in statements:
                con.execute(statement)
                count += 1
        con.execute("RELEASE eng2a_write")
        return {"status": "success", "executed_statements": count, "error": None}
    except sqlite3.Error as exc:
        try:
            con.execute("ROLLBACK TO eng2a_write")
            con.execute("RELEASE eng2a_write")
        except sqlite3.Error:
            con.rollback()
        return {"status": "execution_error", "executed_statements": 0, "error": str(exc)}


def evaluate_sql(
    row: dict[str, Any],
    stage_dir: Path,
    statements: list[str],
    *,
    params: tuple[Any, ...] | None = None,
    statement_params: list[list[Any]] | None = None,
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
            pred_execution = execute_statements(pred_conn, statements, params=params, statement_params=statement_params)
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


class MockGenerator:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = {row["sample_id"]: row for row in rows}

    def generate(self, method_id: str, row: dict[str, Any], prompt: Any, max_new_tokens: int) -> CallResult:
        del prompt, max_new_tokens
        if method_id == "M0_DIRECT_SQL":
            raw = row["evaluator_side_expected"]["gold_sql"][0]
        elif method_id == "M1_J_FS":
            raw = canonical_json({"records": [{"table": row["evaluator_side_expected"]["gold_target_table"], "operation": "insert", "values": row["assigned_values_for_mock"]}]})
        elif method_id == "M2_FROZEN_A7":
            raw = canonical_json(row["label_side_expected"]["phase_o"])
        else:
            raise KeyError(method_id)
        return CallResult(row["sample_id"], method_id, raw, input_tokens=0, output_tokens=len(raw.split()), generation_metadata={"backend": "mock"})

    def metadata(self) -> dict[str, Any]:
        return {"backend": "mock", "model_called": False, "label_side_data_used_for_constraints": True}


class HFChatGenerator:
    def __init__(self, *, model_name_or_path: str, trust_remote_code: bool, max_input_tokens: int, seed: int):
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:  # pragma: no cover
            raise SystemExit("STOP: HF generation requires torch and transformers") from exc
        self.runtime_lock = validate_runtime_versions()
        self.torch = torch
        self.seed = seed
        self.max_input_tokens = max_input_tokens
        kwargs = {"trust_remote_code": trust_remote_code}
        self.tokenizer = AutoTokenizer.from_pretrained(model_name_or_path, **kwargs)
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.tokenizer.padding_side = "left"
        model_kwargs = {"trust_remote_code": trust_remote_code, "device_map": "auto", "torch_dtype": "auto"}
        self.model = AutoModelForCausalLM.from_pretrained(model_name_or_path, **model_kwargs)
        self.model.eval()
        self.model_name_or_path = model_name_or_path

    def generate(self, method_id: str, row: dict[str, Any], prompt: str, max_new_tokens: int) -> CallResult:
        torch = self.torch
        messages = [{"role": "user", "content": prompt}]
        rendered = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.tokenizer(rendered, return_tensors="pt", add_special_tokens=True, truncation=False)
        token_count = int(inputs["input_ids"].shape[-1])
        if token_count > self.max_input_tokens:
            return CallResult(row["sample_id"], method_id, "", status="input_too_long", error=f"{token_count}>{self.max_input_tokens}", input_tokens=token_count)
        inputs = {key: value.to(self.model.device) for key, value in inputs.items()}
        torch.manual_seed(self.seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(self.seed)
        start = time.monotonic()
        with torch.inference_mode():
            output = self.model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False, temperature=None, top_p=None, top_k=None, pad_token_id=self.tokenizer.eos_token_id, eos_token_id=self.tokenizer.eos_token_id)
        generated_ids = output[0][token_count:]
        raw = self.tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
        return CallResult(row["sample_id"], method_id, raw, input_tokens=token_count, output_tokens=int(generated_ids.shape[-1]), latency_sec=time.monotonic() - start, hit_max_new_tokens=int(generated_ids.shape[-1]) >= max_new_tokens, generation_metadata={"backend": "transformers_hf_unconstrained"})

    def metadata(self) -> dict[str, Any]:
        torch = self.torch
        return {"backend": "transformers_hf_unconstrained", "model_called": True, "model_name_or_path": self.model_name_or_path, "runtime_lock": self.runtime_lock, "torch_version": torch.__version__, "cuda_available": bool(torch.cuda.is_available())}


def method_prompt(method_id: str, row: dict[str, Any], stage_dir: Path, direct_config: dict[str, Any], jfs_config: dict[str, Any]) -> Any:
    profile = build_profile(stage_dir / row["synthetic_db_spec"]["sqlite_db_path"], db_id=row["sample_id"])
    question = row["model_side_input"]["question"]
    if method_id == "M0_DIRECT_SQL":
        return build_direct_prompt(question, profile, direct_config)
    if method_id == "M1_J_FS":
        return build_legacy_json_prompt(question, profile, jfs_config)
    if method_id == "M2_FROZEN_A7":
        return render_phase_o_messages(row)[0]
    raise KeyError(method_id)


def evaluate_method(method_id: str, row: dict[str, Any], stage_dir: Path, raw: str) -> tuple[dict[str, Any], dict[str, Any]]:
    parsed: dict[str, Any] = {"sample_id": row["sample_id"], "method_id": method_id}
    if method_id == "M0_DIRECT_SQL":
        statements, error = extract_sql_statements(raw)
        parsed.update({"parse_status": "success" if not error else "parse_error", "direct_sql": statements, "diagnostics": [error] if error else []})
        evaluation = evaluate_sql(row, stage_dir, list(statements or []), parse_status=parsed["parse_status"])
        return parsed, evaluation
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
        return parsed, evaluate_sql(
            row,
            stage_dir,
            [statement.sql for statement in program.statements],
            statement_params=[statement.params for statement in program.statements],
        )
    if method_id == "M2_FROZEN_A7":
        try:
            phase_o = parse_phase_o_column_conditioned_output(raw, row["runtime_constraints"]["phase_o_schema"])
            predicted = json.loads(canonical_json(row))
            predicted["label_side_expected"]["phase_o"] = phase_o
            oracle = oracle_column_conditioned_path(predicted, stage_dir / row["synthetic_db_spec"]["sqlite_db_path"])
            parsed.update({"parse_status": "success", "phase_o": phase_o, "oracle": oracle})
            preflight = {"accepted": oracle["preflight"] == "ADMITTED", "error": oracle.get("preflight_reason_code")}
            evaluation = evaluate_sql(row, stage_dir, [oracle["compiled_sql"]], params=tuple(oracle["compiled_parameters"]), preflight=preflight)
            return parsed, evaluation
        except Exception as exc:
            parsed.update({"parse_status": "parse_error", "diagnostics": [str(exc)]})
            return parsed, evaluate_sql(row, stage_dir, [], parse_status="parse_error")
    raise KeyError(method_id)


def taxonomy_code(row: dict[str, Any]) -> str:
    if row.get("target_state_correct"):
        return "E10"
    stage = str(row.get("failure_stage") or row.get("error_type") or "")
    output = str(row.get("raw_model_output") or "")
    if "required_column_omitted" in stage or '"OMIT"' in output:
        return "E1"
    if "parse" in stage or "json" in stage:
        return "E7"
    if "execution" in stage or "preflight" in stage:
        return "E8"
    return "E9"


def summarize(result_root: Path, rows: list[dict[str, Any]], cases: list[dict[str, Any]], raw_rows: list[dict[str, Any]], backend: str, metadata: dict[str, Any]) -> dict[str, Any]:
    methods = {}
    for method_id in METHODS:
        subset = [row for row in cases if row["method_id"] == method_id]
        n = len(subset)
        target = sum(bool(row.get("target_state_correct")) for row in subset)
        execution = sum(bool(row.get("execution_success")) for row in subset)
        admitted = sum(bool(row.get("admitted")) for row in subset)
        accepted_correct = sum(bool(row.get("admitted") and row.get("target_state_correct")) for row in subset)
        off_target = sum(bool(row.get("any_off_target_change")) for row in subset)
        token_in = sum(int(row.get("token_count", {}).get("input_tokens") or 0) for row in subset)
        token_out = sum(int(row.get("token_count", {}).get("output_tokens") or 0) for row in subset)
        methods[method_id] = {
            "samples": n,
            "target_state_correct": target,
            "target_state_accuracy": f"{target}/{n}",
            "execution_success": execution,
            "execution_success_rate": f"{execution}/{n}",
            "admission_count": admitted,
            "admission_rate": f"{admitted}/{n}",
            "accepted_write_correctness": f"{accepted_correct}/{admitted}" if admitted else "0/0",
            "off_target_state_change": off_target,
            "model_calls": len([row for row in raw_rows if row["method_id"] == method_id]),
            "input_tokens": token_in,
            "output_tokens": token_out,
        }
    summary = {
        "stage": STAGE_NAME,
        "backend": backend,
        "status": "PASS" if all(value["samples"] == EXPECTED_PILOT_N for value in methods.values()) else "FAIL",
        "pilot_n": len(rows),
        "methods": methods,
        "model_calls_total": len(raw_rows),
        "model_calls_per_sample_per_method": 1,
        "retry_count": 0,
        "raw_model_outputs_sha256": sha256_file(result_root / "raw" / "model_outputs.jsonl"),
        "per_sample_results_sha256": sha256_file(result_root / "results" / "per_sample_results.jsonl"),
        "generation_metadata": metadata,
    }
    write_json(result_root / "results" / "summary.json", summary)
    lines = ["# Stage ENG2A Summary", "", "| Method | Target State | Exec. Success | Admission | Accepted Correct | Off-target | Calls | Tokens |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for method_id, item in methods.items():
        lines.append(f"| {method_id} | {item['target_state_accuracy']} | {item['execution_success_rate']} | {item['admission_rate']} | {item['accepted_write_correctness']} | {item['off_target_state_change']} | {item['model_calls']} | {item['input_tokens'] + item['output_tokens']} |")
    (result_root / "results" / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    a7_rows = [row | {"taxonomy_code": taxonomy_code(row)} for row in cases if row["method_id"] == "M2_FROZEN_A7"]
    counts = Counter(row["taxonomy_code"] for row in a7_rows)
    write_json(result_root / "analysis" / "a7_failure_taxonomy.json", {"stage": STAGE_NAME, "taxonomy_codes": list(FAILURE_TAXONOMY), "counts": dict(counts), "rows": a7_rows})
    return summary


def run_stageeng2a(args: argparse.Namespace) -> dict[str, Any]:
    stage_dir = Path(args.stage_dir).resolve()
    result_root = Path(args.result_root).resolve()
    backend = str(args.backend).lower()
    if result_root.exists():
        shutil.rmtree(result_root)
    rows = read_jsonl(stage_dir / "ENG2A_PILOT_100_FREEZE.jsonl")
    if len(rows) != EXPECTED_PILOT_N:
        raise SystemExit(f"STOP: expected {EXPECTED_PILOT_N} frozen rows, found {len(rows)}")
    direct_config = read_json(PROJECT_ROOT / DIRECT_CONFIG_REL)
    jfs_config = read_json(PROJECT_ROOT / JFS_CONFIG_REL)
    result_root.mkdir(parents=True)
    if backend == "mock":
        generator: Any = MockGenerator(rows)
        constrained_generator = None
    elif backend == "hf":
        generator = HFChatGenerator(model_name_or_path=args.model_name_or_path, trust_remote_code=args.trust_remote_code, max_input_tokens=args.max_input_tokens, seed=args.seed)
        constrained_generator = ConstrainedTransformersChatGenerator(model_name_or_path=args.model_name_or_path, quantization=args.quantization, trust_remote_code=args.trust_remote_code, max_input_tokens=args.max_input_tokens, seed=args.seed)
        if constrained_generator.metadata().get("backend") != CONSTRAINED_BACKEND_ID:
            raise SystemExit("STOP: constrained A7 backend unavailable")
    else:
        raise SystemExit(f"STOP: unsupported backend {backend}")
    metadata = {"unconstrained": generator.metadata(), "constrained": constrained_generator.metadata() if constrained_generator else None, "runtime_versions": runtime_versions()}
    write_json(result_root / "run_manifest.json", {"stage": STAGE_NAME, "backend": backend, "pilot_n": len(rows), "methods": list(METHODS), "model": metadata, "retry": 0})
    raw_rows: list[dict[str, Any]] = []
    parsed_rows: list[dict[str, Any]] = []
    case_rows: list[dict[str, Any]] = []
    for row in rows:
        for method_id in METHODS:
            prompt = method_prompt(method_id, row, stage_dir, direct_config, jfs_config)
            if method_id == "M2_FROZEN_A7" and backend == "hf":
                call = constrained_generator.generate(sample_id=row["sample_id"], messages=prompt, max_new_tokens=args.phase_o_max_new_tokens, row=row)
                call_row = {"sample_id": row["sample_id"], "method_id": method_id, **asdict(call)}
            else:
                call = generator.generate(method_id, row, prompt, args.max_new_tokens)
                call_row = asdict(call)
            raw_rows.append(call_row)
            parsed, evaluation = evaluate_method(method_id, row, stage_dir, call.raw_output if isinstance(call, CallResult) else call.raw_output)
            parsed_rows.append(parsed)
            admitted = bool(evaluation.get("execution_success")) if method_id != "M2_FROZEN_A7" else bool((parsed.get("oracle") or {}).get("preflight") == "ADMITTED")
            case_rows.append(
                {
                    "sample_id": row["sample_id"],
                    "method_id": method_id,
                    "status": "PASS" if evaluation.get("target_state_correct") else "FAIL",
                    "raw_model_output": call.raw_output,
                    "parse_status": parsed.get("parse_status"),
                    "admitted": admitted,
                    **evaluation,
                    "token_count": {"input_tokens": call.input_tokens, "output_tokens": call.output_tokens},
                    "latency": call.latency_sec,
                }
            )
            write_jsonl(result_root / "raw" / "model_outputs.jsonl", raw_rows)
            write_jsonl(result_root / "raw" / "parsed_outputs.jsonl", parsed_rows)
            write_jsonl(result_root / "results" / "per_sample_results.jsonl", case_rows)
    write_jsonl(result_root / "efficiency" / "token_usage.jsonl", [{"sample_id": row["sample_id"], "method_id": row["method_id"], **row["token_count"]} for row in case_rows])
    write_jsonl(result_root / "efficiency" / "latency.jsonl", [{"sample_id": row["sample_id"], "method_id": row["method_id"], "latency": row["latency"]} for row in case_rows])
    write_json(result_root / "environment" / "environment.json", {"stage": STAGE_NAME, "backend": backend, "runtime_versions": runtime_versions(), "metadata": metadata})
    write_json(result_root / "audits" / "denominator_audit.json", {"stage": STAGE_NAME, "status": "PASS", "pilot_n": len(rows), "result_rows": len(case_rows), "expected_result_rows": len(rows) * len(METHODS), "silent_skip_count": 0})
    write_json(result_root / "audits" / "retry_audit.json", {"stage": STAGE_NAME, "status": "PASS", "retry_count": 0, "allowed_retries": 0})
    write_json(result_root / "audits" / "model_call_audit.json", {"stage": STAGE_NAME, "status": "PASS", "model_calls_total": len(raw_rows), "model_calls_per_sample_per_method": 1})
    return summarize(result_root, rows, case_rows, raw_rows, backend, metadata)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--result-root", type=Path, default=PROJECT_ROOT / STAGE_NAME / "mock_dry_run")
    parser.add_argument("--backend", choices=["hf", "mock"], default="hf")
    parser.add_argument("--model-name-or-path", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--quantization", default="none")
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--phase-o-max-new-tokens", type=int, default=512)
    parser.add_argument("--max-input-tokens", type=int, default=28672)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument("--allow-result-root-inside-git", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_stageeng2a(args), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
