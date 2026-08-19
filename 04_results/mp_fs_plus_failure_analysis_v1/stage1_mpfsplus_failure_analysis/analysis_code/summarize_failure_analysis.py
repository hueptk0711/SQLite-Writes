from __future__ import annotations

import json

from .stage1_failure_analysis import (
    build_master,
    build_performance_by_database,
    build_performance_by_dependency_sensitivity,
    build_performance_by_input_type,
    build_performance_by_operation_type,
    build_reviewed_root_cause_summary,
    build_root_cause_summary_auto,
    build_stage_failure_summary,
    build_systematic_audit_summary,
)


def main() -> None:
    rows, _ = build_master()
    summary = {
        "stage_failure_summary": build_stage_failure_summary(rows),
        "root_cause_summary_auto": build_root_cause_summary_auto(rows),
        "reviewed_root_cause_summary": build_reviewed_root_cause_summary(rows),
        "performance_by_input_type": build_performance_by_input_type(rows),
        "performance_by_dependency_sensitivity": build_performance_by_dependency_sensitivity(rows),
        "performance_by_operation_type": build_performance_by_operation_type(rows),
        "performance_by_database": build_performance_by_database(rows),
        "systematic_audit_summary": build_systematic_audit_summary(rows),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
