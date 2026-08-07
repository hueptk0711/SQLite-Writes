from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from .external_holdout import audit_external_holdout_metadata


CONSUMED_DATABASES = frozenset(
    {
        "california_schools",
        "card_games",
        "codebase_community",
        "debit_card_specializing",
        "european_football_2",
        "financial",
        "formula_1",
        "student_club",
        "superhero",
        "thrombosis_prediction",
        "toxicology",
    }
)
EXPECTED_OPERATIONS = {
    "plain_insert": 20,
    "insert_ignore": 20,
    "upsert_update": 20,
}
EXPECTED_INPUT_MODES = {
    "free_text": 20,
    "semi_structured": 40,
}
EXPECTED_COMPLEXITIES = {
    "single_row": 20,
    "small_batch": 20,
    "large_or_relational": 20,
}


def audit_calibration_metadata(
    samples: list[dict[str, Any]],
    *,
    reserved_final_db_ids: Iterable[str],
    consumed_sample_ids: Iterable[str] = (),
    consumed_source_groups: Iterable[str] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Audit the frozen 60-sample calibration design before any model run."""
    issues, base_summary = audit_external_holdout_metadata(
        samples,
        strict_final=False,
    )
    operation_counts: Counter[str] = Counter()
    input_mode_counts: Counter[str] = Counter()
    complexity_counts: Counter[str] = Counter()
    database_counts: Counter[str] = Counter()
    author_counts: Counter[str] = Counter()
    reviewer_counts: Counter[str] = Counter()
    multi_table = 0
    consumed_ids = {str(value) for value in consumed_sample_ids if str(value)}
    consumed_groups = {
        str(value) for value in consumed_source_groups if str(value)
    }
    reserved_final = {
        str(value) for value in reserved_final_db_ids if str(value)
    }

    def issue(sample_id: str | None, code: str, message: str) -> None:
        issues.append(
            {
                "sample_id": sample_id,
                "error_code": code,
                "message": message,
            }
        )

    if not 3 <= len(reserved_final) <= 5:
        issue(
            None,
            "INVALID_RESERVED_FINAL_DATABASE_COUNT",
            "Reserve 3-5 final-holdout databases before calibration freeze.",
        )

    for index, sample in enumerate(samples):
        sample_id = str(
            sample.get("id") or sample.get("sample_id") or f"<row-{index}>"
        )
        db_id = str(sample.get("db_id") or "")
        operation = str(sample.get("operation_semantics") or "")
        complexity = str(sample.get("complexity") or "")
        input_mode = str(sample.get("input_mode") or "")
        if not input_mode:
            input_mode = (
                "free_text"
                if str(sample.get("input_format") or "") == "free_text"
                else "semi_structured"
            )
        operation_counts[operation] += 1
        complexity_counts[complexity] += 1
        input_mode_counts[input_mode] += 1
        database_counts[db_id] += 1
        multi_table += sample.get("multi_table") is True

        if sample_id in consumed_ids:
            issue(
                sample_id,
                "CONSUMED_SAMPLE_ID_OVERLAP",
                "Calibration sample ID overlaps consumed development data.",
            )
        source_group = str(sample.get("source_group") or "")
        if source_group in consumed_groups:
            issue(
                sample_id,
                "CONSUMED_SOURCE_GROUP_OVERLAP",
                "Calibration source group overlaps consumed development data.",
            )
        if db_id in CONSUMED_DATABASES:
            issue(
                sample_id,
                "CONSUMED_DATABASE_OVERLAP",
                f"Calibration database {db_id!r} is one of the 11 consumed databases.",
            )
        if db_id in reserved_final:
            issue(
                sample_id,
                "FINAL_DATABASE_OVERLAP",
                f"Calibration database {db_id!r} is reserved for final holdout.",
            )
        if sample.get("is_augmented") is True or sample.get(
            "augmentation_type"
        ):
            issue(
                sample_id,
                "AUGMENTED_CALIBRATION_SAMPLE",
                "Calibration requires original requests, not format variants.",
            )

        author_id = str(sample.get("author_id") or "")
        if not author_id:
            issue(sample_id, "MISSING_AUTHOR_ID", "author_id is required.")
        else:
            author_counts[author_id] += 1
        reviews = sample.get("qa_reviews")
        approved_reviewers: set[str] = set()
        if not isinstance(reviews, list) or len(reviews) < 2:
            issue(
                sample_id,
                "INSUFFICIENT_CALIBRATION_QA",
                "Two independent approving calibration reviews are required.",
            )
            continue
        for review in reviews:
            if not isinstance(review, dict):
                continue
            reviewer_id = str(review.get("reviewer_id") or "")
            quality_flags = (
                review.get("decision") == "approved"
                and review.get("semantics_correct") is True
                and review.get("gold_target_correct") is True
                and review.get("conflict_target_correct") is True
                and review.get("update_columns_correct") is True
                and review.get("hidden_policy") is False
            )
            if reviewer_id and quality_flags:
                approved_reviewers.add(reviewer_id)
                reviewer_counts[reviewer_id] += 1
        if len(approved_reviewers) < 2:
            issue(
                sample_id,
                "CALIBRATION_QA_NOT_APPROVED",
                "Two distinct reviewers must approve every semantic QA flag.",
            )
        if author_id and author_id in approved_reviewers:
            issue(
                sample_id,
                "AUTHOR_REVIEWER_NOT_INDEPENDENT",
                "The request author cannot be one of its two QA reviewers.",
            )

    if len(samples) != 60:
        issue(
            None,
            "INVALID_CALIBRATION_SAMPLE_COUNT",
            f"Calibration requires exactly 60 samples, got {len(samples)}.",
        )
    if len(database_counts) != 2 or set(database_counts.values()) != {30}:
        issue(
            None,
            "INVALID_CALIBRATION_DATABASE_BALANCE",
            "Calibration requires exactly two databases with 30 samples each.",
        )
    if dict(operation_counts) != EXPECTED_OPERATIONS:
        issue(
            None,
            "UNBALANCED_CALIBRATION_OPERATIONS",
            f"Expected {EXPECTED_OPERATIONS}, got {dict(operation_counts)}.",
        )
    if dict(input_mode_counts) != EXPECTED_INPUT_MODES:
        issue(
            None,
            "UNBALANCED_CALIBRATION_INPUT_MODES",
            f"Expected {EXPECTED_INPUT_MODES}, got {dict(input_mode_counts)}.",
        )
    if dict(complexity_counts) != EXPECTED_COMPLEXITIES:
        issue(
            None,
            "UNBALANCED_CALIBRATION_COMPLEXITY",
            f"Expected {EXPECTED_COMPLEXITIES}, got {dict(complexity_counts)}.",
        )
    if multi_table < 20:
        issue(
            None,
            "TOO_FEW_CALIBRATION_MULTI_TABLE",
            "Calibration requires at least 20 multi-table samples.",
        )

    summary = {
        **base_summary,
        "calibration_sample_count": len(samples),
        "reserved_final_database_ids": sorted(reserved_final),
        "database_counts": dict(sorted(database_counts.items())),
        "operation_counts": dict(sorted(operation_counts.items())),
        "input_mode_counts": dict(sorted(input_mode_counts.items())),
        "complexity_counts": dict(sorted(complexity_counts.items())),
        "multi_table_samples": multi_table,
        "author_counts": dict(sorted(author_counts.items())),
        "reviewer_counts": dict(sorted(reviewer_counts.items())),
        "blocking_issue_count": len(issues),
        "status": "valid" if not issues else "invalid",
    }
    return issues, summary
