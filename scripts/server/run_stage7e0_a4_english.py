#!/usr/bin/env python3
"""Stage7E0-A4 candidate-span real-generation preflight runner."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
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
from nldbwrite_v3.v2_a1.phase_m_schema import dynamic_schema  # noqa: E402
from nldbwrite_v3.v2_a1.preflight import preflight_sqlite  # noqa: E402
from nldbwrite_v3.v2_a1.prompt_rendering import (  # noqa: E402
    inventory_payload,
    render_chat_prompt_with_tokenizer,
    render_phase_m_messages,
    rendered_prompt_sha256,
    serialize_prompt_object,
    sha256_text,
)
from nldbwrite_v3.v2_a1.slot_inventory import build_slot_bundle  # noqa: E402
from nldbwrite_v3.v2_a1.typed_materializer import materialize_ir_values  # noqa: E402
from nldbwrite_v3.v2_a1.types import AcceptedSpan, V2A1Error  # noqa: E402
from scripts.data.build_stage7b_a2_candidate_span_reference import CandidateSpan, resolve_selected_span_refs  # noqa: E402
from scripts.server.run_stage7e0_v2_a1_preflight import (  # noqa: E402
    IncrementalJsonSchemaGrammarBackend,
    build_phase_m_constraint_grammar,
)


STAGE7C_A4_DIR = "Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL"
A4_PROMPT_SPEC_REL = f"{STAGE7C_A4_DIR}/PHASE_O_PROMPT_SPEC_A4_ENGLISH.json"
A4_SMOKE_SET_REL = f"{STAGE7C_A4_DIR}/FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl"
MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"
MODEL_REVISION = "c03e6d358207e414f1eca0bb1891e29f1db0e242"
DEFAULT_MODEL_PATH = (
    "/home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/"
    f"snapshots/{MODEL_REVISION}"
)
EXPECTED_CHAT_TEMPLATE_SHA256 = "cd8e9439f0570856fd70470bf8889ebd8b5d1107207f67a5efb46e342330527f"
EXPECTED_PRIMARY_COUNT = 10
PHASE_O_MAX_NEW_TOKENS = 512
PHASE_M_MAX_NEW_TOKENS = 8192
CONSTRAINED_BACKEND_ID = "incremental_json_schema_grammar"
FROZEN_RUNTIME_VERSIONS = {
    "torch": "2.6.0+cu124",
    "transformers": "5.5.3",
    "tokenizers": "0.22.2",
    "accelerate": "1.14.0",
    "safetensors": "0.5.3",
}
KAGGLE_T4X2_RUNTIME_VERSIONS = {
    "torch": "2.13.0",
    "transformers": "5.5.3",
    "tokenizers": "0.22.2",
    "accelerate": "1.14.0",
    "safetensors": "0.5.3",
}
ALLOWED_FROZEN_RUNTIME_PROFILES = [
    {
        "profile_id": "uet_server_cuda124",
        "packages": FROZEN_RUNTIME_VERSIONS,
        "torch_cuda": "12.4",
        "gpu_requirement": "cuda_available=true",
    },
    {
        "profile_id": "kaggle_t4x2_cuda130",
        "packages": KAGGLE_T4X2_RUNTIME_VERSIONS,
        "torch_cuda": "13.0",
        "gpu_requirement": "two Tesla T4 devices expected on Kaggle",
    },
]


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


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
        data = canonical_text(data.decode("utf-8-sig")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def git_output(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True, encoding="utf-8").strip()


def assert_git_lock(accepted_commit: str) -> dict[str, Any]:
    if not (PROJECT_ROOT / ".git").exists():
        return {"accepted_protocol_commit": accepted_commit, "execution_commit": None, "git_available": False}
    head = git_output("rev-parse", "HEAD")
    if head != accepted_commit:
        raise SystemExit(f"STOP: git HEAD {head} != accepted Stage7E0-A4 commit {accepted_commit}")
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


def validate_generation_config(args: argparse.Namespace) -> None:
    backend = str(args.backend).lower()
    if backend == "hf":
        raise SystemExit("STOP: backend=hf is plain unconstrained HF and is forbidden for Stage7E0-A4")
    if backend not in {"constrained_hf", "mock"}:
        raise SystemExit(f"STOP: unsupported backend {backend}")
    if int(args.phase_o_max_new_tokens) != PHASE_O_MAX_NEW_TOKENS:
        raise SystemExit(f"STOP: Phase O max_new_tokens must remain {PHASE_O_MAX_NEW_TOKENS}")
    if int(args.phase_m_max_new_tokens) != PHASE_M_MAX_NEW_TOKENS:
        raise SystemExit(f"STOP: Phase M max_new_tokens must remain {PHASE_M_MAX_NEW_TOKENS}")
    if backend == "constrained_hf" and str(args.quantization).lower() not in {"none", ""}:
        raise SystemExit("STOP: Stage7E0-A4 forbids quantized generation")
    if backend == "constrained_hf" and bool(args.resume):
        raise SystemExit("STOP: Stage7E0-A4 forbids --resume; archive partial output and start a fresh result-root")


def runtime_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in FROZEN_RUNTIME_VERSIONS:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    try:
        import torch

        versions["torch_cuda"] = str(torch.version.cuda)
    except Exception:
        versions["torch_cuda"] = None
    return versions


def validate_runtime_versions(versions: dict[str, str | None] | None = None) -> dict[str, Any]:
    observed = runtime_versions() if versions is None else dict(versions)
    profile_mismatches: dict[str, dict[str, Any]] = {}
    for profile in ALLOWED_FROZEN_RUNTIME_PROFILES:
        package_mismatches = {
            package: {"expected": expected, "observed": observed.get(package)}
            for package, expected in profile["packages"].items()
            if observed.get(package) != expected
        }
        cuda_expected = profile.get("torch_cuda")
        if cuda_expected is not None and observed.get("torch_cuda") != cuda_expected:
            package_mismatches["torch_cuda"] = {"expected": cuda_expected, "observed": observed.get("torch_cuda")}
        if not package_mismatches:
            return {
                "status": "PASS",
                "runtime_profile_id": profile["profile_id"],
                "frozen_runtime_versions": dict(profile["packages"]),
                "allowed_frozen_runtime_profiles": ALLOWED_FROZEN_RUNTIME_PROFILES,
                "observed_runtime_versions": observed,
            }
        profile_mismatches[str(profile["profile_id"])] = package_mismatches
    raise SystemExit(f"STOP: frozen inference runtime version drift: {canonical_json(profile_mismatches)}")


def load_stage7c_a4_rows(root: Path = PROJECT_ROOT) -> list[dict[str, Any]]:
    rows = read_jsonl(root / A4_SMOKE_SET_REL)
    if len(rows) != EXPECTED_PRIMARY_COUNT:
        raise SystemExit(f"STOP: expected {EXPECTED_PRIMARY_COUNT} A4 primary rows, found {len(rows)}")
    for row in rows:
        if set(row.get("model_side_input", {})) != {"question", "schema_inventory", "candidate_inventory_text"}:
            raise SystemExit(f"STOP: model-side leakage boundary changed for {row.get('sample_id')}")
        if sorted(row["label_side_expected"]["phase_o"]) != ["operation", "span_refs"]:
            raise SystemExit(f"STOP: A4 Phase O label contract drifted for {row.get('sample_id')}")
    return rows


def verify_stage7c_a4_lock(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    spec = read_json(root / A4_PROMPT_SPEC_REL)
    lock = read_json(root / STAGE7C_A4_DIR / "STAGE7C_A4_LOCK.json")
    policy = read_json(root / STAGE7C_A4_DIR / "ACCEPTANCE_POLICY_A4.json")
    if spec.get("model_id") != MODEL_ID or spec.get("model_revision") != MODEL_REVISION:
        raise SystemExit("STOP: A4 prompt spec model identity drifted")
    checks = {
        "phase_o_system_prompt_sha256": sha256_text(spec["system_prompt"]),
        "phase_o_user_prompt_template_sha256": sha256_text(spec["user_prompt_template"]),
    }
    for key, digest in checks.items():
        if spec["prompt_hashes"].get(key) != digest:
            raise SystemExit(f"STOP: A4 prompt hash mismatch for {key}")
    gate = policy.get("synthetic_feasibility_gate", {})
    if gate.get("required_pass_count") != "10/10" or gate.get("nine_of_ten_allowed") is not False:
        raise SystemExit("STOP: Stage7E0-A4 primary acceptance policy drifted")
    if lock.get("phase_o_output_keys") != ["operation", "span_refs"]:
        raise SystemExit("STOP: Stage7C-A4 Phase O output contract drifted")
    return {
        "a4_prompt_spec_sha256": sha256_file(root / A4_PROMPT_SPEC_REL),
        "stage7c_a4_lock_sha256": sha256_file(root / STAGE7C_A4_DIR / "STAGE7C_A4_LOCK.json"),
        "a4_smoke_set_sha256": sha256_file(root / A4_SMOKE_SET_REL),
    }


def render_phase_o_a4_messages(row: dict[str, Any], *, root: Path = PROJECT_ROOT) -> tuple[list[dict[str, str]], str]:
    spec = read_json(root / A4_PROMPT_SPEC_REL)
    inventory = build_schema_inventory(row["model_side_input"]["schema_inventory"])
    user = spec["user_prompt_template"].format(
        question=row["model_side_input"]["question"],
        schema_inventory=serialize_prompt_object(inventory_payload(inventory)),
        candidate_inventory=row["model_side_input"]["candidate_inventory_text"],
    )
    messages = [{"role": "system", "content": spec["system_prompt"]}, {"role": "user", "content": user}]
    return messages, sha256_text(serialize_prompt_object(messages))


def candidate_records(row: dict[str, Any]) -> list[CandidateSpan]:
    return [
        CandidateSpan(
            span_ref=str(candidate["span_ref"]),
            start_char=int(candidate["start_char"]),
            end_char=int(candidate["end_char"]),
            text=str(candidate["text"]),
            tags=tuple(str(tag) for tag in candidate.get("tags", [])),
            provenance_tags=tuple(str(tag) for tag in candidate.get("provenance_tags", [])),
        )
        for candidate in row["runtime_constraints"]["candidate_inventory"]
    ]


def parse_phase_o_span_ref_output(raw: str, allowed_refs: list[str]) -> dict[str, Any]:
    obj, error = extract_json_object(raw)
    if obj is None:
        raise V2A1Error("phase_o_json_extract", error or "Could not extract JSON object")
    if set(obj) != {"operation", "span_refs"}:
        raise V2A1Error("phase_o_schema_failure", "A4 Phase O output must contain only operation and span_refs")
    if obj["operation"] != "INSERT":
        raise V2A1Error("phase_o_schema_failure", "A4 Phase O operation must be INSERT")
    refs = obj["span_refs"]
    if not isinstance(refs, list) or not refs:
        raise V2A1Error("phase_o_schema_failure", "span_refs must be a non-empty list")
    if not all(isinstance(ref, str) for ref in refs):
        raise V2A1Error("phase_o_schema_failure", "span_refs must contain strings")
    if len(refs) != len(set(refs)):
        raise V2A1Error("phase_o_duplicate_span_refs", "Duplicate span_refs are forbidden")
    unknown = [ref for ref in refs if ref not in set(allowed_refs)]
    if unknown:
        raise V2A1Error("phase_o_unknown_span_refs", "Unknown span_refs are forbidden", details={"unknown": unknown})
    return {"operation": "INSERT", "span_refs": refs}


def _literal_status(text: str, literal: str, pos: int) -> tuple[str, int]:
    if pos >= len(text):
        return "prefix", pos
    end = min(len(text), pos + len(literal))
    fragment = text[pos:end]
    if not literal.startswith(fragment):
        return "invalid", pos
    if len(fragment) < len(literal):
        return "prefix", len(text)
    return "complete", pos + len(literal)


def _enum_status(text: str, values: list[str], pos: int) -> list[tuple[str, int]]:
    if pos >= len(text):
        return [("prefix", pos)]
    results: list[tuple[str, int]] = []
    for value in values:
        end = min(len(text), pos + len(value))
        fragment = text[pos:end]
        if value.startswith(fragment):
            results.append(("prefix", len(text)) if len(fragment) < len(value) else ("complete", pos + len(value)))
    return results


def _sequence_status(text: str, pos: int, parts: list[Any]) -> list[tuple[str, int]]:
    states = [("complete", pos)]
    for part in parts:
        next_states: list[tuple[str, int]] = []
        for _status, state_pos in states:
            if isinstance(part, str):
                result = _literal_status(text, part, state_pos)
                if result[0] != "invalid":
                    next_states.append(result)
            else:
                next_states.extend(part(text, state_pos))
        if not next_states:
            return []
        if any(status == "prefix" for status, _pos in next_states):
            return next_states
        states = next_states
    return states


def _array_plus_status(text: str, pos: int, item_parser: Any) -> list[tuple[str, int]]:
    item_results = item_parser(text, pos)
    if not item_results:
        return []
    results: list[tuple[str, int]] = []
    for status, item_end in item_results:
        if status == "prefix":
            results.append((status, item_end))
            continue
        if item_end >= len(text):
            results.append(("prefix", item_end))
            continue
        close_result = _literal_status(text, "]", item_end)
        if close_result[0] != "invalid":
            results.append(close_result)
        comma_result = _literal_status(text, ",", item_end)
        if comma_result[0] == "prefix":
            results.append(comma_result)
        elif comma_result[0] == "complete":
            results.extend(_array_plus_status(text, comma_result[1], item_parser))
    return results


def _overall_status(results: list[tuple[str, int]], text: str) -> str:
    if any(status == "complete" and pos == len(text) for status, pos in results):
        return "complete"
    if any(status == "prefix" and pos <= len(text) for status, pos in results):
        return "prefix"
    return "invalid"


def phase_o_span_ref_prefix_status(text: str, operations: list[str], span_refs: list[str]) -> str:
    item_parser = lambda value, value_pos: _sequence_status(value, value_pos, ['"', lambda v, p: _enum_status(v, span_refs, p), '"'])
    results = _sequence_status(
        text,
        0,
        ['{"operation":"', lambda value, value_pos: _enum_status(value, operations, value_pos), '","span_refs":[', lambda value, value_pos: _array_plus_status(value, value_pos, item_parser), "}"],
    )
    return _overall_status(results, text)


@dataclass(frozen=True)
class SpanRefConstraintGrammar:
    phase: str
    schema_sha256: str
    operation_choices: list[str]
    span_ref_choices: list[str]

    @property
    def fingerprint(self) -> str:
        return sha256_text(canonical_json({"phase": self.phase, "schema_sha256": self.schema_sha256, "span_ref_choices": self.span_ref_choices}))

    def status(self, text: str) -> str:
        return phase_o_span_ref_prefix_status(text, self.operation_choices, self.span_ref_choices)

    def is_prefix(self, text: str) -> bool:
        return self.status(text) in {"prefix", "complete"}

    def is_complete(self, text: str) -> bool:
        return self.status(text) == "complete"

    def metadata(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "constraint_source": "json_schema_plus_dynamic_candidate_span_ref_domain",
            "schema_sha256": self.schema_sha256,
            "constraint_grammar_sha256": self.fingerprint,
            "constraint_space_singleton": False,
            "semantic_branch_points_observed": len(self.span_ref_choices) > 1,
            "finite_known_answer_candidates": False,
            "finite_complete_object_enumeration": False,
            "label_side_data_used_for_constraints": False,
            "hard_max_semantic_spans": None,
            "complete_object_candidate_count": None,
            "branching_evidence": {"operation_choices": self.operation_choices, "span_ref_choice_count": len(self.span_ref_choices)},
            "capacity": {"phase_o_schema_min_items": 1, "phase_o_unique_items_postparse_validated": True, "backend_supports_more_than_two_spans": True},
        }


def _schema_values(schema: dict[str, Any], *path: str) -> list[str]:
    node: Any = schema
    for part in path:
        node = node[part]
    values = node.get("enum")
    if values is None and "const" in node:
        values = [node["const"]]
    if not isinstance(values, list) or not values:
        raise V2A1Error("constraint_schema_enum_missing", "A4 constrained generation requires finite enum/const choices")
    return [str(value) for value in values]


def build_phase_o_span_ref_constraint_grammar(schema: dict[str, Any]) -> SpanRefConstraintGrammar:
    return SpanRefConstraintGrammar(
        phase="phase_o",
        schema_sha256=sha256_text(canonical_json(schema)),
        operation_choices=_schema_values(schema, "properties", "operation"),
        span_ref_choices=_schema_values(schema, "properties", "span_refs", "items"),
    )


def generate_constrained_a4(
    model: Any,
    tokenizer: Any,
    messages: list[dict[str, str]],
    *,
    max_new_tokens: int,
    schema: dict[str, Any],
) -> dict[str, Any]:
    import torch

    constraint_grammar = build_phase_o_span_ref_constraint_grammar(schema)
    rendered = render_chat_prompt_with_tokenizer(tokenizer, messages)
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
    latency = time.monotonic() - start
    generated_ids = output[0][prompt_tokens:]
    return {
        "backend": backend.metadata(),
        "rendered_chat_prompt_sha256": rendered_prompt_sha256(rendered),
        "prompt_tokens": prompt_tokens,
        "output_tokens": int(generated_ids.shape[-1]),
        "latency_seconds": latency,
        "hit_max_new_tokens": int(generated_ids.shape[-1]) >= max_new_tokens,
        "raw_output": tokenizer.decode(generated_ids, skip_special_tokens=True).strip(),
    }


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


class TwoCallGenerator(Protocol):
    def generate(self, *, sample_id: str, phase: str, messages: list[dict[str, str]], max_new_tokens: int, row: dict[str, Any] | None = None, operation: str | None = None, inventory: Any | None = None, slots: Any | None = None) -> CallResult:
        ...

    def metadata(self) -> dict[str, Any]:
        ...


class LabelMockGenerator:
    def __init__(self, rows: list[dict[str, Any]]):
        self.by_id = {row["sample_id"]: row for row in rows}

    def generate(self, *, sample_id: str, phase: str, messages: list[dict[str, str]], max_new_tokens: int, row: dict[str, Any] | None = None, operation: str | None = None, inventory: Any | None = None, slots: Any | None = None) -> CallResult:
        del messages, max_new_tokens, row, operation, inventory, slots
        label = self.by_id[sample_id]["label_side_expected"]
        raw = canonical_json(label["phase_o" if phase == "phase_o" else "phase_m"])
        return CallResult(
            sample_id=sample_id,
            phase=phase,
            raw_output=raw,
            input_tokens=0,
            output_tokens=len(raw.split()),
            generation_metadata={
                "backend": "mock",
                "token_level_enforcement": False,
                "fallback_to_unconstrained": False,
                "finite_complete_object_enumeration": False,
                "finite_known_answer_candidates": False,
                "label_side_data_used_for_constraints": True,
                "automatic_repair": False,
                "retry": 0,
            },
        )

    def metadata(self) -> dict[str, Any]:
        return {"backend": "mock", "model_called": False, "mock_uses_label_side_expected": True}


class ConstrainedTransformersChatGenerator:
    def __init__(self, *, model_name_or_path: str, quantization: str, trust_remote_code: bool, max_input_tokens: int, seed: int):
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - server dependency
            raise SystemExit("STOP: constrained real generation requires torch, transformers, and accelerate") from exc
        self.runtime_lock = validate_runtime_versions()
        if quantization not in {"none", "None", ""}:
            raise SystemExit("STOP: Stage7E0-A4 forbids 4-bit or any quantized generation")
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
        if sha256_text(getattr(self.tokenizer, "chat_template", None) or "") != EXPECTED_CHAT_TEMPLATE_SHA256:
            raise SystemExit("STOP: tokenizer chat_template hash does not match Stage7D lock")
        model_kwargs: dict[str, Any] = {"trust_remote_code": trust_remote_code, "device_map": "auto"}
        if not local_model:
            model_kwargs["revision"] = MODEL_REVISION
        if torch.cuda.is_available():
            model_kwargs["torch_dtype"] = "auto"
        self.model = AutoModelForCausalLM.from_pretrained(model_name_or_path, **model_kwargs)
        self.model.eval()
        self.model_name_or_path = model_name_or_path
        self.quantization = "none"

    def generate(self, *, sample_id: str, phase: str, messages: list[dict[str, str]], max_new_tokens: int, row: dict[str, Any] | None = None, operation: str | None = None, inventory: Any | None = None, slots: Any | None = None) -> CallResult:
        torch = self.torch
        prompt = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        token_count = len(self.tokenizer(prompt, add_special_tokens=True, truncation=False)["input_ids"])
        if token_count > self.max_input_tokens:
            return CallResult(sample_id=sample_id, phase=phase, raw_output="", status="input_too_long", error=f"{token_count}>{self.max_input_tokens}", input_tokens=token_count)
        torch.manual_seed(self.seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(self.seed)
        if phase == "phase_o":
            if row is None:
                raise SystemExit("STOP: A4 Phase O constrained generation requires row runtime schema")
            generated = generate_constrained_a4(self.model, self.tokenizer, messages, max_new_tokens=max_new_tokens, schema=row["runtime_constraints"]["phase_o_schema"])
        elif phase == "phase_m":
            if operation is None or inventory is None or slots is None:
                raise SystemExit("STOP: Phase M constrained generation requires operation, inventory, and slots")
            schema = dynamic_schema(operation, inventory, slots, root=PROJECT_ROOT)
            from scripts.server.run_stage7e0_v2_a1_preflight import generate_constrained

            generated = generate_constrained(self.model, self.tokenizer, messages, max_new_tokens=max_new_tokens, schema=schema, phase="phase_m", operation=operation, inventory=inventory, slots=slots, root=PROJECT_ROOT)
        else:
            raise SystemExit(f"STOP: unsupported constrained phase {phase}")
        return CallResult(
            sample_id=sample_id,
            phase=phase,
            raw_output=str(generated["raw_output"]),
            input_tokens=int(generated["prompt_tokens"]),
            output_tokens=int(generated["output_tokens"]),
            latency_sec=float(generated["latency_seconds"]),
            hit_max_new_tokens=bool(generated["hit_max_new_tokens"]),
            generation_metadata=generated["backend"],
        )

    def metadata(self) -> dict[str, Any]:
        torch = self.torch
        return {
            "backend": CONSTRAINED_BACKEND_ID,
            "schema_enforcement_mode": "transformers_prefix_allowed_tokens_fn",
            "token_level_enforcement": True,
            "fallback_to_unconstrained": False,
            "finite_complete_object_enumeration": False,
            "finite_known_answer_candidates": False,
            "label_side_data_used_for_constraints": False,
            "automatic_repair": False,
            "retry": 0,
            "model_called": True,
            "model_id": MODEL_ID,
            "model_name_or_path": self.model_name_or_path,
            "model_revision": MODEL_REVISION,
            "quantization": self.quantization,
            "torch_dtype": "auto",
            "runtime_lock": self.runtime_lock,
            "runtime_profile_id": self.runtime_lock["runtime_profile_id"],
            "allowed_frozen_runtime_profiles": ALLOWED_FROZEN_RUNTIME_PROFILES,
            "chat_template_sha256": EXPECTED_CHAT_TEMPLATE_SHA256,
            "torch_version": torch.__version__,
            "cuda_runtime": str(torch.version.cuda),
            "transformers_version": __import__("transformers").__version__,
            "tokenizers_version": importlib.metadata.version("tokenizers"),
            "accelerate_version": importlib.metadata.version("accelerate"),
            "safetensors_version": importlib.metadata.version("safetensors"),
            "cuda_available": bool(torch.cuda.is_available()),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "gpu_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
            "gpu_devices": [torch.cuda.get_device_name(index) for index in range(torch.cuda.device_count())] if torch.cuda.is_available() else [],
        }


def canonical_target_state(db_path: Path, sql: str, params: tuple[Any, ...], table_name: str) -> tuple[list[dict[str, Any]], str]:
    with sqlite3.connect(db_path) as source, sqlite3.connect(":memory:") as connection:
        source.backup(connection)
        connection.row_factory = sqlite3.Row
        connection.execute(sql, params)
        connection.commit()
        rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{table_name}" ORDER BY rowid')]
    return rows, sha256_text(canonical_json(rows))


def canonical_span_ref_selection(row: dict[str, Any], span_refs: list[str]) -> tuple[str, ...]:
    selected = resolve_selected_span_refs(candidate_records(row), span_refs)
    return tuple(candidate.span_ref for candidate in selected)


def canonical_insert_mapping(ir: dict[str, Any]) -> tuple[str, str, tuple[tuple[str, str, str], ...]]:
    return (
        str(ir["operation"]),
        str(ir["table_ref"]),
        tuple(
            sorted(
                (
                    str(assignment["slot_ref"]),
                    str(assignment["evidence_ref"]),
                    str(assignment["column_ref"]),
                )
                for assignment in ir["assignments"]
            )
        ),
    )


def _failed_row(row: dict[str, Any], stage: str, error: str | None, phase_o_hash: str, phase_m_hash: str | None = None) -> dict[str, Any]:
    return {"sample_id": row["sample_id"], "status": "FAIL", "failure_stage": stage, "error": error, "checks": {}, "phase_o_messages_sha256": phase_o_hash, "phase_m_messages_sha256": phase_m_hash}


def evaluate_primary_case(row: dict[str, Any], generator: TwoCallGenerator, *, phase_o_max_new_tokens: int, phase_m_max_new_tokens: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    sample_id = row["sample_id"]
    inventory = build_schema_inventory(row["model_side_input"]["schema_inventory"])
    phase_o_messages, phase_o_prompt_hash = render_phase_o_a4_messages(row)
    phase_o_call = generator.generate(sample_id=sample_id, phase="phase_o", messages=phase_o_messages, max_new_tokens=phase_o_max_new_tokens, row=row)
    raw_o = asdict(phase_o_call) | {"messages_sha256": phase_o_prompt_hash}
    if phase_o_call.status != "success":
        return _failed_row(row, "phase_o_generation", phase_o_call.error, phase_o_prompt_hash), raw_o, None
    candidate_refs = [candidate["span_ref"] for candidate in row["runtime_constraints"]["candidate_inventory"]]
    try:
        phase_o = parse_phase_o_span_ref_output(phase_o_call.raw_output, candidate_refs)
        selected = resolve_selected_span_refs(candidate_records(row), phase_o["span_refs"])
        spans = tuple(AcceptedSpan(start_char=item.start_char, end_char=item.end_char, text=item.text) for item in selected)
        slots = build_slot_bundle(spans)
    except (V2A1Error, ValueError) as exc:
        reason = exc.reason_code if isinstance(exc, V2A1Error) else "phase_o_span_ref_resolution"
        return _failed_row(row, reason, str(exc), phase_o_prompt_hash), raw_o, None
    phase_m_messages, phase_m_prompt_hash = render_phase_m_messages(phase_o["operation"], inventory, slots)
    phase_m_call = generator.generate(sample_id=sample_id, phase="phase_m", messages=phase_m_messages, max_new_tokens=phase_m_max_new_tokens, operation=phase_o["operation"], inventory=inventory, slots=slots)
    raw_m = asdict(phase_m_call) | {"messages_sha256": phase_m_prompt_hash}
    if phase_m_call.status != "success":
        return _failed_row(row, "phase_m_generation", phase_m_call.error, phase_o_prompt_hash, phase_m_prompt_hash), raw_o, raw_m
    try:
        phase_m_obj, error = extract_json_object(phase_m_call.raw_output)
        if phase_m_obj is None:
            raise V2A1Error("phase_m_json_extract", error or "Could not extract Phase M JSON object")
        ir = parse_phase_m_output(canonical_json(phase_m_obj), phase_o["operation"], inventory, slots)
        materialized = materialize_ir_values(ir, inventory, slots)
        verify_completeness(ir, slots)
        program = compile_sqlite_program(ir, inventory, materialized)
        db_path = PROJECT_ROOT / STAGE7C_A4_DIR / row["synthetic_db_spec"]["sqlite_db_path"]
        preflight = preflight_sqlite(db_path, program)
        if preflight.admitted:
            observed, observed_hash = canonical_target_state(db_path, program.sql, program.parameters, row["synthetic_db_spec"]["table_name"])
        else:
            observed, observed_hash = [], sha256_text(canonical_json([]))
    except V2A1Error as exc:
        return _failed_row(row, exc.reason_code, str(exc), phase_o_prompt_hash, phase_m_prompt_hash), raw_o, raw_m
    expected = row["label_side_expected"]
    predicted_span_ref_selection = canonical_span_ref_selection(row, phase_o["span_refs"])
    expected_span_ref_selection = canonical_span_ref_selection(row, expected["phase_o"]["span_refs"])
    predicted_phase_m_mapping = canonical_insert_mapping(ir)
    expected_phase_m_mapping = canonical_insert_mapping(expected["phase_m"])
    checks = {
        "operation_exact": phase_o["operation"] == expected["phase_o"]["operation"],
        "span_ref_selection_exact": predicted_span_ref_selection == expected_span_ref_selection,
        "no_extra_refs": len(predicted_span_ref_selection) == len(expected_span_ref_selection),
        "all_refs_exist_in_dynamic_enum": set(phase_o["span_refs"]) <= set(candidate_refs),
        "phase_m_mapping_exact": predicted_phase_m_mapping == expected_phase_m_mapping,
        "typed_materialization_pass": True,
        "completeness_pass": True,
        "compile_pass": True,
        "preflight_admitted": bool(preflight.admitted),
        "canonical_target_state_exact": observed == expected["target_state"]["typed_target_rows"],
    }
    return (
        {
            "sample_id": sample_id,
            "status": "PASS" if all(checks.values()) else "FAIL",
            "failure_stage": None if all(checks.values()) else "acceptance_gate",
            "checks": checks,
            "phase_o_predicted": phase_o,
            "phase_o_canonical_span_refs": list(predicted_span_ref_selection),
            "phase_o_expected_canonical_span_refs": list(expected_span_ref_selection),
            "resolved_span_refs": [{"candidate_span_ref": item.span_ref, "start_char": item.start_char, "end_char": item.end_char, "text": item.text} for item in selected],
            "phase_m_predicted": ir,
            "phase_m_canonical_mapping": predicted_phase_m_mapping,
            "phase_m_expected_canonical_mapping": expected_phase_m_mapping,
            "compiled_sql": program.sql,
            "compiled_parameters": list(program.parameters),
            "preflight_reason_code": preflight.reason_code,
            "observed_target_state_hash": observed_hash,
            "expected_target_state_hash": expected["target_state"]["target_state_hash"],
            "phase_o_messages_sha256": phase_o_prompt_hash,
            "phase_m_messages_sha256": phase_m_prompt_hash,
        },
        raw_o,
        raw_m,
    )


def run_stage7e0(args: argparse.Namespace) -> dict[str, Any]:
    result_root = Path(args.result_root).resolve()
    backend = str(args.backend).lower()
    validate_generation_config(args)
    assert_result_root_policy(result_root, backend=backend, allow_inside_git=args.allow_result_root_inside_git)
    git_lock = None if args.skip_git_assertions else assert_git_lock(args.accepted_protocol_commit)
    a4_lock = verify_stage7c_a4_lock()
    rows = load_stage7c_a4_rows()
    if result_root.exists() and not args.resume:
        raise SystemExit("STOP: result-root already exists; do not reuse real A4 result directories")
    result_root.mkdir(parents=True, exist_ok=True)
    generator: TwoCallGenerator
    if backend == "mock":
        generator = LabelMockGenerator(rows)
    elif backend == "constrained_hf":
        generator = ConstrainedTransformersChatGenerator(model_name_or_path=args.model_name_or_path, quantization=args.quantization, trust_remote_code=args.trust_remote_code, max_input_tokens=args.max_input_tokens, seed=args.seed)
    else:
        raise SystemExit(f"STOP: unsupported backend {backend}")
    metadata = generator.metadata()
    if backend == "constrained_hf" and metadata.get("cuda_available") is not True:
        raise SystemExit("STOP: real Stage7E0-A4 generation requires cuda_available=true")
    if backend == "constrained_hf" and metadata.get("backend") != CONSTRAINED_BACKEND_ID:
        raise SystemExit("STOP: constrained backend unavailable; no fallback to unconstrained generation")
    write_json(
        result_root / "run_manifest.json",
        {
            "stage": "Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT",
            "accepted_protocol_commit": args.accepted_protocol_commit,
            "git": git_lock,
            "stage7c_a4_inputs": a4_lock,
            "model": metadata,
            "primary_case_count": len(rows),
            "primary_first_diagnostics_forbidden_until_freeze": True,
            "zero_shot": True,
            "retry": 0,
            "repair": "none",
            "phase_o_max_new_tokens": int(args.phase_o_max_new_tokens),
            "phase_m_max_new_tokens": int(args.phase_m_max_new_tokens),
            "phase_o_prompt_spec_path": A4_PROMPT_SPEC_REL,
            "phase_m_prompt_spec_path": "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json",
        },
    )
    case_results: list[dict[str, Any]] = []
    raw_o_rows: list[dict[str, Any]] = []
    raw_m_rows: list[dict[str, Any]] = []
    for row in rows:
        case_result, raw_o, raw_m = evaluate_primary_case(row, generator, phase_o_max_new_tokens=int(args.phase_o_max_new_tokens), phase_m_max_new_tokens=int(args.phase_m_max_new_tokens))
        case_results.append(case_result)
        raw_o_rows.append(raw_o)
        if raw_m is not None:
            raw_m_rows.append(raw_m)
        write_jsonl(result_root / "primary_case_results.jsonl", case_results)
        write_jsonl(result_root / "raw_phase_o_generations.jsonl", raw_o_rows)
        write_jsonl(result_root / "raw_phase_m_generations.jsonl", raw_m_rows)
    pass_count = sum(1 for row in case_results if row["status"] == "PASS")
    summary = {
        "stage": "Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT",
        "status": "PASS" if pass_count == EXPECTED_PRIMARY_COUNT else "FAIL",
        "backend": backend,
        "protocol_backend": metadata.get("backend"),
        "model_called": backend == "constrained_hf",
        "gpu_called": backend == "constrained_hf",
        "mock_uses_label_side_expected": backend == "mock",
        "phase_o_max_new_tokens": int(args.phase_o_max_new_tokens),
        "phase_m_max_new_tokens": int(args.phase_m_max_new_tokens),
        "primary_pass_count": f"{pass_count}/{EXPECTED_PRIMARY_COUNT}",
        "required_pass_count": "10/10",
        "nine_of_ten_allowed": False,
        "diagnostics_run": False,
        "gretel_pilot_opened": False,
        "raw_phase_o_sha256": sha256_file(result_root / "raw_phase_o_generations.jsonl"),
        "raw_phase_m_sha256": sha256_file(result_root / "raw_phase_m_generations.jsonl"),
        "primary_case_results_sha256": sha256_file(result_root / "primary_case_results.jsonl"),
    }
    write_json(result_root / "primary_summary.json", summary)
    if backend == "constrained_hf" and summary["status"] != "PASS":
        raise SystemExit("STOP: Stage7E0-A4 primary failed; do not open Gretel pilot")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accepted-protocol-commit", required=True)
    parser.add_argument("--result-root", required=True)
    parser.add_argument("--backend", choices=["constrained_hf", "mock", "hf"], default="constrained_hf")
    parser.add_argument("--model-name-or-path", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--quantization", default="none")
    parser.add_argument("--phase-o-max-new-tokens", type=int, default=PHASE_O_MAX_NEW_TOKENS)
    parser.add_argument("--phase-m-max-new-tokens", type=int, default=PHASE_M_MAX_NEW_TOKENS)
    parser.add_argument("--max-input-tokens", type=int, default=28672)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-git-assertions", action="store_true", help="Allowed for extracted reviewer packages and mock tests only.")
    parser.add_argument("--allow-result-root-inside-git", action="store_true", help="Allowed for mock tests only.")
    args = parser.parse_args()
    print(json.dumps(run_stage7e0(args), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
