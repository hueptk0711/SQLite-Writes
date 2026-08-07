from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def read_ids(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--protocol",
        default="configs/experiments/calibration_protocol.json",
    )
    parser.add_argument("--model-manifest")
    parser.add_argument("--inference-config")
    parser.add_argument("--environment-manifest")
    parser.add_argument("--require-gpu", action="store_true")
    parser.add_argument(
        "--output",
        default="diagnostics/calibration_bundle_validation.json",
    )
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    protocol_path = root / args.protocol
    protocol = load_json(protocol_path)
    issues: list[dict[str, Any]] = []

    def issue(code: str, detail: Any) -> None:
        issues.append({"code": code, "detail": detail})

    if protocol.get("status") != "locked_ready_for_gpu_calibration":
        issue("PROTOCOL_NOT_LOCKED", protocol.get("status"))

    checked_hashes: dict[str, dict[str, Any]] = {}
    for relative, expected in protocol.get("locked_files", {}).items():
        path = root / relative
        if not path.is_file():
            issue("MISSING_LOCKED_FILE", relative)
            continue
        actual = sha256_file(path)
        checked_hashes[relative] = {
            "expected": expected,
            "actual": actual,
            "match": actual == expected,
        }
        if actual != expected:
            issue(
                "LOCKED_FILE_HASH_MISMATCH",
                {"path": relative, "expected": expected, "actual": actual},
            )

    dataset_path = root / "data/calibration/dataset.json"
    ids_path = root / "data/calibration/calibration_ids.txt"
    smoke_ids_path = root / protocol["smoke"]["ids_file"]
    dataset = load_json(dataset_path)
    ids = read_ids(ids_path)
    smoke_ids = read_ids(smoke_ids_path)
    dataset_ids = [str(row.get("sample_id") or row.get("id")) for row in dataset]

    if len(dataset) != protocol["sample_count"]:
        issue("DATASET_COUNT", len(dataset))
    if len(ids) != protocol["sample_count"] or len(set(ids)) != len(ids):
        issue("ID_COUNT_OR_UNIQUENESS", {"count": len(ids), "unique": len(set(ids))})
    if dataset_ids != ids:
        issue("DATASET_ID_ORDER_MISMATCH", None)
    if (
        len(smoke_ids) != protocol["smoke"]["sample_count"]
        or len(set(smoke_ids)) != len(smoke_ids)
        or not set(smoke_ids) <= set(ids)
    ):
        issue(
            "SMOKE_ID_SET_INVALID",
            {"count": len(smoke_ids), "unique": len(set(smoke_ids))},
        )

    database_counts = Counter(str(row.get("db_id")) for row in dataset)
    expected_databases = set(
        protocol["database_policy"]["calibration_database_ids"]
    )
    if set(database_counts) != expected_databases:
        issue("DATABASE_SET", dict(database_counts))
    for db_id in expected_databases:
        if database_counts[db_id] != protocol["database_policy"]["samples_per_database"]:
            issue("DATABASE_SAMPLE_COUNT", {db_id: database_counts[db_id]})
    if expected_databases & set(
        protocol["database_policy"]["reserved_final_database_ids"]
    ):
        issue("FINAL_HOLDOUT_OVERLAP", sorted(expected_databases))

    metadata = load_json(root / "artifacts/audit/calibration_metadata.json")
    if metadata.get("issues") or metadata.get("summary", {}).get("status") != "valid":
        issue("METADATA_AUDIT_NOT_VALID", metadata.get("summary"))
    gold = load_json(root / "artifacts/audit/calibration_gold_mp.json")
    gold_summary = gold.get("summary", {})
    if (
        gold.get("issues")
        or gold_summary.get("gpu_run_authorized") is not True
        or gold_summary.get("gold_mp_accuracy") != 1.0
        or gold_summary.get("side_effect") != 0
    ):
        issue("GOLD_MP_GATE_NOT_PASSED", gold_summary)

    model_summary = None
    if args.model_manifest:
        model_path = Path(args.model_manifest)
        if not model_path.is_absolute():
            model_path = root / model_path
        model = load_json(model_path)
        model_summary = {
            "path": str(model_path),
            "aggregate_sha256": model.get("aggregate_sha256"),
            "tokenizer_sha256": model.get("tokenizer_sha256"),
            "model_config_sha256": model.get("model_config_sha256"),
        }
        for key in ("aggregate_sha256",):
            expected = protocol["model_lock"][key]
            actual = model.get(key)
            if actual != expected:
                issue(
                    "MODEL_IDENTITY_MISMATCH",
                    {"field": key, "expected": expected, "actual": actual},
                )
        # The lightweight preflight manifest hashes every local model file but
        # does not instantiate Transformers.  Runtime-derived tokenizer/model
        # identities therefore become available only after HuggingFaceGenerator
        # loads the model.  When present, validate them here; smoke/full gates
        # require them in every actual run's model_manifest.json.
        for key in ("tokenizer_sha256", "model_config_sha256"):
            expected = protocol["model_lock"][key]
            actual = model.get(key)
            if actual is not None and actual != expected:
                issue(
                    "MODEL_IDENTITY_MISMATCH",
                    {"field": key, "expected": expected, "actual": actual},
                )

    inference_summary = None
    if args.inference_config:
        inference_path = Path(args.inference_config)
        if not inference_path.is_absolute():
            inference_path = root / inference_path
        inference = load_json(inference_path)
        inference_summary = {"path": str(inference_path)}
        for key, expected in protocol["inference_lock"].items():
            actual = inference.get(key)
            inference_summary[key] = actual
            if actual != expected:
                issue(
                    "INFERENCE_LOCK_MISMATCH",
                    {"field": key, "expected": expected, "actual": actual},
                )
        if inference.get("model_hash") != protocol["model_lock"]["aggregate_sha256"]:
            issue("INFERENCE_MODEL_HASH_MISMATCH", inference.get("model_hash"))

    environment_summary = None
    if args.environment_manifest:
        environment_path = Path(args.environment_manifest)
        if not environment_path.is_absolute():
            environment_path = root / environment_path
        environment = load_json(environment_path)
        environment_summary = {
            "path": str(environment_path),
            "status": environment.get("status"),
            "cuda_available": environment.get("torch", {}).get("cuda_available"),
            "gpus": environment.get("torch", {}).get("gpus"),
            "dependency_lock_sha256": environment.get("dependency_lock", {}).get(
                "sha256"
            ),
        }
        if args.require_gpu and environment.get("status") != "gpu_ready":
            issue("GPU_ENVIRONMENT_NOT_READY", environment_summary)
        expected_lock = protocol["locked_files"]["requirements-inference.lock.txt"]
        if environment_summary["dependency_lock_sha256"] != expected_lock:
            issue(
                "ENVIRONMENT_DEPENDENCY_LOCK_MISMATCH",
                environment_summary["dependency_lock_sha256"],
            )
    elif args.require_gpu:
        issue("MISSING_ENVIRONMENT_MANIFEST", None)

    report = {
        "validator_version": 1,
        "protocol_id": protocol.get("protocol_id"),
        "status": "pass" if not issues else "fail",
        "gpu_run_authorized": not issues,
        "paper_result_eligible": False,
        "sample_count": len(dataset),
        "database_counts": dict(sorted(database_counts.items())),
        "smoke_ids": smoke_ids,
        "locked_files_checked": len(checked_hashes),
        "locked_file_hashes": checked_hashes,
        "model": model_summary,
        "inference": inference_summary,
        "environment": environment_summary,
        "issues": issues,
    }
    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
