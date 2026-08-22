#!/usr/bin/env python3
"""Mandatory Stage-4 GPU/tokenizer preflight before any fresh 7B generation.

This script does not call ``model.generate``.  It loads the pinned tokenizer,
builds the exact production prompts, applies the same chat template contract as
``HuggingFaceGenerator``, tokenizes without truncation, hashes final input IDs,
checks the total context budget, and proves that Original MP-FS+ and D_G1 can
share one raw MP-FS+ generation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from nldbwrite_v3.experiments.run_method import (  # noqa: E402
    _load_method_config,
    _load_profiles,
    _prompt_for_sample,
)
from scripts.analysis.run_stage3_causal_replay import write_csv, write_json  # noqa: E402
from scripts.analysis.run_stage4_fresh_7b_protocol import (  # noqa: E402
    CONFIGS,
    EXPECTED_SAMPLE_COUNT,
    INFERENCE_LOCK,
    MODEL_LOCK,
    GENERATION_ARMS,
    sha256_text,
    source_group_key,
    stage4_hf_inference_config,
)


EXPECTED_GPU_PYTHON_MAJOR_MINOR = "3.14"
REQUIRED_ENVIRONMENT_PACKAGES = (
    "torch",
    "transformers",
    "accelerate",
    "bitsandbytes",
    "tokenizers",
    "safetensors",
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_ids(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def git_output(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True, encoding="utf-8").strip()


def parse_locked_requirement_versions(path: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        if "==" not in line:
            continue
        package, version = line.split("==", 1)
        package = package.strip().lower().replace("_", "-")
        version = version.strip().split(";", 1)[0].strip()
        versions[package] = version
    return versions


def environment_version_audit(
    *,
    environment: Mapping[str, Any],
    dependency_lock_path: Path,
    expected_python_major_minor: str = EXPECTED_GPU_PYTHON_MAJOR_MINOR,
) -> dict[str, Any]:
    locked = parse_locked_requirement_versions(dependency_lock_path)
    package_rows: list[dict[str, Any]] = []
    for package_name in REQUIRED_ENVIRONMENT_PACKAGES:
        expected = locked.get(package_name)
        actual = environment.get(f"{package_name}_version")
        package_rows.append(
            {
                "package": package_name,
                "expected_version": expected,
                "actual_version": actual,
                "match": bool(expected is not None and actual == expected),
            }
        )
    python_actual = str(environment.get("python") or "")
    python_match = python_actual.startswith(expected_python_major_minor + ".")
    return {
        "dependency_lock": {
            "path": str(dependency_lock_path.resolve()),
            "sha256": hashlib.sha256(dependency_lock_path.read_bytes()).hexdigest(),
        },
        "expected_python_major_minor": expected_python_major_minor,
        "actual_python": python_actual,
        "python_match": python_match,
        "packages": package_rows,
        "status": (
            "PASS"
            if python_match and all(row["match"] for row in package_rows)
            else "STOP"
        ),
    }


def assert_environment_versions(
    *,
    environment: Mapping[str, Any],
    dependency_lock_path: Path,
    expected_python_major_minor: str = EXPECTED_GPU_PYTHON_MAJOR_MINOR,
) -> dict[str, Any]:
    audit = environment_version_audit(
        environment=environment,
        dependency_lock_path=dependency_lock_path,
        expected_python_major_minor=expected_python_major_minor,
    )
    if audit["status"] != "PASS":
        raise SystemExit(
            "STOP: installed GPU environment does not match "
            "requirements-inference.lock.txt exactly"
        )
    return audit


def assert_git_execution_lock(accepted_protocol_commit: str) -> dict[str, str]:
    head = git_output("rev-parse", "HEAD")
    if head != accepted_protocol_commit:
        raise SystemExit(
            "STOP: execution commit does not match accepted_protocol_commit: "
            f"head={head} accepted={accepted_protocol_commit}"
        )
    dirty = git_output("status", "--porcelain")
    if dirty:
        raise SystemExit(
            "STOP: working tree is not clean before generation/preflight. "
            "Generated outputs must be outside git or created after this assertion."
        )
    return {
        "accepted_protocol_commit": accepted_protocol_commit,
        "execution_commit": head,
        "working_tree_clean": "true",
    }


def protocol_path_or_arg(protocol_root: Path, manifest_key: str, override: str | None) -> Path:
    if override:
        return Path(override).resolve()
    manifest = read_json(protocol_root / "data" / "fresh_dataset_manifest.json")
    return Path(str(manifest[manifest_key])).resolve()


def load_frozen_samples(protocol_root: Path, fresh_source_data: Path) -> list[dict[str, Any]]:
    selected_ids = read_ids(protocol_root / "data" / "fresh_sample_ids.txt")
    if len(selected_ids) != EXPECTED_SAMPLE_COUNT:
        raise ValueError(f"Expected 300 frozen sample IDs, found {len(selected_ids)}")
    source_rows = read_json(fresh_source_data)
    by_id = {str(row.get("id") or row.get("sample_id")): row for row in source_rows}
    missing = [sample_id for sample_id in selected_ids if sample_id not in by_id]
    if missing:
        raise ValueError(f"Frozen sample IDs missing from source data: {missing[:10]}")
    return [by_id[sample_id] for sample_id in selected_ids]


def build_prompts(
    *,
    protocol_root: Path,
    fresh_source_data: Path,
    profile_dir: Path,
) -> dict[str, dict[str, str]]:
    samples = load_frozen_samples(protocol_root, fresh_source_data)
    profiles = _load_profiles(profile_dir)
    configs = {
        slug: _load_method_config(PROJECT_ROOT / relative)[0]
        for slug, _label, _method_id, relative, _generation_role, _analysis_role in CONFIGS
    }
    prompts: dict[str, dict[str, str]] = {}
    for sample in samples:
        sample_id = str(sample.get("id") or sample.get("sample_id"))
        db_id = str(sample["db_id"])
        if db_id not in profiles:
            raise ValueError(f"Missing profile for db_id={db_id}")
        prompts[sample_id] = {}
        for slug, _label, method_id, _relative, _generation_role, _analysis_role in CONFIGS:
            if slug in {"d_only_secondary", "full_secondary", "no_c_secondary"}:
                continue
            prompt, _payload = _prompt_for_sample(method_id, sample, profiles[db_id], configs[slug])
            prompts[sample_id][slug] = prompt
    return prompts


def apply_stage4_chat_template(tokenizer: Any, prompt: str) -> str:
    if hasattr(tokenizer, "apply_chat_template"):
        return tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=False,
            add_generation_prompt=True,
        )
    return prompt


def token_ids_for_prompt(tokenizer: Any, prompt: str) -> list[int]:
    chat_prompt = apply_stage4_chat_template(tokenizer, prompt)
    encoded = tokenizer(
        chat_prompt,
        add_special_tokens=True,
        truncation=False,
    )
    return [int(item) for item in encoded["input_ids"]]


def load_tokenizer(model_name_or_path: str) -> Any:
    from transformers import AutoTokenizer

    local_model = Path(model_name_or_path).exists()
    kwargs: dict[str, Any] = {
        "trust_remote_code": bool(INFERENCE_LOCK["trust_remote_code"]),
    }
    if not local_model:
        kwargs["revision"] = MODEL_LOCK["tokenizer_revision"]
    tokenizer = AutoTokenizer.from_pretrained(model_name_or_path, **kwargs)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = str(INFERENCE_LOCK["padding_side"])
    return tokenizer


def capture_environment(model_name_or_path: str, tokenizer: Any) -> dict[str, Any]:
    env: dict[str, Any] = {
        "python": sys.version,
        "model_name_or_path": model_name_or_path,
        "model_revision": MODEL_LOCK["snapshot_revision"],
        "tokenizer_class": type(tokenizer).__name__,
        "tokenizer_padding_side": tokenizer.padding_side,
        "tokenizer_pad_token_id": tokenizer.pad_token_id,
        "tokenizer_eos_token_id": tokenizer.eos_token_id,
        "tokenizer_bos_token_id": tokenizer.bos_token_id,
        "chat_template_sha256": json_sha256(getattr(tokenizer, "chat_template", None)),
    }
    for package_name in ("torch", "transformers", "accelerate", "bitsandbytes", "tokenizers", "safetensors"):
        try:
            module = __import__(package_name)
            env[f"{package_name}_version"] = getattr(module, "__version__", "unknown")
        except Exception as exc:  # pragma: no cover - depends on server env
            env[f"{package_name}_version"] = f"IMPORT_ERROR:{exc}"
    try:
        import torch

        env["cuda_runtime"] = torch.version.cuda
        env["cuda_available"] = bool(torch.cuda.is_available())
        env["gpu_model"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
        env["gpu_driver"] = (
            subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=driver_version",
                    "--format=csv,noheader",
                ],
                text=True,
                encoding="utf-8",
            )
            .splitlines()[0]
            .strip()
            if torch.cuda.is_available()
            else None
        )
    except Exception as exc:  # pragma: no cover - depends on server env
        env["cuda_probe_error"] = str(exc)
    return env


def run_preflight(
    *,
    protocol_root: Path,
    fresh_source_data: Path,
    profile_dir: Path,
    model_name_or_path: str,
    output_dir: Path,
    git_lock: Mapping[str, str] | None,
    dependency_lock_path: Path | None = None,
    expected_python_major_minor: str = EXPECTED_GPU_PYTHON_MAJOR_MINOR,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    tokenizer = load_tokenizer(model_name_or_path)
    prompts = build_prompts(
        protocol_root=protocol_root,
        fresh_source_data=fresh_source_data,
        profile_dir=profile_dir,
    )
    chat_template_hash = json_sha256(getattr(tokenizer, "chat_template", None))
    max_new_tokens = int(INFERENCE_LOCK["max_new_tokens"])
    context_length = int(INFERENCE_LOCK["context_length"])
    max_input_tokens = int(INFERENCE_LOCK["max_input_tokens"])
    rows: list[dict[str, Any]] = []
    equality_rows: list[dict[str, Any]] = []
    equality_count = 0
    overflow_count = 0
    for sample_id, sample_prompts in prompts.items():
        direct_ids = token_ids_for_prompt(tokenizer, sample_prompts["direct"])
        jfs_ids = token_ids_for_prompt(tokenizer, sample_prompts["j_fs"])
        original_ids = token_ids_for_prompt(tokenizer, sample_prompts["original_mp_fs_plus"])
        dg1_ids = token_ids_for_prompt(tokenizer, sample_prompts["d_g1_primary"])
        original_hash = json_sha256(original_ids)
        dg1_hash = json_sha256(dg1_ids)
        equal = original_ids == dg1_ids
        equality_count += int(equal)
        equality_rows.append(
            {
                "sample_id": sample_id,
                "original_pre_chat_prompt_sha256": sha256_text(sample_prompts["original_mp_fs_plus"]),
                "d_g1_pre_chat_prompt_sha256": sha256_text(sample_prompts["d_g1_primary"]),
                "original_input_ids_sha256": original_hash,
                "d_g1_input_ids_sha256": dg1_hash,
                "final_input_ids_equal": int(equal),
            }
        )
        for generation_arm, prompt, input_ids in (
            ("direct", sample_prompts["direct"], direct_ids),
            ("j_fs", sample_prompts["j_fs"], jfs_ids),
            ("mp_fs_plus_shared", sample_prompts["original_mp_fs_plus"], original_ids),
        ):
            token_count = len(input_ids)
            total_budget = token_count + max_new_tokens
            overflow = int(total_budget > context_length or token_count > max_input_tokens)
            overflow_count += overflow
            rows.append(
                {
                    "sample_id": sample_id,
                    "generation_arm": generation_arm,
                    "pre_chat_prompt_sha256": sha256_text(prompt),
                    "chat_template_sha256": chat_template_hash,
                    "input_ids_sha256": json_sha256(input_ids),
                    "input_token_count": token_count,
                    "max_new_tokens": max_new_tokens,
                    "total_budget": total_budget,
                    "overflow": overflow,
                }
            )
    write_csv(
        output_dir / "gpu_preflight_manifest.csv",
        rows,
        [
            "sample_id",
            "generation_arm",
            "pre_chat_prompt_sha256",
            "chat_template_sha256",
            "input_ids_sha256",
            "input_token_count",
            "max_new_tokens",
            "total_budget",
            "overflow",
        ],
    )
    write_csv(
        output_dir / "original_vs_dg1_final_input_equality.csv",
        equality_rows,
        list(equality_rows[0]),
    )
    environment = capture_environment(model_name_or_path, tokenizer)
    environment_audit = (
        assert_environment_versions(
            environment=environment,
            dependency_lock_path=dependency_lock_path,
            expected_python_major_minor=expected_python_major_minor,
        )
        if dependency_lock_path is not None
        else None
    )
    summary = {
        "status": "PASS"
        if overflow_count == 0 and equality_count == EXPECTED_SAMPLE_COUNT
        else "STOP",
        "sample_count": EXPECTED_SAMPLE_COUNT,
        "generation_arms": sorted(GENERATION_ARMS),
        "manifest_rows": len(rows),
        "overflow_count": overflow_count,
        "original_vs_dg1_final_input_equal": f"{equality_count}/{EXPECTED_SAMPLE_COUNT}",
        "exact_tokenizer_preflight": True,
        "inference_config": stage4_hf_inference_config(model_name_or_path),
        "environment": environment,
        "environment_version_audit": environment_audit,
        "git": dict(git_lock or {}),
    }
    write_json(output_dir / "gpu_preflight_summary.json", summary)
    if summary["status"] != "PASS":
        raise SystemExit("STOP: GPU preflight failed; send outputs for review before generation.")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol-root", default="stage4_fresh_7b_protocol")
    parser.add_argument("--fresh-source-data")
    parser.add_argument("--fresh-gold-plans", help="Accepted for interface symmetry; not read by tokenizer preflight.")
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--model-name-or-path", required=True)
    parser.add_argument("--accepted-protocol-commit", required=True)
    parser.add_argument("--dependency-lock", default="requirements-inference.lock.txt")
    parser.add_argument("--expected-python-major-minor", default=EXPECTED_GPU_PYTHON_MAJOR_MINOR)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--skip-git-assertions-for-dry-run-tests",
        action="store_true",
        help="Only for local CPU tests of argument/path plumbing; never use for GPU preflight.",
    )
    args = parser.parse_args()

    protocol_root = Path(args.protocol_root).resolve()
    fresh_source_data = protocol_path_or_arg(
        protocol_root,
        "source_dataset",
        args.fresh_source_data,
    )
    git_lock = None
    if not args.skip_git_assertions_for_dry_run_tests:
        git_lock = assert_git_execution_lock(args.accepted_protocol_commit)
    summary = run_preflight(
        protocol_root=protocol_root,
        fresh_source_data=fresh_source_data,
        profile_dir=Path(args.profile_dir).resolve(),
        model_name_or_path=args.model_name_or_path,
        output_dir=Path(args.output_dir).resolve(),
        git_lock=git_lock,
        dependency_lock_path=Path(args.dependency_lock).resolve(),
        expected_python_major_minor=args.expected_python_major_minor,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
