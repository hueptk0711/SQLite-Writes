from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from nldbwrite_v3.analysis import (
    analyze_source_formats,
    compare_evaluation_runs,
    review_added_samples,
    select_dev_pilot,
)
from nldbwrite_v3.common import dump_json, load_json, read_ids, write_jsonl
from nldbwrite_v3.compiler import compile_write_plan
from nldbwrite_v3.data import (
    audit_gold_dataset,
    compare_snapshots,
    freeze_dataset,
    parse_gold_dataset,
)
from nldbwrite_v3.data.audit import write_snapshot_report
from nldbwrite_v3.experiments import (
    evaluate_saved_run,
    run_method,
    run_oracle_evaluation,
)
from nldbwrite_v3.planner import build_planner_prompt, materialize_mapping_plan
from nldbwrite_v3.schema import load_profile
from nldbwrite_v3.source_parser import parse_source_payload
from nldbwrite_v3.verifier import verify_write_plan


def _read_text(args: argparse.Namespace) -> str:
    if getattr(args, "input_text", None) is not None:
        return args.input_text
    return Path(args.input_file).read_text(encoding="utf-8")


def _emit(value: Any, output: str | None = None) -> None:
    if output:
        dump_json(value, output)
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


def _profiles(path: str | Path) -> dict[str, dict[str, Any]]:
    return {
        item.stem: load_profile(item)
        for item in Path(path).glob("*.json")
    }


def _required_asset(value: str | None, label: str, env_name: str) -> str:
    if value:
        return value
    raise ValueError(
        f"{label} is required; pass the CLI option or set {env_name}"
    )


def command_parse_source(args: argparse.Namespace) -> int:
    payload = parse_source_payload(_read_text(args))
    _emit(payload.to_dict(), args.output)
    return 0


def command_prompt(args: argparse.Namespace) -> int:
    request = _read_text(args)
    payload = parse_source_payload(request)
    prompt = build_planner_prompt(request, payload, load_profile(args.profile))
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(prompt + "\n", encoding="utf-8")
    else:
        print(prompt)
    return 0


def command_materialize(args: argparse.Namespace) -> int:
    payload = parse_source_payload(_read_text(args))
    plan = materialize_mapping_plan(load_json(args.mapping_plan), payload)
    _emit(plan, args.output)
    return 0


def command_verify(args: argparse.Namespace) -> int:
    result = verify_write_plan(
        load_json(args.plan),
        load_profile(args.profile),
    )
    _emit(result.to_dict(), args.output)
    return 0 if result.valid else 2


def command_compile(args: argparse.Namespace) -> int:
    result = compile_write_plan(
        load_json(args.plan),
        load_profile(args.profile),
        strict_atomic=not args.best_effort,
        normalize_values=args.normalize_values,
    )
    _emit(result.to_dict(), args.output)
    return 0 if result.status == "success" else 2


def command_parse_gold(args: argparse.Namespace) -> int:
    samples = load_json(args.data)
    if args.ids:
        selected = set(read_ids(args.ids))
        samples = [row for row in samples if str(row["id"]) in selected]
    plans, diagnostics = parse_gold_dataset(
        samples,
        profiles=_profiles(args.profile_dir) if args.profile_dir else None,
    )
    write_jsonl(plans, args.output)
    write_jsonl(diagnostics, args.diagnostics)
    summary = {
        "samples": len(samples),
        "parsed": len(plans),
        "failed": len(diagnostics),
        "output": str(args.output),
        "diagnostics": str(args.diagnostics),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not diagnostics else 2


def command_audit(args: argparse.Namespace) -> int:
    profile_dir = _required_asset(
        args.profile_dir,
        "profile directory",
        "NLDB_PROFILE_DIR",
    )
    plans, issues, report = audit_gold_dataset(
        args.data,
        profile_dir,
        db_root=args.db_root,
        ids_path=args.ids,
    )
    write_jsonl(plans, args.plans_out)
    write_jsonl(issues, args.issues_out)
    dump_json(report, args.report_out)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def command_diff(args: argparse.Namespace) -> int:
    rows, summary = compare_snapshots(
        args.left,
        args.right,
        left_ids_path=args.left_ids,
        right_ids_path=args.right_ids,
    )
    write_snapshot_report(rows, summary, args.output_csv, args.output_summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def command_freeze(args: argparse.Namespace) -> int:
    profile_dir = _required_asset(
        args.profile_dir,
        "profile directory",
        "NLDB_PROFILE_DIR",
    )
    db_root = _required_asset(
        args.db_root,
        "database root",
        "NLDB_DATABASE_ROOT",
    )
    manifest = freeze_dataset(
        args.data,
        args.split,
        profile_dir,
        args.output_dir,
        db_root=db_root,
        role=args.role,
        disjoint_split_path=args.disjoint_with,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def command_oracle(args: argparse.Namespace) -> int:
    profile_dir = _required_asset(
        args.profile_dir,
        "profile directory",
        "NLDB_PROFILE_DIR",
    )
    db_root = _required_asset(
        args.db_root,
        "database root",
        "NLDB_DATABASE_ROOT",
    )
    metrics = run_oracle_evaluation(
        args.data,
        args.gold_plans,
        profile_dir,
        db_root,
        args.output_dir,
        ids_path=args.ids,
        resume=not args.no_resume,
        max_samples=args.max_samples,
        progress_every=args.progress_every,
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


def command_analyze_source_formats(args: argparse.Namespace) -> int:
    summary = analyze_source_formats(
        args.data,
        args.output_csv,
        args.output_summary,
        ids_path=args.ids,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def command_run_method(args: argparse.Namespace) -> int:
    profile_dir = _required_asset(
        args.profile_dir,
        "profile directory",
        "NLDB_PROFILE_DIR",
    )
    db_root = _required_asset(
        args.db_root,
        "database root",
        "NLDB_DATABASE_ROOT",
    )
    metrics = run_method(
        args.config,
        args.data,
        args.ids,
        profile_dir,
        db_root,
        args.output_dir,
        gold_plans_path=args.gold_plans,
        inference_config_path=args.inference_config,
        resume=not args.no_resume,
        stage=args.stage,
        dependency_lock_path=args.dependency_lock,
        environment_manifest_path=args.environment_manifest,
        locked_config_path=args.locked_config,
        go_decision_path=args.go_decision,
        final_protocol_path=args.final_protocol,
        allow_locked_test_rerun=args.allow_locked_test_rerun,
        v2_source_path=args.v2_source_path,
        reuse_raw_generations_path=args.reuse_raw_generations,
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


def command_compare_runs(args: argparse.Namespace) -> int:
    if not args.exploratory_dev:
        for evaluation_path in (args.left, args.right):
            manifest_path = Path(evaluation_path).parent / "manifest.json"
            if not manifest_path.exists():
                raise ValueError(
                    "Formal comparison requires a sibling manifest.json; "
                    "use --exploratory-dev for non-paper analysis"
                )
            if load_json(manifest_path).get("stage") not in {
                "locked-test",
                "external-holdout",
                "second-model",
            }:
                raise ValueError(
                    "Formal statistical testing is restricted to frozen "
                    "locked-test, external-holdout, or second-model runs; "
                    "use --exploratory-dev for development analysis"
                )
    result = compare_evaluation_runs(
        args.left,
        args.right,
        args.data,
        args.output,
        metric=args.metric,
        bootstrap_iterations=args.bootstrap_iterations,
        seed=args.seed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_summarize_run(args: argparse.Namespace) -> int:
    result = evaluate_saved_run(
        args.evaluation,
        args.metrics_output,
        args.error_output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_review_added_samples(args: argparse.Namespace) -> int:
    result = review_added_samples(
        args.data,
        args.added_ids,
        args.dev_ids,
        args.profile_dir,
        args.db_root,
        args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_select_dev_pilot(args: argparse.Namespace) -> int:
    result = select_dev_pilot(
        args.data,
        args.dev_ids,
        args.output_ids,
        args.output_manifest,
        sample_count=args.sample_count,
        seed=args.seed,
        max_per_source_group=args.max_per_source_group,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nldbwrite-v3",
        description="Mapping-first verified database-write pipeline",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_text_input(target: argparse.ArgumentParser) -> None:
        group = target.add_mutually_exclusive_group(required=True)
        group.add_argument("--input-text")
        group.add_argument("--input-file")

    parse_source = subparsers.add_parser("parse-source")
    add_text_input(parse_source)
    parse_source.add_argument("--output")
    parse_source.set_defaults(func=command_parse_source)

    prompt = subparsers.add_parser("prompt")
    add_text_input(prompt)
    prompt.add_argument("--profile", required=True)
    prompt.add_argument("--output")
    prompt.set_defaults(func=command_prompt)

    materialize = subparsers.add_parser("materialize")
    add_text_input(materialize)
    materialize.add_argument("--mapping-plan", required=True)
    materialize.add_argument("--output")
    materialize.set_defaults(func=command_materialize)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--plan", required=True)
    verify.add_argument("--profile", required=True)
    verify.add_argument("--output")
    verify.set_defaults(func=command_verify)

    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("--plan", required=True)
    compile_parser.add_argument("--profile", required=True)
    compile_parser.add_argument("--output")
    compile_parser.add_argument("--best-effort", action="store_true")
    compile_parser.add_argument("--normalize-values", action="store_true")
    compile_parser.set_defaults(func=command_compile)

    parse_gold = subparsers.add_parser("parse-gold")
    parse_gold.add_argument("--data", required=True)
    parse_gold.add_argument("--ids")
    parse_gold.add_argument("--profile-dir")
    parse_gold.add_argument("--output", required=True)
    parse_gold.add_argument("--diagnostics", required=True)
    parse_gold.set_defaults(func=command_parse_gold)

    audit = subparsers.add_parser("audit")
    audit.add_argument("--data", required=True)
    audit.add_argument("--ids")
    audit.add_argument(
        "--profile-dir",
        default=os.environ.get("NLDB_PROFILE_DIR"),
    )
    audit.add_argument("--db-root", default=os.environ.get("NLDB_DATABASE_ROOT"))
    audit.add_argument("--plans-out", required=True)
    audit.add_argument("--issues-out", required=True)
    audit.add_argument("--report-out", required=True)
    audit.set_defaults(func=command_audit)

    diff = subparsers.add_parser("diff-snapshots")
    diff.add_argument("--left", required=True)
    diff.add_argument("--right", required=True)
    diff.add_argument("--left-ids")
    diff.add_argument("--right-ids")
    diff.add_argument("--output-csv", required=True)
    diff.add_argument("--output-summary", required=True)
    diff.set_defaults(func=command_diff)

    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--data", required=True)
    freeze.add_argument("--split", required=True)
    freeze.add_argument(
        "--profile-dir",
        default=os.environ.get("NLDB_PROFILE_DIR"),
    )
    freeze.add_argument(
        "--db-root",
        default=os.environ.get("NLDB_DATABASE_ROOT"),
    )
    freeze.add_argument("--output-dir", required=True)
    freeze.add_argument("--role", choices=("dev", "test"), default="test")
    freeze.add_argument("--disjoint-with")
    freeze.set_defaults(func=command_freeze)

    oracle = subparsers.add_parser("oracle")
    oracle.add_argument("--data", required=True)
    oracle.add_argument("--ids")
    oracle.add_argument("--gold-plans", required=True)
    oracle.add_argument(
        "--profile-dir",
        default=os.environ.get("NLDB_PROFILE_DIR"),
    )
    oracle.add_argument(
        "--db-root",
        default=os.environ.get("NLDB_DATABASE_ROOT"),
    )
    oracle.add_argument("--output-dir", required=True)
    oracle.add_argument("--no-resume", action="store_true")
    oracle.add_argument("--max-samples", type=int)
    oracle.add_argument("--progress-every", type=int, default=10)
    oracle.set_defaults(func=command_oracle)

    source_formats = subparsers.add_parser("analyze-source-formats")
    source_formats.add_argument("--data", required=True)
    source_formats.add_argument("--ids")
    source_formats.add_argument("--output-csv", required=True)
    source_formats.add_argument("--output-summary", required=True)
    source_formats.set_defaults(func=command_analyze_source_formats)

    run = subparsers.add_parser("run-method")
    run.add_argument("--config", required=True)
    run.add_argument("--data", required=True)
    run.add_argument("--ids", required=True)
    run.add_argument(
        "--profile-dir",
        default=os.environ.get("NLDB_PROFILE_DIR"),
    )
    run.add_argument(
        "--db-root",
        default=os.environ.get("NLDB_DATABASE_ROOT"),
    )
    run.add_argument("--gold-plans")
    run.add_argument("--inference-config")
    run.add_argument("--output-dir", required=True)
    run.add_argument("--no-resume", action="store_true")
    run.add_argument(
        "--stage",
        choices=(
            "dev",
            "calibration",
            "external-holdout",
            "second-model",
            "locked-test",
            "robustness",
        ),
        default="dev",
    )
    run.add_argument(
        "--dependency-lock",
        default="requirements-inference.lock.txt",
    )
    run.add_argument(
        "--environment-manifest",
        default=os.environ.get("NLDB_ENVIRONMENT_MANIFEST"),
    )
    run.add_argument("--locked-config")
    run.add_argument("--go-decision")
    run.add_argument("--final-protocol")
    run.add_argument("--allow-locked-test-rerun", action="store_true")
    run.add_argument(
        "--v2-source-path",
        default=os.environ.get("NLDB_V2_SOURCE"),
    )
    run.add_argument(
        "--reuse-raw-generations",
        help=(
            "Deterministically reprocess an existing raw_generations.jsonl "
            "file. The command fails closed unless every selected sample is "
            "present and prompt_sha256 matches the current method prompt."
        ),
    )
    run.set_defaults(func=command_run_method)

    compare_runs = subparsers.add_parser("compare-runs")
    compare_runs.add_argument("--left", required=True)
    compare_runs.add_argument("--right", required=True)
    compare_runs.add_argument("--data", required=True)
    compare_runs.add_argument("--metric", default="target_state_correct")
    compare_runs.add_argument("--bootstrap-iterations", type=int, default=10000)
    compare_runs.add_argument("--seed", type=int, default=13)
    compare_runs.add_argument("--output", required=True)
    compare_runs.add_argument("--exploratory-dev", action="store_true")
    compare_runs.set_defaults(func=command_compare_runs)

    summarize = subparsers.add_parser("summarize-run")
    summarize.add_argument("--evaluation", required=True)
    summarize.add_argument("--metrics-output", required=True)
    summarize.add_argument("--error-output", required=True)
    summarize.set_defaults(func=command_summarize_run)

    added = subparsers.add_parser("review-added-samples")
    added.add_argument("--data", required=True)
    added.add_argument("--added-ids", required=True)
    added.add_argument("--dev-ids", required=True)
    added.add_argument("--profile-dir", required=True)
    added.add_argument("--db-root", required=True)
    added.add_argument("--output", required=True)
    added.set_defaults(func=command_review_added_samples)

    pilot = subparsers.add_parser("select-dev-pilot")
    pilot.add_argument("--data", required=True)
    pilot.add_argument("--dev-ids", required=True)
    pilot.add_argument("--output-ids", required=True)
    pilot.add_argument("--output-manifest", required=True)
    pilot.add_argument("--sample-count", type=int, default=120)
    pilot.add_argument("--seed", type=int, default=42)
    pilot.add_argument("--max-per-source-group", type=int, default=2)
    pilot.set_defaults(func=command_select_dev_pilot)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
