#!/usr/bin/env python3
"""Stage6H confirmatory execution harness.

This harness is an orchestration/provenance layer around the frozen method. It
does not change the method implementation. Its setup mode is CPU-only; later GPU
execution must use the same guard functions before any call to model.generate().
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

AUTHORIZATION_DIR = "stage6_confirmation_run_authorization"
STAGE6H_DIR = "stage6_confirmation_execution"
DEFAULT_EXECUTION_ROOT = "../stage6_confirmation_run_outputs"
RUN_STATE_FILENAME = "CONFIRMATION_RUN_STATE.json"
STAGE6F_RUNTIME_DIR = (
    "stage6_gpu_preflight_acceptance/server_output_zip_extract_patch2/"
    "stage6f_gpu_preflight_patch2_outputs/stage6_gpu_preflight"
)
DEFAULT_PROMPT_TOKEN_AUDIT_PATH = f"{STAGE6F_RUNTIME_DIR}/PROMPT_TOKEN_AUDIT.jsonl"
DEFAULT_GPU_ENVIRONMENT_MANIFEST_PATH = f"{STAGE6F_RUNTIME_DIR}/GPU_ENVIRONMENT_MANIFEST.json"
STAGE6G_AUTHORIZATION_COMMIT = "ead5015c3efaa174772e8595b7b65a8f5c032166"
FINAL_CONFIRMATION_N = 481
FINAL_MANIFEST_PATH = "stage6_final_registration_revision/artifacts/FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl"
FINAL_MANIFEST_SHA256 = "6a9fc9812d768001e3a8e8b87d2387a7b943c83237a4bca7603c304acf88bcc7"
FINAL_GOLD_CORPUS_PATH = "stage6_final_registration_revision/artifacts/FINAL_GOLD_CORPUS.jsonl"
FINAL_GOLD_CORPUS_SHA256 = "2082e892858c065531e2456239e77e51bae6232fccdf717497fecadc5421fd16"
STAGE6_CRUDSQL_DB_ROOT = "stage6_crudsql_registration"
PROMPT_TOKEN_AUDIT_SHA256 = "e9bcf79074bcac4284f412314e40cbc7567940a36258dcb27f62ff7862eadae6"
GENERATION_LOCK_SHA256 = "890da57fbe137f4b3f64e002a29d76a3c630cdc8290c924ac1d1f9da585a9c14"
MODEL_SHA256 = "e2026c78ea002527089b088023b7ae2c1486f127f667cafbb823225877cd268c"
TOKENIZER_SHA256 = "06d1f5403e9eda68466f91b5c235eab56b530a9b8155e21f3bd0523b4b29e468"
MODEL_REVISION = "c03e6d358207e414f1eca0bb1891e29f1db0e242"

STREAMS = {
    "direct": {
        "method_id": "D-FS-M",
        "prompt_audit_arm": "direct",
        "config_path": "configs/stage5/resolved_direct_confirmation.json",
        "raw_generation_path": "raw_generations/direct.jsonl",
        "run_method_output_dir": "runs/stage6_confirmation/direct",
    },
    "j_fs": {
        "method_id": "J-FS-M",
        "prompt_audit_arm": "j_fs",
        "config_path": "configs/stage5/resolved_j_fs_confirmation.json",
        "raw_generation_path": "raw_generations/j_fs.jsonl",
        "run_method_output_dir": "runs/stage6_confirmation/j_fs",
    },
    "original_mp_fs_plus": {
        "method_id": "MP-FS+",
        "prompt_audit_arm": "original_mp_fs_plus",
        "config_path": "configs/stage5/resolved_original_mp_fs_plus.json",
        "raw_generation_path": "raw_generations/original_mp_fs_plus.jsonl",
        "run_method_output_dir": "runs/stage6_confirmation/original_mp_fs_plus",
    },
    "shared_mp_fs_plus_generation": {
        "method_id": "MP-FS+",
        "prompt_audit_arm": "d_g1_control",
        "identity_audit_arm": "d_f_g1_vnext",
        "config_path": "configs/stage5/resolved_d_g1_control.json",
        "raw_generation_path": "raw_generations/shared_mp_fs_plus_generation.jsonl",
        "run_method_output_dir": "runs/stage6_confirmation/shared_mp_fs_plus_generation",
        "deterministic_replay_arms": ["d_g1_control", "d_f_g1_vnext"],
    },
}

ARM_CONFIG_KEYS = {
    "direct": "direct",
    "j_fs": "j_fs",
    "original_mp_fs_plus": "original_mp_fs_plus",
    "shared_mp_fs_plus_generation": "d_g1_control",
}
STREAM_ORDER = [
    "direct",
    "j_fs",
    "original_mp_fs_plus",
    "shared_mp_fs_plus_generation",
]

REQUIRED_AUDIT_FIELDS = [
    "stage6_sample_id",
    "arm",
    "prompt_sha256",
    "chat_prompt_sha256",
    "input_ids_sha256",
    "input_token_count",
]

REQUIRED_RAW_ROW_FIELDS = [
    "sample_id",
    "stage6_sample_id",
    "generation_stream",
    "prompt_sha256",
    "chat_prompt_sha256",
    "input_ids_sha256",
    "input_token_count",
    "raw_output",
    "raw_output_sha256",
    "output_token_count",
    "hit_max_new_tokens",
    "model_revision",
    "model_sha256",
    "tokenizer_sha256",
    "generation_lock_sha256",
    "generation_status",
    "generation_error",
    "run_id",
    "raw_generation_row_sha256",
]

REQUIRED_REPLAY_FIELDS = [
    "stage6_sample_id",
    "replay_arm",
    "source_raw_generation_stream",
    "shared_raw_generation_row_sha256",
]


class HarnessError(RuntimeError):
    """Raised when a Stage6 execution guard blocks generation."""


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def canonical_row_hash(row: dict[str, Any]) -> str:
    value = {key: item for key, item in row.items() if key != "raw_generation_row_sha256"}
    return sha256_text(canonical_json(value))


def canonical_sha256(value: Any) -> str:
    return sha256_text(canonical_json(value))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def load_final_confirmation_manifest(repo_root: Path = PROJECT_ROOT) -> list[dict[str, Any]]:
    path = repo_root / FINAL_MANIFEST_PATH
    if sha256_file(path) != FINAL_MANIFEST_SHA256:
        raise HarnessError("Stage6E final confirmation manifest SHA-256 mismatch")
    rows = read_jsonl(path)
    if len(rows) != FINAL_CONFIRMATION_N:
        raise HarnessError(f"Stage6E final manifest must contain 481 rows, got {len(rows)}")
    expected_ids = {str(row["stage6_sample_id"]) for row in rows}
    assert_exact_sample_coverage(rows, expected_ids, context="Stage6E final manifest")
    return sorted(rows, key=lambda row: str(row["stage6_sample_id"]))


def verify_stage6e_stage6f_id_chain(
    final_manifest_rows: list[dict[str, Any]],
    prompt_audit_index: dict[tuple[str, str], dict[str, Any]],
) -> set[str]:
    final_ids = {str(row["stage6_sample_id"]) for row in final_manifest_rows}
    if len(final_ids) != FINAL_CONFIRMATION_N:
        raise HarnessError("Stage6E final manifest sample ID set must contain 481 IDs")
    audit_arms = {
        "direct",
        "j_fs",
        "original_mp_fs_plus",
        "d_g1_control",
        "d_f_g1_vnext",
    }
    for arm in sorted(audit_arms):
        audit_rows = [row for (row_arm, _sample_id), row in prompt_audit_index.items() if row_arm == arm]
        if len(audit_rows) != FINAL_CONFIRMATION_N:
            raise HarnessError(f"Stage6F audit arm {arm} must contain 481 rows, got {len(audit_rows)}")
        audit_ids = {str(row["stage6_sample_id"]) for row in audit_rows}
        if audit_ids != final_ids:
            missing = sorted(final_ids - audit_ids)
            unexpected = sorted(audit_ids - final_ids)
            raise HarnessError(
                f"Stage6E/Stage6F ID mismatch for {arm}: "
                f"missing={missing[:10]} unexpected={unexpected[:10]}"
            )
    return final_ids


def git_output(repo_root: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def load_stage6g_validator(repo_root: Path):
    path = repo_root / "scripts" / "data" / "validate_stage6g_confirmation_authorization.py"
    spec = importlib.util.spec_from_file_location("validate_stage6g_confirmation_authorization", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def verify_authorization_boundary(
    repo_root: Path,
    *,
    expected_git_head: str,
    require_git_clean: bool = True,
    check_absent_raw_generations: bool = True,
) -> dict[str, Any]:
    validator = load_stage6g_validator(repo_root)
    report = validator.validate(
        repo_root / AUTHORIZATION_DIR,
        repo_root=repo_root,
        expected_git_head=expected_git_head,
        require_git_clean=require_git_clean,
        check_absent_raw_generations=check_absent_raw_generations,
    )
    if report["status"] != "PASS":
        raise HarnessError(f"Stage6G authorization boundary failed: {report['violations']}")
    return report


def load_prompt_audit(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    if sha256_file(path) != PROMPT_TOKEN_AUDIT_SHA256:
        raise HarnessError(
            f"Prompt token audit SHA-256 mismatch for {path}: "
            f"expected {PROMPT_TOKEN_AUDIT_SHA256}"
        )
    rows = read_jsonl(path)
    if len(rows) != FINAL_CONFIRMATION_N * 5:
        raise HarnessError(f"Prompt audit must contain 2405 rows, got {len(rows)}")
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        for field in REQUIRED_AUDIT_FIELDS:
            if field not in row:
                raise HarnessError(f"Prompt audit row missing {field}")
        key = (str(row["arm"]), str(row["stage6_sample_id"]))
        if key in index:
            raise HarnessError(f"Duplicate prompt audit key: {key}")
        index[key] = row
    return index


def method_source_tree_hash(repo_root: Path = PROJECT_ROOT) -> str:
    manifest = read_json(repo_root / "stage5_method_revision_freeze" / "EXECUTABLE_FREEZE_MANIFEST.json")
    rows = []
    for relative in sorted(manifest.get("method_implementation_files") or []):
        path = repo_root / relative
        rows.append(f"{sha256_file(path)}  {relative}")
    return sha256_text("\n".join(rows) + "\n")


def execution_code_manifest(repo_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    config_hashes = {
        key: {
            "path": STREAMS[stream]["config_path"],
            "sha256": sha256_file(repo_root / STREAMS[stream]["config_path"]),
        }
        for stream, key in ARM_CONFIG_KEYS.items()
    }
    config_hashes["d_f_g1_vnext"] = {
        "path": "configs/stage5/resolved_mp_fs_plus_vnext_r1.json",
        "sha256": sha256_file(repo_root / "configs/stage5/resolved_mp_fs_plus_vnext_r1.json"),
    }
    return {
        "stage": "Stage6H_EXECUTION_CODE_MANIFEST",
        "accepted_harness_commit": git_output(repo_root, "rev-parse", "HEAD"),
        "runner": {
            "path": "scripts/server/run_stage6_confirmation.py",
            "sha256": sha256_file(repo_root / "scripts/server/run_stage6_confirmation.py"),
        },
        "stage6g_validator": {
            "path": "scripts/data/validate_stage6g_confirmation_authorization.py",
            "sha256": sha256_file(repo_root / "scripts/data/validate_stage6g_confirmation_authorization.py"),
        },
        "method_source_tree_sha256": method_source_tree_hash(repo_root),
        "resolved_config_hashes": config_hashes,
        "generation_lock_sha256": GENERATION_LOCK_SHA256,
    }


def validate_execution_code_manifest(
    manifest: dict[str, Any],
    *,
    repo_root: Path = PROJECT_ROOT,
) -> None:
    actual = execution_code_manifest(repo_root)
    for key in ("runner", "stage6g_validator", "method_source_tree_sha256", "resolved_config_hashes", "generation_lock_sha256"):
        if manifest.get(key) != actual.get(key):
            raise HarnessError(f"Execution code manifest mismatch: {key}")


def row_sample_id(row: dict[str, Any]) -> str:
    sample_id = row.get("sample_id", row.get("stage6_sample_id"))
    if sample_id is None:
        raise HarnessError("Row is missing sample_id/stage6_sample_id")
    return str(sample_id)


def assert_exact_sample_coverage(
    rows: list[dict[str, Any]],
    expected_sample_ids: set[str],
    *,
    context: str,
    allow_partial: bool = False,
) -> None:
    actual_ids = [row_sample_id(row) for row in rows]
    if len(actual_ids) != len(rows):
        raise HarnessError(f"{context} sample ID extraction failed")
    duplicate_count = len(actual_ids) - len(set(actual_ids))
    if duplicate_count:
        raise HarnessError(f"{context} has {duplicate_count} duplicate sample IDs")
    actual_set = set(actual_ids)
    unexpected = sorted(actual_set - expected_sample_ids)
    if unexpected:
        raise HarnessError(f"{context} has unexpected sample IDs: {unexpected[:10]}")
    if not allow_partial:
        missing = sorted(expected_sample_ids - actual_set)
        if missing:
            raise HarnessError(f"{context} is missing expected sample IDs: {missing[:10]}")
        if len(rows) != FINAL_CONFIRMATION_N:
            raise HarnessError(f"{context} must contain 481 rows, got {len(rows)}")


def prompt_audit_arm_for_stream(stream: str) -> str:
    if stream not in STREAMS:
        raise HarnessError(f"Unknown generation stream: {stream}")
    return str(STREAMS[stream]["prompt_audit_arm"])


def rows_for_stream(index: dict[tuple[str, str], dict[str, Any]], stream: str) -> list[dict[str, Any]]:
    arm_name = prompt_audit_arm_for_stream(stream)
    rows = [row for (arm, _sample_id), row in index.items() if arm == arm_name]
    if len(rows) != FINAL_CONFIRMATION_N:
        raise HarnessError(f"{stream}/{arm_name} prompt audit must contain 481 rows, got {len(rows)}")
    expected_ids = {str(row["stage6_sample_id"]) for row in rows}
    assert_exact_sample_coverage(rows, expected_ids, context=f"{stream}/{arm_name} prompt audit")
    return sorted(rows, key=lambda row: str(row["stage6_sample_id"]))


def verify_shared_stream_audit_identity(index: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    left_rows = rows_for_stream(index, "shared_mp_fs_plus_generation")
    right_arm = str(STREAMS["shared_mp_fs_plus_generation"]["identity_audit_arm"])
    right_rows = [row for (arm, _sample_id), row in index.items() if arm == right_arm]
    if len(right_rows) != FINAL_CONFIRMATION_N:
        raise HarnessError(f"{right_arm} prompt audit must contain 481 rows, got {len(right_rows)}")
    left = {str(row["stage6_sample_id"]): row for row in left_rows}
    right = {str(row["stage6_sample_id"]): row for row in right_rows}
    if set(left) != set(right):
        raise HarnessError("D_G1 and D_F_G1 audit sample IDs differ")
    checked = 0
    for sample_id, left_row in left.items():
        right_row = right[sample_id]
        for field in ("prompt_sha256", "chat_prompt_sha256", "input_ids_sha256", "input_token_count"):
            if left_row.get(field) != right_row.get(field):
                raise HarnessError(f"H2 shared audit identity mismatch for {sample_id}: {field}")
        checked += 1
    return {
        "status": "PASS",
        "checked_pairs": checked,
        "prompt_audit_arm": "d_g1_control",
        "identity_audit_arm": right_arm,
    }


def verify_stream_input_identity(
    *,
    stream: str,
    expected_index: dict[tuple[str, str], dict[str, Any]],
    current_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if stream == "shared_mp_fs_plus_generation":
        verify_shared_stream_audit_identity(expected_index)
    expected_rows = {str(row["stage6_sample_id"]): row for row in rows_for_stream(expected_index, stream)}
    assert_exact_sample_coverage(
        current_rows,
        set(expected_rows),
        context=f"{stream} current runtime inputs",
    )
    for row in current_rows:
        sample_id = row_sample_id(row)
        expected = expected_rows.get(sample_id)
        if expected is None:
            raise HarnessError(f"{stream} current audit has unknown sample {sample_id}")
        for field in ("prompt_sha256", "chat_prompt_sha256", "input_ids_sha256", "input_token_count"):
            if row.get(field) != expected.get(field):
                raise HarnessError(
                    f"{stream} input identity mismatch for {sample_id}: {field}"
                )
    return sorted(current_rows, key=row_sample_id)


def check_clean_initial_outputs(repo_root: Path) -> None:
    existing = [
        stream["raw_generation_path"]
        for stream in STREAMS.values()
        if (repo_root / stream["raw_generation_path"]).exists()
    ]
    if existing:
        raise HarnessError(f"Pre-existing raw generation files block initial execution: {existing}")


def validate_model_identity(metadata: dict[str, Any]) -> None:
    expected = {
        "model_revision": MODEL_REVISION,
        "model_sha256": MODEL_SHA256,
        "tokenizer_sha256": TOKENIZER_SHA256,
        "generation_lock_sha256": GENERATION_LOCK_SHA256,
    }
    mismatches = {
        key: {"expected": expected[key], "actual": metadata.get(key)}
        for key in expected
        if metadata.get(key) != expected[key]
    }
    if mismatches:
        raise HarnessError(f"Model/generation identity mismatch: {mismatches}")


def apply_chat_template(tokenizer: Any, prompt: str) -> str:
    if hasattr(tokenizer, "apply_chat_template"):
        return tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=False,
            add_generation_prompt=True,
        )
    return prompt


def tokenize_prompt(tokenizer: Any, prompt: str) -> tuple[str, list[int]]:
    chat_prompt = apply_chat_template(tokenizer, prompt)
    encoded = tokenizer(chat_prompt, add_special_tokens=True, truncation=False)
    return chat_prompt, [int(token_id) for token_id in encoded["input_ids"]]


def load_stage6h_runtime_dependencies() -> dict[str, Any]:
    from nldbwrite_v3.experiments.run_method import _prompt_for_sample
    from nldbwrite_v3.inference import GenerationRequest, create_generator
    from nldbwrite_v3.schema import build_profile

    return {
        "_prompt_for_sample": _prompt_for_sample,
        "GenerationRequest": GenerationRequest,
        "create_generator": create_generator,
        "build_profile": build_profile,
    }


def sample_to_method_row(sample: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": sample["stage6_sample_id"],
        "sample_id": sample["stage6_sample_id"],
        "input_text": sample["question"],
        "db_id": sample["table_id"],
    }


def load_arm_config_for_stream(stream: str, repo_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    config_path = repo_root / STREAMS[stream]["config_path"]
    return read_json(config_path)


def build_profile_cache(
    samples: list[dict[str, Any]],
    *,
    repo_root: Path = PROJECT_ROOT,
) -> dict[str, dict[str, Any]]:
    deps = load_stage6h_runtime_dependencies()
    build_profile = deps["build_profile"]
    cache: dict[str, dict[str, Any]] = {}
    db_root = repo_root / STAGE6_CRUDSQL_DB_ROOT
    for sample in samples:
        table_id = str(sample["table_id"])
        if table_id in cache:
            continue
        db_path = db_root / str(sample["isolated_db"])
        if not db_path.is_file():
            raise HarnessError(f"Missing isolated SQLite DB for Stage6H execution: {db_path}")
        cache[table_id] = build_profile(db_path, db_id=table_id)
    return cache


def build_verified_runtime_request(
    *,
    stream: str,
    sample: dict[str, Any],
    config: dict[str, Any],
    profile: dict[str, Any],
    tokenizer: Any,
    audit_row: dict[str, Any],
) -> dict[str, Any]:
    deps = load_stage6h_runtime_dependencies()
    prompt_for_sample = deps["_prompt_for_sample"]
    prompt, payload = prompt_for_sample(
        STREAMS[stream]["method_id"],
        sample_to_method_row(sample),
        profile,
        config,
    )
    chat_prompt, token_ids = tokenize_prompt(tokenizer, prompt)
    actual = {
        "stage6_sample_id": str(sample["stage6_sample_id"]),
        "prompt": prompt,
        "payload_mode": getattr(payload, "mode", None),
        "prompt_sha256": sha256_text(prompt),
        "chat_prompt": chat_prompt,
        "chat_prompt_sha256": sha256_text(chat_prompt),
        "input_ids": token_ids,
        "input_ids_sha256": canonical_sha256(token_ids),
        "input_token_count": len(token_ids),
    }
    for field in ("prompt_sha256", "chat_prompt_sha256", "input_ids_sha256", "input_token_count"):
        if actual[field] != audit_row.get(field):
            raise HarnessError(f"{stream} integrated request mismatch for {sample['stage6_sample_id']}: {field}")
    return actual


class IntegratedStage6Runner:
    """Production runner that binds verified prompts directly to model generation."""

    def __init__(
        self,
        *,
        stream: str,
        prompt_audit_index: dict[tuple[str, str], dict[str, Any]],
        tokenizer: Any,
        generator: Any,
        final_manifest_rows: list[dict[str, Any]],
        repo_root: Path = PROJECT_ROOT,
    ) -> None:
        if stream not in STREAMS:
            raise HarnessError(f"Unknown generation stream: {stream}")
        self.stream = stream
        self.prompt_audit_index = prompt_audit_index
        self.tokenizer = tokenizer
        self.generator = generator
        self.final_manifest_rows = final_manifest_rows
        self.repo_root = repo_root
        verify_stage6e_stage6f_id_chain(final_manifest_rows, prompt_audit_index)
        self.samples_by_id = {
            str(row["stage6_sample_id"]): row for row in final_manifest_rows
        }
        self.config = load_arm_config_for_stream(stream, repo_root=repo_root)
        self.profiles = build_profile_cache(final_manifest_rows, repo_root=repo_root)

    def verified_requests_for_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        verified_rows = verify_stream_input_identity(
            stream=self.stream,
            expected_index=self.prompt_audit_index,
            current_rows=rows,
        )
        requests: list[dict[str, Any]] = []
        for row in verified_rows:
            sample_id = str(row["stage6_sample_id"])
            sample = self.samples_by_id.get(sample_id)
            if sample is None:
                raise HarnessError(f"{self.stream} sample missing from Stage6E manifest: {sample_id}")
            request = build_verified_runtime_request(
                stream=self.stream,
                sample=sample,
                config=self.config,
                profile=self.profiles[str(sample["table_id"])],
                tokenizer=self.tokenizer,
                audit_row=row,
            )
            requests.append(request)
        return requests

    def generate_one(self, request: dict[str, Any]) -> dict[str, Any]:
        deps = load_stage6h_runtime_dependencies()
        GenerationRequest = deps["GenerationRequest"]
        generation_request = GenerationRequest(
            sample_id=str(request["stage6_sample_id"]),
            prompt=str(request["prompt"]),
        )
        started = time.perf_counter()
        results = self.generator.generate([generation_request], batch_size=1)
        elapsed = time.perf_counter() - started
        if len(results) != 1:
            raise HarnessError(f"{self.stream} generator returned {len(results)} rows for one request")
        result = results[0]
        result_row = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        result_row.update(
            {
                "sample_id": request["stage6_sample_id"],
                "stage6_sample_id": request["stage6_sample_id"],
                "prompt_sha256": request["prompt_sha256"],
                "chat_prompt_sha256": request["chat_prompt_sha256"],
                "input_ids_sha256": request["input_ids_sha256"],
                "input_token_count": request["input_token_count"],
                "latency_sec": result_row.get("latency_sec", elapsed),
            }
        )
        return result_row

    def generation_metadata(self) -> dict[str, Any]:
        generator_metadata = self.generator.metadata() if hasattr(self.generator, "metadata") else {}
        return {
            "model_revision": MODEL_REVISION,
            "model_sha256": generator_metadata.get("model_hash", generator_metadata.get("model_sha256", MODEL_SHA256)),
            "tokenizer_sha256": generator_metadata.get("tokenizer_sha256", TOKENIZER_SHA256),
            "generation_lock_sha256": GENERATION_LOCK_SHA256,
        }


def normalize_raw_generation_row(
    *,
    stream: str,
    audit_row: dict[str, Any],
    generation_row: dict[str, Any],
    run_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    validate_model_identity(metadata)
    sample_id = str(audit_row["stage6_sample_id"])
    if row_sample_id(generation_row) != sample_id:
        raise HarnessError(f"{stream} generation row sample mismatch for {sample_id}")
    if generation_row.get("stage6_sample_id") is not None and str(generation_row["stage6_sample_id"]) != sample_id:
        raise HarnessError(f"{stream} generation row stage6_sample_id mismatch for {sample_id}")
    for field in ("prompt_sha256", "chat_prompt_sha256", "input_ids_sha256", "input_token_count"):
        actual = generation_row.get(field, generation_row.get(f"actual_{field}"))
        if actual != audit_row.get(field):
            raise HarnessError(f"{stream} actual generation input mismatch for {sample_id}: {field}")
    raw_output = str(generation_row.get("raw_output") or "")
    row = {
        "sample_id": sample_id,
        "stage6_sample_id": sample_id,
        "generation_stream": stream,
        "prompt_sha256": audit_row["prompt_sha256"],
        "chat_prompt_sha256": audit_row["chat_prompt_sha256"],
        "input_ids_sha256": audit_row["input_ids_sha256"],
        "input_token_count": audit_row["input_token_count"],
        "raw_output": raw_output,
        "raw_output_sha256": sha256_text(raw_output),
        "output_token_count": generation_row.get("output_token_count", generation_row.get("output_tokens")),
        "hit_max_new_tokens": bool(generation_row.get("hit_max_new_tokens")),
        "model_revision": metadata["model_revision"],
        "model_sha256": metadata["model_sha256"],
        "tokenizer_sha256": metadata["tokenizer_sha256"],
        "generation_lock_sha256": metadata["generation_lock_sha256"],
        "generation_status": generation_row.get("generation_status", generation_row.get("status", "success")),
        "generation_error": generation_row.get("generation_error", generation_row.get("error")),
        "latency_sec": generation_row.get("latency_sec"),
        "run_id": run_id,
    }
    row["raw_generation_row_sha256"] = canonical_row_hash(row)
    validate_raw_generation_rows([row])
    return row


def validate_raw_generation_rows(
    rows: list[dict[str, Any]],
    *,
    expected_sample_ids: set[str] | None = None,
    expected_run_id: str | None = None,
    full_completion: bool = False,
) -> None:
    if expected_sample_ids is not None:
        assert_exact_sample_coverage(
            rows,
            expected_sample_ids,
            context="raw generation rows",
            allow_partial=not full_completion,
        )
    observed_run_ids: set[str] = set()
    for row in rows:
        missing = [field for field in REQUIRED_RAW_ROW_FIELDS if field not in row]
        if missing:
            raise HarnessError(f"Raw generation row missing required fields: {missing}")
        if str(row["sample_id"]) != str(row["stage6_sample_id"]):
            raise HarnessError("Raw generation row sample_id and stage6_sample_id differ")
        observed_run_ids.add(str(row["run_id"]))
        expected_row_hash = canonical_row_hash(row)
        if row.get("raw_generation_row_sha256") != expected_row_hash:
            raise HarnessError("Raw generation row hash mismatch")
    if len(observed_run_ids) > 1:
        raise HarnessError(f"Raw generation rows contain mixed run_id values: {sorted(observed_run_ids)}")
    if expected_run_id is not None and observed_run_ids and observed_run_ids != {expected_run_id}:
        raise HarnessError(
            f"Raw generation rows run_id mismatch: expected {expected_run_id}, actual {sorted(observed_run_ids)}"
        )


def write_checkpoint(
    raw_path: Path,
    rows: list[dict[str, Any]],
    *,
    expected_sample_ids: set[str] | None = None,
    expected_run_id: str | None = None,
    full_completion: bool = False,
) -> Path:
    validate_raw_generation_rows(
        rows,
        expected_sample_ids=expected_sample_ids,
        expected_run_id=expected_run_id,
        full_completion=full_completion,
    )
    observed_run_ids = {str(row["run_id"]) for row in rows}
    checkpoint = raw_path.with_suffix(raw_path.suffix + ".checkpoint.json")
    payload = {
        "raw_generation_path": raw_path.as_posix(),
        "run_id": expected_run_id or (next(iter(observed_run_ids)) if observed_run_ids else None),
        "row_count": len(rows),
        "rows": {
            str(row["stage6_sample_id"]): row["raw_generation_row_sha256"]
            for row in rows
        },
    }
    write_json(checkpoint, payload)
    return checkpoint


def verify_resume_checkpoint(
    raw_path: Path,
    checkpoint_path: Path,
    *,
    expected_sample_ids: set[str] | None = None,
    expected_run_id: str | None = None,
    full_completion: bool = False,
) -> None:
    rows = read_jsonl(raw_path)
    validate_raw_generation_rows(
        rows,
        expected_sample_ids=expected_sample_ids,
        expected_run_id=expected_run_id,
        full_completion=full_completion,
    )
    checkpoint = read_json(checkpoint_path)
    observed_run_ids = {str(row["run_id"]) for row in rows}
    observed_run_id = next(iter(observed_run_ids)) if observed_run_ids else None
    expected_checkpoint_run_id = expected_run_id or observed_run_id
    if checkpoint.get("run_id") != expected_checkpoint_run_id:
        raise HarnessError("Resume checkpoint run_id does not match raw rows or expected run_id")
    expected = checkpoint.get("rows") or {}
    if checkpoint.get("row_count") != len(rows):
        raise HarnessError("Resume checkpoint row_count does not match raw rows")
    if len(expected) != len(rows):
        raise HarnessError("Resume checkpoint rows map size does not match raw rows")
    actual = {
        str(row["stage6_sample_id"]): row["raw_generation_row_sha256"]
        for row in rows
    }
    if actual != expected:
        raise HarnessError("Existing raw rows do not match resume checkpoint")


def write_rows_incrementally(
    raw_path: Path,
    rows: list[dict[str, Any]],
    *,
    existing_rows: list[dict[str, Any]] | None = None,
    expected_sample_ids: set[str] | None = None,
    expected_run_id: str | None = None,
) -> None:
    accumulated = list(existing_rows or [])
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    for row in rows:
        accumulated.append(row)
        write_jsonl(raw_path, accumulated)
        write_checkpoint(
            raw_path,
            accumulated,
            expected_sample_ids=expected_sample_ids,
            expected_run_id=expected_run_id,
            full_completion=False,
        )
    if expected_sample_ids is not None and len(accumulated) == len(expected_sample_ids):
        write_checkpoint(
            raw_path,
            accumulated,
            expected_sample_ids=expected_sample_ids,
            expected_run_id=expected_run_id,
            full_completion=True,
        )


def generation_config(model_name_or_path: str) -> dict[str, Any]:
    return {
        "backend": "hf",
        "model_name_or_path": model_name_or_path,
        "revision": MODEL_REVISION,
        "model_hash": MODEL_SHA256,
        "quantization": "4bit",
        "compute_dtype": "float16",
        "device_map": "auto",
        "max_input_tokens": 28672,
        "max_new_tokens": 4096,
        "input_truncation_policy": "error",
        "bnb_4bit_quant_type": "fp4",
        "bnb_4bit_use_double_quant": False,
        "bnb_4bit_quant_storage": "uint8",
        "do_sample": False,
        "temperature": None,
        "top_p": None,
        "top_k": None,
        "seed": 42,
        "trust_remote_code": False,
    }


def execute_stream_integrated(
    *,
    stream: str,
    prompt_audit_path: Path,
    output_root: Path,
    run_id: str,
    mode: str = "initial",
    repo_root: Path = PROJECT_ROOT,
    expected_git_head: str | None = None,
    require_git_clean: bool = True,
    model_name_or_path: str | None = None,
    tokenizer: Any | None = None,
    generator: Any | None = None,
) -> list[dict[str, Any]]:
    if stream not in STREAMS:
        raise HarnessError(f"Unknown generation stream: {stream}")
    if mode not in {"initial", "resume"}:
        raise HarnessError(f"Unknown execution mode: {mode}")
    if expected_git_head is not None:
        verify_authorization_boundary(
            repo_root,
            expected_git_head=expected_git_head,
            require_git_clean=require_git_clean,
        )
    if generator is None:
        if not model_name_or_path:
            raise HarnessError("--model-name-or-path is required for integrated GPU execution")
        deps = load_stage6h_runtime_dependencies()
        generator = deps["create_generator"](generation_config(model_name_or_path))
    if tokenizer is None:
        tokenizer = getattr(generator, "tokenizer", None)
    if tokenizer is None:
        raise HarnessError("Integrated execution requires the exact tokenizer used by the generator")

    prompt_audit_index = load_prompt_audit(prompt_audit_path)
    final_manifest_rows = load_final_confirmation_manifest(repo_root)
    expected_sample_ids = verify_stage6e_stage6f_id_chain(final_manifest_rows, prompt_audit_index)
    current_rows = rows_for_stream(prompt_audit_index, stream)
    raw_path = output_root / STREAMS[stream]["raw_generation_path"]
    checkpoint_path = raw_path.with_suffix(raw_path.suffix + ".checkpoint.json")
    existing_rows: list[dict[str, Any]] = []
    completed_ids: set[str] = set()
    if mode == "initial":
        if raw_path.exists() or checkpoint_path.exists():
            raise HarnessError(f"{stream} initial execution requires no existing raw/checkpoint file")
    else:
        if not raw_path.exists() or not checkpoint_path.exists():
            raise HarnessError(f"{stream} resume execution requires existing raw and checkpoint files")
        verify_resume_checkpoint(
            raw_path,
            checkpoint_path,
            expected_sample_ids=expected_sample_ids,
            expected_run_id=run_id,
            full_completion=False,
        )
        existing_rows = read_jsonl(raw_path)
        completed_ids = {str(row["stage6_sample_id"]) for row in existing_rows}

    runner = IntegratedStage6Runner(
        stream=stream,
        prompt_audit_index=prompt_audit_index,
        tokenizer=tokenizer,
        generator=generator,
        final_manifest_rows=final_manifest_rows,
        repo_root=repo_root,
    )
    verified_requests_all = runner.verified_requests_for_rows(current_rows)
    verified_requests = [
        request for request in verified_requests_all
        if str(request["stage6_sample_id"]) not in completed_ids
    ]
    accumulated = list(existing_rows)
    metadata = runner.generation_metadata()
    for request in verified_requests:
        generation_row = runner.generate_one(request)
        normalized = normalize_raw_generation_row(
            stream=stream,
            audit_row=request,
            generation_row=generation_row,
            run_id=run_id,
            metadata=metadata,
        )
        combined = accumulated + [normalized]
        validate_raw_generation_rows(
            combined,
            expected_sample_ids=expected_sample_ids,
            expected_run_id=run_id,
            full_completion=len(combined) == FINAL_CONFIRMATION_N,
        )
        write_rows_incrementally(
            raw_path,
            [normalized],
            existing_rows=accumulated,
            expected_sample_ids=expected_sample_ids,
            expected_run_id=run_id,
        )
        accumulated.append(normalized)
    if len(accumulated) == FINAL_CONFIRMATION_N:
        validate_raw_generation_rows(
            accumulated,
            expected_sample_ids=expected_sample_ids,
            expected_run_id=run_id,
            full_completion=True,
        )
        write_checkpoint(
            raw_path,
            accumulated,
            expected_sample_ids=expected_sample_ids,
            expected_run_id=run_id,
            full_completion=True,
        )
        if stream == "shared_mp_fs_plus_generation":
            d_g1 = make_replay_provenance_rows(accumulated, replay_arm="d_g1_control")
            d_f_g1 = make_replay_provenance_rows(accumulated, replay_arm="d_f_g1_vnext")
            validate_shared_replay_provenance(d_g1, d_f_g1)
            write_jsonl(output_root / "replay_provenance" / "d_g1_control.jsonl", d_g1)
            write_jsonl(output_root / "replay_provenance" / "d_f_g1_vnext.jsonl", d_f_g1)
    return accumulated


def stream_raw_path(execution_root: Path, stream: str) -> Path:
    return execution_root / STREAMS[stream]["raw_generation_path"]


def stream_checkpoint_path(execution_root: Path, stream: str) -> Path:
    raw_path = stream_raw_path(execution_root, stream)
    return raw_path.with_suffix(raw_path.suffix + ".checkpoint.json")


def check_run_level_initial_absent(execution_root: Path) -> None:
    existing = []
    for stream in STREAM_ORDER:
        raw_path = stream_raw_path(execution_root, stream)
        checkpoint_path = stream_checkpoint_path(execution_root, stream)
        if raw_path.exists():
            existing.append(raw_path.as_posix())
        if checkpoint_path.exists():
            existing.append(checkpoint_path.as_posix())
    if existing:
        raise HarnessError(f"Initial confirmation run requires absent stream outputs: {existing}")


def stream_completion_status(
    *,
    execution_root: Path,
    stream: str,
    expected_sample_ids: set[str],
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    raw_path = stream_raw_path(execution_root, stream)
    checkpoint_path = stream_checkpoint_path(execution_root, stream)
    if not raw_path.exists() and not checkpoint_path.exists():
        return {
            "stream": stream,
            "status": "not_started",
            "row_count": 0,
            "raw_generation_path": raw_path.as_posix(),
        }
    if not raw_path.exists() or not checkpoint_path.exists():
        raise HarnessError(f"{stream} has incomplete raw/checkpoint pair")
    verify_resume_checkpoint(
        raw_path,
        checkpoint_path,
        expected_sample_ids=expected_sample_ids,
        expected_run_id=expected_run_id,
        full_completion=False,
    )
    rows = read_jsonl(raw_path)
    full = len(rows) == FINAL_CONFIRMATION_N
    if full:
        validate_raw_generation_rows(
            rows,
            expected_sample_ids=expected_sample_ids,
            expected_run_id=expected_run_id,
            full_completion=True,
        )
    return {
        "stream": stream,
        "status": "complete" if full else "partial",
        "row_count": len(rows),
        "unique_sample_ids": len({str(row["stage6_sample_id"]) for row in rows}),
        "raw_generation_path": raw_path.as_posix(),
        "raw_generation_sha256": sha256_file(raw_path),
        "checkpoint_path": checkpoint_path.as_posix(),
        "checkpoint_sha256": sha256_file(checkpoint_path),
    }


def write_confirmation_run_state(
    *,
    execution_root: Path,
    run_id: str,
    mode: str,
    prompt_audit_path: Path,
    expected_git_head: str,
    expected_sample_ids: set[str],
) -> dict[str, Any]:
    stream_states = [
        stream_completion_status(
            execution_root=execution_root,
            stream=stream,
            expected_sample_ids=expected_sample_ids,
            expected_run_id=run_id,
        )
        for stream in STREAM_ORDER
    ]
    complete = all(state["status"] == "complete" for state in stream_states)
    manifest = {
        "stage": "Stage6H_CONFIRMATION_RUN_STATE",
        "status": "COMPLETE" if complete else "PARTIAL",
        "run_id": run_id,
        "mode": mode,
        "expected_git_head": expected_git_head,
        "execution_root": execution_root.resolve().as_posix(),
        "prompt_token_audit_path": prompt_audit_path.as_posix(),
        "prompt_token_audit_sha256": sha256_file(prompt_audit_path),
        "final_confirmation_n": FINAL_CONFIRMATION_N,
        "stream_order": STREAM_ORDER,
        "streams": stream_states,
        "total_rows": sum(int(state.get("row_count") or 0) for state in stream_states),
        "confirmation_predictions_created": any(int(state.get("row_count") or 0) for state in stream_states),
    }
    write_json(execution_root / RUN_STATE_FILENAME, manifest)
    return manifest


def run_state_path(execution_root: Path) -> Path:
    return execution_root / RUN_STATE_FILENAME


def load_run_state(execution_root: Path) -> dict[str, Any]:
    path = run_state_path(execution_root)
    if not path.is_file():
        raise HarnessError("Resume run requires existing CONFIRMATION_RUN_STATE.json")
    state = read_json(path)
    if not state.get("run_id"):
        raise HarnessError("Existing run state is missing run_id")
    return state


def resolve_run_id_for_mode(
    *,
    execution_root: Path,
    mode: str,
    requested_run_id: str | None,
) -> str:
    path = run_state_path(execution_root)
    if mode == "initial":
        if path.exists():
            raise HarnessError("Initial confirmation run requires no existing CONFIRMATION_RUN_STATE.json")
        return requested_run_id or f"stage6h_{int(time.time())}"
    if mode == "resume":
        state = load_run_state(execution_root)
        existing_run_id = str(state["run_id"])
        if requested_run_id is not None and requested_run_id != existing_run_id:
            raise HarnessError(
                f"Resume run_id mismatch: state has {existing_run_id}, CLI requested {requested_run_id}"
            )
        return existing_run_id
    raise HarnessError(f"Unknown run mode: {mode}")


def path_is_relative_to(child: Path, parent: Path) -> bool:
    child_resolved = child.resolve()
    parent_resolved = parent.resolve()
    try:
        child_resolved.relative_to(parent_resolved)
    except ValueError:
        return False
    return True


def validate_execution_root_outside_repo(execution_root: Path, repo_root: Path) -> None:
    if path_is_relative_to(execution_root, repo_root):
        raise HarnessError("GPU confirmation execution_root must be outside the source checkout")


def execute_all_streams(
    *,
    prompt_audit_path: Path,
    execution_root: Path,
    run_id: str | None,
    expected_git_head: str,
    model_name_or_path: str | None,
    mode: str = "initial",
    repo_root: Path = PROJECT_ROOT,
    tokenizer: Any | None = None,
    generator: Any | None = None,
    enforce_external_execution_root: bool = True,
) -> dict[str, Any]:
    if mode not in {"initial", "resume"}:
        raise HarnessError(f"Unknown run mode: {mode}")
    if not expected_git_head:
        raise HarnessError("--expected-git-head is required for GPU confirmation execution")
    if enforce_external_execution_root:
        validate_execution_root_outside_repo(execution_root, repo_root)
    verify_authorization_boundary(
        repo_root,
        expected_git_head=expected_git_head,
        require_git_clean=True,
        check_absent_raw_generations=(mode == "initial"),
    )
    prompt_audit_index = load_prompt_audit(prompt_audit_path)
    final_manifest_rows = load_final_confirmation_manifest(repo_root)
    expected_sample_ids = verify_stage6e_stage6f_id_chain(final_manifest_rows, prompt_audit_index)
    code_manifest = execution_code_manifest(repo_root)
    validate_execution_code_manifest(code_manifest, repo_root=repo_root)
    execution_root.mkdir(parents=True, exist_ok=True)
    run_id = resolve_run_id_for_mode(
        execution_root=execution_root,
        mode=mode,
        requested_run_id=run_id,
    )
    write_json(execution_root / "EXECUTION_CODE_MANIFEST.json", code_manifest)
    if mode == "initial":
        check_run_level_initial_absent(execution_root)

    if generator is None:
        if not model_name_or_path:
            raise HarnessError("--model-name-or-path is required for integrated GPU execution")
        deps = load_stage6h_runtime_dependencies()
        generator = deps["create_generator"](generation_config(model_name_or_path))
    if tokenizer is None:
        tokenizer = getattr(generator, "tokenizer", None)
    if tokenizer is None:
        raise HarnessError("Integrated execution requires the exact tokenizer used by the generator")

    run_state = write_confirmation_run_state(
        execution_root=execution_root,
        run_id=run_id,
        mode=mode,
        prompt_audit_path=prompt_audit_path,
        expected_git_head=expected_git_head,
        expected_sample_ids=expected_sample_ids,
    )
    for stream in STREAM_ORDER:
        state = stream_completion_status(
            execution_root=execution_root,
            stream=stream,
            expected_sample_ids=expected_sample_ids,
            expected_run_id=run_id,
        )
        if state["status"] == "complete":
            continue
        stream_mode = "resume" if state["status"] == "partial" else "initial"
        execute_stream_integrated(
            stream=stream,
            prompt_audit_path=prompt_audit_path,
            output_root=execution_root,
            run_id=run_id,
            mode=stream_mode,
            repo_root=repo_root,
            expected_git_head=None,
            model_name_or_path=None,
            tokenizer=tokenizer,
            generator=generator,
        )
        run_state = write_confirmation_run_state(
            execution_root=execution_root,
            run_id=run_id,
            mode=mode,
            prompt_audit_path=prompt_audit_path,
            expected_git_head=expected_git_head,
            expected_sample_ids=expected_sample_ids,
        )
    return run_state


def run_stream_with_guard(
    *,
    stream: str,
    expected_index: dict[tuple[str, str], dict[str, Any]],
    current_rows: list[dict[str, Any]],
    generation_callable: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    output_root: Path,
    run_id: str,
    metadata: dict[str, Any],
    mode: str = "initial",
) -> list[dict[str, Any]]:
    if stream not in STREAMS:
        raise HarnessError(f"Unknown generation stream: {stream}")
    if mode not in {"initial", "resume"}:
        raise HarnessError(f"Unknown execution mode: {mode}")
    verified_rows = verify_stream_input_identity(
        stream=stream,
        expected_index=expected_index,
        current_rows=current_rows,
    )
    expected_sample_ids = {str(row["stage6_sample_id"]) for row in rows_for_stream(expected_index, stream)}
    raw_path = output_root / STREAMS[stream]["raw_generation_path"]
    checkpoint_path = raw_path.with_suffix(raw_path.suffix + ".checkpoint.json")
    existing_rows: list[dict[str, Any]] = []
    completed_ids: set[str] = set()
    if mode == "initial":
        if raw_path.exists() or checkpoint_path.exists():
            raise HarnessError(f"{stream} initial execution requires no existing raw/checkpoint file")
    else:
        if not raw_path.exists() or not checkpoint_path.exists():
            raise HarnessError(f"{stream} resume execution requires existing raw and checkpoint files")
        verify_resume_checkpoint(
            raw_path,
            checkpoint_path,
            expected_sample_ids=expected_sample_ids,
            expected_run_id=run_id,
            full_completion=False,
        )
        existing_rows = read_jsonl(raw_path)
        completed_ids = {str(row["stage6_sample_id"]) for row in existing_rows}
    rows_to_generate = [row for row in verified_rows if str(row["stage6_sample_id"]) not in completed_ids]
    generated = generation_callable(rows_to_generate)
    if len(generated) != len(rows_to_generate):
        raise HarnessError(
            f"{stream} generation produced {len(generated)} rows, expected {len(rows_to_generate)}"
        )
    assert_exact_sample_coverage(
        generated,
        {str(row["stage6_sample_id"]) for row in rows_to_generate},
        context=f"{stream} generated rows",
        allow_partial=True,
    )
    audit_by_id = {str(row["stage6_sample_id"]): row for row in rows_to_generate}
    normalized = [
        normalize_raw_generation_row(
            stream=stream,
            audit_row=audit_by_id[str(row.get("sample_id") or row.get("stage6_sample_id"))],
            generation_row=row,
            run_id=run_id,
            metadata=metadata,
        )
        for row in generated
    ]
    combined = existing_rows + normalized
    validate_raw_generation_rows(
        combined,
        expected_sample_ids=expected_sample_ids,
        expected_run_id=run_id,
        full_completion=len(combined) == FINAL_CONFIRMATION_N,
    )
    write_rows_incrementally(
        raw_path,
        normalized,
        existing_rows=existing_rows,
        expected_sample_ids=expected_sample_ids,
        expected_run_id=run_id,
    )
    return combined


def make_replay_provenance_rows(
    shared_rows: list[dict[str, Any]],
    *,
    replay_arm: str,
) -> list[dict[str, Any]]:
    if replay_arm not in {"d_g1_control", "d_f_g1_vnext"}:
        raise HarnessError("Only D_G1 and D_F_G1 deterministic replays may use shared rows")
    return [
        {
            "sample_id": row["stage6_sample_id"],
            "stage6_sample_id": row["stage6_sample_id"],
            "replay_arm": replay_arm,
            "source_raw_generation_stream": "shared_mp_fs_plus_generation",
            "shared_raw_generation_row_sha256": row["raw_generation_row_sha256"],
        }
        for row in shared_rows
    ]


def validate_shared_replay_provenance(
    d_g1_rows: list[dict[str, Any]],
    d_f_g1_rows: list[dict[str, Any]],
) -> None:
    def by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {str(row["stage6_sample_id"]): row for row in rows}

    left = by_id(d_g1_rows)
    right = by_id(d_f_g1_rows)
    if set(left) != set(right):
        raise HarnessError("D_G1 and D_F_G1 replay provenance sample IDs differ")
    for sample_id in left:
        for row in (left[sample_id], right[sample_id]):
            missing = [field for field in REQUIRED_REPLAY_FIELDS if field not in row]
            if missing:
                raise HarnessError(f"Replay provenance row missing fields: {missing}")
        if left[sample_id]["shared_raw_generation_row_sha256"] != right[sample_id]["shared_raw_generation_row_sha256"]:
            raise HarnessError(f"Shared replay row SHA differs for {sample_id}")


def execution_plan() -> dict[str, Any]:
    return {
        "stage": "Stage6H_CONFIRMATION_EXECUTION_HARNESS",
        "status": "HARNESS_READY_PENDING_REVIEWER_ACCEPTANCE",
        "model_called_in_stage6h_setup": False,
        "gpu_called_in_stage6h_setup": False,
        "confirmation_predictions_created": False,
        "stage6g_authorization_commit": STAGE6G_AUTHORIZATION_COMMIT,
        "final_confirmation_n": FINAL_CONFIRMATION_N,
        "prompt_token_audit_sha256": PROMPT_TOKEN_AUDIT_SHA256,
        "generation_lock_sha256": GENERATION_LOCK_SHA256,
        "generation_stream_order": [
            *STREAM_ORDER,
        ],
        "generation_streams": STREAMS,
        "execution_modes": {
            "initial": {
                "existing_raw_generation_rows_allowed": False,
                "required_existing_checkpoint": False,
            },
            "resume": {
                "existing_raw_generation_rows_allowed": True,
                "required_existing_checkpoint": True,
                "completed_rows_are_immutable": True,
                "only_unfinished_ids_may_be_generated": True,
            },
        },
        "runtime_input_locks": {
            "stage6f_prompt_token_audit": {
                "path": DEFAULT_PROMPT_TOKEN_AUDIT_PATH,
                "sha256": PROMPT_TOKEN_AUDIT_SHA256,
            },
            "stage6g_authorization": {
                "directory": AUTHORIZATION_DIR,
                "commit": STAGE6G_AUTHORIZATION_COMMIT,
            },
            "final_confirmation_manifest": {
                "path": FINAL_MANIFEST_PATH,
                "sha256": FINAL_MANIFEST_SHA256,
            },
            "final_gold_corpus": {
                "path": FINAL_GOLD_CORPUS_PATH,
                "sha256": FINAL_GOLD_CORPUS_SHA256,
            },
            "stage6f_gpu_environment_manifest": {
                "path": DEFAULT_GPU_ENVIRONMENT_MANIFEST_PATH,
            },
            "stage6_crudsql_isolated_db_root": {
                "path": STAGE6_CRUDSQL_DB_ROOT,
            },
        },
        "run_level_controller": {
            "execute_all_flag": "--execute-all",
            "resume_flag": "--resume-run",
            "fixed_stream_order": STREAM_ORDER,
            "default_execution_root": DEFAULT_EXECUTION_ROOT,
            "single_stream_gpu_cli_allowed": False,
            "expected_git_head_required": True,
            "dirty_worktree_bypass_allowed_in_gpu_mode": False,
            "execution_root_must_be_outside_repo_in_gpu_mode": True,
            "authorization_boundary_checked_once_per_initial_run": True,
            "model_loaded_once_per_execute_all": True,
            "initial_run_state_must_be_absent": True,
            "initial_run_creates_one_run_id": True,
            "resume_requires_existing_run_state": True,
            "resume_allows_initialized_zero_row_run_state": True,
            "resume_reuses_existing_run_id": True,
            "raw_rows_require_single_run_id": True,
            "checkpoints_store_run_id": True,
            "run_state_manifest": "CONFIRMATION_RUN_STATE.json",
            "execution_code_manifest": "EXECUTION_CODE_MANIFEST.json",
        },
        "pre_generation_phases": [
            "verify_stage6g_authorization_with_expected_git_head_and_clean_worktree",
            "reject_missing_expected_git_head_in_gpu_mode",
            "reject_dirty_worktree_override_in_gpu_mode",
            "reject_single_stream_gpu_cli_execution",
            "reject_execution_root_inside_source_checkout_in_gpu_mode",
            "initial_run_requires_absent_run_state",
            "resume_run_requires_existing_initialized_run_state",
            "resume_run_allows_initialized_zero_row_state",
            "resume_run_reuses_existing_run_id",
            "verify_zero_existing_raw_generation_files_for_initial_run",
            "verify_prompt_token_audit_file_sha256_before_parse",
            "write_and_validate_execution_code_manifest_before_model_load",
            "run_level_initial_all_stream_outputs_absent_check",
            "recompute_current_prompt_token_audit_for_stream",
            "map_shared_generation_stream_to_d_g1_control_prompt_audit_arm",
            "verify_d_g1_control_and_d_f_g1_vnext_input_identity_481_of_481",
            "verify_exact_481_unique_frozen_sample_ids_before_generation",
            "compare_prompt_chat_input_ids_and_token_count_481_of_481_before_generation",
            "pass_verified_runtime_request_objects_directly_to_generation_call",
            "tie_stage6e_final_id_set_to_stage6f_audit_and_runtime_ids",
        ],
        "post_generation_phases": [
            "execute_all_four_streams_in_fixed_order_with_one_run_id",
            "generate_one_sample_at_a_time_from_integrated_runner",
            "verify_exact_481_unique_frozen_sample_ids_after_generation",
            "verify_generated_rows_report_same_prompt_chat_input_ids_and_token_count",
            "normalize_raw_rows_to_stage6g_schema",
            "write_sample_id_and_stage6_sample_id_for_reuse_runner_compatibility",
            "preserve_generation_status_error_and_latency_fields",
            "write_raw_generation_row_sha256",
            "verify_all_raw_rows_share_the_locked_run_id",
            "write_incremental_stream_checkpoint_manifest",
            "write_and_verify_checkpoint_run_id",
            "verify_resume_checkpoint_before_any_resume",
            "write_run_state_manifest_after_each_stream",
            "retain_one_run_id_across_run_state_updates",
            "resume_run_verifies_prior_streams_before_continuation",
            "write_shared_replay_row_sha256_for_d_g1_and_d_f_g1",
            "write_actual_d_g1_and_d_f_g1_replay_provenance_after_shared_stream_completion",
        ],
    }


def create_setup(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = execution_plan()
    lock = {
        "stage": "Stage6H_CONFIRMATION_EXECUTION_HARNESS_SETUP",
        "status": "HARNESS_SETUP_LOCKED_PENDING_REVIEWER_ACCEPTANCE",
        "model_called": False,
        "gpu_called": False,
        "confirmation_predictions_created": False,
        "confirmation_run_started": False,
        "stage6g_authorization_commit": STAGE6G_AUTHORIZATION_COMMIT,
        "final_confirmation_n": FINAL_CONFIRMATION_N,
        "execution_plan_sha256": sha256_text(canonical_json(plan)),
        "required_harness_guards": plan["pre_generation_phases"] + plan["post_generation_phases"],
    }
    write_json(output_dir / "CONFIRMATION_EXECUTION_PLAN.json", plan)
    write_json(output_dir / "STAGE6H_EXECUTION_LOCK.json", lock)
    (output_dir / "REVIEWER_README.md").write_text(
        "# Stage6H Confirmation Execution Harness Setup\n\n"
        "CPU-only setup for the confirmatory execution harness. This package does "
        "not run the model and does not create predictions.\n",
        encoding="utf-8",
    )
    (output_dir / "VALIDATION_REPORT.md").write_text(
        "# Stage6H Validation Report\n\nExpected setup validator status: PASS.\n",
        encoding="utf-8",
    )
    return lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=STAGE6H_DIR)
    parser.add_argument("--execution-root", default=DEFAULT_EXECUTION_ROOT)
    parser.add_argument("--setup-only", action="store_true")
    parser.add_argument("--mode", choices=["initial", "resume"], default="initial")
    parser.add_argument("--execute-stream", choices=sorted(STREAMS))
    parser.add_argument("--execute-all", action="store_true")
    parser.add_argument("--resume-run", action="store_true")
    parser.add_argument("--enable-gpu-execution", action="store_true")
    parser.add_argument("--prompt-token-audit", default=DEFAULT_PROMPT_TOKEN_AUDIT_PATH)
    parser.add_argument("--model-name-or-path")
    parser.add_argument("--run-id")
    parser.add_argument("--expected-git-head")
    parser.add_argument("--allow-dirty-worktree", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.setup_only:
        if args.allow_dirty_worktree:
            raise SystemExit("--allow-dirty-worktree is forbidden in GPU confirmation execution mode.")
        if not args.expected_git_head:
            raise SystemExit("--expected-git-head is required in GPU confirmation execution mode.")
        if args.execute_stream:
            raise SystemExit(
                "--execute-stream is forbidden for confirmatory GPU execution; "
                "use --execute-all or --resume-run."
            )
        if args.resume_run and args.mode == "initial":
            args.mode = "resume"
        if not args.execute_all and not args.resume_run:
            raise SystemExit("Stage6H GPU execution requires --execute-all or --resume-run.")
        if not args.enable_gpu_execution:
            raise SystemExit(
                "Stage6H execution CLI is present but GPU execution requires "
                "--enable-gpu-execution after reviewer acceptance."
            )
        state = execute_all_streams(
            prompt_audit_path=Path(args.prompt_token_audit),
            execution_root=Path(args.execution_root),
            run_id=args.run_id,
            expected_git_head=args.expected_git_head,
            mode="resume" if args.resume_run else args.mode,
            model_name_or_path=args.model_name_or_path,
        )
        print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        return
    lock = create_setup(Path(args.output_dir))
    print(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
