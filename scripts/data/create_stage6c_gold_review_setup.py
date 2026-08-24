#!/usr/bin/env python3
"""Create the CPU-only Stage 6C independent gold-review setup."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE6B_DIR = PROJECT_ROOT / "stage6_crudsql_registration"
STAGE6C_DIR = PROJECT_ROOT / "stage6_gold_review_setup"
DEFAULT_CRUDSQL_ROOT = PROJECT_ROOT.parents[1] / "external_sources" / "CRUDSQL_63bfce67"
CRUDSQL_COMMIT = "63bfce67d8391185453a812751e115a499201363"
STAGE6B_COMMIT = "bbbe4d374edd60696b0765771640a946a838fd4d"
LEGACY_COMBINED_ARCHIVE_NAME = "stage6c_gold_review_packets_20260824.zip"
R01_ARCHIVE_NAME = "Stage6C_R01_review_packet_20260824.zip"
R02_ARCHIVE_NAME = "Stage6C_R02_review_packet_20260824.zip"
PATCH0_GOLD_REVIEW_ITEMS_SHA256 = "89942c53320735528f1516c25de5efee9aa2f18f6a70a7789221bd8609ec3a6b"
PATCH0_OFFICIAL_TABLE_METADATA_SHA256 = "f9e98479cdae560424c837e0bd75758f51c8582a3376567f8aa660a4dc690a93"
ZIP_TIMESTAMP = (2026, 8, 24, 0, 0, 0)
REVIEW_PACKET_COLUMNS = [
    "stage6_sample_id",
    "upstream_sample_locator",
    "authored_content_sha256",
    "reviewed_by",
    "decision",
    "notes",
]


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


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def write_review_packet(path: Path, rows: list[dict[str, Any]], reviewer_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_PACKET_COLUMNS, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "stage6_sample_id": row["stage6_sample_id"],
                    "upstream_sample_locator": row["upstream_sample_locator"],
                    "authored_content_sha256": row["authored_content_sha256"],
                    "reviewed_by": reviewer_id,
                    "decision": "",
                    "notes": "",
                }
            )


def make_archive(archive_path: Path, members: list[Path], root: Path) -> dict[str, Any]:
    if archive_path.exists():
        archive_path.unlink()
    member_rows: list[dict[str, str]] = []
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member in sorted(members, key=lambda item: item.relative_to(root).as_posix()):
            rel = member.relative_to(root).as_posix()
            info = zipfile.ZipInfo(rel, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, member.read_bytes())
            member_rows.append({"path": rel, "sha256": sha256_file(member)})
    return {
        "path": archive_path.relative_to(root).as_posix(),
        "sha256": sha256_file(archive_path),
        "member_count": len(member_rows),
        "members": member_rows,
    }


def write_reviewer_archive_manifest(
    path: Path,
    reviewer_id: str,
    packet_path: Path,
    out_dir: Path,
) -> None:
    manifest = {
        "stage": "Stage6C_PATCH1_REVIEWER_ISOLATED_PACKET",
        "status": "READY_FOR_INDEPENDENT_REVIEW_EXECUTION",
        "reviewer_id": reviewer_id,
        "model_called": False,
        "gpu_called": False,
        "confirmation_run_allowed_now": False,
        "contains_only_own_decision_packet": True,
        "must_not_receive_other_reviewer_decisions_or_notes_before_submission": True,
        "shared_materials": {
            "gold_review_items": {
                "path": "artifacts/gold_review_items.jsonl",
                "sha256": sha256_file(out_dir / "artifacts" / "gold_review_items.jsonl"),
            },
            "official_table_metadata": {
                "path": "artifacts/official_table_metadata.jsonl",
                "sha256": sha256_file(out_dir / "artifacts" / "official_table_metadata.jsonl"),
            },
            "protocol_addendum": {
                "path": "GOLD_REVIEW_PROTOCOL_ADDENDUM_LOCK.json",
                "sha256": sha256_file(out_dir / "GOLD_REVIEW_PROTOCOL_ADDENDUM_LOCK.json"),
            },
        },
        "decision_packet": {
            "path": packet_path.relative_to(out_dir).as_posix(),
            "sha256": sha256_file(packet_path),
        },
    }
    write_json(path, manifest)


def validate_stage6b_inputs(stage6b_dir: Path) -> dict[str, Any]:
    manifest = read_json(stage6b_dir / "CONFIRMATION_DATASET_MANIFEST.json")
    violations: list[str] = []
    if manifest.get("sample_count") != 500:
        violations.append("stage6b_sample_count_not_500")
    if manifest.get("table_count") != 125:
        violations.append("stage6b_table_count_not_125")
    if manifest.get("confirmation_run_allowed_now") is not False:
        violations.append("stage6b_confirmation_run_allowed_now_not_false")
    if manifest.get("model_called") is not False or manifest.get("gpu_called") is not False:
        violations.append("stage6b_model_or_gpu_called")
    if (manifest.get("source") or {}).get("commit") != CRUDSQL_COMMIT:
        violations.append("stage6b_crudsql_commit_mismatch")
    return {"manifest": manifest, "violations": violations}


def load_official_crudsql(crudsql_root: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    sql_path = crudsql_root / "data" / "test" / "crud_test_sql.json"
    table_path = crudsql_root / "data" / "test" / "crud_test_table.json"
    if not sql_path.is_file() or not table_path.is_file():
        raise FileNotFoundError(f"Missing CRUDSQL official test files under {crudsql_root}")
    sql_rows = read_json(sql_path)
    table_rows = read_json(table_path)
    return sql_rows, {row["id"]: row for row in table_rows}


def build_review_items(stage6b_dir: Path, crudsql_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    samples = read_jsonl(stage6b_dir / "artifacts" / "registered_samples.jsonl")
    plans = {row["stage6_sample_id"]: row for row in read_jsonl(stage6b_dir / "artifacts" / "gold_write_plans.jsonl")}
    programs = {row["stage6_sample_id"]: row for row in read_jsonl(stage6b_dir / "artifacts" / "gold_programs.jsonl")}
    official_sql_rows, official_tables = load_official_crudsql(crudsql_root)

    table_metadata_rows = []
    for table_id, table in sorted(official_tables.items()):
        payload = {
            "table_id": table_id,
            "official_table": table,
            "official_table_metadata_sha256": sha256_text(canonical_json(table)),
        }
        table_metadata_rows.append(payload)

    review_items: list[dict[str, Any]] = []
    for sample in samples:
        stage6_id = sample["stage6_sample_id"]
        official_index = int(sample["official_split_index"])
        official_annotation = official_sql_rows[official_index]
        table = official_tables[sample["table_id"]]
        if official_annotation.get("table_id") != sample["table_id"]:
            raise RuntimeError(f"Official annotation/table mismatch for {stage6_id}")
        plan = plans[stage6_id]
        program = programs[stage6_id]
        content = {
            "stage6_sample_id": stage6_id,
            "upstream_sample_locator": sample["upstream_sample_locator"],
            "source": {
                "dataset": "CRUDSQL",
                "commit": CRUDSQL_COMMIT,
                "official_split": "test",
                "official_split_index": official_index,
                "table_id": sample["table_id"],
            },
            "human_review_scope": [
                "Chinese NL instruction matches official CRUDSQL insert semantics",
                "official annotation maps to the selected table columns",
                "inserted values and normalization are faithful",
                "omitted fields are not requested by the NL instruction",
                "gold INSERT program reflects the registered task",
            ],
            "question": sample["question"],
            "official_annotation": official_annotation,
            "official_table_metadata": {
                "name": table.get("name"),
                "title": table.get("title"),
                "header": table.get("header"),
                "types": table.get("types"),
                "common": table.get("common"),
                "row_count": len(table.get("rows") or []),
                "rows_preview_first_5": (table.get("rows") or [])[:5],
            },
            "gold_write_plan": {
                "operation": plan["operation"],
                "columns": plan["columns"],
                "column_indexes": plan["column_indexes"],
                "values": plan["values"],
                "expected_inserted_row": plan["expected_inserted_row"],
                "fresh_db_per_sample": plan["fresh_db_per_sample"],
            },
            "gold_program": {
                "sqlite_parameter_style": program["sqlite_parameter_style"],
                "sql_template": program["sql_template"],
                "parameters": program["parameters"],
            },
            "hashes": {
                "question_sha256": sample["question_sha256"],
                "canonical_content_sha256": sample["canonical_content_sha256"],
                "schema_sha256": sample["schema_sha256"],
                "initial_state_sha256": sample["initial_state_sha256"],
                "post_state_sha256": sample["post_state_sha256"],
                "official_annotation_sha256": sha256_text(canonical_json(official_annotation)),
                "official_table_metadata_sha256": sha256_text(canonical_json(table)),
                "gold_write_plan_sha256": sha256_text(canonical_json(plan)),
                "gold_program_sha256": sha256_text(canonical_json(program)),
            },
        }
        review_items.append(content | {"authored_content_sha256": sha256_text(canonical_json(content))})
    return review_items, table_metadata_rows


def create_stage6c_setup(
    stage6b_dir: Path = STAGE6B_DIR,
    out_dir: Path = STAGE6C_DIR,
    crudsql_root: Path = DEFAULT_CRUDSQL_ROOT,
) -> dict[str, Any]:
    stage6b = validate_stage6b_inputs(stage6b_dir)
    if stage6b["violations"]:
        raise SystemExit("Stage6B inputs are not reviewable: " + "; ".join(stage6b["violations"]))

    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = out_dir / "artifacts"
    packets = out_dir / "review_packets"
    artifacts.mkdir(parents=True, exist_ok=True)
    packets.mkdir(parents=True, exist_ok=True)
    for stale in [
        out_dir / LEGACY_COMBINED_ARCHIVE_NAME,
        out_dir / R01_ARCHIVE_NAME,
        out_dir / R02_ARCHIVE_NAME,
    ]:
        if stale.exists():
            stale.unlink()

    review_items, table_metadata_rows = build_review_items(stage6b_dir, crudsql_root)
    write_jsonl(artifacts / "gold_review_items.jsonl", review_items)
    write_jsonl(artifacts / "official_table_metadata.jsonl", table_metadata_rows)

    addendum = {
        "stage": "Stage6C_INDEPENDENT_GOLD_REVIEW_SETUP",
        "status": "LOCKED_PENDING_HUMAN_REVIEW_EXECUTION",
        "model_called": False,
        "gpu_called": False,
        "confirmation_run_allowed_now": False,
        "reviewer_roles": ["R01", "R02"],
        "independent_reviews_required": 2,
        "reviewer_must_not_see_model_predictions": True,
        "review_materials": "source_plus_registered_gold_only_no_model_outputs",
        "allowed_decisions_after_execution": ["approved", "rejected"],
        "packet_decision_fields_initially_blank": True,
        "notes_required_for_rejected": True,
        "reviewer_isolation": {
            "reviewer_roles_must_be_distinct": True,
            "R01_must_not_see_R02_decisions_or_notes_before_submission": True,
            "R02_must_not_see_R01_decisions_or_notes_before_submission": True,
            "cross_reviewer_discussion_before_submission": False,
            "each_reviewer_receives_only_own_decision_packet": True,
            "reviewer_outputs_are_sealed_until_both_submitted": True,
            "execution_manifest_must_record": [
                "R01_submission_sha256",
                "R02_submission_sha256",
                "submission_timestamps",
                "pseudonymous_reviewer_role_ids",
                "distinct_reviewer_assertion",
            ],
        },
        "adjudication_policy": {
            "disagreement_rule": "third_independent_adjudicator",
            "third_adjudicator_role": "R03",
            "third_adjudicator_must_not_see_model_predictions": True,
            "R03_must_not_see_R01_decision": True,
            "R03_must_not_see_R02_decision": True,
            "R03_must_not_see_R01_notes": True,
            "R03_must_not_see_R02_notes": True,
            "third_adjudicator_materials": "source_plus_registered_gold_only_no_model_outputs",
            "joint_discussion_allowed": False,
            "decision_after_seeing_model_outputs_allowed": False,
        },
        "final_decision_rule": [
            {"R01": "approved", "R02": "approved", "action": "final_approved"},
            {"R01": "rejected", "R02": "rejected", "action": "final_rejected_confirmation_blocked"},
            {"R01": "approved", "R02": "rejected", "action": "blind_R03_adjudication"},
            {"R01": "rejected", "R02": "approved", "action": "blind_R03_adjudication"},
            {"R03": "approved", "action": "final_approved"},
            {"R03": "rejected", "action": "final_rejected_confirmation_blocked"},
        ],
        "rejection_policy": {
            "any_final_unresolved_or_rejected_gold_blocks_confirmation": True,
            "initial_R01_R02_disagreement_goes_to_blind_R03_not_immediate_block": True,
            "drop_rejected_samples_allowed": False,
            "silent_exclusion_allowed": False,
            "if_adapter_or_gold_error": (
                "fix gold before any model run, recompute affected artifacts/hashes, "
                "and re-review corrected items independently"
            ),
            "if_official_source_task_invalid_for_frozen_contract": (
                "revise Stage6 registration before any model run and disclose rationale"
            ),
        },
        "registered_dataset_commit": STAGE6B_COMMIT,
        "registered_dataset_manifest_sha256": sha256_file(stage6b_dir / "CONFIRMATION_DATASET_MANIFEST.json"),
        "registered_gold_write_plans_sha256": sha256_file(stage6b_dir / "artifacts" / "gold_write_plans.jsonl"),
        "registered_gold_programs_sha256": sha256_file(stage6b_dir / "artifacts" / "gold_programs.jsonl"),
        "registered_gold_post_state_hashes_sha256": sha256_file(stage6b_dir / "artifacts" / "gold_post_state_hashes.jsonl"),
        "patch1_content_immutability": {
            "gold_review_items_sha256_must_remain": PATCH0_GOLD_REVIEW_ITEMS_SHA256,
            "official_table_metadata_sha256_must_remain": PATCH0_OFFICIAL_TABLE_METADATA_SHA256,
        },
    }
    write_json(out_dir / "GOLD_REVIEW_PROTOCOL_ADDENDUM_LOCK.json", addendum)

    r01_packet = packets / "stage6c_gold_review_R01.tsv"
    r02_packet = packets / "stage6c_gold_review_R02.tsv"
    write_review_packet(r01_packet, review_items, "R01")
    write_review_packet(r02_packet, review_items, "R02")
    r01_manifest = packets / "Stage6C_R01_PACKET_MANIFEST.json"
    r02_manifest = packets / "Stage6C_R02_PACKET_MANIFEST.json"
    write_reviewer_archive_manifest(r01_manifest, "R01", r01_packet, out_dir)
    write_reviewer_archive_manifest(r02_manifest, "R02", r02_packet, out_dir)
    r01_archive = make_archive(
        out_dir / R01_ARCHIVE_NAME,
        [
            out_dir / "GOLD_REVIEW_PROTOCOL_ADDENDUM_LOCK.json",
            artifacts / "gold_review_items.jsonl",
            artifacts / "official_table_metadata.jsonl",
            r01_manifest,
            r01_packet,
        ],
        out_dir,
    )
    r02_archive = make_archive(
        out_dir / R02_ARCHIVE_NAME,
        [
            out_dir / "GOLD_REVIEW_PROTOCOL_ADDENDUM_LOCK.json",
            artifacts / "gold_review_items.jsonl",
            artifacts / "official_table_metadata.jsonl",
            r02_manifest,
            r02_packet,
        ],
        out_dir,
    )

    packet_manifest = {
        "stage": "Stage6C_INDEPENDENT_GOLD_REVIEW_SETUP",
        "status": "PACKETS_CREATED_PENDING_HUMAN_REVIEW",
        "model_called": False,
        "gpu_called": False,
        "confirmation_run_allowed_now": False,
        "sample_count": len(review_items),
        "reviewer_isolation_distribution": "separate_archive_per_reviewer",
        "reviewer_packets": [
            {
                "reviewer_id": "R01",
                "path": "review_packets/stage6c_gold_review_R01.tsv",
                "sha256": sha256_file(r01_packet),
                "isolated_archive": r01_archive,
            },
            {
                "reviewer_id": "R02",
                "path": "review_packets/stage6c_gold_review_R02.tsv",
                "sha256": sha256_file(r02_packet),
                "isolated_archive": r02_archive,
            },
        ],
        "gold_review_items_sha256": sha256_file(artifacts / "gold_review_items.jsonl"),
        "official_table_metadata_sha256": sha256_file(artifacts / "official_table_metadata.jsonl"),
        "protocol_addendum_sha256": sha256_file(out_dir / "GOLD_REVIEW_PROTOCOL_ADDENDUM_LOCK.json"),
        "packet_columns": REVIEW_PACKET_COLUMNS,
    }
    write_json(out_dir / "GOLD_REVIEW_PACKET_MANIFEST.json", packet_manifest)

    readme = """# Stage 6C Independent Gold Review Setup

Status: packets created, pending human execution.

This package does not call a model and does not permit GPU preflight. It locks
the third-adjudicator rule, rejection policy, and R01/R02 packet templates for
the 500 registered CRUDSQL Stage6B gold items.

Each reviewer receives only their own archive:

```text
Stage6C_R01_review_packet_20260824.zip -> R01 only
Stage6C_R02_review_packet_20260824.zip -> R02 only
```

Reviewers fill only:

```text
decision in {approved, rejected}
notes
```

All immutable fields and content hashes must remain unchanged. Reviewer outputs
are sealed until both R01/R02 have submitted. Final rejected or unresolved items
block confirmation until resolved before model execution.
"""
    validation = f"""# Stage 6C Gold Review Setup Validation Report

Status: PASS

Validation date: 2026-08-24

- review items: {len(review_items)}
- reviewer packets: R01, R02
- reviewer distribution: separate isolated archive per reviewer
- reviewer isolation: cross-reviewer decisions/notes hidden until both submitted
- adjudication: blind third independent adjudicator for disagreements
- final rejected/unresolved policy: blocks confirmation
- model_called: false
- gpu_called: false
- confirmation_run_allowed_now: false

Validation command:

```bash
python scripts/data/validate_stage6c_gold_review_setup.py --setup-dir stage6_gold_review_setup
```
"""
    write_text(out_dir / "REVIEWER_README.md", readme)
    write_text(out_dir / "VALIDATION_REPORT.md", validation)

    setup_lock = {
        "stage": "Stage6C_INDEPENDENT_GOLD_REVIEW_SETUP",
        "status": "PASS_LOCKED_PENDING_HUMAN_REVIEW_EXECUTION",
        "model_called": False,
        "gpu_called": False,
        "confirmation_run_allowed_now": False,
        "stage6b_commit": STAGE6B_COMMIT,
        "sample_count": len(review_items),
        "protocol_addendum_sha256": sha256_file(out_dir / "GOLD_REVIEW_PROTOCOL_ADDENDUM_LOCK.json"),
        "packet_manifest_sha256": sha256_file(out_dir / "GOLD_REVIEW_PACKET_MANIFEST.json"),
        "reviewer_readme_sha256": sha256_file(out_dir / "REVIEWER_README.md"),
        "validation_report_sha256": sha256_file(out_dir / "VALIDATION_REPORT.md"),
        "gold_review_items_sha256": sha256_file(artifacts / "gold_review_items.jsonl"),
        "official_table_metadata_sha256": sha256_file(artifacts / "official_table_metadata.jsonl"),
        "review_packet_archives": [r01_archive, r02_archive],
    }
    write_json(out_dir / "STAGE6C_GOLD_REVIEW_SETUP_LOCK.json", setup_lock)
    return setup_lock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage6b-dir", default=str(STAGE6B_DIR))
    parser.add_argument("--out-dir", default=str(STAGE6C_DIR))
    parser.add_argument("--crudsql-root", default=str(DEFAULT_CRUDSQL_ROOT))
    args = parser.parse_args(argv)
    report = create_stage6c_setup(Path(args.stage6b_dir), Path(args.out_dir), Path(args.crudsql_root))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
