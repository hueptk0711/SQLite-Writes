#!/usr/bin/env python3
"""Stage7E0-A3 English real-generation runner.

The runner is intentionally narrow: it loads the accepted Stage7C-A3 Phase O
prompt spec directly, uses the frozen V2-A1 Phase M prompt, performs at most one
Phase O call and one Phase M call per primary case, and evaluates the eight
fresh A3 English cases before any diagnostic case can be run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from nldbwrite_v3.inference.parse_output import extract_json_object  # noqa: E402
from nldbwrite_v3.v2_a1.compiler import compile_sqlite_program  # noqa: E402
from nldbwrite_v3.v2_a1.completeness import verify_completeness  # noqa: E402
from nldbwrite_v3.v2_a1.inventories import build_schema_inventory  # noqa: E402
from nldbwrite_v3.v2_a1.phase_m_output import parse_phase_m_output  # noqa: E402
from nldbwrite_v3.v2_a1.phase_o_output import parse_phase_o_output  # noqa: E402
from nldbwrite_v3.v2_a1.preflight import preflight_sqlite  # noqa: E402
from nldbwrite_v3.v2_a1.prompt_rendering import (  # noqa: E402
    inventory_payload,
    offset_guide,
    render_phase_m_messages,
    serialize_prompt_object,
    sha256_text,
)
from nldbwrite_v3.v2_a1.slot_inventory import build_slot_bundle  # noqa: E402
from nldbwrite_v3.v2_a1.span_validation import validate_and_sort_spans  # noqa: E402
from nldbwrite_v3.v2_a1.typed_materializer import materialize_ir_values  # noqa: E402
from nldbwrite_v3.v2_a1.types import V2A1Error  # noqa: E402


STAGE7C_A3_DIR = "Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT"
A3_PROMPT_SPEC_REL = f"{STAGE7C_A3_DIR}/PHASE_O_PROMPT_SPEC_A3_ENGLISH.json"
A3_SMOKE_SET_REL = f"{STAGE7C_A3_DIR}/FRESH_ENGLISH_A3_SMOKE_SET.jsonl"
MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"
MODEL_REVISION = "c03e6d358207e414f1eca0bb1891e29f1db0e242"
DEFAULT_MODEL_PATH = (
    "/home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/"
    f"snapshots/{MODEL_REVISION}"
)
EXPECTED_CHAT_TEMPLATE_SHA256 = (
    "cd8e9439f0570856fd70470bf8889ebd8b5d1107207f67a5efb46e342330527f"
)
EXPECTED_PRIMARY_COUNT = 8


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl", ".md", ".py", ".txt", ".toml", ".sh"}:
        data = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def git_output(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True, encoding="utf-8").strip()


def assert_git_lock(accepted_commit: str) -> dict[str, Any]:
    if not (PROJECT_ROOT / ".git").exists():
        return {"accepted_protocol_commit": accepted_commit, "execution_commit": None, "git_available": False}
    head = git_output("rev-parse", "HEAD")
    if head != accepted_commit:
        raise SystemExit(f"STOP: git HEAD {head} != accepted Stage7E0 commit {accepted_commit}")
    dirty = git_output("status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise SystemExit("STOP: tracked working tree must be clean before real generation")
    return {"accepted_protocol_commit": accepted_commit, "execution_commit": head, "git_available": True}


def assert_result_root_policy(result_root: Path, *, backend: str, allow_inside_git: bool) -> None:
    if allow_inside_git or backend == "mock":
        return
    try:
        result_root.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return
    raise SystemExit("STOP: real model result-root must be outside the git checkout")


def load_stage7c_a3_rows(root: Path = PROJECT_ROOT) -> list[dict[str, Any]]:
    rows = read_jsonl(root / A3_SMOKE_SET_REL)
    if len(rows) != EXPECTED_PRIMARY_COUNT:
        raise SystemExit(f"STOP: expected {EXPECTED_PRIMARY_COUNT} A3 primary rows, found {len(rows)}")
    for row in rows:
        if sorted(row.get("model_side_input", {})) != ["question", "schema_inventory"]:
            raise SystemExit(f"STOP: model-side leakage boundary changed for {row.get('sample_id')}")
    return rows


def verify_stage7c_a3_lock(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    spec = read_json(root / A3_PROMPT_SPEC_REL)
    lock = read_json(root / STAGE7C_A3_DIR / "STAGE7C_A3_LOCK.json")
    policy = read_json(root / STAGE7C_A3_DIR / "ACCEPTANCE_POLICY_A3.json")
    if spec.get("model_id") != MODEL_ID or spec.get("model_revision") != MODEL_REVISION:
        raise SystemExit("STOP: A3 prompt spec model identity drifted")
    checks = {
        "phase_o_system_prompt_sha256": sha256_text(spec["system_prompt"]),
        "phase_o_user_prompt_template_sha256": sha256_text(spec["user_prompt_template"]),
    }
    for key, digest in checks.items():
        if spec["prompt_hashes"].get(key) != digest:
            raise SystemExit(f"STOP: A3 prompt hash mismatch for {key}")
    if lock.get("stage7e0_a3_primary_acceptance") != "8/8 required; no average and no 7/8 acceptance":
        raise SystemExit("STOP: Stage7C A3 acceptance lock drifted")
    primary = policy.get("primary_stage7e0_a3_acceptance", {})
    if primary.get("required_pass_count") != "8/8" or primary.get("seven_of_eight_allowed") is not False:
        raise SystemExit("STOP: Stage7E0 A3 primary acceptance policy drifted")
    return {"a3_prompt_spec_sha256": sha256_file(root / A3_PROMPT_SPEC_REL), "stage7c_lock_sha256": sha256_file(root / STAGE7C_A3_DIR / "STAGE7C_A3_LOCK.json")}


def render_phase_o_a3_messages(question: str, model_side_input: dict[str, Any], *, root: Path = PROJECT_ROOT) -> tuple[list[dict[str, str]], str]:
    spec = read_json(root / A3_PROMPT_SPEC_REL)
    inventory = build_schema_inventory(model_side_input)
    user = spec["user_prompt_template"].format(
        question=question,
        offset_guide=offset_guide(question),
        schema_inventory=serialize_prompt_object(inventory_payload(inventory)),
    )
    messages = [{"role": "system", "content": spec["system_prompt"]}, {"role": "user", "content": user}]
    return messages, sha256_text(serialize_prompt_object(messages))


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


class TwoCallGenerator(Protocol):
    def generate(self, *, sample_id: str, phase: str, messages: list[dict[str, str]], max_new_tokens: int) -> CallResult:
        ...

    def metadata(self) -> dict[str, Any]:
        ...


class LabelMockGenerator:
    def __init__(self, rows: list[dict[str, Any]]):
        self.by_id = {row["sample_id"]: row for row in rows}

    def generate(self, *, sample_id: str, phase: str, messages: list[dict[str, str]], max_new_tokens: int) -> CallResult:
        del messages, max_new_tokens
        label = self.by_id[sample_id]["label_side_expected"]
        key = "phase_o" if phase == "phase_o" else "phase_m"
        raw = canonical_json(label[key])
        return CallResult(sample_id=sample_id, phase=phase, raw_output=raw, input_tokens=0, output_tokens=len(raw.split()))

    def metadata(self) -> dict[str, Any]:
        return {"backend": "mock", "model_called": False, "mock_uses_label_side_expected": True}


class TransformersChatGenerator:
    def __init__(self, *, model_name_or_path: str, quantization: str, trust_remote_code: bool, max_input_tokens: int, seed: int):
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        except ImportError as exc:  # pragma: no cover - server dependency
            raise SystemExit("STOP: real generation requires torch, transformers, accelerate, and bitsandbytes") from exc
        self.torch = torch
        self.seed = seed
        self.max_input_tokens = max_input_tokens
        local_model = Path(model_name_or_path).exists()
        if local_model and Path(model_name_or_path).name != MODEL_REVISION:
            raise SystemExit("STOP: local model snapshot path must end with the frozen revision")
        kwargs: dict[str, Any] = {"trust_remote_code": trust_remote_code}
        if not local_model:
            kwargs["revision"] = MODEL_REVISION
        self.tokenizer = AutoTokenizer.from_pretrained(model_name_or_path, **kwargs)
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.tokenizer.padding_side = "left"
        chat_template = getattr(self.tokenizer, "chat_template", None)
        if sha256_text(chat_template or "") != EXPECTED_CHAT_TEMPLATE_SHA256:
            raise SystemExit("STOP: tokenizer chat_template hash does not match Stage7D lock")
        model_kwargs: dict[str, Any] = {"trust_remote_code": trust_remote_code, "device_map": "auto"}
        if not local_model:
            model_kwargs["revision"] = MODEL_REVISION
        if quantization in {"4bit", "4-bit"}:
            model_kwargs["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16)
        elif torch.cuda.is_available():
            model_kwargs["torch_dtype"] = torch.float16
        self.model = AutoModelForCausalLM.from_pretrained(model_name_or_path, **model_kwargs)
        self.model.eval()
        self.model_name_or_path = model_name_or_path
        self.quantization = quantization

    def generate(self, *, sample_id: str, phase: str, messages: list[dict[str, str]], max_new_tokens: int) -> CallResult:
        torch = self.torch
        prompt = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        encoded_probe = self.tokenizer(prompt, add_special_tokens=True, truncation=False)
        original_tokens = len(encoded_probe["input_ids"])
        if original_tokens > self.max_input_tokens:
            return CallResult(sample_id=sample_id, phase=phase, raw_output="", status="input_too_long", error=f"{original_tokens}>{self.max_input_tokens}", input_tokens=original_tokens)
        encoded = self.tokenizer(prompt, return_tensors="pt", truncation=False)
        device = next(self.model.parameters()).device
        encoded = {key: value.to(device) for key, value in encoded.items()}
        torch.manual_seed(self.seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(self.seed)
        started = time.perf_counter()
        with torch.inference_mode():
            generated = self.model.generate(
                **encoded,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=self.tokenizer.pad_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )
        elapsed = time.perf_counter() - started
        output_ids = generated[0, encoded["input_ids"].shape[1] :]
        token_ids = [int(item) for item in output_ids.tolist()]
        eos = self.tokenizer.eos_token_id
        eos_ids = set(eos) if isinstance(eos, list) else ({int(eos)} if eos is not None else set())
        for index, token_id in enumerate(token_ids):
            if token_id in eos_ids:
                token_ids = token_ids[: index + 1]
                break
        raw = self.tokenizer.decode(token_ids, skip_special_tokens=True)
        return CallResult(sample_id=sample_id, phase=phase, raw_output=raw, input_tokens=original_tokens, output_tokens=len(token_ids), latency_sec=elapsed, hit_max_new_tokens=len(token_ids) >= max_new_tokens)

    def metadata(self) -> dict[str, Any]:
        torch = self.torch
        return {
            "backend": "hf",
            "model_called": True,
            "model_id": MODEL_ID,
            "model_name_or_path": self.model_name_or_path,
            "model_revision": MODEL_REVISION,
            "quantization": self.quantization,
            "chat_template_sha256": EXPECTED_CHAT_TEMPLATE_SHA256,
            "torch_version": torch.__version__,
            "transformers_version": __import__("transformers").__version__,
            "cuda_available": bool(torch.cuda.is_available()),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        }


def extracted_json_text(raw_output: str) -> tuple[str | None, str | None]:
    obj, error = extract_json_object(raw_output)
    if obj is None:
        return None, error
    return canonical_json(obj), None


def canonical_target_state(db_path: Path, sql: str, params: tuple[Any, ...], table_name: str) -> tuple[list[dict[str, Any]], str]:
    with sqlite3.connect(db_path) as source, sqlite3.connect(":memory:") as connection:
        source.backup(connection)
        connection.row_factory = sqlite3.Row
        connection.execute(sql, params)
        connection.commit()
        rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{table_name}" ORDER BY rowid')]
    return rows, sha256_text(canonical_json(rows))


def evaluate_primary_case(row: dict[str, Any], generator: TwoCallGenerator, result_root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    sample_id = row["sample_id"]
    question = row["model_side_input"]["question"]
    inventory = build_schema_inventory(row["model_side_input"])
    phase_o_messages, phase_o_prompt_hash = render_phase_o_a3_messages(question, row["model_side_input"])
    phase_o_call = generator.generate(sample_id=sample_id, phase="phase_o", messages=phase_o_messages, max_new_tokens=512)
    raw_o = asdict(phase_o_call) | {"messages_sha256": phase_o_prompt_hash}
    if phase_o_call.status != "success":
        return _failed_row(row, "phase_o_generation", phase_o_call.error, phase_o_prompt_hash), raw_o, None
    phase_o_text, error = extracted_json_text(phase_o_call.raw_output)
    if error:
        return _failed_row(row, "phase_o_json_extract", error, phase_o_prompt_hash), raw_o, None
    try:
        phase_o = parse_phase_o_output(phase_o_text or "")
        spans = validate_and_sort_spans(question, phase_o["value_spans"])
        slots = build_slot_bundle(spans)
    except V2A1Error as exc:
        return _failed_row(row, exc.reason_code, str(exc), phase_o_prompt_hash), raw_o, None
    phase_m_messages, phase_m_prompt_hash = render_phase_m_messages(phase_o["operation"], inventory, slots)
    phase_m_call = generator.generate(sample_id=sample_id, phase="phase_m", messages=phase_m_messages, max_new_tokens=1024)
    raw_m = asdict(phase_m_call) | {"messages_sha256": phase_m_prompt_hash}
    if phase_m_call.status != "success":
        return _failed_row(row, "phase_m_generation", phase_m_call.error, phase_o_prompt_hash, phase_m_prompt_hash), raw_o, raw_m
    phase_m_text, error = extracted_json_text(phase_m_call.raw_output)
    if error:
        return _failed_row(row, "phase_m_json_extract", error, phase_o_prompt_hash, phase_m_prompt_hash), raw_o, raw_m
    try:
        ir = parse_phase_m_output(phase_m_text or "", phase_o["operation"], inventory, slots)
        materialized = materialize_ir_values(ir, inventory, slots)
        verify_completeness(ir, slots)
        program = compile_sqlite_program(ir, inventory, materialized)
        db_path = PROJECT_ROOT / STAGE7C_A3_DIR / row["synthetic_db_spec"]["sqlite_db_path"]
        preflight = preflight_sqlite(db_path, program)
        observed, observed_hash = canonical_target_state(db_path, program.sql, program.parameters, row["label_side_expected"]["target_state"]["table_name"])
    except V2A1Error as exc:
        return _failed_row(row, exc.reason_code, str(exc), phase_o_prompt_hash, phase_m_prompt_hash), raw_o, raw_m
    expected = row["label_side_expected"]
    checks = {
        "phase_o_operation_exact": phase_o.get("operation") == expected["phase_o"]["operation"],
        "phase_o_spans_exact": phase_o.get("value_spans") == expected["phase_o"]["value_spans"],
        "no_extra_spans": len(phase_o.get("value_spans", [])) == len(expected["phase_o"]["value_spans"]),
        "phase_m_mapping_exact": ir == expected["phase_m"],
        "preflight_admitted": bool(preflight.admitted),
        "canonical_target_state_exact": observed == expected["target_state"]["typed_target_rows"],
    }
    passed = all(checks.values())
    result = {
        "sample_id": sample_id,
        "status": "PASS" if passed else "FAIL",
        "failure_stage": None if passed else "acceptance_gate",
        "checks": checks,
        "phase_o_predicted": phase_o,
        "phase_m_predicted": ir,
        "compiled_sql": program.sql,
        "compiled_parameters": list(program.parameters),
        "preflight_reason_code": preflight.reason_code,
        "observed_target_state_hash": observed_hash,
        "expected_target_state_hash": expected["target_state"]["target_state_hash"],
        "phase_o_messages_sha256": phase_o_prompt_hash,
        "phase_m_messages_sha256": phase_m_prompt_hash,
    }
    return result, raw_o, raw_m


def _failed_row(row: dict[str, Any], stage: str, error: str | None, phase_o_hash: str, phase_m_hash: str | None = None) -> dict[str, Any]:
    return {
        "sample_id": row["sample_id"],
        "status": "FAIL",
        "failure_stage": stage,
        "error": error,
        "checks": {},
        "phase_o_messages_sha256": phase_o_hash,
        "phase_m_messages_sha256": phase_m_hash,
    }


def run_stage7e0(args: argparse.Namespace) -> dict[str, Any]:
    result_root = Path(args.result_root).resolve()
    backend = str(args.backend).lower()
    assert_result_root_policy(result_root, backend=backend, allow_inside_git=args.allow_result_root_inside_git)
    git_lock = None if args.skip_git_assertions else assert_git_lock(args.accepted_protocol_commit)
    a3_lock = verify_stage7c_a3_lock()
    rows = load_stage7c_a3_rows()
    if result_root.exists() and not args.resume:
        raise SystemExit("STOP: result-root already exists; pass --resume only for infrastructure resume")
    result_root.mkdir(parents=True, exist_ok=True)
    generator: TwoCallGenerator
    if backend == "mock":
        generator = LabelMockGenerator(rows)
    elif backend == "hf":
        generator = TransformersChatGenerator(
            model_name_or_path=args.model_name_or_path,
            quantization=args.quantization,
            trust_remote_code=args.trust_remote_code,
            max_input_tokens=args.max_input_tokens,
            seed=args.seed,
        )
    else:
        raise SystemExit(f"STOP: unsupported backend {backend}")
    metadata = generator.metadata()
    if backend == "hf" and metadata.get("cuda_available") is not True:
        raise SystemExit("STOP: real Stage7E0-A3 generation requires cuda_available=true")
    write_json(
        result_root / "run_manifest.json",
        {
            "stage": "Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT",
            "accepted_protocol_commit": args.accepted_protocol_commit,
            "git": git_lock,
            "stage7c_a3_inputs": a3_lock,
            "model": metadata,
            "primary_case_count": len(rows),
            "primary_first_diagnostics_forbidden_until_freeze": True,
            "zero_shot": True,
            "retry": 0,
            "repair": "none",
            "phase_o_prompt_spec_path": A3_PROMPT_SPEC_REL,
            "phase_m_prompt_spec_path": "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json",
        },
    )
    case_results: list[dict[str, Any]] = []
    raw_o_rows: list[dict[str, Any]] = []
    raw_m_rows: list[dict[str, Any]] = []
    for row in rows:
        case_result, raw_o, raw_m = evaluate_primary_case(row, generator, result_root)
        case_results.append(case_result)
        raw_o_rows.append(raw_o)
        if raw_m is not None:
            raw_m_rows.append(raw_m)
        write_jsonl(result_root / "primary_case_results.jsonl", case_results)
        write_jsonl(result_root / "raw_phase_o_generations.jsonl", raw_o_rows)
        write_jsonl(result_root / "raw_phase_m_generations.jsonl", raw_m_rows)
    pass_count = sum(1 for row in case_results if row["status"] == "PASS")
    summary = {
        "stage": "Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT",
        "status": "PASS" if pass_count == EXPECTED_PRIMARY_COUNT else "FAIL",
        "backend": backend,
        "model_called": backend == "hf",
        "gpu_called": backend == "hf",
        "mock_uses_label_side_expected": backend == "mock",
        "primary_pass_count": f"{pass_count}/{EXPECTED_PRIMARY_COUNT}",
        "required_pass_count": "8/8",
        "seven_of_eight_allowed": False,
        "diagnostics_run": False,
        "gretel_pilot_opened": False,
        "raw_phase_o_sha256": sha256_file(result_root / "raw_phase_o_generations.jsonl"),
        "raw_phase_m_sha256": sha256_file(result_root / "raw_phase_m_generations.jsonl"),
        "primary_case_results_sha256": sha256_file(result_root / "primary_case_results.jsonl"),
    }
    write_json(result_root / "primary_summary.json", summary)
    if backend == "hf" and summary["status"] != "PASS":
        raise SystemExit("STOP: Stage7E0-A3 primary failed; do not open Gretel pilot")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accepted-protocol-commit", required=True)
    parser.add_argument("--result-root", required=True)
    parser.add_argument("--backend", choices=["hf", "mock"], default="hf")
    parser.add_argument("--model-name-or-path", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--quantization", default="4bit")
    parser.add_argument("--max-input-tokens", type=int, default=28672)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-git-assertions", action="store_true", help="Allowed for extracted reviewer packages and mock tests only.")
    parser.add_argument("--allow-result-root-inside-git", action="store_true", help="Allowed for mock tests only.")
    args = parser.parse_args()
    summary = run_stage7e0(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
