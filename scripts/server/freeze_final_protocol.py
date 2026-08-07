from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nldbwrite_v3.common import dump_json, load_json, sha256_file
from nldbwrite_v3.experiments.run_method import _load_method_config


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _parse_run(value: str) -> tuple[str, str, Path, Path | None]:
    parts = value.split("|")
    if len(parts) != 4:
        raise ValueError(
            "--run must be STAGE|METHOD|METHOD_CONFIG|INFERENCE_CONFIG_OR_DASH"
        )
    stage, method, config_path, inference_path = parts
    return (
        stage,
        method,
        Path(config_path),
        None if inference_path == "-" else Path(inference_path),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve and freeze the final external-holdout protocol."
    )
    parser.add_argument("--template", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--ids", required=True)
    parser.add_argument("--gold-plans", required=True)
    parser.add_argument(
        "--run",
        action="append",
        default=[],
        help=(
            "Repeat STAGE|METHOD|METHOD_CONFIG|INFERENCE_CONFIG_OR_DASH. "
            "All primary external-holdout methods are required."
        ),
    )
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help=(
            "If the output already exists, verify its frozen inputs and "
            "authorized runs instead of creating a new protocol."
        ),
    )
    args = parser.parse_args()

    protocol = load_json(args.template)
    protocol["authorized_hashes"] = {
        "dataset_sha256": sha256_file(args.data),
        "split_sha256": sha256_file(args.ids),
        "gold_plans_sha256": sha256_file(args.gold_plans),
    }
    authorized_runs: dict[str, dict[str, dict[str, Any]]] = {}
    for raw_run in args.run:
        stage, expected_method, config_path, inference_path = _parse_run(
            raw_run
        )
        config, _ = _load_method_config(config_path)
        actual_method = str(config.get("method_id") or "")
        if actual_method != expected_method:
            raise ValueError(
                f"Run declares {expected_method!r}, config is {actual_method!r}."
            )
        if inference_path is not None:
            config = {
                **config,
                "inference": load_json(inference_path),
            }
        authorized_runs.setdefault(stage, {})[expected_method] = {
            "resolved_config_sha256": _canonical_sha256(config),
            "inference_config_sha256": (
                sha256_file(inference_path)
                if inference_path is not None
                else None
            ),
            "method_config": str(config_path.resolve()),
            "inference_config": (
                str(inference_path.resolve())
                if inference_path is not None
                else None
            ),
        }

    required_primary = set(protocol.get("methods") or [])
    supplied_primary = set(authorized_runs.get("external-holdout") or {})
    missing_primary = sorted(required_primary - supplied_primary)
    if missing_primary:
        raise ValueError(
            "Cannot freeze final protocol; missing primary runs: "
            + ", ".join(missing_primary)
        )
    protocol["authorized_runs"] = authorized_runs
    protocol["status"] = "frozen"
    protocol["frozen_at_utc"] = datetime.now(timezone.utc).isoformat()
    protocol["frozen_inputs"] = {
        "dataset": str(Path(args.data).resolve()),
        "split": str(Path(args.ids).resolve()),
        "gold_plans": str(Path(args.gold_plans).resolve()),
    }
    output = Path(args.output)
    if output.exists() and args.verify_existing:
        existing = load_json(output)
        stable_keys = (
            "protocol_id",
            "status",
            "authorized_hashes",
            "authorized_runs",
            "primary_holdout",
            "methods",
            "pre_registered_comparisons",
            "primary_metrics",
            "statistics",
            "freeze_contract",
            "diagnostic_677_policy",
            "frozen_inputs",
        )
        mismatched = [
            key
            for key in stable_keys
            if existing.get(key) != protocol.get(key)
        ]
        if mismatched:
            raise ValueError(
                "Existing final protocol differs from current frozen inputs: "
                + ", ".join(mismatched)
            )
        status = "verified_existing"
    else:
        dump_json(protocol, output)
        status = "frozen"
    print(
        json.dumps(
            {
                "status": status,
                "output": str(output.resolve()),
                "protocol_sha256": sha256_file(output),
                "authorized_primary_methods": sorted(supplied_primary),
                "authorized_second_model_methods": sorted(
                    authorized_runs.get("second-model") or {}
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
