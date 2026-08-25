#!/usr/bin/env python3
"""CPU-only Stage 6B registration for the CRUDSQL confirmation dataset."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE6A_DIR = PROJECT_ROOT / "stage6_crudsql_eligibility_audit"
STAGE6B_DIR = PROJECT_ROOT / "stage6_crudsql_registration"
CRUDSQL_COMMIT = "63bfce67d8391185453a812751e115a499201363"
STAGE6A_ACCEPTED_COMMIT = "6aba9a40de238f272ae7d4a907f797ef65bf875a"
STAGE5_PROTOCOL_COMMIT = "a7742b4c9150ab208e7c5d6708f0dff40bf05440"
STAGE5_METHOD_COMMIT = "79f6a82144ec0407444ef37121f70eed2b20e01c"
ARCHIVE_NAME = "stage6b_crudsql_confirmation_dataset_20260824.zip"
ZIP_TIMESTAMP = (2026, 8, 24, 0, 0, 0)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def registry_self_hash(registry: dict[str, Any]) -> str:
    return sha256_text(
        canonical_json(
            {
                key: value
                for key, value in registry.items()
                if key != "registry_sha256_excluding_self"
            }
        )
    )


def file_manifest(paths: list[Path], root: Path) -> list[dict[str, str]]:
    manifest = []
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        manifest.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_file(path),
            }
        )
    return manifest


def validate_stage6a_inputs(stage6a_dir: Path) -> dict[str, Any]:
    artifacts = stage6a_dir / "artifacts"
    candidates = read_jsonl(artifacts / "crudsql_stage6_candidate_samples.jsonl")
    gold_rows = read_jsonl(artifacts / "crudsql_gold_adapter_audit.jsonl")
    post_hashes = read_jsonl(artifacts / "gold_post_state_hashes.jsonl")
    table_manifest = read_json(artifacts / "isolated_table_db_manifest.json")
    overlap = read_json(artifacts / "crudsql_overlap_audit.json")
    registry = read_json(artifacts / "stage6_seen_reference_registry.json")
    sensitivity = read_json(artifacts / "mcnemar_threshold_sensitivity.json")

    violations: list[str] = []
    if len(candidates) != 500:
        violations.append("candidate_count_not_500")
    if len(gold_rows) != 500:
        violations.append("gold_adapter_count_not_500")
    if len(post_hashes) != 500:
        violations.append("post_state_hash_count_not_500")
    if len(table_manifest) != 125:
        violations.append("isolated_table_count_not_125")

    for field in ["stage6_sample_id", "upstream_sample_locator"]:
        values = [row[field] for row in candidates]
        if len(values) != len(set(values)):
            violations.append(f"duplicate_{field}")

    candidate_ids = {row["stage6_sample_id"] for row in candidates}
    gold_ids = {row["stage6_sample_id"] for row in gold_rows}
    post_ids = {row["stage6_sample_id"] for row in post_hashes}
    if candidate_ids != gold_ids or candidate_ids != post_ids:
        violations.append("candidate_gold_post_id_mismatch")
    if any(row.get("adapter_status") != "PASS" for row in gold_rows):
        violations.append("gold_adapter_not_all_pass")

    table_ids = {row["table_id"] for row in table_manifest}
    if len(table_ids) != 125:
        violations.append("isolated_table_ids_not_unique_125")
    db_hashes = [row["isolated_db_sha256"] for row in table_manifest]
    if len(db_hashes) != len(set(db_hashes)):
        violations.append("isolated_db_sha256_not_unique")
    for row in table_manifest:
        db_path = stage6a_dir / row["isolated_db_path"]
        if not db_path.is_file():
            violations.append(f"missing_isolated_db:{row['table_id']}")
        elif sha256_file(db_path) != row["isolated_db_sha256"]:
            violations.append(f"isolated_db_sha256_mismatch:{row['table_id']}")

    overlap_counts = {key: value for key, value in overlap.items() if key.endswith("_overlap_count")}
    nonzero_overlaps = {key: value for key, value in overlap_counts.items() if int(value) != 0}
    if overlap.get("status") != "PASS":
        violations.append("stage6a_overlap_status_not_pass")
    if nonzero_overlaps:
        violations.append("stage6a_overlap_nonzero:" + ",".join(sorted(nonzero_overlaps)))

    declared_registry_hash = registry.get("registry_sha256_excluding_self")
    if declared_registry_hash != registry_self_hash(registry):
        violations.append("stage6a_registry_self_hash_mismatch")
    if int((registry.get("digest_counts") or {}).get("input_text_sha256") or 0) <= 0:
        violations.append("stage6a_registry_input_text_sha256_empty")

    sensitivity_ns = [row["n"] for row in sensitivity.get("rows", [])]
    if sensitivity_ns != sorted(set(sensitivity_ns)):
        violations.append("mcnemar_threshold_sensitivity_n_not_unique_sorted")

    return {
        "violations": violations,
        "candidates": candidates,
        "gold_rows": gold_rows,
        "post_hashes": post_hashes,
        "table_manifest": table_manifest,
        "overlap": overlap,
        "registry": registry,
        "sensitivity": sensitivity,
    }


def build_registered_samples(candidates: list[dict[str, Any]], gold_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gold_by_id = {row["stage6_sample_id"]: row for row in gold_rows}
    rows = []
    for candidate in candidates:
        gold = gold_by_id[candidate["stage6_sample_id"]]
        rows.append(
            {
                "stage6_sample_id": candidate["stage6_sample_id"],
                "upstream_sample_locator": candidate["upstream_sample_locator"],
                "official_split": "test",
                "official_split_index": candidate["official_split_index"],
                "source_commit": CRUDSQL_COMMIT,
                "table_id": candidate["table_id"],
                "question_sha256": candidate["question_sha256"],
                "canonical_content_sha256": candidate["canonical_content_sha256"],
                "schema_sha256": candidate["schema_sha256"],
                "initial_state_sha256": candidate["initial_state_sha256"],
                "post_state_sha256": candidate["post_state_sha256"],
                "isolated_db": f"isolated_table_dbs/crudsql_db_{candidate['table_id']}.sqlite",
                "question": gold["question"],
            }
        )
    return rows


def build_gold_artifacts(gold_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    plans = []
    programs = []
    for row in gold_rows:
        base = {
            "stage6_sample_id": row["stage6_sample_id"],
            "upstream_sample_locator": row["upstream_sample_locator"],
            "table_id": row["table_id"],
            "official_split_index": row["official_split_index"],
            "isolated_db": f"isolated_table_dbs/crudsql_db_{row['table_id']}.sqlite",
            "schema_sha256": row["schema_sha256"],
            "initial_state_sha256": row["initial_state_sha256"],
            "post_state_sha256": row["post_state_sha256"],
        }
        plans.append(
            base
            | {
                "operation": "INSERT",
                "column_indexes": row["column_indexes"],
                "columns": row["columns"],
                "values": row["values"],
                "expected_inserted_row": row["expected_inserted_row"],
                "fresh_db_per_sample": True,
            }
        )
        programs.append(
            base
            | {
                "sqlite_parameter_style": "qmark",
                "sql_template": row["insert_sql_template"],
                "parameters": row["values"],
                "expected_inserted_row": row["expected_inserted_row"],
            }
        )
    return plans, programs


def build_distribution_report(samples: list[dict[str, Any]], gold_rows: list[dict[str, Any]], table_manifest: list[dict[str, Any]]) -> dict[str, Any]:
    table_by_id = {row["table_id"]: row for row in table_manifest}
    column_counts = [row["column_count"] for row in table_manifest]
    row_counts = [row["row_count"] for row in table_manifest]
    inserted_field_counts = [len(row["columns"]) for row in gold_rows]
    question_lengths = [len(row["question"]) for row in samples]
    samples_by_table = Counter(row["table_id"] for row in samples)
    type_counter: Counter[str] = Counter()
    null_unspecified = 0
    unspecified_total = 0
    for row in gold_rows:
        table = table_by_id[row["table_id"]]
        expected = row["expected_inserted_row"]
        inserted_indexes = set(row["column_indexes"])
        unspecified_total += len(expected) - len(inserted_indexes)
        null_unspecified += sum(
            value is None for index, value in enumerate(expected) if index not in inserted_indexes
        )
        type_counter.update(type(value).__name__ for value in row["values"])

    def summary(values: list[int]) -> dict[str, float | int]:
        return {
            "min": min(values),
            "max": max(values),
            "mean": statistics.mean(values),
            "median": statistics.median(values),
        }

    return {
        "status": "PASS",
        "sample_count": len(samples),
        "table_count": len(table_manifest),
        "samples_per_table": dict(sorted(Counter(samples_by_table.values()).items())),
        "columns_per_table": summary(column_counts),
        "initial_rows_per_table": summary(row_counts),
        "inserted_fields_per_sample": summary(inserted_field_counts),
        "question_length_characters": summary(question_lengths),
        "inserted_value_python_type_counts": dict(sorted(type_counter.items())),
        "unspecified_columns": {
            "total": unspecified_total,
            "null_after_insert": null_unspecified,
            "all_unspecified_columns_null": unspecified_total == null_unspecified,
        },
        "language": "Chinese questions retained from official CRUDSQL",
    }


def make_archive(archive_path: Path, members: list[Path], root: Path) -> dict[str, Any]:
    if archive_path.exists():
        archive_path.unlink()
    member_manifest = file_manifest(members, root)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member in sorted(members, key=lambda item: item.relative_to(root).as_posix()):
            rel = member.relative_to(root).as_posix()
            info = zipfile.ZipInfo(rel, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, member.read_bytes())
    return {
        "path": archive_path.relative_to(root).as_posix(),
        "sha256": sha256_file(archive_path),
        "member_count": len(members),
        "members": member_manifest,
    }


def register_stage6b(stage6a_dir: Path = STAGE6A_DIR, out_dir: Path = STAGE6B_DIR) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = out_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    inputs = validate_stage6a_inputs(stage6a_dir)
    if inputs["violations"]:
        raise SystemExit("Stage6A inputs are not registerable: " + "; ".join(inputs["violations"]))

    samples = build_registered_samples(inputs["candidates"], inputs["gold_rows"])
    gold_plans, gold_programs = build_gold_artifacts(inputs["gold_rows"])
    distribution = build_distribution_report(samples, inputs["gold_rows"], inputs["table_manifest"])

    write_jsonl(artifacts / "registered_samples.jsonl", samples)
    write_jsonl(artifacts / "gold_write_plans.jsonl", gold_plans)
    write_jsonl(artifacts / "gold_programs.jsonl", gold_programs)
    write_jsonl(artifacts / "gold_post_state_hashes.jsonl", inputs["post_hashes"])
    write_json(artifacts / "isolated_table_db_manifest.json", inputs["table_manifest"])
    write_json(artifacts / "distribution_report.json", distribution)
    write_json(artifacts / "stage6_seen_reference_registry.json", inputs["registry"])
    write_json(artifacts / "crudsql_overlap_audit.json", inputs["overlap"])
    write_text(
        artifacts / "registered_ids.tsv",
        "stage6_sample_id\tupstream_sample_locator\tofficial_split_index\ttable_id\tquestion_sha256\n"
        + "\n".join(
            "\t".join(
                [
                    row["stage6_sample_id"],
                    row["upstream_sample_locator"],
                    str(row["official_split_index"]),
                    row["table_id"],
                    row["question_sha256"],
                ]
            )
            for row in samples
        )
        + "\n",
    )

    gold_review_protocol = {
        "status": "LOCKED_PENDING_REVIEW_EXECUTION",
        "two_independent_reviews_required": True,
        "reviewer_roles": ["primary_gold_reviewer", "secondary_gold_reviewer"],
        "reviewer_must_not_see_model_predictions": True,
        "allowed_labels": ["approved", "rejected"],
        "disagreement_adjudication": "third_adjudicator_or_joint_protocol_decision_before_gpu",
        "gold_edit_cutoff": "before_any_model_or_gpu_confirmation_run",
        "final_gold_hash_required_before_gpu": True,
        "confirmation_run_allowed_now": False,
    }
    write_json(out_dir / "GOLD_REVIEW_PROTOCOL_LOCK.json", gold_review_protocol)

    dataset_manifest = {
        "stage": "Stage6B_CRUDSQL_CONFIRMATION_DATASET_REGISTRATION",
        "status": "REGISTERED_PENDING_REVIEWER_ACCEPTANCE",
        "confirmation_run_allowed_now": False,
        "model_called": False,
        "gpu_called": False,
        "source": {
            "dataset": "CRUDSQL",
            "repository": "https://github.com/bizard-lab/CRUDSQL.git",
            "commit": CRUDSQL_COMMIT,
            "split": "official test",
            "subset": "all type=0 Create examples",
            "sampling": "none_use_all_eligible_examples",
        },
        "sample_count": 500,
        "table_count": 125,
        "stage6a_accepted_commit": STAGE6A_ACCEPTED_COMMIT,
        "stage5_protocol_commit": STAGE5_PROTOCOL_COMMIT,
        "stage5_method_commit": STAGE5_METHOD_COMMIT,
        "registered_ids_sha256": sha256_file(artifacts / "registered_ids.tsv"),
        "registered_samples_sha256": sha256_file(artifacts / "registered_samples.jsonl"),
        "gold_write_plans_sha256": sha256_file(artifacts / "gold_write_plans.jsonl"),
        "gold_programs_sha256": sha256_file(artifacts / "gold_programs.jsonl"),
        "gold_post_state_hashes_sha256": sha256_file(artifacts / "gold_post_state_hashes.jsonl"),
        "isolated_table_db_manifest_sha256": sha256_file(artifacts / "isolated_table_db_manifest.json"),
        "overlap_registry_sha256": sha256_file(artifacts / "stage6_seen_reference_registry.json"),
        "overlap_registry_self_hash": inputs["registry"]["registry_sha256_excluding_self"],
        "overlap_audit_sha256": sha256_file(artifacts / "crudsql_overlap_audit.json"),
        "distribution_report_sha256": sha256_file(artifacts / "distribution_report.json"),
        "gold_review_protocol_sha256": sha256_file(out_dir / "GOLD_REVIEW_PROTOCOL_LOCK.json"),
    }
    write_json(out_dir / "CONFIRMATION_DATASET_MANIFEST.json", dataset_manifest)

    # Copy isolated DBs into Stage6B so the registered archive is self-contained.
    db_dir = out_dir / "isolated_table_dbs"
    db_dir.mkdir(exist_ok=True)
    archive_members = [
        out_dir / "CONFIRMATION_DATASET_MANIFEST.json",
        out_dir / "GOLD_REVIEW_PROTOCOL_LOCK.json",
        *sorted(artifacts.glob("*")),
    ]
    for source_db in sorted((stage6a_dir / "isolated_table_dbs").glob("*.sqlite")):
        target = db_dir / source_db.name
        target.write_bytes(source_db.read_bytes())
        archive_members.append(target)

    archive = make_archive(out_dir / ARCHIVE_NAME, archive_members, out_dir)
    registration_lock = {
        "stage": "Stage6B_CRUDSQL_CONFIRMATION_DATASET_REGISTRATION",
        "status": "PASS_REGISTERED_PENDING_REVIEWER_ACCEPTANCE",
        "confirmation_run_allowed_now": False,
        "model_called": False,
        "gpu_called": False,
        "dataset_manifest_sha256": sha256_file(out_dir / "CONFIRMATION_DATASET_MANIFEST.json"),
        "dataset_archive": archive,
        "validation_policy": {
            "any_nonzero_overlap_count_fails": True,
            "database_id_namespace_overlap_count_fails_if_nonzero": True,
            "stage5_method_protocol_unchanged": True,
            "gold_review_required_before_gpu": True,
        },
    }
    write_json(out_dir / "STAGE6B_REGISTRATION_LOCK.json", registration_lock)

    validation_report = f"""# Stage 6B CRUDSQL Registration Validation Report

Status: PASS

Validation date: 2026-08-24

Stage6B registers all 500 official CRUDSQL test Create examples. It does not
call Qwen, does not run GPU inference, and does not permit confirmation runs
yet.

Key results:

- registered samples: 500
- isolated SQLite DBs: 125
- gold write plans: 500
- gold programs: 500
- post-state hashes: 500
- prior input-text hashes: {inputs['registry']['digest_counts']['input_text_sha256']}
- overlap counts: all zero, including database ID namespace
- dataset archive SHA-256: {archive['sha256']}
- confirmation_run_allowed_now: false

Tests:

- `python scripts/data/validate_stage6b_registration.py --registration-dir stage6_crudsql_registration`
- `python -m pytest -q tests/test_stage6b_registration.py`
"""
    reviewer_readme = f"""# Stage 6B CRUDSQL Confirmation Dataset Registration

Status: registered pending reviewer acceptance.

This package is CPU-only. It freezes all 500 official CRUDSQL test `type=0`
Create examples, 125 isolated single-table SQLite databases, deterministic gold
write plans/programs, post-state hashes, overlap registry, and gold-review
protocol. It does not call a model and does not allow GPU confirmation yet.

Important lock:

```text
N = 500
source = CRUDSQL commit {CRUDSQL_COMMIT}
split = official test
subset = all type=0 Create examples
sampling = none
confirmation_run_allowed_now = false
```

Run validation:

```bash
python scripts/data/validate_stage6b_registration.py --registration-dir stage6_crudsql_registration
PYTHONPATH=tests/support/windows_py314_pytest_tempdir \\
python -m pytest -q tests/test_stage6b_registration.py --basetemp pytest_tmp_stage6b_tests
```
"""
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report)
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme)

    # Refresh lock hashes for README/report files after writing them.
    registration_lock["reviewer_readme_sha256"] = sha256_file(out_dir / "REVIEWER_README.md")
    registration_lock["validation_report_sha256"] = sha256_file(out_dir / "VALIDATION_REPORT.md")
    write_json(out_dir / "STAGE6B_REGISTRATION_LOCK.json", registration_lock)
    return registration_lock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage6a-dir", default=str(STAGE6A_DIR))
    parser.add_argument("--out-dir", default=str(STAGE6B_DIR))
    args = parser.parse_args(argv)
    report = register_stage6b(Path(args.stage6a_dir), Path(args.out_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
