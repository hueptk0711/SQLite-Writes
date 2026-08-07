#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from nldbwrite_v3.compiler import preflight_program
from nldbwrite_v3.evaluator import evaluate_candidate_sample, find_database
from nldbwrite_v3.pipeline import MappingFirstPipeline
from nldbwrite_v3.planner import parse_llm_plan
from nldbwrite_v3.schema import load_profile
from nldbwrite_v3.source_parser import parse_source_payload


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay locked MP-FS+ raw generations through the current "
            "deterministic pipeline without calling a model."
        )
    )
    parser.add_argument("--raw-generations", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--baseline-evaluation")
    parser.add_argument("--project-root")
    args = parser.parse_args()

    project_root = (
        Path(args.project_root).resolve()
        if args.project_root
        else Path(__file__).resolve().parents[2]
    )
    data = load_json(project_root / "data" / "calibration" / "dataset.json")
    profiles = {
        path.stem: load_profile(path)
        for path in (
            project_root
            / "data"
            / "calibration"
            / "authoring_kit"
            / "profiles"
        ).glob("*.json")
    }
    db_root = (
        project_root
        / "data"
        / "calibration"
        / "authoring_kit"
        / "databases"
    )
    raw_rows = load_jsonl(Path(args.raw_generations))
    raw_by_id = {str(row["sample_id"]): row for row in raw_rows}
    expected_ids = [str(sample["id"]) for sample in data]
    if (
        len(raw_rows) != len(expected_ids)
        or set(raw_by_id) != set(expected_ids)
    ):
        raise SystemExit(
            "Raw generation identity mismatch: "
            f"rows={len(raw_rows)}, unique={len(raw_by_id)}, "
            f"expected={len(expected_ids)}"
        )

    baseline: dict[str, dict[str, Any]] = {}
    if args.baseline_evaluation:
        baseline = {
            str(row["sample_id"]): row
            for row in load_jsonl(Path(args.baseline_evaluation))
        }

    replay_rows: list[dict[str, Any]] = []
    for sample in data:
        sample_id = str(sample["id"])
        request = str(sample.get("input_text") or "")
        profile = profiles[str(sample["db_id"])]
        payload = parse_source_payload(request)
        plan_kind = (
            "mapping" if payload.mode == "semi_structured" else "free_text"
        )
        parsed = parse_llm_plan(
            str(raw_by_id[sample_id].get("raw_output") or ""),
            plan_kind=plan_kind,
            reference_mode=True,
        )
        pipeline_result = None
        program = None
        if parsed.success:
            pipeline_result = MappingFirstPipeline(
                profile,
                normalization_mode="lossless",
                reference_planning=True,
            ).run(request, parsed.plan)
            program = pipeline_result.program
        build_success = bool(program and program.status == "success")
        db_path = find_database(db_root, str(sample["db_id"]))
        preflight = (
            preflight_program(db_path, program)
            if build_success and program is not None
            else {
                "status": "abstained",
                "accepted": False,
                "action": "abstain",
                "deterministic_repair_applied": False,
                "error_class": "upstream_rejection",
                "error": "Plan did not reach successful compilation.",
                "executed_statements": 0,
                "latency_sec": 0.0,
            }
        )
        evaluation = evaluate_candidate_sample(
            sample,
            db_path,
            program=program,
            parse_status=parsed.parse_status,
            build_status="success" if build_success else "error",
            preflight=preflight,
        )
        verification_errors = (
            [
                item.to_dict()
                for item in pipeline_result.verification.errors
            ]
            if pipeline_result is not None
            and pipeline_result.verification is not None
            else []
        )
        before = baseline.get(sample_id) or {}
        replay_rows.append(
            {
                "sample_id": sample_id,
                "detected_mode": payload.mode,
                "parse_success": parsed.success,
                "pipeline_stage": (
                    pipeline_result.stage
                    if pipeline_result is not None
                    else "parse"
                ),
                "build_success": build_success,
                "accepted_output": bool(preflight.get("accepted")),
                "execution_success": bool(
                    evaluation.get("execution_success")
                ),
                "target_state_correct": bool(
                    evaluation.get("target_state_correct")
                ),
                "strict_full_state_correct": bool(
                    evaluation.get("strict_full_state_correct")
                ),
                "error_type": evaluation.get("error_type"),
                "target_mismatched_tables": evaluation.get(
                    "target_mismatched_tables"
                ),
                "verification_errors": verification_errors,
                "baseline": {
                    "accepted_output": before.get("accepted_output"),
                    "target_state_correct": before.get(
                        "target_state_correct"
                    ),
                },
            }
        )

    sample_count = len(replay_rows)
    accepted = [row for row in replay_rows if row["accepted_output"]]
    accepted_correct = [
        row for row in accepted if row["target_state_correct"]
    ]
    summary = {
        "status": "forensic_replay_not_reportable",
        "samples": sample_count,
        "parse_success": sum(
            row["parse_success"] for row in replay_rows
        )
        / sample_count,
        "build_success": sum(
            row["build_success"] for row in replay_rows
        )
        / sample_count,
        "execution_success": sum(
            row["execution_success"] for row in replay_rows
        )
        / sample_count,
        "target_state_accuracy": sum(
            row["target_state_correct"] for row in replay_rows
        )
        / sample_count,
        "coverage": len(accepted) / sample_count,
        "accepted_output_accuracy": (
            len(accepted_correct) / len(accepted) if accepted else None
        ),
        "accepted_wrong_ids": [
            row["sample_id"]
            for row in accepted
            if not row["target_state_correct"]
        ],
        "newly_correct_ids": [
            row["sample_id"]
            for row in replay_rows
            if row["target_state_correct"]
            and row["baseline"]["target_state_correct"] is False
        ],
        "newly_abstained_ids": [
            row["sample_id"]
            for row in replay_rows
            if not row["accepted_output"]
            and row["baseline"]["accepted_output"] is True
        ],
        "regressed_ids": [
            row["sample_id"]
            for row in replay_rows
            if not row["target_state_correct"]
            and row["baseline"]["target_state_correct"] is True
        ],
    }
    report = {"summary": summary, "samples": replay_rows}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
