from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from nldbwrite_v3.analysis.exploratory_v2_4 import run_exploratory_v2_4
from nldbwrite_v3.analysis.reporting_v2_3 import reproduce


def _portable_display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reproduce the corrective v2.3 reports and post-hoc v2.4 "
            "analyses from immutable artifacts; no model inference is run."
        )
    )
    parser.add_argument("--artifact", default="final_release", choices=["final_release"])
    parser.add_argument("--workspace-root")
    parser.add_argument("--output-root")
    parser.add_argument(
        "--config",
        help="Release config JSON (default: <workspace>/release_config.json).",
    )
    parser.add_argument(
        "--keep-temp-on-failure",
        action="store_true",
        help="Keep exploratory work files if that stage raises an exception.",
    )
    parser.add_argument(
        "--stage",
        choices=["all", "corrective", "exploratory"],
        default="all",
        help="Run the full release or one deterministic reporting group.",
    )
    args = parser.parse_args()
    workspace = (
        Path(args.workspace_root).resolve()
        if args.workspace_root
        else Path(__file__).resolve().parent
    )
    output_root = (
        Path(args.output_root).resolve()
        if args.output_root
        else workspace / "04_results" / "03_analysis_work"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    config_path = (
        Path(args.config).resolve()
        if args.config
        else workspace / "release_config.json"
    )
    release_config = json.loads(config_path.read_text(encoding="utf-8"))
    if release_config.get("canonical_source_root") != ".":
        raise ValueError("release_config canonical_source_root must be '.'")
    if release_config.get("gpu_required_for_reporting") is not False:
        raise ValueError("release_config must declare CPU-only reporting")
    corrective_dir = Path(
        str(release_config["corrective_reporting_output"])
    ).name
    exploratory_dir = Path(
        str(release_config["exploratory_reporting_output"])
    ).name
    timings: dict[str, dict[str, object]] = {}

    def run_stage(number: int, name: str, callback):
        print(f"[{number}/4] {name}...", flush=True)
        started = time.perf_counter()
        value = callback()
        timings[name] = {
            "status": "pass",
            "seconds": round(time.perf_counter() - started, 6),
        }
        print(
            f"[{number}/4] {name}: PASS ({timings[name]['seconds']} s)",
            flush=True,
        )
        return value

    print("[1/4] Verifying frozen artifacts and primary anchors...", flush=True)
    corrective = None
    exploratory = None
    if args.stage in {"all", "corrective"}:
        corrective = run_stage(
            2,
            "corrective_reporting",
            lambda: reproduce(
                workspace,
                output_root / corrective_dir,
            ),
        )
    if args.stage in {"all", "exploratory"}:
        exploratory = run_stage(
            3,
            "exploratory_reporting",
            lambda: run_exploratory_v2_4(
                workspace,
                output_root / exploratory_dir,
                keep_temp_on_failure=args.keep_temp_on_failure,
            ),
        )
    print("[4/4] Writing reproduction records...", flush=True)
    result = {
        "status": "pass",
        "artifact": args.artifact,
        "gpu_required": False,
        "model_inference_rerun": False,
        "primary_results_modified": False,
        "requested_stage": args.stage,
        "release_config": _portable_display_path(config_path, workspace),
        "keep_temp_on_failure": args.keep_temp_on_failure,
        "corrective_v2_3": corrective,
        "exploratory_v2_4": (
            {
                "status": exploratory["status"],
                "samples": exploratory["samples"],
                "cascade_accuracy": exploratory["cascade_accuracy"],
                "downstream_ablation_variants": exploratory["downstream_ablation_variants"],
            }
            if exploratory is not None
            else None
        ),
    }
    record_dir = output_root / exploratory_dir
    record_dir.mkdir(parents=True, exist_ok=True)
    pass_path = record_dir / "REPRODUCTION_V2_4_PASS.json"
    pass_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    result["reproduction_record"] = _portable_display_path(
        pass_path,
        output_root,
    )
    timing_record = {
        "status": "pass",
        "requested_stage": args.stage,
        "python": sys.version,
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "stages": timings,
    }
    timing_path = output_root / "reproduction_timing.json"
    timing_path.write_text(
        json.dumps(timing_record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    result["timing_record"] = _portable_display_path(
        timing_path,
        output_root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
