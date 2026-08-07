from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from nldbwrite_v3.evaluator import evaluate_oracle_sample, find_database
from nldbwrite_v3.schema import load_profile

from .audit import audit_gold_dataset
from .authoring import (
    audit_authoring_assets,
    audit_calibration_authoring_completion,
    read_noncomment_ids,
)
from .calibration_semantics import audit_calibration_semantics


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(value: Any, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load_samples(path: str | Path) -> list[dict[str, Any]]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("Calibration dataset must be a JSON array.")
    return [row for row in value if isinstance(row, dict)]


def evaluate_calibration_freeze_readiness(
    *,
    kit_dir: str | Path,
    data_path: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    root = Path(kit_dir)
    samples = _load_samples(data_path)
    asset_issues, asset_summary = audit_authoring_assets(root)
    authoring_issues, authoring_summary = (
        audit_calibration_authoring_completion(
            samples,
            calibration_database_ids=read_noncomment_ids(
                root / "calibration_database_ids.txt"
            ),
            reserved_final_database_ids=read_noncomment_ids(
                root / "reserved_final_database_ids.txt"
            ),
            frozen_allocation_manifest=(
                root / "frozen_allocation_manifest.json"
            ),
            review_ledger_path=root / "review_ledger.csv",
        )
    )
    semantic_issues, semantic_summary, plans = audit_calibration_semantics(
        samples,
        kit_dir=root,
    )
    issues = [*asset_issues, *authoring_issues, *semantic_issues]
    summary = {
        **asset_summary,
        **authoring_summary,
        **semantic_summary,
        "total_blocking_issue_count": len(issues),
        "status": "ready_for_freeze" if not issues else "draft_or_invalid",
        "paper_result_eligible": False,
        "gpu_run_authorized": False,
    }
    return issues, summary, plans


def freeze_calibration_authoring(
    *,
    kit_dir: str | Path,
    data_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    root = Path(kit_dir)
    output = Path(output_dir)
    issues, summary, plans = evaluate_calibration_freeze_readiness(
        kit_dir=root,
        data_path=data_path,
    )
    if issues:
        raise ValueError(
            f"Calibration freeze rejected: {len(issues)} blocking issues remain."
        )
    samples = _load_samples(data_path)
    targets = {
        "dataset": output / "dataset.json",
        "ids": output / "calibration_ids.txt",
        "plans": output / "gold_write_plans.jsonl",
        "reserved_final": output / "reserved_final_database_ids.txt",
        "review_ledger": output / "review_ledger.csv",
        "manifest": output / "calibration_freeze_manifest.json",
        "dataset_sha256": output / "dataset.sha256",
    }
    existing = [str(path) for path in targets.values() if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to overwrite frozen calibration artifacts: "
            f"{existing}"
        )
    output.mkdir(parents=True, exist_ok=True)
    _write_json(samples, targets["dataset"])
    targets["ids"].write_text(
        "\n".join(str(sample["id"]) for sample in samples) + "\n",
        encoding="utf-8",
    )
    plans_by_id = {str(plan.get("sample_id") or ""): plan for plan in plans}
    ordered_plans = [plans_by_id[str(sample["id"])] for sample in samples]
    targets["plans"].write_text(
        "".join(
            json.dumps(plan, ensure_ascii=False, sort_keys=True) + "\n"
            for plan in ordered_plans
        ),
        encoding="utf-8",
    )
    shutil.copy2(
        root / "reserved_final_database_ids.txt",
        targets["reserved_final"],
    )
    shutil.copy2(root / "review_ledger.csv", targets["review_ledger"])
    targets["dataset_sha256"].write_text(
        f"{_sha256_file(targets['dataset'])}  dataset.json\n",
        encoding="utf-8",
    )
    asset_manifest = json.loads(
        (root / "source_asset_manifest.json").read_text(encoding="utf-8")
    )
    manifest = {
        "version": "2.0",
        "status": "frozen_ready_for_metadata_and_gold_mp",
        "sample_count": len(samples),
        "authoring_summary": summary,
        "source_asset_manifest_sha256": _sha256_file(
            root / "source_asset_manifest.json"
        ),
        "frozen_allocation_manifest_sha256": _sha256_file(
            root / "frozen_allocation_manifest.json"
        ),
        "hashes": {
            "dataset_sha256": _sha256_file(targets["dataset"]),
            "calibration_ids_sha256": _sha256_file(targets["ids"]),
            "gold_write_plans_sha256": _sha256_file(targets["plans"]),
            "reserved_final_database_ids_sha256": _sha256_file(
                targets["reserved_final"]
            ),
            "review_ledger_sha256": _sha256_file(targets["review_ledger"]),
            "database_sha256": asset_manifest.get("database_sha256") or {},
            "profile_sha256": asset_manifest.get("profile_sha256") or {},
        },
        "paper_result_eligible": False,
        "gpu_run_authorized": False,
    }
    _write_json(manifest, targets["manifest"])
    return manifest


def audit_calibration_gold_mp(
    *,
    dataset_path: str | Path,
    profile_dir: str | Path,
    db_root: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    samples = _load_samples(dataset_path)
    plans, issues, base_metrics = audit_gold_dataset(
        dataset_path,
        profile_dir,
        db_root=db_root,
    )
    plans_by_id = {str(plan.get("sample_id") or ""): plan for plan in plans}
    profiles = {
        path.stem: load_profile(path)
        for path in Path(profile_dir).glob("*.json")
    }
    results: list[dict[str, Any]] = []
    for sample in samples:
        sample_id = str(sample.get("id") or "")
        db_id = str(sample.get("db_id") or "")
        plan = plans_by_id.get(sample_id)
        profile = profiles.get(db_id)
        if plan is None or profile is None:
            continue
        result = evaluate_oracle_sample(
            sample,
            plan,
            profile,
            find_database(db_root, db_id),
        )
        results.append(result)
        if not result.get("plan_valid"):
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "GOLD_MP_PLAN_INVALID",
                    "message": "Gold-MP plan validation failed.",
                }
            )
        if not result.get("build_success"):
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "GOLD_MP_BUILD_FAILED",
                    "message": "Gold-MP compiler build failed.",
                }
            )
        if not result.get("execution_success"):
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "GOLD_MP_EXECUTION_FAILED",
                    "message": "Gold-MP execution failed.",
                }
            )
        if not result.get("target_state_correct"):
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "GOLD_MP_TARGET_STATE_INCORRECT",
                    "message": "Gold-MP target state differs from gold SQL.",
                }
            )
        if not result.get("strict_full_state_correct"):
            issues.append(
                {
                    "sample_id": sample_id,
                    "error_code": "GOLD_MP_STRICT_STATE_INCORRECT",
                    "message": "Gold-MP strict state differs from gold SQL.",
                }
            )
    counts = Counter(
        {
            "plan_valid": sum(row.get("plan_valid") is True for row in results),
            "build_success": sum(
                row.get("build_success") is True for row in results
            ),
            "execution_success": sum(
                row.get("execution_success") is True for row in results
            ),
            "target_state_correct": sum(
                row.get("target_state_correct") is True for row in results
            ),
            "strict_full_state_correct": sum(
                row.get("strict_full_state_correct") is True
                for row in results
            ),
            "side_effect": sum(
                row.get("target_state_correct") is True
                and row.get("strict_full_state_correct") is not True
                for row in results
            ),
        }
    )
    required = len(samples) == 60 and len(results) == 60
    passed = (
        required
        and not issues
        and all(
            counts[key] == 60
            for key in (
                "plan_valid",
                "build_success",
                "execution_success",
                "target_state_correct",
                "strict_full_state_correct",
            )
        )
        and counts["side_effect"] == 0
    )
    summary = {
        "samples": len(samples),
        "parsed_plans": len(plans),
        "evaluated_samples": len(results),
        **dict(counts),
        "base_audit_metrics": base_metrics,
        "blocking_issue_count": len(issues),
        "gold_mp_accuracy": (
            counts["strict_full_state_correct"] / len(samples)
            if samples
            else 0.0
        ),
        "status": (
            "gold_mp_pass_gpu_calibration_authorized"
            if passed
            else "gold_mp_failed_gpu_blocked"
        ),
        "gpu_run_authorized": passed,
        "paper_result_eligible": False,
    }
    return issues, summary
