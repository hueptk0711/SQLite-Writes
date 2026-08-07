from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


BASE_PROTOCOL_SHA256 = (
    "e6bb763334f0b7dcec77523794a20687087fe6f0f62f572cdd3e999b7b48a330"
)
METHODS = {
    "D-FS-M": "configs/final/d_fs_m.json",
    "J-FS-M": "configs/final/j_fs_m.json",
    "MP-FS+": "configs/final/mp_fs_plus.json",
}


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--model-path", required=True)
    args = parser.parse_args()

    project = Path(args.project_root).resolve()
    model_path = Path(args.model_path).resolve()
    sys.path.insert(0, str(project / "src"))
    from nldbwrite_v3.common import dump_json, load_json, sha256_file
    from nldbwrite_v3.experiments.run_method import _load_method_config
    from nldbwrite_v3.inference import build_local_model_manifest

    base_protocol_path = project / "configs/experiments/final_protocol.json"
    if sha256_file(base_protocol_path) != BASE_PROTOCOL_SHA256:
        raise ValueError("Base final protocol SHA-256 does not match the frozen paper protocol")
    if not model_path.is_dir():
        raise ValueError(f"Model path is missing: {model_path}")

    base_protocol = load_json(base_protocol_path)
    data = project / "data/external_holdout/dataset.final.json"
    ids = project / "data/external_holdout/final_holdout_ids.txt"
    gold = project / "data/external_holdout/gold_plans.runtime.jsonl"
    actual_assets = {
        "dataset_sha256": sha256_file(data),
        "split_sha256": sha256_file(ids),
        "gold_plans_sha256": sha256_file(gold),
    }
    if actual_assets != base_protocol["authorized_hashes"]:
        raise ValueError("Runtime holdout assets differ from the frozen base protocol")

    artifact_dir = project / "artifacts/server"
    protocol_dir = project / "configs/experiments"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    protocol_dir.mkdir(parents=True, exist_ok=True)
    model_manifest = build_local_model_manifest(model_path)
    model_hash = str(model_manifest["aggregate_sha256"])
    manifest_path = artifact_dir / "second_model_qwen25_coder_14b_manifest.json"
    dump_json(model_manifest, manifest_path)

    inference = {
        "backend": "hf",
        "model_name_or_path": str(model_path),
        "batch_size": 1,
        "max_input_tokens": 28672,
        "max_new_tokens": 8192,
        "input_truncation_policy": "error",
        "quantization": "4bit",
        "compute_dtype": "float16",
        "device_map": "auto",
        "do_sample": False,
        "seed": 42,
        "trust_remote_code": False,
        "model_hash": model_hash,
    }
    inference_path = artifact_dir / "hf_second_model_qwen25_coder_14b_in28672_out8192.json"
    dump_json(inference, inference_path)

    authorized_runs = {}
    for method_id, relative_config in METHODS.items():
        config_path = project / relative_config
        method, _base = _load_method_config(config_path)
        resolved = {**method, "inference": inference}
        if str(resolved.get("method_id")) != method_id:
            raise ValueError(f"Method config mismatch for {method_id}")
        authorized_runs[method_id] = {
            "resolved_config_sha256": canonical_sha256(resolved),
            "inference_config_sha256": sha256_file(inference_path),
            "method_config": str(config_path),
            "inference_config": str(inference_path),
        }

    protocol = {
        "protocol_id": "mp_fs_plus_post_hoc_second_model_qwen25_coder_14b_v1",
        "status": "frozen",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "analysis_class": "post_hoc_cross_model_robustness",
        "paper_primary_result": False,
        "base_primary_protocol_sha256": BASE_PROTOCOL_SHA256,
        "holdout_status": "consumed_reused_for_labeled_post_hoc_analysis",
        "authorized_hashes": actual_assets,
        "authorized_runs": {"second-model": authorized_runs},
        "methods": list(METHODS),
        "model": {
            "name": "Qwen2.5-Coder-14B-Instruct",
            "path": str(model_path),
            "aggregate_sha256": model_hash,
            "manifest_sha256": sha256_file(manifest_path),
        },
        "generation_policy": inference,
        "no_tuning_after_freeze": True,
        "failure_policy": "retain_every_selected_sample_in_denominator_and_report_any_mechanical_limit",
    }
    protocol_path = protocol_dir / "second_model_qwen25_coder_14b_protocol_v1.json"
    dump_json(protocol, protocol_path)
    print(
        json.dumps(
            {
                "status": "frozen",
                "protocol": str(protocol_path),
                "protocol_sha256": sha256_file(protocol_path),
                "model_aggregate_sha256": model_hash,
                "inference_config": str(inference_path),
                "next": "Review the printed hashes, then run run_second_model_robustness.sh",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
