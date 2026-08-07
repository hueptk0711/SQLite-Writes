from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


EXPECTED_DATABASES = ("archeology", "polar", "robot", "vaccine", "virtual")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the frozen 300-sample release and create the deterministic "
            "JSONL gold-plan file consumed by run_method."
        )
    )
    parser.add_argument(
        "--data-root",
        default="data/external_holdout",
    )
    parser.add_argument(
        "--calibration-go",
        default="artifacts/calibration/calibration_go_decision.json",
    )
    parser.add_argument(
        "--runtime-gold-output",
        default="data/external_holdout/gold_plans.runtime.jsonl",
    )
    parser.add_argument(
        "--output",
        default="diagnostics/final_asset_preflight.json",
    )
    args = parser.parse_args()

    root = Path(args.data_root)
    manifest = load_json(root / "FINAL_RELEASE_MANIFEST.json")
    validation = load_json(root / "final_validation_report.json")
    calibration = load_json(Path(args.calibration_go))
    issues: list[str] = []

    if manifest.get("status") != "frozen":
        issues.append("release manifest is not frozen")
    if manifest.get("paper_result_eligible") is not True:
        issues.append("release manifest is not paper-result eligible")
    if validation.get("status") != "pass" or validation.get("issue_count") != 0:
        issues.append("final validation report is not PASS with zero issues")
    if calibration.get("status") != "go":
        issues.append("calibration decision is not GO")
    if calibration.get("final_protocol_freeze_authorized") is not True:
        issues.append("calibration does not authorize final protocol freeze")

    file_issues: list[dict[str, str]] = []
    manifest_files = manifest.get("files") or {}
    for relative, expected in sorted(manifest_files.items()):
        path = root / relative
        if not path.is_file():
            file_issues.append({"path": relative, "reason": "missing"})
            continue
        actual = sha256_file(path)
        if actual != expected.get("sha256"):
            file_issues.append(
                {
                    "path": relative,
                    "reason": "sha256_mismatch",
                    "expected": str(expected.get("sha256")),
                    "actual": actual,
                }
            )
    if file_issues:
        issues.append(f"{len(file_issues)} release files failed identity checks")

    dataset = load_json(root / "dataset.final.json")
    gold_plans = load_json(root / "gold_plans.final.json")
    ids = [
        line.strip()
        for line in (root / "final_holdout_ids.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    sample_ids = [str(row.get("id") or row.get("sample_id")) for row in dataset]
    gold_ids = [str(row.get("sample_id")) for row in gold_plans]

    if len(dataset) != 300 or len(set(sample_ids)) != 300:
        issues.append("dataset does not contain 300 unique samples")
    if len(ids) != 300 or len(set(ids)) != 300:
        issues.append("split does not contain 300 unique sample IDs")
    if len(gold_plans) != 300 or len(set(gold_ids)) != 300:
        issues.append("gold plan source does not contain 300 unique plans")
    if set(ids) != set(sample_ids) or set(ids) != set(gold_ids):
        issues.append("dataset, split, and gold-plan sample IDs differ")

    database_counts = Counter(str(row.get("db_id")) for row in dataset)
    operation_counts = Counter(
        str(row.get("operation_semantics")) for row in dataset
    )
    input_format_counts = Counter(str(row.get("input_format")) for row in dataset)
    complexity_counts = Counter(str(row.get("complexity")) for row in dataset)
    if tuple(sorted(database_counts)) != EXPECTED_DATABASES:
        issues.append("database allocation differs from the five frozen databases")
    if any(database_counts.get(db_id) != 60 for db_id in EXPECTED_DATABASES):
        issues.append("each frozen database must contain exactly 60 samples")

    database_checks: dict[str, str] = {}
    for db_id in EXPECTED_DATABASES:
        database_path = root / "databases" / db_id / f"{db_id}.sqlite"
        profile_path = root / "profiles" / f"{db_id}.json"
        if not database_path.is_file() or not profile_path.is_file():
            issues.append(f"missing database or profile for {db_id}")
            continue
        with sqlite3.connect(
            f"file:{database_path.resolve()}?mode=ro",
            uri=True,
        ) as connection:
            result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        database_checks[db_id] = result
        if result != "ok":
            issues.append(f"SQLite quick_check failed for {db_id}: {result}")
        profile = load_json(profile_path)
        if not isinstance(profile, dict):
            issues.append(f"profile is not a JSON object for {db_id}")

    ledger_rows = 0
    with (root / "review_ledger.csv").open(
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        ledger_rows = sum(1 for _ in csv.DictReader(handle))
    if ledger_rows != 674:
        issues.append(f"review ledger has {ledger_rows} rows instead of 674")

    runtime_gold = Path(args.runtime_gold_output)
    runtime_gold.parent.mkdir(parents=True, exist_ok=True)
    runtime_text = "".join(
        json.dumps(
            plan,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for plan in gold_plans
    )
    runtime_gold.write_text(runtime_text, encoding="utf-8", newline="\n")

    report = {
        "status": "pass" if not issues else "fail",
        "paper_result_eligible": not issues,
        "release_id": manifest.get("release_id"),
        "sample_count": len(dataset),
        "split_count": len(ids),
        "gold_plan_count": len(gold_plans),
        "review_ledger_rows": ledger_rows,
        "database_counts": dict(sorted(database_counts.items())),
        "operation_counts": dict(sorted(operation_counts.items())),
        "input_format_counts": dict(sorted(input_format_counts.items())),
        "complexity_counts": dict(sorted(complexity_counts.items())),
        "database_quick_checks": database_checks,
        "dataset_sha256": sha256_file(root / "dataset.final.json"),
        "split_sha256": sha256_file(root / "final_holdout_ids.txt"),
        "source_gold_plans_sha256": sha256_file(root / "gold_plans.final.json"),
        "runtime_gold_plans_sha256": sha256_file(runtime_gold),
        "runtime_gold_plans_path": str(runtime_gold.resolve()),
        "release_file_issue_count": len(file_issues),
        "release_file_issues": file_issues,
        "issues": issues,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
