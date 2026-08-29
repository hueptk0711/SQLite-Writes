#!/usr/bin/env python3
"""Run Stage7E0-A2 real-generation preflight on locked synthetic fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import scripts.server.run_stage7e0_v2_a1_preflight as patch9  # noqa: E402
from nldbwrite_v3.v2_a1.compiler import compile_sqlite_program, quote_identifier  # noqa: E402
from nldbwrite_v3.v2_a1.completeness import verify_completeness  # noqa: E402
from nldbwrite_v3.v2_a1.inventories import build_schema_inventory  # noqa: E402
from nldbwrite_v3.v2_a1.phase_m_output import parse_phase_m_output  # noqa: E402
from nldbwrite_v3.v2_a1.phase_m_schema import dynamic_schema  # noqa: E402
from nldbwrite_v3.v2_a1.phase_o_output import parse_phase_o_output, phase_o_json_schema  # noqa: E402
from nldbwrite_v3.v2_a1.preflight import preflight_sqlite  # noqa: E402
from nldbwrite_v3.v2_a1.prompt_rendering import (  # noqa: E402
    inventory_payload,
    offset_guide,
    render_chat_prompt_with_tokenizer,
    render_phase_m_prompt,
    rendered_prompt_sha256,
    serialize_prompt_object,
    sha256_text,
)
from nldbwrite_v3.v2_a1.protocol import initialize_v2_a1_runtime, read_json, sha256_file  # noqa: E402
from nldbwrite_v3.v2_a1.slot_inventory import build_slot_bundle  # noqa: E402
from nldbwrite_v3.v2_a1.span_validation import validate_and_sort_spans  # noqa: E402
from nldbwrite_v3.v2_a1.typed_materializer import materialize_ir_values  # noqa: E402
from nldbwrite_v3.v2_a1.types import V2A1Error  # noqa: E402


STAGE = "Stage7E0_A2_REAL_GENERATION_PREFLIGHT"
MODEL_ID = patch9.MODEL_ID
REVISION = patch9.REVISION
TOKENIZER_CONFIG_SHA256 = patch9.TOKENIZER_CONFIG_SHA256
CHAT_TEMPLATE_SHA256 = patch9.CHAT_TEMPLATE_SHA256
A2_DIR = "stage7c_a2_phase_o_prompt_feasibility_amendment"
A2_PHASE_O_SPEC = f"{A2_DIR}/PHASE_O_PROMPT_SPEC_A2.json"
FRESH_SMOKE_SET = f"{A2_DIR}/FRESH_SYNTHETIC_SMOKE_SET.jsonl"
EXPECTED_FRESH_IDS = (
    "stage7c_a2_fresh_en_two_value_0001",
    "stage7c_a2_fresh_zh_two_value_0002",
    "stage7c_a2_fresh_en_three_value_0003",
    "stage7c_a2_fresh_zh_three_value_0004",
)


@dataclass(frozen=True)
class A2Fixture:
    sample_id: str
    question: str
    schema_input: dict[str, Any]
    phase_o_label: dict[str, Any]
    phase_m_label: dict[str, Any]
    synthetic_db_spec: dict[str, Any]
    target_state: dict[str, Any]
    acceptance_role: str


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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def a2_phase_o_prompt_spec(root: Path = ROOT) -> dict[str, Any]:
    return read_json(root / A2_PHASE_O_SPEC)


def render_phase_o_prompt_a2(question: str, inventory: Any, *, root: Path = ROOT) -> tuple[list[dict[str, str]], str]:
    spec = a2_phase_o_prompt_spec(root)
    user = spec["user_prompt_template"].format(
        question=question,
        offset_guide=offset_guide(question),
        schema_inventory=serialize_prompt_object(inventory_payload(inventory)),
    )
    messages = [{"role": "system", "content": spec["system_prompt"]}, {"role": "user", "content": user}]
    return messages, sha256_text(serialize_prompt_object(messages))


def validate_a2_prompt_hashes(root: Path = ROOT) -> dict[str, Any]:
    spec = a2_phase_o_prompt_spec(root)
    hashes = spec["prompt_hashes"]
    checks = {
        "phase_o_system_prompt_sha256": sha256_text(spec["system_prompt"]),
        "phase_o_user_prompt_template_sha256": sha256_text(spec["user_prompt_template"]),
    }
    violations = [key for key, digest in checks.items() if hashes.get(key) != digest]
    return {
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "phase_o_system_prompt_sha256": checks["phase_o_system_prompt_sha256"],
        "phase_o_user_prompt_template_sha256": checks["phase_o_user_prompt_template_sha256"],
        "phase_m_prompt_hashes_unchanged_from_a1": spec.get("phase_m_prompt_hashes_unchanged_from_a1"),
        "zero_shot": spec.get("zero_shot"),
        "few_shot_examples_in_prompt": spec.get("few_shot_examples_in_prompt"),
        "gold_visible": spec.get("gold_visible"),
    }


def fresh_acceptance_fixtures(root: Path = ROOT) -> list[A2Fixture]:
    rows = read_jsonl(root / FRESH_SMOKE_SET)
    fixtures: list[A2Fixture] = []
    for row in rows:
        model_side = row["model_side_input"]
        label_side = row["label_side_expected"]
        fixtures.append(
            A2Fixture(
                sample_id=row["sample_id"],
                question=model_side["question"],
                schema_input={"question": model_side["question"], "schema_inventory": model_side["schema_inventory"]},
                phase_o_label=label_side["phase_o"],
                phase_m_label=label_side["phase_m"],
                synthetic_db_spec=row["synthetic_db_spec"],
                target_state=label_side["target_state"],
                acceptance_role="primary_fresh_acceptance",
            )
        )
    observed = tuple(fixture.sample_id for fixture in fixtures)
    if observed != EXPECTED_FRESH_IDS:
        raise V2A1Error("stage7e0_a2_fresh_fixture_ids_changed", "Stage7E0-A2 requires the locked Stage7C-A2 fresh fixture IDs", details={"observed": list(observed), "expected": list(EXPECTED_FRESH_IDS)})
    return fixtures


def old_patch9_diagnostic_fixtures() -> list[A2Fixture]:
    fixtures: list[A2Fixture] = []
    for old in patch9.smoke_fixtures():
        table = old.schema_input["schema_inventory"]["tables"][0]["table_name"]
        columns = old.schema_input["schema_inventory"]["columns"]
        spans = validate_and_sort_spans(old.question, old.phase_o_label["value_spans"])
        row_values: list[Any] = []
        for span, column in zip(spans, columns, strict=True):
            raw = old.question[span.start_char : span.end_char]
            if "INT" in column["source_type"].upper():
                row_values.append(int(raw))
            else:
                row_values.append(raw)
        fixtures.append(
            A2Fixture(
                sample_id=old.sample_id,
                question=old.question,
                schema_input=old.schema_input,
                phase_o_label=old.phase_o_label,
                phase_m_label=old.phase_m_label,
                synthetic_db_spec={
                    "engine": "sqlite",
                    "table": table,
                    "columns": [
                        {"name": column["column_name"], "source_type": column["source_type"], "nullable": False}
                        for column in columns
                    ],
                    "create_sql": f'CREATE TABLE "{table}" ("name" TEXT UNIQUE NOT NULL, "age" INTEGER NOT NULL);',
                    "initial_rows": [],
                    "deterministic_fixture_policy": "diagnostic-only old Stage7E0 PATCH9 smoke fixture",
                },
                target_state={
                    "format": "canonical_sqlite_post_state",
                    "table_name": table,
                    "columns": [column["column_name"] for column in columns],
                    "rows": [row_values],
                },
                acceptance_role="old_patch9_regression_diagnostic_only",
            )
        )
    return fixtures


def make_synthetic_db(path: Path, db_spec: dict[str, Any]) -> Path:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.execute(db_spec["create_sql"])
        columns = [column["name"] for column in db_spec.get("columns", [])]
        for row in db_spec.get("initial_rows", []):
            placeholders = ",".join("?" for _ in columns)
            col_sql = ",".join(quote_identifier(column) for column in columns)
            conn.execute(f"INSERT INTO {quote_identifier(db_spec['table'])} ({col_sql}) VALUES ({placeholders})", tuple(row))
        conn.commit()
    finally:
        conn.close()
    return path


def fetch_target_state(db_path: Path, target_state: dict[str, Any]) -> dict[str, Any]:
    table = target_state["table_name"]
    columns = list(target_state["columns"])
    sql = f"SELECT {','.join(quote_identifier(column) for column in columns)} FROM {quote_identifier(table)} ORDER BY rowid"
    conn = sqlite3.connect(db_path)
    try:
        rows = [list(row) for row in conn.execute(sql).fetchall()]
    finally:
        conn.close()
    return {
        "format": "canonical_sqlite_post_state",
        "table_name": table,
        "columns": columns,
        "rows": rows,
    }


def execute_program_and_fetch_target(db_path: Path, program: Any, target_state: dict[str, Any]) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(program.sql, program.parameters)
        conn.commit()
    finally:
        conn.close()
    return fetch_target_state(db_path, target_state)


def downstream_check(root: Path, output_dir: Path, fixture: A2Fixture, phase_o_obj: dict[str, Any], phase_m_obj: dict[str, Any]) -> dict[str, Any]:
    inventory = build_schema_inventory(fixture.schema_input)
    spans = validate_and_sort_spans(fixture.question, phase_o_obj["value_spans"])
    slots = build_slot_bundle(spans)
    materialized = materialize_ir_values(phase_m_obj, inventory, slots)
    verify_completeness(phase_m_obj, slots)
    program = compile_sqlite_program(phase_m_obj, inventory, materialized)
    db_path = make_synthetic_db(output_dir / f"{fixture.sample_id}.sqlite", fixture.synthetic_db_spec)
    preflight = preflight_sqlite(db_path, program)
    target_actual = None
    target_eval = {"status": "NOT_RUN", "reason_code": "preflight_not_admitted"}
    if preflight.admitted:
        target_actual = execute_program_and_fetch_target(db_path, program, fixture.target_state)
        target_eval = {
            "status": "PASS" if target_actual == fixture.target_state else "FAIL",
            "actual": target_actual,
            "expected": fixture.target_state,
        }
    return {
        "accepted_spans": [span.__dict__ for span in spans],
        "evidence_inventory": [item.__dict__ for item in slots.evidence],
        "semantic_slot_inventory": [item.__dict__ for item in slots.slots],
        "materialized_bindings": [binding.__dict__ for binding in materialized.values()],
        "completeness": {"status": "PASS"},
        "compiled_sql": program.sql,
        "compiled_parameters": list(program.parameters),
        "preflight": preflight.__dict__,
        "target_state_evaluation": target_eval,
    }


def parse_status(kind: str, raw: str, root: Path, inventory: Any | None = None, slots: Any | None = None) -> dict[str, Any]:
    try:
        parsed = parse_phase_o_output(raw, root=root) if kind == "phase_o" else parse_phase_m_output(raw, "INSERT", inventory, slots, root=root)
        return {"status": "PASS", "parsed": parsed}
    except V2A1Error as exc:
        return {"status": "FAIL", "reason_code": exc.reason_code, "message": str(exc), "details": exc.details}
    except Exception as exc:
        return {"status": "FAIL", "reason_code": "unexpected_exception", "message": repr(exc)}


def run_fixture(root: Path, output_dir: Path, model: Any, tokenizer: Any, fixture: A2Fixture, *, phase_o_max_new_tokens: int, phase_m_max_new_tokens: int) -> dict[str, Any]:
    inventory = build_schema_inventory(fixture.schema_input)
    phase_o_messages, phase_o_messages_sha256 = render_phase_o_prompt_a2(fixture.question, inventory, root=root)
    phase_o_schema = phase_o_json_schema(root)
    write_json(output_dir / f"PHASE_O_A2_MESSAGES_{fixture.sample_id}.json", {"messages": phase_o_messages, "messages_sha256": phase_o_messages_sha256})
    write_json(output_dir / f"PHASE_O_SCHEMA_USED_{fixture.sample_id}.json", phase_o_schema)

    phase_o_generation = patch9.generate_constrained(
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
    row: dict[str, Any] = {
        "sample_id": fixture.sample_id,
        "acceptance_role": fixture.acceptance_role,
        "question": fixture.question,
        "phase_o_prompt": "Stage7C-A2 PHASE_O_PROMPT_SPEC_A2.json",
        "phase_o": {
            **phase_o_generation,
            "messages_sha256": phase_o_messages_sha256,
            "parse_schema_validation": phase_o_parse,
        },
    }
    if phase_o_parse["status"] != "PASS":
        row["status"] = "FAIL"
        row["violations"] = ["phase_o_real_generation_failed"]
        return row

    phase_o_label_eval = patch9.evaluate_phase_o_label(phase_o_parse["parsed"], fixture.phase_o_label, fixture.question)
    row["phase_o"]["label_evaluation"] = phase_o_label_eval
    if phase_o_label_eval["status"] != "PASS":
        row["status"] = "FAIL"
        row["violations"] = [patch9.phase_o_label_violation(phase_o_label_eval)]
        return row

    spans = validate_and_sort_spans(fixture.question, phase_o_parse["parsed"]["value_spans"])
    slots = build_slot_bundle(spans)
    phase_m_messages, phase_m_messages_sha256 = render_phase_m_prompt(phase_o_parse["parsed"]["operation"], inventory, slots, root=root)
    phase_m_schema = dynamic_schema(phase_o_parse["parsed"]["operation"], inventory, slots, root=root)
    write_json(output_dir / f"PHASE_M_MESSAGES_{fixture.sample_id}.json", {"messages": phase_m_messages, "messages_sha256": phase_m_messages_sha256})
    write_json(output_dir / f"PHASE_M_DYNAMIC_SCHEMA_USED_{fixture.sample_id}.json", phase_m_schema)

    phase_m_generation = patch9.generate_constrained(
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

    phase_m_label_eval = patch9.evaluate_phase_m_label(phase_m_parse["parsed"], fixture.phase_m_label)
    row["phase_m"]["label_evaluation"] = phase_m_label_eval
    if phase_m_label_eval["status"] != "PASS":
        row["status"] = "FAIL"
        row["violations"] = ["phase_m_label_mismatch"]
        return row

    downstream = downstream_check(root, output_dir, fixture, phase_o_parse["parsed"], phase_m_parse["parsed"])
    row["downstream"] = downstream
    row["status"] = "PASS" if downstream["preflight"]["admitted"] is True and downstream["target_state_evaluation"]["status"] == "PASS" else "FAIL"
    row["violations"] = [] if row["status"] == "PASS" else downstream_violations(downstream)
    return row


def downstream_violations(downstream: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    if downstream.get("preflight", {}).get("admitted") is not True:
        violations.append("synthetic_transactional_preflight_failed")
    if downstream.get("target_state_evaluation", {}).get("status") == "FAIL":
        violations.append("target_state_mismatch")
    return violations or ["downstream_failed"]


def collect_primary_violations(rows: list[dict[str, Any]]) -> list[str]:
    violations: list[str] = []
    for row in rows:
        sample_id = row.get("sample_id")
        if row.get("acceptance_role") != "primary_fresh_acceptance":
            continue
        if row.get("status") != "PASS":
            violations.append(f"primary_smoke_failed:{sample_id}")
        phase_o = row.get("phase_o")
        if isinstance(phase_o, dict):
            if phase_o.get("parse_schema_validation", {}).get("status") != "PASS":
                violations.append(f"phase_o_real_generation_failed:{sample_id}")
            if phase_o.get("label_evaluation", {}).get("status") == "FAIL":
                violations.append(f"{patch9.phase_o_label_violation(phase_o['label_evaluation'])}:{sample_id}")
        phase_m = row.get("phase_m")
        if isinstance(phase_m, dict):
            if phase_m.get("parse_schema_validation", {}).get("status") != "PASS":
                violations.append(f"phase_m_real_generation_failed:{sample_id}")
            if phase_m.get("label_evaluation", {}).get("status") == "FAIL":
                violations.append(f"phase_m_label_mismatch:{sample_id}")
        downstream = row.get("downstream")
        if isinstance(downstream, dict):
            if downstream.get("preflight", {}).get("admitted") is not True:
                violations.append(f"synthetic_preflight_not_admitted:{sample_id}")
            if downstream.get("target_state_evaluation", {}).get("status") == "FAIL":
                violations.append(f"target_state_mismatch:{sample_id}")
    return violations


def primary_acceptance_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    primary = [row for row in rows if row.get("acceptance_role") == "primary_fresh_acceptance"]
    passed = [row["sample_id"] for row in primary if row.get("status") == "PASS"]
    failed = [row["sample_id"] for row in primary if row.get("status") != "PASS"]
    return {
        "status": "PASS" if len(primary) == 4 and len(passed) == 4 else "FAIL",
        "required_pass_count": 4,
        "observed_primary_count": len(primary),
        "passed_count": len(passed),
        "failed_count": len(failed),
        "passed_sample_ids": passed,
        "failed_sample_ids": failed,
        "old_patch9_diagnostics_can_compensate_fresh_failures": False,
    }


def diagnostic_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "PASS" if all(row.get("status") == "PASS" for row in rows) else "FAIL",
        "diagnostic_only": True,
        "not_used_for_primary_acceptance": True,
        "sample_ids": [row.get("sample_id") for row in rows],
        "failed_sample_ids": [row.get("sample_id") for row in rows if row.get("status") != "PASS"],
    }


def fixture_payload(fixtures: list[A2Fixture]) -> list[dict[str, Any]]:
    return [
        {
            "sample_id": fixture.sample_id,
            "question": fixture.question,
            "schema_input": fixture.schema_input,
            "phase_o_label": fixture.phase_o_label,
            "phase_m_label": fixture.phase_m_label,
            "synthetic_db_spec": fixture.synthetic_db_spec,
            "target_state": fixture.target_state,
            "acceptance_role": fixture.acceptance_role,
        }
        for fixture in fixtures
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage7E0-A2 real-generation preflight.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "stage7e0_a2_real_generation_preflight_patch0")
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
    prompt_audit = validate_a2_prompt_hashes(root)
    write_json(output_dir / "A2_PHASE_O_PROMPT_PREFLIGHT.json", prompt_audit)
    if prompt_audit["status"] != "PASS":
        violations.append("a2_phase_o_prompt_hash_mismatch")

    fresh_fixtures = fresh_acceptance_fixtures(root)
    diagnostic_fixtures = old_patch9_diagnostic_fixtures()
    write_jsonl(output_dir / "PRIMARY_FRESH_ACCEPTANCE_FIXTURES.jsonl", fixture_payload(fresh_fixtures))
    write_jsonl(output_dir / "OLD_PATCH9_DIAGNOSTIC_FIXTURES.jsonl", fixture_payload(diagnostic_fixtures))
    write_json(
        output_dir / "GENERATION_CONFIG.json",
        {
            "stage": STAGE,
            "model_id": MODEL_ID,
            "model_path": str(args.model_path),
            "model_revision": REVISION,
            "tokenizer_revision": REVISION,
            "phase_o_prompt_spec": A2_PHASE_O_SPEC,
            "phase_o_prompt_hashes": {
                "phase_o_system_prompt_sha256": prompt_audit["phase_o_system_prompt_sha256"],
                "phase_o_user_prompt_template_sha256": prompt_audit["phase_o_user_prompt_template_sha256"],
            },
            "phase_m_prompt": "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json",
            "backend": "Stage7E0 PATCH9 incremental constrained backend",
            "do_sample": False,
            "temperature": 0.0,
            "top_p": 1.0,
            "retry": 0,
            "phase_o_max_new_tokens": args.phase_o_max_new_tokens,
            "phase_m_max_new_tokens": args.phase_m_max_new_tokens,
            "primary_fresh_acceptance_cases": list(EXPECTED_FRESH_IDS),
            "old_patch9_diagnostic_cases": [fixture.sample_id for fixture in diagnostic_fixtures],
            "train_dev_generation_run": False,
            "confirmation_481_evaluated": False,
            "live_sql_bench_gt_opened": False,
        },
    )

    capacity_audit = patch9.constraint_capacity_audit(root)
    write_json(output_dir / "CONSTRAINT_CAPACITY_AUDIT.json", capacity_audit)
    if capacity_audit["status"] != "PASS":
        violations.append("constraint_capacity_audit_failed")

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
        "packages": {name: patch9.package_version(name) for name in ("torch", "transformers", "accelerate", "bitsandbytes", "tokenizers", "safetensors")},
        "nvidia_smi": patch9.nvidia_smi(),
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
    elif hashlib.sha256(patch9.canonical_bytes(tokenizer_config)).hexdigest() != TOKENIZER_CONFIG_SHA256:
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
        "backend": "incremental_json_schema_grammar",
        "backend_source": "Stage7E0 PATCH9",
        "schema_mode": "incremental_json_schema_grammar",
        "schema_enforcement_mode": "transformers_prefix_allowed_tokens_fn",
        "constraint_source": "json_schema_plus_runtime_domains_not_label_side_answers",
        "finite_known_answer_candidates": False,
        "finite_complete_object_enumeration": False,
        "label_side_data_used_for_constraints": False,
        "hard_max_semantic_spans": None,
        "backend_supports_more_than_two_spans": True,
        "phase_m_complete_mapping_permutation_enumeration": False,
        "token_level_enforcement": True,
        "fallback_to_unconstrained": False,
        "automatic_repair": False,
        "retry": 0,
        "primary_acceptance_policy": "4/4 fresh locked Stage7C-A2 cases must pass exact end-to-end",
        "old_patch9_regression_policy": "diagnostic-only; not used to compensate fresh failures",
    }
    write_json(output_dir / "CONSTRAINED_GENERATION_BACKEND.json", backend_summary)

    primary_rows: list[dict[str, Any]] = []
    for fixture in fresh_fixtures:
        try:
            primary_rows.append(
                run_fixture(
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
            primary_rows.append({"sample_id": fixture.sample_id, "acceptance_role": fixture.acceptance_role, "status": "FAIL", "violations": ["unexpected_primary_exception"], "error": repr(exc)})
    write_jsonl(output_dir / "PRIMARY_FRESH_GENERATIONS.jsonl", primary_rows)

    diagnostic_rows: list[dict[str, Any]] = []
    for fixture in diagnostic_fixtures:
        try:
            diagnostic_rows.append(
                run_fixture(
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
            diagnostic_rows.append({"sample_id": fixture.sample_id, "acceptance_role": fixture.acceptance_role, "status": "FAIL", "violations": ["unexpected_diagnostic_exception"], "error": repr(exc)})
    write_jsonl(output_dir / "OLD_PATCH9_DIAGNOSTIC_GENERATIONS.jsonl", diagnostic_rows)
    write_jsonl(output_dir / "SMOKE_GENERATIONS.jsonl", primary_rows + diagnostic_rows)

    injection_audit = patch9.answer_injection_audit(fresh_fixtures, root)
    write_json(output_dir / "ANSWER_INJECTION_AUDIT.json", injection_audit)
    if injection_audit["status"] != "PASS":
        violations.append("answer_injection_audit_failed")

    acceptance = primary_acceptance_report(primary_rows)
    diagnostics = diagnostic_report(diagnostic_rows)
    primary_violations = collect_primary_violations(primary_rows)
    violations.extend(primary_violations)
    if acceptance["status"] != "PASS" and not primary_violations:
        violations.append("primary_acceptance_not_4_of_4")

    result = {
        "stage": STAGE,
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "model_called": True,
        "gpu_called": True,
        "generation_run": True,
        "answer_injection_audit_status": injection_audit["status"],
        "constraint_capacity_audit_status": capacity_audit["status"],
        "constraint_source": "json_schema_plus_runtime_domains_not_label_side_answers",
        "constraint_space_singleton": False,
        "finite_expected_candidate_trie": False,
        "finite_complete_object_enumeration": False,
        "label_side_data_used_for_constraints": False,
        "hard_max_semantic_spans": None,
        "backend_supports_more_than_two_spans": True,
        "primary_acceptance": acceptance,
        "old_patch9_diagnostics": diagnostics,
        "old_patch9_diagnostics_used_for_acceptance": False,
        "train_dev_generation_run": False,
        "confirmation_481_evaluated": False,
        "live_sql_bench_gt_opened": False,
    }
    write_json(output_dir / "PREFLIGHT_RESULT.json", result)
    report = (
        "# Stage7E0-A2 Real Generation Preflight\n\n"
        f"Status: {result['status']}\n\n"
        f"Primary acceptance: {json.dumps(acceptance, ensure_ascii=False, sort_keys=True)}\n\n"
        f"Old PATCH9 diagnostics: {json.dumps(diagnostics, ensure_ascii=False, sort_keys=True)}\n\n"
        f"violations: {json.dumps(violations, ensure_ascii=False, sort_keys=True)}\n\n"
        "Scope: A2 Phase O prompt, Stage7E0 PATCH9 incremental constrained backend, unchanged Phase M prompt and Stage7D implementation. "
        "No train/dev generation, no 481 confirmation evaluation, and no LiveSQLBench ground truth.\n"
    )
    (output_dir / "VALIDATION_REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
