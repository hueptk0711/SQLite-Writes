from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nldbwrite_v3.v2_a1.compiler import compile_sqlite_program  # noqa: E402
from nldbwrite_v3.v2_a1.inventories import build_schema_inventory  # noqa: E402
from nldbwrite_v3.v2_a1.phase_m_output import parse_phase_m_output  # noqa: E402
from nldbwrite_v3.v2_a1.phase_o_output import parse_phase_o_output  # noqa: E402
from nldbwrite_v3.v2_a1.pipeline import run_mocked_pipeline  # noqa: E402
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


def canonical_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    return text.encode("utf-8")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


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
    return {
        "available": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def schema_input() -> dict[str, Any]:
    return {
        "question": "Alice 20",
        "schema_inventory": {
            "tables": [{"table_ref": "TAB_1", "table_name": "people"}],
            "columns": [
                {"column_ref": "COL_1", "column_name": "name", "source_type": "TEXT"},
                {"column_ref": "COL_2", "column_name": "age", "source_type": "INTEGER"},
            ],
            "constraints": [{"constraint_ref": "CONSTRAINT_1", "column_refs": ["COL_1"]}],
        },
    }


def deterministic_schema_smoke(root: Path) -> dict[str, Any]:
    phase_o = '{"operation":"INSERT","value_spans":[{"start_char":0,"end_char":5},{"start_char":6,"end_char":8}]}'
    phase_m = json.dumps(
        {
            "operation": "INSERT",
            "table_ref": "TAB_1",
            "assignments": [
                {"column_ref": "COL_1", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"},
                {"column_ref": "COL_2", "evidence_ref": "EV_2", "slot_ref": "SLOT_2"},
            ],
        },
        ensure_ascii=False,
    )
    result = run_mocked_pipeline(
        question="Alice 20",
        model_side_input=schema_input(),
        phase_o_output_json=phase_o,
        phase_m_output_json=phase_m,
        root=root,
    )
    return {"status": "PASS", "state": result.state, "sql": result.sql, "reason_code": result.reason_code}


def parse_attempt(kind: str, raw: str, root: Path, inventory: Any | None = None, slots: Any | None = None) -> dict[str, Any]:
    try:
        if kind == "phase_o":
            obj = parse_phase_o_output(raw, root=root)
        else:
            obj = parse_phase_m_output(raw, "INSERT", inventory, slots, root=root)
        return {"parse_status": "PASS", "parsed": obj}
    except V2A1Error as exc:
        return {"parse_status": "FAIL", "reason_code": exc.reason_code, "message": str(exc), "details": exc.details}
    except Exception as exc:
        return {"parse_status": "FAIL", "reason_code": "unexpected_parse_error", "message": repr(exc)}


def generate_once(model: Any, tokenizer: Any, messages: list[dict[str, str]], *, max_new_tokens: int) -> dict[str, Any]:
    import torch

    rendered = render_chat_prompt_with_tokenizer(tokenizer, messages)
    inputs = tokenizer(rendered, return_tensors="pt")
    device = next(model.parameters()).device
    inputs = {key: value.to(device) for key, value in inputs.items()}
    start = time.monotonic()
    with torch.inference_mode():
        output = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    latency = time.monotonic() - start
    prompt_tokens = int(inputs["input_ids"].shape[-1])
    generated_ids = output[0][prompt_tokens:]
    raw = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
    return {
        "rendered_chat_prompt_sha256": rendered_prompt_sha256(rendered),
        "prompt_tokens": prompt_tokens,
        "output_tokens": int(generated_ids.shape[-1]),
        "latency_seconds": latency,
        "hit_max_new_tokens": int(generated_ids.shape[-1]) >= max_new_tokens,
        "raw_output": raw,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage7E0 V2-A1 real generation preflight.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "stage7e0_real_generation_preflight")
    parser.add_argument("--phase-o-max-new-tokens", type=int, default=512)
    parser.add_argument("--phase-m-max-new-tokens", type=int, default=8192)
    parser.add_argument("--skip-model-load", action="store_true", help="Only run deterministic lock/hash checks; not acceptable as final Stage7E0 GPU preflight.")
    args = parser.parse_args()

    root = args.root.resolve()
    output_dir = args.output_dir.resolve()
    violations: list[str] = []
    smoke_path = output_dir / "SMOKE_GENERATIONS.jsonl"
    if smoke_path.exists():
        smoke_path.unlink()

    try:
        initialize_v2_a1_runtime(root)
    except Exception as exc:
        violations.append(f"runtime_integrity_failed:{exc!r}")

    generation_protocol = read_json(root / "stage7c_a1_v2_development_protocol/GENERATION_PROTOCOL_A1.json")
    chat_preflight = read_json(root / "stage7d_v2_a1_implementation/CHAT_TEMPLATE_PREFLIGHT.json")
    model_config = generation_protocol["model_config"]
    for key, expected in {
        "model_id": MODEL_ID,
        "model_revision": REVISION,
        "tokenizer_revision": REVISION,
        "tokenizer_config_file_sha256": TOKENIZER_CONFIG_SHA256,
    }.items():
        if model_config.get(key) != expected:
            violations.append(f"generation_protocol_{key}_mismatch")
    if chat_preflight.get("actual_chat_template_string_sha256") != CHAT_TEMPLATE_SHA256:
        violations.append("stage7d_chat_template_hash_mismatch")

    env = {
        "stage": STAGE,
        "platform": platform.platform(),
        "python": sys.version,
        "executable": sys.executable,
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

    try:
        deterministic = deterministic_schema_smoke(root)
    except Exception as exc:
        deterministic = {"status": "FAIL", "error": repr(exc)}
        violations.append("deterministic_schema_smoke_failed")
    write_json(output_dir / "DETERMINISTIC_SCHEMA_SMOKE.json", deterministic)

    if args.skip_model_load:
        violations.append("model_load_skipped")
    else:
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
        model = AutoModelForCausalLM.from_pretrained(
            str(args.model_path),
            device_map="auto",
            torch_dtype="auto",
            trust_remote_code=False,
        )
        model.eval()
        load_seconds = time.monotonic() - load_started

        inventory = build_schema_inventory(schema_input())
        phase_o_messages, phase_o_messages_hash = render_phase_o_prompt("Alice 20", inventory, root=root)
        phase_o_generation = generate_once(model, tokenizer, phase_o_messages, max_new_tokens=args.phase_o_max_new_tokens)
        phase_o_generation.update(
            {
                "phase": "phase_o",
                "sample_id": "synthetic_stage7e0_smoke_0001",
                "messages_sha256": phase_o_messages_hash,
                "parse_attempt": parse_attempt("phase_o", phase_o_generation["raw_output"], root),
            }
        )
        append_jsonl(smoke_path, phase_o_generation)

        spans = validate_and_sort_spans("Alice 20", [{"start_char": 0, "end_char": 5}, {"start_char": 6, "end_char": 8}])
        slots = build_slot_bundle(spans)
        phase_m_messages, phase_m_messages_hash = render_phase_m_prompt("INSERT", inventory, slots, root=root)
        phase_m_generation = generate_once(model, tokenizer, phase_m_messages, max_new_tokens=args.phase_m_max_new_tokens)
        phase_m_generation.update(
            {
                "phase": "phase_m",
                "sample_id": "synthetic_stage7e0_smoke_0001",
                "messages_sha256": phase_m_messages_hash,
                "parse_attempt": parse_attempt("phase_m", phase_m_generation["raw_output"], root, inventory, slots),
            }
        )
        append_jsonl(smoke_path, phase_m_generation)

        materialized = materialize_ir_values(
            {
                "operation": "INSERT",
                "table_ref": "TAB_1",
                "assignments": [
                    {"column_ref": "COL_1", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"},
                    {"column_ref": "COL_2", "evidence_ref": "EV_2", "slot_ref": "SLOT_2"},
                ],
            },
            inventory,
            slots,
        )
        program = compile_sqlite_program(
            {
                "operation": "INSERT",
                "table_ref": "TAB_1",
                "assignments": [
                    {"column_ref": "COL_1", "evidence_ref": "EV_1", "slot_ref": "SLOT_1"},
                    {"column_ref": "COL_2", "evidence_ref": "EV_2", "slot_ref": "SLOT_2"},
                ],
            },
            inventory,
            materialized,
        )
        write_json(
            output_dir / "MODEL_LOAD_AND_TOKENIZER_PREFLIGHT.json",
            {
                "status": "PASS",
                "model_id": MODEL_ID,
                "model_path": str(args.model_path),
                "model_revision": REVISION,
                "tokenizer_config_sha256": sha256_file(tokenizer_config),
                "chat_template_sha256": chat_template_hash,
                "load_seconds": load_seconds,
                "synthetic_compiled_sql": program.sql,
                "synthetic_compiled_parameters": list(program.parameters),
            },
        )

    result = {
        "stage": STAGE,
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "model_called": not args.skip_model_load,
        "gpu_called": not args.skip_model_load,
        "generation_run": not args.skip_model_load,
        "train_dev_generation_run": False,
        "confirmation_481_evaluated": False,
        "live_sql_bench_gt_opened": False,
        "deterministic_schema_smoke_status": deterministic.get("status"),
        "smoke_generations_path": str(smoke_path) if smoke_path.exists() else None,
    }
    write_json(output_dir / "PREFLIGHT_RESULT.json", result)
    report = (
        "# Stage7E0 V2-A1 Real Generation Preflight\n\n"
        f"Status: {result['status']}\n\n"
        f"violations: {json.dumps(violations, ensure_ascii=False)}\n\n"
        "Scope: synthetic infrastructure smoke only; no train/dev generation, no 481 confirmation evaluation, and no LiveSQLBench ground truth.\n"
    )
    (output_dir / "VALIDATION_REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
