from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATE = "20260826"
STAGE = "Stage6K_FROZEN_STATISTICAL_ANALYSIS"
FINAL_N = 481
BOOTSTRAP_SEED = 240824
BOOTSTRAP_REPLICATES = 10000
CLUSTER_KEY = "source_group"
CI_LEVEL = 0.95
ALPHA = 0.05
PRIMARY_METRIC = "target_state_correct"
CONFIRMATORY_HYPOTHESES = ("H1", "H2")

ARM_FILES = {
    "direct_correct": "direct.jsonl",
    "j_fs_correct": "j_fs.jsonl",
    "original_mp_fs_plus_correct": "original_mp_fs_plus.jsonl",
    "d_g1_correct": "d_g1_control.jsonl",
    "d_f_g1_correct": "d_f_g1_vnext.jsonl",
}

ARM_LABELS = {
    "direct_correct": "Direct",
    "j_fs_correct": "J-FS",
    "original_mp_fs_plus_correct": "Original MP-FS+",
    "d_g1_correct": "D+G1",
    "d_f_g1_correct": "D+F+G1",
}

STAGE6J_REQUIRED_FILES = (
    "STAGE6J_REPLAY_EVALUATION_LOCK.json",
    "REPLAY_ARM_MANIFEST.json",
    "REPLAY_EVALUATION_SUMMARY.json",
    "replay_outcomes/direct.jsonl",
    "replay_outcomes/j_fs.jsonl",
    "replay_outcomes/original_mp_fs_plus.jsonl",
    "replay_outcomes/d_g1_control.jsonl",
    "replay_outcomes/d_f_g1_vnext.jsonl",
)

STAGE6E_REQUIRED_FILES = (
    "STAGE6E_FINAL_REGISTRATION_LOCK.json",
    "artifacts/FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl",
)

STAGE6K_ARTIFACTS = (
    "FROZEN_OUTCOME_MANIFEST.json",
    "PAIRED_OUTCOME_TABLE.jsonl",
    "MCNEMAR_H1.json",
    "MCNEMAR_H2.json",
    "HOLM_CORRECTION.json",
    "CLUSTER_BOOTSTRAP.json",
    "SECONDARY_RESULTS.json",
    "REVIEWER_README.md",
    "VALIDATION_REPORT.md",
)


class Stage6KError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def safe_reset_output_dir(path: Path, *, force: bool) -> None:
    if path.exists():
        if not force:
            raise Stage6KError(f"Output directory already exists: {path}. Use --force to overwrite it.")
        resolved = path.resolve()
        allowed = (PROJECT_ROOT / "stage6_frozen_statistical_analysis").resolve()
        if resolved != allowed:
            raise Stage6KError(f"Refusing to remove unexpected output directory: {resolved}")
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def relative_hashes(root: Path, files: tuple[str, ...]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for rel in files:
        path = root / rel
        if not path.is_file():
            raise Stage6KError(f"Missing required frozen input: {path}")
        hashes[rel.replace("\\", "/")] = sha256_file(path)
    return hashes


def validate_stage6j_frozen_inputs(stage6j_dir: Path, input_hashes: dict[str, str]) -> None:
    lock = read_json(stage6j_dir / "STAGE6J_REPLAY_EVALUATION_LOCK.json")
    summary = read_json(stage6j_dir / "REPLAY_EVALUATION_SUMMARY.json")
    arm_manifest = read_json(stage6j_dir / "REPLAY_ARM_MANIFEST.json")
    if lock.get("model_called") is not False or lock.get("gpu_called") is not False:
        raise Stage6KError("Stage6J lock must record model_called=false and gpu_called=false.")
    if summary.get("model_called") is not False or summary.get("gpu_called") is not False:
        raise Stage6KError("Stage6J summary must record model_called=false and gpu_called=false.")
    if summary.get("statistics_computed") is not False or summary.get("significance_tests_computed") is not False:
        raise Stage6KError("Stage6J must remain replay-only with no significance statistics.")
    if int(summary.get("final_confirmation_n", -1)) != FINAL_N:
        raise Stage6KError("Stage6J final_confirmation_n must be 481.")
    if lock.get("arm_manifest_sha256") != input_hashes["stage6j/REPLAY_ARM_MANIFEST.json"]:
        raise Stage6KError("Stage6J lock does not match REPLAY_ARM_MANIFEST hash.")
    if lock.get("summary_sha256") != input_hashes["stage6j/REPLAY_EVALUATION_SUMMARY.json"]:
        raise Stage6KError("Stage6J lock does not match REPLAY_EVALUATION_SUMMARY hash.")
    manifest_arms = arm_manifest.get("eval_arms") or {}
    for _field, filename in ARM_FILES.items():
        arm = filename.removesuffix(".jsonl")
        if arm not in manifest_arms:
            raise Stage6KError(f"Stage6J arm manifest missing arm: {arm}")
        outcome_key = f"stage6j/replay_outcomes/{filename}"
        if manifest_arms[arm].get("outcome_sha256") != input_hashes[outcome_key]:
            raise Stage6KError(f"Stage6J arm manifest hash mismatch for {arm}.")


def load_final_manifest(stage6e_dir: Path) -> list[dict[str, Any]]:
    rows = sorted(read_jsonl(stage6e_dir / "artifacts" / "FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl"), key=lambda r: str(r["stage6_sample_id"]))
    ids = [str(row.get("stage6_sample_id")) for row in rows]
    missing_source_group = [sample_id for sample_id, row in zip(ids, rows) if not row.get(CLUSTER_KEY)]
    if len(rows) != FINAL_N:
        raise Stage6KError(f"Stage6E final manifest must contain 481 rows, got {len(rows)}.")
    if len(set(ids)) != FINAL_N:
        raise Stage6KError("Stage6E final manifest contains duplicate sample IDs.")
    if missing_source_group:
        raise Stage6KError(f"Stage6E final manifest has missing source_group values, first={missing_source_group[0]}.")
    return rows


def load_outcome_map(path: Path, expected_ids: set[str]) -> dict[str, dict[str, Any]]:
    rows = read_jsonl(path)
    ids = [str(row.get("stage6_sample_id")) for row in rows]
    if len(rows) != FINAL_N:
        raise Stage6KError(f"Outcome file must contain 481 rows: {path}")
    if len(set(ids)) != FINAL_N:
        raise Stage6KError(f"Outcome file contains duplicate sample IDs: {path}")
    actual_ids = set(ids)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)[:3]
        extra = sorted(actual_ids - expected_ids)[:3]
        raise Stage6KError(f"Outcome IDs do not match Stage6E denominator: {path}; missing={missing}; extra={extra}")
    for row in rows:
        if not isinstance(row.get(PRIMARY_METRIC), bool):
            raise Stage6KError(f"Outcome target_state_correct must be boolean: {path}:{row.get('stage6_sample_id')}")
    return {str(row["stage6_sample_id"]): row for row in rows}


def build_paired_table(stage6j_dir: Path, stage6e_dir: Path) -> list[dict[str, Any]]:
    samples = load_final_manifest(stage6e_dir)
    expected_ids = {str(row["stage6_sample_id"]) for row in samples}
    outcomes = {
        field: load_outcome_map(stage6j_dir / "replay_outcomes" / filename, expected_ids)
        for field, filename in ARM_FILES.items()
    }
    paired: list[dict[str, Any]] = []
    for sample in samples:
        sample_id = str(sample["stage6_sample_id"])
        row = {
            "stage6_sample_id": sample_id,
            "source_group": str(sample[CLUSTER_KEY]),
        }
        for field in ARM_FILES:
            row[field] = bool(outcomes[field][sample_id][PRIMARY_METRIC])
        paired.append(row)
    return paired


def validate_paired_table(rows: list[dict[str, Any]], expected_ids: set[str]) -> None:
    ids = [str(row.get("stage6_sample_id")) for row in rows]
    if len(rows) != FINAL_N:
        raise Stage6KError(f"Paired table row count must be 481, got {len(rows)}.")
    if len(set(ids)) != FINAL_N:
        raise Stage6KError("Paired table contains duplicate stage6_sample_id values.")
    if set(ids) != expected_ids:
        raise Stage6KError("Paired table ID set does not exactly match Stage6E final denominator.")
    if any(not row.get("source_group") for row in rows):
        raise Stage6KError("Paired table contains missing source_group.")
    for field in ARM_FILES:
        if any(not isinstance(row.get(field), bool) for row in rows):
            raise Stage6KError(f"Paired table contains non-boolean field: {field}")


def mcnemar(a_values: list[bool], b_values: list[bool], *, hypothesis: str, arm_a: str, arm_b: str) -> dict[str, Any]:
    n11 = sum(1 for a, b in zip(a_values, b_values) if a and b)
    n10 = sum(1 for a, b in zip(a_values, b_values) if a and not b)
    n01 = sum(1 for a, b in zip(a_values, b_values) if not a and b)
    n00 = sum(1 for a, b in zip(a_values, b_values) if not a and not b)
    discordant = n10 + n01
    if discordant == 0:
        p_value = 1.0
        degenerate = True
    else:
        tail = sum(math.comb(discordant, k) for k in range(0, min(n10, n01) + 1)) / (2**discordant)
        p_value = min(1.0, 2.0 * tail)
        degenerate = False
    return {
        "stage": STAGE,
        "hypothesis": hypothesis,
        "primary_metric": PRIMARY_METRIC,
        "arm_a": arm_a,
        "arm_b": arm_b,
        "paired_n": len(a_values),
        "contingency": {
            "n11_a_correct_b_correct": n11,
            "n10_a_correct_b_incorrect": n10,
            "n01_a_incorrect_b_correct": n01,
            "n00_a_incorrect_b_incorrect": n00,
        },
        "discordant_pairs": discordant,
        "degenerate_no_discordant_pairs": degenerate,
        "raw_p_value": p_value,
        "reject": p_value <= ALPHA,
        "alpha": ALPHA,
        "test": "exact_two_sided_mcnemar",
        "zero_discordant_convention": "p_value=1.0",
    }


def holm(results: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(results, key=lambda row: (float(row["raw_p_value"]), str(row["hypothesis"])))
    adjusted_by_hypothesis: dict[str, float] = {}
    running = 0.0
    m = len(ordered)
    for rank, row in enumerate(ordered, start=1):
        adjusted = min(1.0, (m - rank + 1) * float(row["raw_p_value"]))
        running = max(running, adjusted)
        adjusted_by_hypothesis[str(row["hypothesis"])] = running
    family = []
    for row in results:
        hypothesis = str(row["hypothesis"])
        family.append(
            {
                "hypothesis": hypothesis,
                "comparison": f"{row['arm_a']} vs {row['arm_b']}",
                "raw_p_value": float(row["raw_p_value"]),
                "holm_adjusted_p_value": adjusted_by_hypothesis[hypothesis],
                "reject": adjusted_by_hypothesis[hypothesis] <= ALPHA,
            }
        )
    return {
        "stage": STAGE,
        "method": "Holm-Bonferroni",
        "alpha": ALPHA,
        "confirmatory_family": list(CONFIRMATORY_HYPOTHESES),
        "family_size": len(CONFIRMATORY_HYPOTHESES),
        "results": family,
    }


def paired_accuracy_difference(rows: list[dict[str, Any]], arm_a_field: str, arm_b_field: str) -> float:
    if not rows:
        raise Stage6KError("Cannot compute accuracy difference with zero rows.")
    return sum((1 if row[arm_a_field] else 0) - (1 if row[arm_b_field] else 0) for row in rows) / len(rows)


def percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise Stage6KError("Cannot compute percentile over empty values.")
    position = (len(sorted_values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def cluster_bootstrap(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row[CLUSTER_KEY])].append(row)
    group_names = sorted(groups)
    rng = random.Random(BOOTSTRAP_SEED)
    specs = {
        "H1": ("d_f_g1_correct", "original_mp_fs_plus_correct", "D+F+G1", "Original MP-FS+"),
        "H2": ("d_f_g1_correct", "d_g1_correct", "D+F+G1", "D+G1"),
    }
    values: dict[str, list[float]] = {key: [] for key in specs}
    for _ in range(BOOTSTRAP_REPLICATES):
        sampled_rows: list[dict[str, Any]] = []
        for group_name in rng.choices(group_names, k=len(group_names)):
            sampled_rows.extend(groups[group_name])
        for hypothesis, (left, right, _left_label, _right_label) in specs.items():
            values[hypothesis].append(paired_accuracy_difference(sampled_rows, left, right))
    results = []
    for hypothesis, (left, right, left_label, right_label) in specs.items():
        observed = paired_accuracy_difference(rows, left, right)
        sorted_values = sorted(values[hypothesis])
        results.append(
            {
                "hypothesis": hypothesis,
                "estimand": f"Accuracy({left_label}) - Accuracy({right_label})",
                "observed_difference": observed,
                "observed_difference_percentage_points": observed * 100.0,
                "ci_lower": percentile(sorted_values, (1.0 - CI_LEVEL) / 2.0),
                "ci_upper": percentile(sorted_values, 1.0 - (1.0 - CI_LEVEL) / 2.0),
                "ci_lower_percentage_points": percentile(sorted_values, (1.0 - CI_LEVEL) / 2.0) * 100.0,
                "ci_upper_percentage_points": percentile(sorted_values, 1.0 - (1.0 - CI_LEVEL) / 2.0) * 100.0,
            }
        )
    return {
        "stage": STAGE,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "cluster_key": CLUSTER_KEY,
        "cluster_count": len(group_names),
        "ci_level": CI_LEVEL,
        "resampling_unit": "source_group",
        "results": results,
    }


def secondary_results(rows: list[dict[str, Any]], stage6j_dir: Path) -> dict[str, Any]:
    arm_results = []
    summary = read_json(stage6j_dir / "REPLAY_EVALUATION_SUMMARY.json")
    for field in ARM_FILES:
        correct = sum(1 for row in rows if row[field])
        arm_name = ARM_LABELS[field]
        stage6j_arm = ARM_FILES[field].removesuffix(".jsonl")
        stage6j_summary = (summary.get("arms") or {}).get(stage6j_arm) or {}
        arm_results.append(
            {
                "arm": arm_name,
                "paired_table_field": field,
                "correct": correct,
                "n": len(rows),
                "accuracy": correct / len(rows),
                "accuracy_percent": correct * 100.0 / len(rows),
                "failure_stage_counts": stage6j_summary.get("failure_stage_counts", {}),
                "parse_status_counts": stage6j_summary.get("parse_status_counts", {}),
                "verification_status_counts": stage6j_summary.get("verification_status_counts", {}),
            }
        )
    return {
        "stage": STAGE,
        "status": "DESCRIPTIVE_ONLY",
        "primary_metric": PRIMARY_METRIC,
        "n": len(rows),
        "no_secondary_p_values": True,
        "arms": arm_results,
    }


def build_input_hashes(stage6j_dir: Path, stage6e_dir: Path) -> dict[str, str]:
    stage6j_hashes = {f"stage6j/{key}": value for key, value in relative_hashes(stage6j_dir, STAGE6J_REQUIRED_FILES).items()}
    stage6e_hashes = {f"stage6e/{key}": value for key, value in relative_hashes(stage6e_dir, STAGE6E_REQUIRED_FILES).items()}
    return {**stage6j_hashes, **stage6e_hashes}


def reviewer_readme() -> str:
    return f"""# Stage6K Frozen Statistical Analysis

This package closes the V1 confirmatory statistical analysis from frozen Stage6J replay outcomes.

The final reviewer ZIP is self-contained for Stage6K validation: it includes the minimal frozen Stage6J replay outcomes and Stage6E final denominator needed by the validator and tests.

Scope:
- No model calls.
- No GPU calls.
- No new generations.
- No gold, denominator, metric, or hypothesis changes.
- Confirmatory family contains only H1 and H2.

Commands:
```bash
python scripts/data/build_stage6k_frozen_statistics.py --force
python scripts/data/validate_stage6k_frozen_statistics.py
python -m pytest -q tests/test_stage6k_frozen_statistics.py
```

Primary metric: `{PRIMARY_METRIC}`

Confirmatory hypotheses:
- H1: D+F+G1 vs Original MP-FS+
- H2: D+F+G1 vs D+G1

Bootstrap protocol:
- cluster key: `{CLUSTER_KEY}`
- seed: `{BOOTSTRAP_SEED}`
- replicates: `{BOOTSTRAP_REPLICATES}`
- CI level: `{CI_LEVEL}`
"""


def pending_validation_report_text() -> str:
    return f"""# Stage6K Validation Report

Status: PENDING_VALIDATION

Frozen protocol checks:
- final_n: {FINAL_N}
- primary_metric: {PRIMARY_METRIC}
- confirmatory_family: H1, H2 only
- bootstrap_seed: {BOOTSTRAP_SEED}
- bootstrap_replicates: {BOOTSTRAP_REPLICATES}
- cluster_key: {CLUSTER_KEY}
- model_called: false
- gpu_called: false

This placeholder is written by the builder before the independent validator runs. Run `python scripts/data/validate_stage6k_frozen_statistics.py` to create the final validation report from actual validator execution.
"""


def stage6k_lock(output_dir: Path, input_hashes: dict[str, str]) -> dict[str, Any]:
    artifact_hashes = {rel: sha256_file(output_dir / rel) for rel in STAGE6K_ARTIFACTS}
    return {
        "stage": STAGE,
        "status": "PASS_FROZEN_STATISTICAL_ANALYSIS_LOCKED",
        "date": DATE,
        "final_n": FINAL_N,
        "primary_metric": PRIMARY_METRIC,
        "confirmatory_hypotheses": list(CONFIRMATORY_HYPOTHESES),
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "cluster_key": CLUSTER_KEY,
        "ci_level": CI_LEVEL,
        "artifact_hashes": artifact_hashes,
        "upstream_dependency_hashes": {
            "stage6j/STAGE6J_REPLAY_EVALUATION_LOCK.json": input_hashes["stage6j/STAGE6J_REPLAY_EVALUATION_LOCK.json"],
            "stage6e/STAGE6E_FINAL_REGISTRATION_LOCK.json": input_hashes["stage6e/STAGE6E_FINAL_REGISTRATION_LOCK.json"],
        },
        "input_hashes": input_hashes,
        "model_called": False,
        "gpu_called": False,
        "raw_generation_rewritten": False,
        "gold_changed": False,
        "sample_exclusion_added": False,
        "new_hypothesis_added": False,
    }


def build_stage6k(stage6j_dir: Path, stage6e_dir: Path, output_dir: Path, *, force: bool = False) -> dict[str, Any]:
    safe_reset_output_dir(output_dir, force=force)
    input_hashes = build_input_hashes(stage6j_dir, stage6e_dir)
    validate_stage6j_frozen_inputs(stage6j_dir, input_hashes)
    paired = build_paired_table(stage6j_dir, stage6e_dir)
    expected_ids = {str(row["stage6_sample_id"]) for row in load_final_manifest(stage6e_dir)}
    validate_paired_table(paired, expected_ids)

    h1 = mcnemar(
        [row["d_f_g1_correct"] for row in paired],
        [row["original_mp_fs_plus_correct"] for row in paired],
        hypothesis="H1",
        arm_a="D+F+G1",
        arm_b="Original MP-FS+",
    )
    h2 = mcnemar(
        [row["d_f_g1_correct"] for row in paired],
        [row["d_g1_correct"] for row in paired],
        hypothesis="H2",
        arm_a="D+F+G1",
        arm_b="D+G1",
    )
    holm_result = holm([h1, h2])
    bootstrap = cluster_bootstrap(paired)
    secondary = secondary_results(paired, stage6j_dir)
    source_groups = Counter(str(row[CLUSTER_KEY]) for row in paired)
    manifest = {
        "stage": "Stage6K",
        "stage_name": STAGE,
        "status": "FROZEN_OUTCOMES_LOCKED",
        "date": DATE,
        "final_n": FINAL_N,
        "input_stage": "Stage6J",
        "primary_metric": PRIMARY_METRIC,
        "confirmatory_hypotheses": list(CONFIRMATORY_HYPOTHESES),
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "cluster_key": CLUSTER_KEY,
        "ci_level": CI_LEVEL,
        "input_hashes": input_hashes,
        "paired_table": {
            "row_count": len(paired),
            "unique_stage6_sample_id_count": len({row["stage6_sample_id"] for row in paired}),
            "missing_source_group_count": sum(1 for row in paired if not row.get(CLUSTER_KEY)),
            "duplicate_stage6_sample_id_count": len(paired) - len({row["stage6_sample_id"] for row in paired}),
            "source_group_count": len(source_groups),
        },
        "model_called": False,
        "gpu_called": False,
    }

    write_json(output_dir / "FROZEN_OUTCOME_MANIFEST.json", manifest)
    write_jsonl(output_dir / "PAIRED_OUTCOME_TABLE.jsonl", paired)
    write_json(output_dir / "MCNEMAR_H1.json", h1)
    write_json(output_dir / "MCNEMAR_H2.json", h2)
    write_json(output_dir / "HOLM_CORRECTION.json", holm_result)
    write_json(output_dir / "CLUSTER_BOOTSTRAP.json", bootstrap)
    write_json(output_dir / "SECONDARY_RESULTS.json", secondary)
    (output_dir / "REVIEWER_README.md").write_text(reviewer_readme(), encoding="utf-8")
    (output_dir / "VALIDATION_REPORT.md").write_text(pending_validation_report_text(), encoding="utf-8")
    lock = stage6k_lock(output_dir, input_hashes)
    write_json(output_dir / "STAGE6K_STATISTICAL_LOCK.json", lock)
    return {
        "status": "PASS",
        "stage": STAGE,
        "final_n": len(paired),
        "mcnemar_h1": h1,
        "mcnemar_h2": h2,
        "holm": holm_result,
        "bootstrap": bootstrap,
        "output_dir": str(output_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage6j-dir", type=Path, default=PROJECT_ROOT / "stage6_replay_evaluation")
    parser.add_argument("--stage6e-dir", type=Path, default=PROJECT_ROOT / "stage6_final_registration_revision")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "stage6_frozen_statistical_analysis")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = build_stage6k(args.stage6j_dir, args.stage6e_dir, args.output_dir, force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
