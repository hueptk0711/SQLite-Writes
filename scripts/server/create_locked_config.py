from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from nldbwrite_v3.common import (
    dump_json,
    load_json,
    read_ids,
    sha256_file,
)
from nldbwrite_v3.experiments.run_lock import build_run_lock
from nldbwrite_v3.experiments.run_method import (
    V2_BUILDER_METHODS,
    _load_method_config,
    _load_profiles,
    _prompt_for_sample,
)
from nldbwrite_v3.inference import verify_local_model


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _model_metadata(inference: dict[str, Any]) -> dict[str, Any]:
    backend = str(inference.get("backend") or "hf").casefold()
    if backend not in {"hf", "huggingface"}:
        return {"backend": backend}
    model_name = str(inference.get("model_name_or_path") or "")
    if not model_name:
        raise ValueError("model_name_or_path is required")
    revision = str(inference.get("revision") or "")
    configured_hash = str(inference.get("model_hash") or "")
    local = Path(model_name).is_dir()
    if local:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", configured_hash):
            raise ValueError(
                "A 64-character model_hash is required for a local model"
            )
        manifest = verify_local_model(model_name, configured_hash)
        model_identity = configured_hash
    else:
        if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
            raise ValueError(
                "Remote models require an immutable 40-character commit"
            )
        manifest = {"aggregate_sha256": revision}
        model_identity = revision
    return {
        "backend": "hf",
        "model_name_or_path": model_name,
        "revision": revision or None,
        "model_hash": model_identity,
        "model_manifest": manifest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create the exact configuration authorization used by the "
            "locked-test stage."
        )
    )
    parser.add_argument("--method-config", required=True)
    parser.add_argument("--inference-config", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--ids", required=True)
    parser.add_argument("--gold-plans", required=True)
    parser.add_argument(
        "--profile-dir",
        default=os.environ.get("NLDB_PROFILE_DIR"),
    )
    parser.add_argument(
        "--db-root",
        default=os.environ.get("NLDB_DATABASE_ROOT"),
    )
    parser.add_argument(
        "--dependency-lock",
        default="requirements-inference.lock.txt",
    )
    parser.add_argument(
        "--environment-manifest",
        default=os.environ.get("NLDB_ENVIRONMENT_MANIFEST"),
    )
    parser.add_argument(
        "--v2-source",
        default=os.environ.get("NLDB_V2_SOURCE"),
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.profile_dir or not args.db_root:
        raise ValueError(
            "Set NLDB_PROFILE_DIR and NLDB_DATABASE_ROOT or pass both paths"
        )
    if not args.environment_manifest:
        raise ValueError(
            "Set NLDB_ENVIRONMENT_MANIFEST or pass --environment-manifest"
        )
    environment = load_json(args.environment_manifest)
    if environment.get("status") != "gpu_ready":
        raise ValueError("Environment manifest is not GPU-ready")
    dependency_sha256 = sha256_file(args.dependency_lock)
    recorded_dependency = (
        environment.get("dependency_lock") or {}
    ).get("sha256")
    if recorded_dependency != dependency_sha256:
        raise ValueError(
            "Environment manifest uses a different dependency lock"
        )

    project_root = Path(__file__).resolve().parents[2]
    method, base_path = _load_method_config(args.method_config)
    inference = load_json(args.inference_config)
    resolved = {**method, "inference": inference}
    method_id = str(resolved.get("method_id") or "")
    if method_id in V2_BUILDER_METHODS and not args.v2_source:
        raise ValueError("S-FS-v2 requires NLDB_V2_SOURCE or --v2-source")

    samples = {
        str(sample["id"]): sample for sample in load_json(args.data)
    }
    selected_ids = read_ids(args.ids)
    missing = [item for item in selected_ids if item not in samples]
    if missing:
        raise ValueError(
            f"Split references {len(missing)} samples absent from data"
        )
    profiles = _load_profiles(args.profile_dir)
    prompt_hashes = []
    for sample_id in selected_ids:
        sample = samples[sample_id]
        profile = profiles[str(sample["db_id"])]
        prompt, _ = _prompt_for_sample(
            method_id,
            sample,
            profile,
            resolved,
        )
        prompt_hashes.append(
            hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        )
    prompt_set_sha256 = hashlib.sha256(
        "".join(prompt_hashes).encode("utf-8")
    ).hexdigest()

    prepared_run_lock = build_run_lock(
        project_root=project_root,
        stage="locked-test",
        method_id=method_id,
        method_config_path=args.method_config,
        inference_config_path=args.inference_config,
        base_config_path=base_path,
        resolved_config_sha256=_canonical_sha256(resolved),
        dataset_path=args.data,
        split_path=args.ids,
        gold_plans_path=args.gold_plans,
        profile_dir=args.profile_dir,
        db_root=args.db_root,
        selected_db_ids={
            str(samples[sample_id]["db_id"]) for sample_id in selected_ids
        },
        prompt_set_sha256=prompt_set_sha256,
        model_metadata=_model_metadata(inference),
        dependency_lock_path=args.dependency_lock,
        environment_manifest_path=args.environment_manifest,
        v2_source_path=(
            args.v2_source if method_id in V2_BUILDER_METHODS else None
        ),
    )
    locked = {
        "lock_version": 2,
        "status": "locked_after_dev_go_decision_pending",
        "method_id": method_id,
        "hashes": {
            "method_config_sha256": sha256_file(args.method_config),
            "base_config_sha256": (
                sha256_file(base_path) if base_path is not None else None
            ),
            "inference_config_sha256": sha256_file(
                args.inference_config
            ),
            "resolved_config_sha256": _canonical_sha256(resolved),
            "dataset_sha256": sha256_file(args.data),
            "split_sha256": sha256_file(args.ids),
        },
        "authorized_run_hashes": prepared_run_lock["hashes"],
        "authorized_model": {
            key: value
            for key, value in prepared_run_lock["model"].items()
            if value is not None
        },
        "generation": {
            key: inference.get(key)
            for key in (
                "batch_size",
                "max_input_tokens",
                "max_new_tokens",
                "input_truncation_policy",
                "do_sample",
                "temperature",
                "top_p",
                "seed",
            )
        },
    }
    dump_json(locked, args.output)
    result = {
        "locked_config": str(Path(args.output).resolve()),
        "locked_config_sha256": sha256_file(args.output),
        "method_id": locked["method_id"],
        "authorized_run_hash_count": len(locked["authorized_run_hashes"]),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
