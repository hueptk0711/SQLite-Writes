from __future__ import annotations

from collections import Counter
from typing import Any


OPERATIONS = {"plain_insert", "insert_ignore", "upsert_update"}
INPUT_FORMATS = {
    "free_text",
    "json",
    "markdown",
    "key_value",
    "csv_or_mixed",
}
COMPLEXITIES = {"single_row", "small_batch", "large_or_relational"}


def audit_external_holdout_metadata(
    samples: list[dict[str, Any]],
    *,
    strict_final: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Audit the pre-registered external-holdout design before gold execution."""
    issues: list[dict[str, Any]] = []
    operation_counts: Counter[str] = Counter()
    format_counts: Counter[str] = Counter()
    complexity_counts: Counter[str] = Counter()
    database_counts: Counter[str] = Counter()
    sample_ids: set[str] = set()
    source_groups: set[str] = set()
    reviewer_pairs: set[tuple[str, str]] = set()
    state_changing = 0
    conflict_sensitive = 0
    multi_table = 0
    independently_authored = 0

    def issue(sample_id: str | None, code: str, message: str) -> None:
        issues.append(
            {
                "sample_id": sample_id,
                "error_code": code,
                "message": message,
            }
        )

    for index, sample in enumerate(samples):
        sample_id = str(
            sample.get("id") or sample.get("sample_id") or ""
        )
        if not sample_id:
            issue(None, "MISSING_SAMPLE_ID", f"Row {index} has no sample ID.")
            sample_id = f"<row-{index}>"
        elif sample_id in sample_ids:
            issue(sample_id, "DUPLICATE_SAMPLE_ID", "Sample ID is duplicated.")
        sample_ids.add(sample_id)

        db_id = str(sample.get("db_id") or "")
        if not db_id:
            issue(sample_id, "MISSING_DB_ID", "db_id is required.")
        else:
            database_counts[db_id] += 1

        operation = str(sample.get("operation_semantics") or "")
        if operation not in OPERATIONS:
            issue(
                sample_id,
                "INVALID_OPERATION_SEMANTICS",
                f"Expected one of {sorted(OPERATIONS)}, got {operation!r}.",
            )
        else:
            operation_counts[operation] += 1

        input_format = str(sample.get("input_format") or "")
        if input_format not in INPUT_FORMATS:
            issue(
                sample_id,
                "INVALID_INPUT_FORMAT",
                f"Expected one of {sorted(INPUT_FORMATS)}, got {input_format!r}.",
            )
        else:
            format_counts[input_format] += 1

        complexity = str(sample.get("complexity") or "")
        if complexity not in COMPLEXITIES:
            issue(
                sample_id,
                "INVALID_COMPLEXITY",
                f"Expected one of {sorted(COMPLEXITIES)}, got {complexity!r}.",
            )
        else:
            complexity_counts[complexity] += 1

        if not str(sample.get("input_text") or "").strip():
            issue(sample_id, "MISSING_INPUT_TEXT", "input_text is required.")
        if not isinstance(sample.get("gold_sql"), list) or not sample.get(
            "gold_sql"
        ):
            issue(sample_id, "MISSING_GOLD_SQL", "gold_sql must be non-empty.")
        if not isinstance(sample.get("gold_plan"), dict):
            issue(sample_id, "MISSING_GOLD_PLAN", "gold_plan is required.")

        explicit = sample.get("semantics_explicit_in_request") is True
        semantics_source = str(sample.get("semantics_source") or "")
        if not explicit and semantics_source != "system_policy":
            issue(
                sample_id,
                "HIDDEN_CONFLICT_POLICY",
                "Conflict semantics must be explicit or supplied by system policy.",
            )
        conflict_target = sample.get("conflict_target")
        update_columns = sample.get("update_columns")
        if not isinstance(conflict_target, list):
            issue(
                sample_id,
                "INVALID_CONFLICT_TARGET",
                "conflict_target must be a list.",
            )
            conflict_target = []
        if not isinstance(update_columns, list):
            issue(
                sample_id,
                "INVALID_UPDATE_COLUMNS",
                "update_columns must be a list.",
            )
            update_columns = []
        if operation in {"insert_ignore", "upsert_update"} and not conflict_target:
            issue(
                sample_id,
                "MISSING_CONFLICT_TARGET",
                f"{operation} requires an explicit conflict target.",
            )
        if operation == "upsert_update" and not update_columns:
            issue(
                sample_id,
                "MISSING_UPDATE_COLUMNS",
                "upsert_update requires an explicit update-column mask.",
            )
        if operation != "upsert_update" and update_columns:
            issue(
                sample_id,
                "UNEXPECTED_UPDATE_COLUMNS",
                f"{operation} must not declare update columns.",
            )

        source_group = str(sample.get("source_group") or "")
        if not source_group:
            issue(sample_id, "MISSING_SOURCE_GROUP", "source_group is required.")
        elif source_group in source_groups:
            issue(
                sample_id,
                "REUSED_SOURCE_GROUP",
                "Primary holdout requests must be independently authored.",
            )
        source_groups.add(source_group)

        if sample.get("independently_authored") is True:
            independently_authored += 1
        else:
            issue(
                sample_id,
                "NOT_INDEPENDENTLY_AUTHORED",
                "Primary holdout must not contain augmented format variants.",
            )
        state_changing += sample.get("state_changing") is True
        conflict_sensitive += sample.get("conflict_sensitive") is True
        multi_table += sample.get("multi_table") is True

        reviews = sample.get("qa_reviews")
        if strict_final:
            if not isinstance(reviews, list) or len(reviews) < 2:
                issue(
                    sample_id,
                    "INSUFFICIENT_QA_REVIEWS",
                    "Two independent QA reviews are required before freeze.",
                )
            else:
                approved = [
                    review
                    for review in reviews
                    if isinstance(review, dict)
                    and review.get("decision") == "approved"
                    and str(review.get("reviewer_id") or "")
                ]
                reviewers = {
                    str(review["reviewer_id"]) for review in approved
                }
                if len(reviewers) < 2:
                    issue(
                        sample_id,
                        "QA_NOT_INDEPENDENT",
                        "Two distinct approving reviewer IDs are required.",
                    )
                elif len(reviewers) >= 2:
                    reviewer_pairs.add(tuple(sorted(reviewers)[:2]))

    if strict_final:
        expected_count = 300
        if len(samples) != expected_count:
            issue(
                None,
                "INVALID_FINAL_SAMPLE_COUNT",
                f"Final holdout requires {expected_count} samples.",
            )
        if not 3 <= len(database_counts) <= 5:
            issue(
                None,
                "INVALID_DATABASE_COUNT",
                "Final holdout requires 3-5 unseen databases.",
            )
        expected_operations = {operation: 100 for operation in OPERATIONS}
        if dict(operation_counts) != expected_operations:
            issue(
                None,
                "UNBALANCED_OPERATIONS",
                f"Expected {expected_operations}, got {dict(operation_counts)}.",
            )
        expected_formats = {input_format: 60 for input_format in INPUT_FORMATS}
        if dict(format_counts) != expected_formats:
            issue(
                None,
                "UNBALANCED_INPUT_FORMATS",
                f"Expected {expected_formats}, got {dict(format_counts)}.",
            )
        expected_complexity = {
            complexity: 100 for complexity in COMPLEXITIES
        }
        if dict(complexity_counts) != expected_complexity:
            issue(
                None,
                "UNBALANCED_COMPLEXITY",
                f"Expected {expected_complexity}, got {dict(complexity_counts)}.",
            )
        if multi_table < 80:
            issue(None, "TOO_FEW_MULTI_TABLE", "At least 80 cases are required.")
        if conflict_sensitive < 120:
            issue(
                None,
                "TOO_FEW_CONFLICT_SENSITIVE",
                "At least 120 conflict-sensitive cases are required.",
            )
        if state_changing < 240:
            issue(
                None,
                "TOO_FEW_STATE_CHANGING",
                "At least 80% of cases must change database state.",
            )

    summary = {
        "samples": len(samples),
        "unique_sample_ids": len(sample_ids),
        "unique_source_groups": len(source_groups),
        "database_counts": dict(sorted(database_counts.items())),
        "operation_counts": dict(sorted(operation_counts.items())),
        "input_format_counts": dict(sorted(format_counts.items())),
        "complexity_counts": dict(sorted(complexity_counts.items())),
        "multi_table_samples": multi_table,
        "conflict_sensitive_samples": conflict_sensitive,
        "state_changing_samples": state_changing,
        "independently_authored_samples": independently_authored,
        "reviewer_pair_count": len(reviewer_pairs),
        "blocking_issue_count": len(issues),
        "status": "valid" if not issues else "invalid",
    }
    return issues, summary
