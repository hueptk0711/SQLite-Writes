#!/usr/bin/env python3
"""Create Stage6F GPU environment preflight artifacts.

The default mode is deliberately conservative: it verifies all frozen
Stage5/Stage6E CPU-side anchors and writes a pending GPU-preflight package.
Passing ``--execute-gpu-preflight`` on a clean GPU checkout loads the pinned
tokenizer/model environment, builds the exact production prompts, tokenizes
without truncation, and records the preflight.  It never generates predictions
for the 481 confirmation samples.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from nldbwrite_v3.experiments.run_method import _prompt_for_sample  # noqa: E402
from nldbwrite_v3.schema import build_profile  # noqa: E402


STAGE = "Stage6F_GPU_ENVIRONMENT_PREFLIGHT"
DATE = "20260825"
STAGE6E_COMMIT = "f32e8b2c7152e0f31829eab004da0f396084e57e"
STAGE5_METHOD_COMMIT = "79f6a82144ec0407444ef37121f70eed2b20e01c"
STAGE5_PROTOCOL_COMMIT = "a7742b4c9150ab208e7c5d6708f0dff40bf05440"
STAGE5_METHOD_SOURCE_TREE_SHA256 = (
    "78901f8fec28b2aa6e75166283415926da2b5af1dec48c4ba37e71be3d73d67b"
)

FINAL_ARTIFACTS: dict[str, tuple[str, str]] = {
    "final_confirmation_sample_manifest": (
        "stage6_final_registration_revision/artifacts/FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl",
        "6a9fc9812d768001e3a8e8b87d2387a7b943c83237a4bca7603c304acf88bcc7",
    ),
    "final_gold_write_plans": (
        "stage6_final_registration_revision/artifacts/FINAL_GOLD_WRITE_PLANS.jsonl",
        "7d57145a062a91221ce95d74e5414e7f1d79bda9bea217ab016cb2175f8ddda8",
    ),
    "final_gold_programs": (
        "stage6_final_registration_revision/artifacts/FINAL_GOLD_PROGRAMS.jsonl",
        "d34208d3def6434591f05cb396505475f3fd1e5d057326baf8f7207cdceaa3cf",
    ),
    "final_gold_post_state_hashes": (
        "stage6_final_registration_revision/artifacts/FINAL_GOLD_POST_STATE_HASHES.jsonl",
        "ea2fc586c764592268d9f330651d7c14855a731b4045a2f79d26dd1853b32cc6",
    ),
    "final_gold_corpus": (
        "stage6_final_registration_revision/artifacts/FINAL_GOLD_CORPUS.jsonl",
        "2082e892858c065531e2456239e77e51bae6232fccdf717497fecadc5421fd16",
    ),
    "final_reviewed_gold_provenance": (
        "stage6_final_registration_revision/artifacts/FINAL_REVIEWED_GOLD_PROVENANCE.jsonl",
        "69e31913af8b0156807f3e2bf3e86ca495a9d1f779815933adb124a61b6c0f98",
    ),
}

STAGE5_CONFIGS: dict[str, tuple[str, str]] = {
    "direct": (
        "configs/stage5/resolved_direct_confirmation.json",
        "0795d31926345c62d5ba832d8374c9ac067967a3842c45854a2fff9b32c9f826",
    ),
    "j_fs": (
        "configs/stage5/resolved_j_fs_confirmation.json",
        "a4006a423eb62fd37e5b370aca48a3b9337971f49d94700703d634a3d25c0cfe",
    ),
    "original_mp_fs_plus": (
        "configs/stage5/resolved_original_mp_fs_plus.json",
        "ddda333ccb9b307ed3002213dad6572daa959c2dd5deb2e7d4623cb3aeead84d",
    ),
    "d_g1_control": (
        "configs/stage5/resolved_d_g1_control.json",
        "c7c9c4d54e59662ee8e251af3aea1747fa035cb306213f20c819098e96f1b6ca",
    ),
    "d_f_g1_vnext": (
        "configs/stage5/resolved_mp_fs_plus_vnext_r1.json",
        "b3a946fc977c3ea95d3226dca1361b1885c098fddf4afdc650f4d36f0e1ce9bf",
    ),
}

EXPECTED_ENVIRONMENT = {
    "python_major_minor": "3.12",
    "torch": "2.6.0+cu124",
    "transformers": "5.5.3",
    "accelerate": "1.14.0",
    "bitsandbytes": "0.47.0",
    "tokenizers": "0.22.2",
    "safetensors": "0.5.3",
    "cuda_wheel_index": "https://download.pytorch.org/whl/cu124",
}

MODEL_LOCK = {
    "backend": "hf",
    "model_id": "Qwen/Qwen2.5-Coder-7B-Instruct",
    "revision": "c03e6d358207e414f1eca0bb1891e29f1db0e242",
    "model_hash": "e2026c78ea002527089b088023b7ae2c1486f127f667cafbb823225877cd268c",
    "tokenizer_sha256": "06d1f5403e9eda68466f91b5c235eab56b530a9b8155e21f3bd0523b4b29e468",
    "model_config_sha256": "326f5a48d12e88e8115048769fd5bb4eac3f56dee63847b983bc908456d5c357",
    "device_map": "auto",
    "batch_size": 1,
    "context_length": 32768,
    "max_input_tokens": 28672,
    "max_new_tokens": 4096,
    "input_truncation_policy": "error",
    "quantization": "4bit",
    "compute_dtype": "float16",
    "bnb_4bit_quant_type": "fp4",
    "bnb_4bit_use_double_quant": False,
    "bnb_4bit_quant_storage": "uint8",
    "do_sample": False,
    "temperature": None,
    "top_p": None,
    "top_k": None,
    "seed": 42,
    "trust_remote_code": False,
    "chat_template_usage": {
        "messages": [{"role": "user", "content": "<production_prompt>"}],
        "tokenize": False,
        "add_generation_prompt": True,
    },
    "padding_side": "left",
}

ARM_CONFIGS = {
    "direct": ("D-FS-M", "direct"),
    "j_fs": ("J-FS-M", "j_fs"),
    "original_mp_fs_plus": ("MP-FS+", "original_mp_fs_plus"),
    "d_g1_control": ("MP-FS+", "d_g1_control"),
    "d_f_g1_vnext": ("MP-FS+", "d_f_g1_vnext"),
}

GENERATION_STREAMS = {
    "direct": {
        "config_arm": "direct",
        "raw_generation_path": "raw_generations/direct.jsonl",
        "role": "secondary_baseline",
    },
    "j_fs": {
        "config_arm": "j_fs",
        "raw_generation_path": "raw_generations/j_fs.jsonl",
        "role": "secondary_baseline",
    },
    "original_mp_fs_plus": {
        "config_arm": "original_mp_fs_plus",
        "raw_generation_path": "raw_generations/original_mp_fs_plus.jsonl",
        "role": "H1_comparator",
    },
    "shared_mp_fs_plus_generation": {
        "config_arm": "d_g1_control",
        "raw_generation_path": "raw_generations/shared_mp_fs_plus_generation.jsonl",
        "deterministic_replay_arms": ["d_g1_control", "d_f_g1_vnext"],
        "role": "H2_shared_raw_generation",
    },
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return sha256_bytes(data)
    return sha256_text(text.replace("\r\n", "\n").replace("\r", "\n"))


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def canonical_sha256(value: Any) -> str:
    return sha256_text(canonical_json(value))


def tree_hash(paths: Iterable[Path]) -> str:
    rows = []
    for path in sorted(paths, key=lambda item: item.as_posix()):
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        rows.append(f"{sha256_file(path)}  {relative}")
    return sha256_text("\n".join(rows) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def git_output(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=PROJECT_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def package_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def capture_environment() -> dict[str, Any]:
    packages = {
        name: package_version(name)
        for name in (
            "torch",
            "transformers",
            "accelerate",
            "bitsandbytes",
            "tokenizers",
            "safetensors",
        )
    }
    torch_info: dict[str, Any] = {}
    try:
        import torch  # type: ignore

        torch_info = {
            "torch_imported": True,
            "torch_version": getattr(torch, "__version__", None),
            "cuda_available": bool(torch.cuda.is_available()),
            "cuda_version": getattr(torch.version, "cuda", None),
            "device_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
            "devices": [
                {
                    "index": index,
                    "name": torch.cuda.get_device_name(index),
                    "total_memory_bytes": int(torch.cuda.get_device_properties(index).total_memory),
                }
                for index in range(torch.cuda.device_count())
            ]
            if torch.cuda.is_available()
            else [],
        }
    except Exception as exc:
        torch_info = {"torch_imported": False, "error": str(exc)}
    return {
        "captured_at_unix": int(time.time()),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "packages": packages,
        "environment_variables": {
            "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "NVIDIA_VISIBLE_DEVICES": os.environ.get("NVIDIA_VISIBLE_DEVICES"),
            "HF_HOME": os.environ.get("HF_HOME"),
            "TRANSFORMERS_CACHE": os.environ.get("TRANSFORMERS_CACHE"),
        },
        "sqlite_runtime": {
            "sqlite_version": sqlite3.sqlite_version,
            "sqlite_version_info": list(sqlite3.sqlite_version_info),
            "python_sqlite_module_file": getattr(sqlite3, "__file__", None),
        },
        "torch_cuda": torch_info,
        "nvidia_smi": run_nvidia_smi(),
        "expected_environment": EXPECTED_ENVIRONMENT,
        "environment_matches_expected": environment_matches(packages, platform.python_version()),
    }


def run_nvidia_smi() -> dict[str, Any]:
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader",
            ],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=20,
        ).strip()
    except Exception as exc:
        return {"available": False, "error": str(exc)}
    return {"available": True, "raw": output}


def environment_matches(packages: dict[str, str | None], python_version: str) -> bool:
    if not python_version.startswith(EXPECTED_ENVIRONMENT["python_major_minor"] + "."):
        return False
    for key in ("torch", "transformers", "accelerate", "bitsandbytes", "tokenizers", "safetensors"):
        if packages.get(key) != EXPECTED_ENVIRONMENT[key]:
            return False
    return True


def audit_frozen_artifacts() -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    file_rows: list[dict[str, Any]] = []
    for group, expected_map in (
        ("stage6e_final_artifacts", FINAL_ARTIFACTS),
        ("stage5_confirmation_configs", STAGE5_CONFIGS),
    ):
        for name, (relative, expected) in expected_map.items():
            path = PROJECT_ROOT / relative
            actual = sha256_file(path) if path.is_file() else None
            row = {
                "group": group,
                "name": name,
                "path": relative,
                "expected_sha256": expected,
                "actual_sha256": actual,
                "match": actual == expected,
            }
            file_rows.append(row)
            if actual != expected:
                violations.append({"code": "frozen_file_hash_mismatch", **row})

    executable_manifest_path = PROJECT_ROOT / "stage5_method_revision_freeze" / "EXECUTABLE_FREEZE_MANIFEST.json"
    executable_manifest = read_json(executable_manifest_path)
    implementation_files = [
        PROJECT_ROOT / relative
        for relative in executable_manifest.get("method_implementation_files") or []
    ]
    method_tree = tree_hash(implementation_files)
    if method_tree != STAGE5_METHOD_SOURCE_TREE_SHA256:
        violations.append(
            {
                "code": "method_source_tree_sha256_mismatch",
                "expected": STAGE5_METHOD_SOURCE_TREE_SHA256,
                "actual": method_tree,
            }
        )
    if executable_manifest.get("accepted_method_freeze_commit") != STAGE5_METHOD_COMMIT:
        violations.append(
            {
                "code": "stage5_method_commit_mismatch",
                "expected": STAGE5_METHOD_COMMIT,
                "actual": executable_manifest.get("accepted_method_freeze_commit"),
            }
        )

    final_manifest = read_jsonl(PROJECT_ROOT / FINAL_ARTIFACTS["final_confirmation_sample_manifest"][0])
    stage6e_lock = read_json(PROJECT_ROOT / "stage6_final_registration_revision" / "STAGE6E_FINAL_REGISTRATION_LOCK.json")
    expected_counts = {
        "final_confirmation_n": 481,
        "source_task_invalid_n": 19,
        "replacement_samples": 0,
    }
    for key, expected in expected_counts.items():
        if stage6e_lock.get(key) != expected:
            violations.append(
                {
                    "code": "stage6e_count_mismatch",
                    "field": key,
                    "expected": expected,
                    "actual": stage6e_lock.get(key),
                }
            )
    if len(final_manifest) != 481:
        violations.append(
            {"code": "final_manifest_row_count_mismatch", "expected": 481, "actual": len(final_manifest)}
        )

    return {
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "files": file_rows,
        "method_source_tree_sha256": {
            "expected": STAGE5_METHOD_SOURCE_TREE_SHA256,
            "actual": method_tree,
            "match": method_tree == STAGE5_METHOD_SOURCE_TREE_SHA256,
        },
        "stage5_method_commit": STAGE5_METHOD_COMMIT,
        "stage5_protocol_commit": STAGE5_PROTOCOL_COMMIT,
        "stage6e_commit": STAGE6E_COMMIT,
        "final_counts": {
            "final_confirmation_n": len(final_manifest),
            "source_task_invalid_n": stage6e_lock.get("source_task_invalid_n"),
            "replacement_samples": stage6e_lock.get("replacement_samples"),
        },
    }


def build_confirmation_run_plan() -> dict[str, Any]:
    return {
        "status": "LOCKED_NOT_AUTHORIZED_FOR_CONFIRMATION_RUN",
        "final_confirmation_n": 481,
        "generation_streams": GENERATION_STREAMS,
        "hypotheses": {
            "H1": "d_f_g1_vnext_vs_original_mp_fs_plus",
            "H2": "d_f_g1_vnext_vs_d_g1_control_shared_raw_generation",
        },
        "statistics": {
            "primary_metric": "target_state_correct",
            "paired_test": "exact_two_sided_McNemar",
            "family_correction": "Holm_over_H1_H2",
            "cluster_bootstrap_replicates": 10000,
            "cluster_key": "source_group",
            "seed": 240824,
        },
        "forbidden_confirmation_arms": [
            "FULL",
            "D_ONLY",
            "NO_C",
            "G2",
            "second_model",
            "post_hoc_token_budget_increase",
        ],
        "confirmation_predictions_created": False,
        "confirmation_run_allowed_now": False,
    }


def sample_to_method_row(sample: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": sample["stage6_sample_id"],
        "sample_id": sample["stage6_sample_id"],
        "input_text": sample["question"],
        "db_id": sample["table_id"],
    }


def load_arm_configs() -> dict[str, dict[str, Any]]:
    configs: dict[str, dict[str, Any]] = {}
    for arm, (_method_id, config_key) in ARM_CONFIGS.items():
        relative = STAGE5_CONFIGS[config_key][0]
        configs[arm] = read_json(PROJECT_ROOT / relative)
    return configs


def build_profile_cache(samples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    db_root = PROJECT_ROOT / "stage6_crudsql_registration"
    for sample in samples:
        table_id = str(sample["table_id"])
        if table_id in cache:
            continue
        db_path = db_root / str(sample["isolated_db"])
        if not db_path.is_file():
            raise FileNotFoundError(f"Missing isolated SQLite DB: {db_path}")
        cache[table_id] = build_profile(db_path, db_id=table_id)
    return cache


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


def load_tokenizer(model_name_or_path: str) -> Any:
    from transformers import AutoTokenizer

    kwargs: dict[str, Any] = {"trust_remote_code": False}
    if not Path(model_name_or_path).exists():
        kwargs["revision"] = MODEL_LOCK["revision"]
    tokenizer = AutoTokenizer.from_pretrained(model_name_or_path, **kwargs)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"
    return tokenizer


def tokenizer_manifest(tokenizer: Any | None, model_name_or_path: str | None) -> dict[str, Any]:
    if tokenizer is None:
        return {
            "status": "NOT_RUN",
            "reason": "tokenizer_not_loaded_without_execute_gpu_preflight",
            "expected": MODEL_LOCK,
        }
    stage5_identity = {
        "class": type(tokenizer).__name__,
        "vocab_sha256": canonical_sha256(tokenizer.get_vocab()),
        "special_tokens_map": tokenizer.special_tokens_map,
        "chat_template": getattr(tokenizer, "chat_template", None),
        "model_max_length": tokenizer.model_max_length,
        "padding_side": tokenizer.padding_side,
    }
    expanded_identity = {
        **stage5_identity,
        "pad_token_id": tokenizer.pad_token_id,
        "eos_token_id": tokenizer.eos_token_id,
        "bos_token_id": tokenizer.bos_token_id,
    }
    actual_tokenizer_sha256 = canonical_sha256(stage5_identity)
    return {
        "status": "PASS" if actual_tokenizer_sha256 == MODEL_LOCK["tokenizer_sha256"] else "FAIL",
        "model_name_or_path": model_name_or_path,
        "revision": MODEL_LOCK["revision"],
        "expected_tokenizer_sha256": MODEL_LOCK["tokenizer_sha256"],
        "actual_tokenizer_sha256": actual_tokenizer_sha256,
        "tokenizer_match": actual_tokenizer_sha256 == MODEL_LOCK["tokenizer_sha256"],
        "stage5_tokenizer_identity": stage5_identity,
        "tokenizer_identity": expanded_identity,
        "tokenizer_identity_sha256": canonical_sha256(expanded_identity),
        "chat_template_sha256": canonical_sha256(stage5_identity["chat_template"]),
    }


def create_prompt_token_audit(
    *,
    output_dir: Path,
    tokenizer: Any | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    samples = read_jsonl(PROJECT_ROOT / FINAL_ARTIFACTS["final_confirmation_sample_manifest"][0])
    expected_rows = len(samples) * len(ARM_CONFIGS)
    if tokenizer is None:
        write_jsonl(output_dir / "PROMPT_TOKEN_AUDIT.jsonl", [])
        summary = {
            "status": "NOT_RUN",
            "reason": "tokenizer_not_loaded_without_execute_gpu_preflight",
            "final_confirmation_n": len(samples),
            "expected_prompt_rows": expected_rows,
            "actual_prompt_rows": 0,
            "max_input_tokens": MODEL_LOCK["max_input_tokens"],
            "input_truncation_violations": "not_evaluated",
        }
        h2 = {
            "status": "NOT_RUN",
            "reason": "tokenizer_not_loaded_without_execute_gpu_preflight",
            "expected_shared_prompt_pairs": len(samples),
            "checked_pairs": 0,
            "all_shared_prompt_hashes_match": False,
            "independent_D_F_G1_generation_allowed": False,
            "F_changes_prompt_surface": False,
        }
        original_decision = {
            "status": "LOCKED_DECISION_PENDING_TOKEN_AUDIT",
            "original_mp_fs_plus_uses_independent_generation": True,
            "d_g1_and_d_f_g1_share_generation": True,
            "reason": "original prompt surface is not asserted identical to D_G1; H2 sharing is required and checked on GPU preflight",
        }
        return summary, h2, original_decision

    configs = load_arm_configs()
    profiles = build_profile_cache(samples)
    rows: list[dict[str, Any]] = []
    by_sample_arm: dict[tuple[str, str], dict[str, Any]] = {}
    for sample in samples:
        method_row = sample_to_method_row(sample)
        profile = profiles[str(sample["table_id"])]
        for arm, (method_id, _config_key) in ARM_CONFIGS.items():
            prompt, payload = _prompt_for_sample(method_id, method_row, profile, configs[arm])
            chat_prompt, token_ids = tokenize_prompt(tokenizer, prompt)
            row = {
                "stage6_sample_id": sample["stage6_sample_id"],
                "upstream_sample_locator": sample["upstream_sample_locator"],
                "arm": arm,
                "method_id": method_id,
                "payload_mode": getattr(payload, "mode", None),
                "prompt_sha256": sha256_text(prompt),
                "chat_prompt_sha256": sha256_text(chat_prompt),
                "input_token_count": len(token_ids),
                "input_ids_sha256": canonical_sha256(token_ids),
                "input_truncated": False,
                "within_max_input_tokens": len(token_ids) <= int(MODEL_LOCK["max_input_tokens"]),
            }
            rows.append(row)
            by_sample_arm[(str(sample["stage6_sample_id"]), arm)] = row
    write_jsonl(output_dir / "PROMPT_TOKEN_AUDIT.jsonl", rows)
    over_limit = [row for row in rows if not row["within_max_input_tokens"]]
    h2_mismatches = []
    for sample in samples:
        sid = str(sample["stage6_sample_id"])
        d_g1 = by_sample_arm[(sid, "d_g1_control")]
        d_f_g1 = by_sample_arm[(sid, "d_f_g1_vnext")]
        if d_g1["chat_prompt_sha256"] != d_f_g1["chat_prompt_sha256"]:
            h2_mismatches.append(
                {
                    "stage6_sample_id": sid,
                    "d_g1_chat_prompt_sha256": d_g1["chat_prompt_sha256"],
                    "d_f_g1_chat_prompt_sha256": d_f_g1["chat_prompt_sha256"],
                }
            )
    counts = Counter(row["arm"] for row in rows)
    summary = {
        "status": "PASS" if not over_limit and len(rows) == expected_rows else "FAIL",
        "final_confirmation_n": len(samples),
        "expected_prompt_rows": expected_rows,
        "actual_prompt_rows": len(rows),
        "rows_per_arm": dict(sorted(counts.items())),
        "max_input_tokens": MODEL_LOCK["max_input_tokens"],
        "max_observed_input_tokens": max(row["input_token_count"] for row in rows),
        "input_truncation_error_count": len(over_limit),
        "over_limit_samples": over_limit[:20],
        "prompt_token_audit_sha256": sha256_file(output_dir / "PROMPT_TOKEN_AUDIT.jsonl"),
    }
    h2 = {
        "status": "PASS" if not h2_mismatches else "FAIL",
        "expected_shared_prompt_pairs": len(samples),
        "checked_pairs": len(samples),
        "mismatch_count": len(h2_mismatches),
        "mismatches": h2_mismatches[:20],
        "all_shared_prompt_hashes_match": not h2_mismatches,
        "independent_D_F_G1_generation_allowed": False,
        "F_changes_prompt_surface": False,
        "shared_raw_generation_path": "raw_generations/shared_mp_fs_plus_generation.jsonl",
    }
    original_decision = {
        "status": "LOCKED",
        "original_mp_fs_plus_uses_independent_generation": True,
        "d_g1_and_d_f_g1_share_generation": True,
        "original_raw_generation_path": "raw_generations/original_mp_fs_plus.jsonl",
        "h2_shared_raw_generation_path": "raw_generations/shared_mp_fs_plus_generation.jsonl",
        "reason": "H1 compares final method to original MP-FS+ as independent generation arms; H2 isolates F by replaying D_G1 and D_F_G1 from the same MP-FS+ raw generation.",
    }
    return summary, h2, original_decision


def model_asset_manifest(
    *,
    model_name_or_path: str | None,
    execute_gpu_preflight: bool,
    load_model: bool,
    tokenizer: Any | None = None,
    run_synthetic_smoke: bool = False,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "expected_model_lock": MODEL_LOCK,
        "model_name_or_path": model_name_or_path,
        "model_loaded": False,
        "model_generate_called": False,
        "model_generate_called_for_synthetic_smoke": False,
        "model_generate_called_for_confirmation_samples": False,
        "confirmation_prediction_generated": False,
    }
    if not execute_gpu_preflight:
        manifest["status"] = "NOT_RUN"
        manifest["reason"] = "execute_gpu_preflight_false"
        return manifest
    if not model_name_or_path:
        manifest["status"] = "FAIL"
        manifest["reason"] = "model_name_or_path_required_for_gpu_preflight"
        return manifest
    if not load_model:
        manifest["status"] = "PASS_TOKENIZER_ONLY"
        manifest["reason"] = "model_load_not_requested"
        return manifest
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig
    import torch
    from nldbwrite_v3.inference.model_manifest import build_local_model_manifest

    local_manifest = None
    if Path(model_name_or_path).exists():
        local_manifest = build_local_model_manifest(model_name_or_path)
    kwargs: dict[str, Any] = {
        "trust_remote_code": False,
        "device_map": "auto",
        "quantization_config": BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_quant_type="fp4",
            bnb_4bit_use_double_quant=False,
            bnb_4bit_quant_storage=torch.uint8,
        ),
    }
    if not Path(model_name_or_path).exists():
        kwargs["revision"] = MODEL_LOCK["revision"]
    model = AutoModelForCausalLM.from_pretrained(model_name_or_path, **kwargs)
    model.eval()
    model_config_sha256 = canonical_sha256(model.config.to_dict())
    synthetic_report = run_synthetic_generation_smoke(model, tokenizer, torch) if run_synthetic_smoke else None
    manifest.update(
        {
            "status": (
                "PASS"
                if (
                    (local_manifest or {}).get("aggregate_sha256") == MODEL_LOCK["model_hash"]
                    and model_config_sha256 == MODEL_LOCK["model_config_sha256"]
                    and (not run_synthetic_smoke or synthetic_report["status"] == "PASS")
                )
                else "FAIL"
            ),
            "model_loaded": True,
            "model_class": type(model).__name__,
            "expected_model_revision": MODEL_LOCK["revision"],
            "actual_model_revision": MODEL_LOCK["revision"] if MODEL_LOCK["revision"] in str(model_name_or_path) else None,
            "expected_model_aggregate_sha256": MODEL_LOCK["model_hash"],
            "actual_model_aggregate_sha256": (local_manifest or {}).get("aggregate_sha256"),
            "model_aggregate_match": (local_manifest or {}).get("aggregate_sha256") == MODEL_LOCK["model_hash"],
            "local_model_manifest": local_manifest,
            "expected_model_config_sha256": MODEL_LOCK["model_config_sha256"],
            "model_config_sha256": model_config_sha256,
            "model_config_match": model_config_sha256 == MODEL_LOCK["model_config_sha256"],
            "generation_config_sha256": canonical_sha256(model.generation_config.to_dict()),
            "model_generate_called": bool(synthetic_report),
            "model_generate_called_for_synthetic_smoke": bool(synthetic_report),
            "synthetic_smoke_embedded_report": synthetic_report,
        }
    )
    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return manifest


def run_synthetic_generation_smoke(model: Any, tokenizer: Any | None, torch: Any) -> dict[str, Any]:
    if tokenizer is None:
        return {
            "status": "FAIL",
            "error": "tokenizer_required_for_synthetic_smoke",
            "confirmation_samples_used": 0,
            "confirmation_predictions_created": False,
        }
    prompt = (
        "You are checking GPU generation plumbing only. "
        "Given CREATE TABLE demo(id INTEGER PRIMARY KEY, name TEXT); "
        "write one SQLite INSERT for id 1 and name Alice."
    )
    chat_prompt = apply_chat_template(tokenizer, prompt)
    encoded = tokenizer(chat_prompt, return_tensors="pt", add_special_tokens=True, truncation=False)
    device = next(model.parameters()).device
    encoded = {key: value.to(device) for key, value in encoded.items()}
    input_len = int(encoded["input_ids"].shape[-1])
    with torch.no_grad():
        output = model.generate(
            **encoded,
            max_new_tokens=32,
            do_sample=False,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
    new_tokens = output[0][input_len:]
    decoded = tokenizer.decode(new_tokens, skip_special_tokens=True)
    return {
        "status": "PASS",
        "prompt_sha256": sha256_text(prompt),
        "chat_prompt_sha256": sha256_text(chat_prompt),
        "input_token_count": input_len,
        "max_new_tokens": 32,
        "output_token_count": int(new_tokens.shape[-1]),
        "decoded_output_sha256": sha256_text(decoded),
        "decoded_output_preview": decoded[:500],
        "confirmation_samples_used": 0,
        "model_generate_called_for_synthetic_smoke": True,
        "model_generate_called_for_confirmation_samples": False,
        "confirmation_predictions_created": False,
    }


def synthetic_smoke_report(*, model_info: dict[str, Any], run_synthetic_smoke: bool) -> dict[str, Any]:
    embedded = model_info.get("synthetic_smoke_embedded_report")
    if embedded:
        return embedded
    return {
        "status": "NOT_RUN",
        "run_synthetic_smoke_requested": bool(run_synthetic_smoke),
        "reason": "synthetic smoke was not requested or model load failed",
        "confirmation_samples_used": 0,
        "model_generate_called_for_synthetic_smoke": False,
        "model_generate_called_for_confirmation_samples": False,
        "confirmation_predictions_created": False,
    }


def model_tokenizer_asset_audit(model_info: dict[str, Any], tokenizer_info: dict[str, Any]) -> dict[str, Any]:
    rows = [
        {
            "name": "model_revision",
            "expected": MODEL_LOCK["revision"],
            "actual": model_info.get("actual_model_revision"),
            "match": model_info.get("actual_model_revision") == MODEL_LOCK["revision"],
        },
        {
            "name": "model_aggregate_sha256",
            "expected": MODEL_LOCK["model_hash"],
            "actual": model_info.get("actual_model_aggregate_sha256"),
            "match": model_info.get("actual_model_aggregate_sha256") == MODEL_LOCK["model_hash"],
        },
        {
            "name": "tokenizer_sha256",
            "expected": MODEL_LOCK["tokenizer_sha256"],
            "actual": tokenizer_info.get("actual_tokenizer_sha256"),
            "match": tokenizer_info.get("actual_tokenizer_sha256") == MODEL_LOCK["tokenizer_sha256"],
        },
        {
            "name": "model_config_sha256",
            "expected": MODEL_LOCK["model_config_sha256"],
            "actual": model_info.get("model_config_sha256"),
            "match": model_info.get("model_config_sha256") == MODEL_LOCK["model_config_sha256"],
        },
    ]
    return {
        "status": "PASS" if all(row["match"] for row in rows) else "FAIL",
        "hashing_procedure": {
            "model_aggregate_sha256": "nldbwrite_v3.inference.model_manifest.build_local_model_manifest",
            "tokenizer_sha256": "HuggingFaceGenerator tokenizer identity JSON SHA-256",
            "model_config_sha256": "model.config.to_dict canonical JSON SHA-256",
        },
        "expected_model_revision": MODEL_LOCK["revision"],
        "actual_model_revision": model_info.get("actual_model_revision"),
        "expected_model_aggregate_sha256": MODEL_LOCK["model_hash"],
        "actual_model_aggregate_sha256": model_info.get("actual_model_aggregate_sha256"),
        "model_aggregate_match": model_info.get("actual_model_aggregate_sha256") == MODEL_LOCK["model_hash"],
        "expected_tokenizer_sha256": MODEL_LOCK["tokenizer_sha256"],
        "actual_tokenizer_sha256": tokenizer_info.get("actual_tokenizer_sha256"),
        "tokenizer_match": tokenizer_info.get("actual_tokenizer_sha256") == MODEL_LOCK["tokenizer_sha256"],
        "expected_model_config_sha256": MODEL_LOCK["model_config_sha256"],
        "actual_model_config_sha256": model_info.get("model_config_sha256"),
        "model_config_match": model_info.get("model_config_sha256") == MODEL_LOCK["model_config_sha256"],
        "checks": rows,
    }


def create_server_commands() -> str:
    return f"""# Stage6F GPU preflight server commands

Run this after the Stage6F reviewer accepts the script package. These commands
do not run confirmatory inference and do not create predictions for the 481
confirmation samples.

```bash
ssh uet@222.255.250.24
mkdir -p /home/uet/hue_ptk
cd /home/uet/hue_ptk

if [ ! -d SQLite-Writes ]; then
  git clone https://github.com/hueptk0711/SQLite-Writes.git SQLite-Writes
fi

cd SQLite-Writes
git fetch --all --tags
git checkout stage6f/gpu-environment-preflight
EXPECTED_EXECUTION_COMMIT="$(git rev-parse HEAD)"
echo "$EXPECTED_EXECUTION_COMMIT"
git status --porcelain

# The checkout must be clean before preflight.
# Replace MODEL_PATH with the local cached Qwen2.5-Coder-7B-Instruct snapshot
# path if the server uses an offline HF cache.
MODEL_PATH="${{MODEL_PATH:-Qwen/Qwen2.5-Coder-7B-Instruct}}"
OUT_DIR="/home/uet/hue_ptk/stage6f_gpu_preflight_outputs/stage6_gpu_preflight"

python scripts/data/create_stage6f_gpu_preflight.py \\
  --output-dir "$OUT_DIR" \\
  --execute-gpu-preflight \\
  --expected-execution-commit "$EXPECTED_EXECUTION_COMMIT" \\
  --model-name-or-path "$MODEL_PATH" \\
  --load-model \\
  --run-synthetic-smoke

python scripts/data/validate_stage6f_gpu_preflight.py \\
  --preflight-dir "$OUT_DIR" \\
  --require-gpu-pass
```
"""


def create_preflight(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    git_head = git_output("rev-parse", "HEAD")
    git_status_before = git_output("status", "--porcelain")
    frozen_audit = audit_frozen_artifacts()
    environment = capture_environment()

    tokenizer = None
    tokenizer_error = None
    if args.execute_gpu_preflight:
        if args.expected_execution_commit and git_head != args.expected_execution_commit:
            tokenizer_error = (
                "execute_gpu_preflight requires exact expected execution commit "
                f"{args.expected_execution_commit}; observed {git_head}"
            )
        elif git_status_before:
            tokenizer_error = "execute_gpu_preflight requires clean git status before writing preflight outputs"
        elif not args.model_name_or_path:
            tokenizer_error = "execute_gpu_preflight requires --model-name-or-path"
        else:
            try:
                tokenizer = load_tokenizer(args.model_name_or_path)
            except Exception as exc:
                tokenizer_error = str(exc)

    prompt_summary, h2_audit, original_decision = create_prompt_token_audit(
        output_dir=output_dir,
        tokenizer=tokenizer,
    )
    tokenizer_info = tokenizer_manifest(tokenizer, args.model_name_or_path)
    if tokenizer_error:
        tokenizer_info = {
            **tokenizer_info,
            "status": "FAIL",
            "error": tokenizer_error,
        }
    model_info = model_asset_manifest(
        model_name_or_path=args.model_name_or_path,
        execute_gpu_preflight=bool(args.execute_gpu_preflight and not tokenizer_error),
        load_model=bool(args.load_model),
        tokenizer=tokenizer,
        run_synthetic_smoke=bool(args.run_synthetic_smoke),
    )
    smoke = synthetic_smoke_report(model_info=model_info, run_synthetic_smoke=args.run_synthetic_smoke)
    asset_audit = model_tokenizer_asset_audit(model_info, tokenizer_info)
    run_plan = build_confirmation_run_plan()

    gpu_pass = (
        bool(args.execute_gpu_preflight)
        and frozen_audit["status"] == "PASS"
        and environment["environment_matches_expected"]
        and tokenizer_info.get("status") == "PASS"
        and prompt_summary["status"] == "PASS"
        and h2_audit["status"] == "PASS"
        and model_info.get("status") in {"PASS", "PASS_TOKENIZER_ONLY"}
        and smoke.get("status") == "PASS"
        and asset_audit.get("status") == "PASS"
    )
    status = "PASS_GPU_PREFLIGHT_COMPLETE" if gpu_pass else "PENDING_GPU_EXECUTION"
    if args.execute_gpu_preflight and not gpu_pass:
        status = "FAIL_GPU_PREFLIGHT"

    lock = {
        "stage": STAGE,
        "date": DATE,
        "status": status,
        "stage6e_accepted_commit": STAGE6E_COMMIT,
        "expected_execution_commit": args.expected_execution_commit,
        "observed_git_head": git_head,
        "git_status_porcelain_before_preflight_outputs": git_status_before or "",
        "frozen_artifact_audit_status": frozen_audit["status"],
        "gpu_environment_matches_expected": bool(environment["environment_matches_expected"]),
        "model_asset_status": model_info.get("status"),
        "model_tokenizer_asset_audit_status": asset_audit.get("status"),
        "tokenizer_status": tokenizer_info.get("status"),
        "prompt_token_audit_status": prompt_summary["status"],
        "h2_shared_prompt_identity_status": h2_audit["status"],
        "final_confirmation_n": 481,
        "confirmation_predictions_created": False,
        "model_generate_called_for_confirmation_samples": False,
        "gpu_called": bool(args.execute_gpu_preflight),
        "model_called": bool(model_info.get("model_loaded")),
        "gpu_environment_preflight_passed": gpu_pass,
        "confirmation_run_allowed_now": False,
        "next_step": (
            "reviewer_acceptance_then_confirmation_run_authorization_lock"
            if gpu_pass
            else "run_stage6f_gpu_preflight_on_locked_gpu_server"
        ),
        "validation_violations": frozen_audit["violations"],
    }

    write_json(output_dir / "FROZEN_ARTIFACT_AUDIT.json", frozen_audit)
    write_json(output_dir / "GPU_ENVIRONMENT_MANIFEST.json", environment)
    write_json(output_dir / "MODEL_ASSET_MANIFEST.json", model_info)
    write_json(output_dir / "TOKENIZER_MANIFEST.json", tokenizer_info)
    write_json(output_dir / "MODEL_TOKENIZER_ASSET_AUDIT.json", asset_audit)
    write_json(output_dir / "PROMPT_TOKEN_SUMMARY.json", prompt_summary)
    write_json(output_dir / "H2_SHARED_PROMPT_IDENTITY_AUDIT.json", h2_audit)
    write_json(output_dir / "ORIGINAL_VS_VNEXT_GENERATION_IDENTITY_DECISION.json", original_decision)
    write_json(output_dir / "SYNTHETIC_GPU_SMOKE_REPORT.json", smoke)
    write_json(output_dir / "CONFIRMATION_RUN_PLAN.json", run_plan)
    write_json(output_dir / "STAGE6F_GPU_PREFLIGHT_LOCK.json", lock)
    (output_dir / "RUN_STAGE6F_ON_SERVER.md").write_text(create_server_commands(), encoding="utf-8")
    return lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="stage6_gpu_preflight")
    parser.add_argument("--execute-gpu-preflight", action="store_true")
    parser.add_argument("--model-name-or-path")
    parser.add_argument("--expected-execution-commit")
    parser.add_argument("--load-model", action="store_true")
    parser.add_argument("--run-synthetic-smoke", action="store_true")
    return parser.parse_args()


def main() -> None:
    lock = create_preflight(parse_args())
    print(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
