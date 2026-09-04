#!/usr/bin/env python3
"""Canonical ENG2B final proposed-method runner.

This entrypoint is intentionally separate from the historical ENG2A runner:
M2_FINAL_ENG2B builds column-specific runtime domains, generates against the
resulting dynamic schema, parses against the same schema object, and uses the
ENG2B stateful prefix grammar for non-OMIT SPAN uniqueness.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from nldbwrite_v3.v2_a1.eng2b_runtime import build_eng2b_constraint_grammar, prepare_eng2b_runtime_row  # noqa: E402
from nldbwrite_v3.v2_a1.types import V2A1Error  # noqa: E402
from scripts.data.build_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze import (  # noqa: E402
    canonical_json,
    oracle_column_conditioned_path,
    render_phase_o_messages,
)
from scripts.data.build_stageeng2a_gretel_external_development_pilot import STAGE_NAME as ENG2A_STAGE_NAME  # noqa: E402
from scripts.server.run_stage7e0_a6_english import (  # noqa: E402
    CallResult,
    ConstrainedTransformersChatGenerator,
    PHASE_O_MAX_NEW_TOKENS,
    parse_phase_o_column_conditioned_output,
)
from scripts.server.run_stage7e0_v2_a1_preflight import IncrementalJsonSchemaGrammarBackend  # noqa: E402
from scripts.server.run_stageeng2a_gretel_pilot import evaluate_sql, failure_stage_from_v2a1_error  # noqa: E402


METHOD_ID = "M2_FINAL_ENG2B"


def generate_constrained_eng2b(model: Any, tokenizer: Any, messages: list[dict[str, str]], *, max_new_tokens: int, schema: dict[str, Any]) -> dict[str, Any]:
    import torch

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


class Eng2BConstrainedTransformersChatGenerator(ConstrainedTransformersChatGenerator):
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
            generation_metadata=generated["backend"] | {"method_id": METHOD_ID},
        )

    def metadata(self) -> dict[str, Any]:
        metadata = super().metadata()
        metadata["method_id"] = METHOD_ID
        metadata["constraint_source"] = "ENG2B_dynamic_column_specific_json_schema"
        metadata["stateful_unique_non_omit_span_refs"] = True
        return metadata


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
        predicted = json.loads(canonical_json(runtime_row))
        predicted["label_side_expected"]["phase_o"] = phase_o
        oracle = oracle_column_conditioned_path(predicted, stage_dir / runtime_row["synthetic_db_spec"]["sqlite_db_path"])
        parsed["oracle"] = oracle
        preflight = {"accepted": oracle["preflight"] == "ADMITTED", "error": oracle.get("preflight_reason_code")}
        evaluation = evaluate_sql(runtime_row, stage_dir, [oracle["compiled_sql"]], params=tuple(oracle["compiled_parameters"]), preflight=preflight)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / ENG2A_STAGE_NAME)
    parser.add_argument("--rows", type=Path, default=PROJECT_ROOT / ENG2A_STAGE_NAME / "ENG2A_PILOT_100_FREEZE.jsonl")
    parser.add_argument("--replay-raw", type=Path, required=True, help="JSONL containing sample_id and raw_output for deterministic no-model replay.")
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.rows.read_text(encoding="utf-8").splitlines() if line.strip()]
    raw_rows = [json.loads(line) for line in args.replay_raw.read_text(encoding="utf-8").splitlines() if line.strip()]
    raw_by_id = {str(row["sample_id"]): str(row["raw_output"]) for row in raw_rows}
    generator = ReplayGenerator(raw_by_id)
    results = []
    for row in rows:
        parsed, evaluation, raw_o = evaluate_final_method(row, args.stage_dir, generator)
        results.append({"sample_id": row["sample_id"], "parsed": parsed, "evaluation": evaluation, "raw": raw_o})
    print(json.dumps({"method_id": METHOD_ID, "model_calls_new": 0, "results": results}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
