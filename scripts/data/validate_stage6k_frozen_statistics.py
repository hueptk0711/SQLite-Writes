from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
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

STAGE6K_REQUIRED_FILES = (
    "FROZEN_OUTCOME_MANIFEST.json",
    "PAIRED_OUTCOME_TABLE.jsonl",
    "MCNEMAR_H1.json",
    "MCNEMAR_H2.json",
    "HOLM_CORRECTION.json",
    "CLUSTER_BOOTSTRAP.json",
    "SECONDARY_RESULTS.json",
    "STAGE6K_STATISTICAL_LOCK.json",
    "REVIEWER_README.md",
    "VALIDATION_REPORT.md",
)

STAGE6K_HASHED_ARTIFACTS = tuple(rel for rel in STAGE6K_REQUIRED_FILES if rel != "STAGE6K_STATISTICAL_LOCK.json")


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


def relative_hashes(root: Path, files: tuple[str, ...], violations: list[str], prefix: str) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for rel in files:
        path = root / rel
        key = f"{prefix}/{rel.replace('\\', '/')}"
        if not path.is_file():
            violations.append(f"missing_required_input:{key}")
            continue
        hashes[key] = sha256_file(path)
    return hashes


def load_final_manifest(stage6e_dir: Path, violations: list[str]) -> list[dict[str, Any]]:
    path = stage6e_dir / "artifacts" / "FINAL_CONFIRMATION_SAMPLE_MANIFEST.jsonl"
    if not path.is_file():
        violations.append("missing_stage6e_final_manifest")
        return []
    rows = sorted(read_jsonl(path), key=lambda row: str(row.get("stage6_sample_id")))
    ids = [str(row.get("stage6_sample_id")) for row in rows]
    if len(rows) != FINAL_N:
        violations.append(f"stage6e_final_manifest_row_count:{len(rows)}")
    if len(set(ids)) != len(ids):
        violations.append("stage6e_final_manifest_duplicate_ids")
    if len(set(ids)) != FINAL_N:
        violations.append(f"stage6e_final_manifest_unique_id_count:{len(set(ids))}")
    if any(not row.get(CLUSTER_KEY) for row in rows):
        violations.append("stage6e_final_manifest_missing_source_group")
    return rows


def load_outcome_rows(stage6j_dir: Path, field: str, expected_ids: set[str], violations: list[str]) -> dict[str, dict[str, Any]]:
    filename = ARM_FILES[field]
    path = stage6j_dir / "replay_outcomes" / filename
    if not path.is_file():
        violations.append(f"missing_stage6j_outcome:{filename}")
        return {}
    rows = read_jsonl(path)
    ids = [str(row.get("stage6_sample_id")) for row in rows]
    if len(rows) != FINAL_N:
        violations.append(f"stage6j_outcome_row_count:{filename}:{len(rows)}")
    if len(set(ids)) != len(ids):
        violations.append(f"stage6j_outcome_duplicate_ids:{filename}")
    actual_ids = set(ids)
    if expected_ids and actual_ids != expected_ids:
        violations.append(f"stage6j_outcome_id_set_mismatch:{filename}")
    for row in rows:
        sample_id = str(row.get("stage6_sample_id"))
        if not isinstance(row.get(PRIMARY_METRIC), bool):
            violations.append(f"stage6j_outcome_metric_not_bool:{filename}:{sample_id}")
            break
    return {str(row["stage6_sample_id"]): row for row in rows if "stage6_sample_id" in row}


def rebuild_paired_table(stage6j_dir: Path, stage6e_dir: Path, violations: list[str]) -> list[dict[str, Any]]:
    samples = load_final_manifest(stage6e_dir, violations)
    expected_ids = {str(row.get("stage6_sample_id")) for row in samples}
    outcomes = {field: load_outcome_rows(stage6j_dir, field, expected_ids, violations) for field in ARM_FILES}
    rebuilt: list[dict[str, Any]] = []
    for sample in samples:
        sample_id = str(sample.get("stage6_sample_id"))
        row: dict[str, Any] = {
            "stage6_sample_id": sample_id,
            "source_group": str(sample.get(CLUSTER_KEY) or ""),
        }
        for field in ARM_FILES:
            outcome = outcomes[field].get(sample_id)
            if outcome is None:
                violations.append(f"missing_stage6j_outcome_row:{field}:{sample_id}")
                row[field] = False
            else:
                row[field] = bool(outcome.get(PRIMARY_METRIC))
        rebuilt.append(row)
    return rebuilt


def validate_saved_paired_table(rows: list[dict[str, Any]], expected_ids: set[str], violations: list[str]) -> None:
    ids = [str(row.get("stage6_sample_id")) for row in rows]
    if len(rows) != FINAL_N:
        violations.append(f"paired_table_row_count:{len(rows)}")
    if len(set(ids)) != len(ids):
        violations.append("paired_table_duplicate_ids")
    if len(set(ids)) != FINAL_N:
        violations.append(f"paired_table_unique_id_count:{len(set(ids))}")
    actual_ids = set(ids)
    if actual_ids != expected_ids:
        missing = len(expected_ids - actual_ids)
        extra = len(actual_ids - expected_ids)
        violations.append(f"paired_table_id_set_mismatch:missing={missing}:extra={extra}")
    if any(not row.get("source_group") for row in rows):
        violations.append("paired_table_missing_source_group")
    for field in ARM_FILES:
        if any(not isinstance(row.get(field), bool) for row in rows):
            violations.append(f"paired_table_non_boolean:{field}")


def mcnemar_recompute(a_values: list[bool], b_values: list[bool], hypothesis: str, arm_a: str, arm_b: str) -> dict[str, Any]:
    n11 = n10 = n01 = n00 = 0
    for a_correct, b_correct in zip(a_values, b_values):
        if a_correct and b_correct:
            n11 += 1
        elif a_correct and not b_correct:
            n10 += 1
        elif not a_correct and b_correct:
            n01 += 1
        else:
            n00 += 1
    discordant = n10 + n01
    if discordant == 0:
        p_value = 1.0
        degenerate = True
    else:
        smaller = min(n10, n01)
        mass = 0.0
        for k in range(smaller + 1):
            mass += math.comb(discordant, k) / (2**discordant)
        p_value = min(1.0, 2.0 * mass)
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


def holm_recompute(mcnemar_results: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(mcnemar_results, key=lambda row: (float(row["raw_p_value"]), str(row["hypothesis"])))
    running_adjusted = 0.0
    adjusted: dict[str, float] = {}
    for rank, row in enumerate(ordered, start=1):
        candidate = min(1.0, (len(ordered) - rank + 1) * float(row["raw_p_value"]))
        running_adjusted = max(running_adjusted, candidate)
        adjusted[str(row["hypothesis"])] = running_adjusted
    rows = []
    for row in mcnemar_results:
        hypothesis = str(row["hypothesis"])
        rows.append(
            {
                "hypothesis": hypothesis,
                "comparison": f"{row['arm_a']} vs {row['arm_b']}",
                "raw_p_value": float(row["raw_p_value"]),
                "holm_adjusted_p_value": adjusted[hypothesis],
                "reject": adjusted[hypothesis] <= ALPHA,
            }
        )
    return {
        "stage": STAGE,
        "method": "Holm-Bonferroni",
        "alpha": ALPHA,
        "confirmatory_family": list(CONFIRMATORY_HYPOTHESES),
        "family_size": len(CONFIRMATORY_HYPOTHESES),
        "results": rows,
    }


def accuracy_delta(rows: list[dict[str, Any]], left: str, right: str) -> float:
    return sum((1 if row[left] else 0) - (1 if row[right] else 0) for row in rows) / len(rows)


def percentile(sorted_values: list[float], q: float) -> float:
    position = (len(sorted_values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    return sorted_values[lower] * (upper - position) + sorted_values[upper] * (position - lower)


def cluster_bootstrap_recompute(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row[CLUSTER_KEY])].append(row)
    group_names = sorted(groups)
    specs = {
        "H1": ("d_f_g1_correct", "original_mp_fs_plus_correct", "D+F+G1", "Original MP-FS+"),
        "H2": ("d_f_g1_correct", "d_g1_correct", "D+F+G1", "D+G1"),
    }
    rng = random.Random(BOOTSTRAP_SEED)
    replicate_values: dict[str, list[float]] = {hypothesis: [] for hypothesis in specs}
    for _ in range(BOOTSTRAP_REPLICATES):
        sampled: list[dict[str, Any]] = []
        for group_name in rng.choices(group_names, k=len(group_names)):
            sampled.extend(groups[group_name])
        for hypothesis, (left, right, _left_label, _right_label) in specs.items():
            replicate_values[hypothesis].append(accuracy_delta(sampled, left, right))
    results = []
    for hypothesis, (left, right, left_label, right_label) in specs.items():
        sorted_values = sorted(replicate_values[hypothesis])
        lower = percentile(sorted_values, (1.0 - CI_LEVEL) / 2.0)
        upper = percentile(sorted_values, 1.0 - (1.0 - CI_LEVEL) / 2.0)
        observed = accuracy_delta(rows, left, right)
        results.append(
            {
                "hypothesis": hypothesis,
                "estimand": f"Accuracy({left_label}) - Accuracy({right_label})",
                "observed_difference": observed,
                "observed_difference_percentage_points": observed * 100.0,
                "ci_lower": lower,
                "ci_upper": upper,
                "ci_lower_percentage_points": lower * 100.0,
                "ci_upper_percentage_points": upper * 100.0,
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


def secondary_recompute(rows: list[dict[str, Any]], stage6j_dir: Path) -> dict[str, Any]:
    labels = {
        "direct_correct": "Direct",
        "j_fs_correct": "J-FS",
        "original_mp_fs_plus_correct": "Original MP-FS+",
        "d_g1_correct": "D+G1",
        "d_f_g1_correct": "D+F+G1",
    }
    summary = read_json(stage6j_dir / "REPLAY_EVALUATION_SUMMARY.json")
    arm_rows = []
    for field, filename in ARM_FILES.items():
        correct = sum(1 for row in rows if row[field])
        stage6j_arm = filename.removesuffix(".jsonl")
        stage6j_summary = (summary.get("arms") or {}).get(stage6j_arm) or {}
        arm_rows.append(
            {
                "arm": labels[field],
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
        "arms": arm_rows,
    }


def validate_stage6j_protocol(stage6j_dir: Path, input_hashes: dict[str, str], violations: list[str]) -> None:
    try:
        lock = read_json(stage6j_dir / "STAGE6J_REPLAY_EVALUATION_LOCK.json")
        summary = read_json(stage6j_dir / "REPLAY_EVALUATION_SUMMARY.json")
        arm_manifest = read_json(stage6j_dir / "REPLAY_ARM_MANIFEST.json")
    except FileNotFoundError:
        violations.append("stage6j_protocol_files_missing")
        return
    if lock.get("model_called") is not False or summary.get("model_called") is not False:
        violations.append("model_call_not_frozen_false")
    if lock.get("gpu_called") is not False or summary.get("gpu_called") is not False:
        violations.append("gpu_call_not_frozen_false")
    if summary.get("statistics_computed") is not False or summary.get("significance_tests_computed") is not False:
        violations.append("stage6j_statistics_already_computed")
    if lock.get("arm_manifest_sha256") != input_hashes.get("stage6j/REPLAY_ARM_MANIFEST.json"):
        violations.append("stage6j_lock_arm_manifest_hash_mismatch")
    if lock.get("summary_sha256") != input_hashes.get("stage6j/REPLAY_EVALUATION_SUMMARY.json"):
        violations.append("stage6j_lock_summary_hash_mismatch")
    manifest_arms = arm_manifest.get("eval_arms") or {}
    for filename in ARM_FILES.values():
        arm = filename.removesuffix(".jsonl")
        expected = input_hashes.get(f"stage6j/replay_outcomes/{filename}")
        if (manifest_arms.get(arm) or {}).get("outcome_sha256") != expected:
            violations.append(f"stage6j_arm_manifest_outcome_hash_mismatch:{arm}")


def compare(name: str, saved: Any, recomputed: Any, violations: list[str]) -> None:
    if saved != recomputed:
        violations.append(f"{name}_recompute_mismatch")


def contains_forbidden_p_value_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"p_value", "raw_p_value", "adjusted_p_value", "holm_adjusted_p_value"}:
                return True
            if contains_forbidden_p_value_key(item):
                return True
    elif isinstance(value, list):
        return any(contains_forbidden_p_value_key(item) for item in value)
    return False


def validation_report_text(report: dict[str, Any]) -> str:
    violations_json = json.dumps(report.get("violations", []), ensure_ascii=False, sort_keys=True)
    return f"""# Stage6K Validation Report

Status: {report["status"]}

violations: {violations_json}

final_n: {report["final_n"]}
paired_table_recomputed: {str(report["paired_table_recomputed"]).lower()}
mcnemar_h1_recomputed: {str(report["mcnemar_h1_recomputed"]).lower()}
mcnemar_h2_recomputed: {str(report["mcnemar_h2_recomputed"]).lower()}
holm_recomputed: {str(report["holm_recomputed"]).lower()}
cluster_bootstrap_recomputed: {str(report["cluster_bootstrap_recomputed"]).lower()}
secondary_results_recomputed: {str(report["secondary_results_recomputed"]).lower()}

model_called: {str(report["model_called"]).lower()}
gpu_called: {str(report["gpu_called"]).lower()}

bootstrap_replicates: {report["bootstrap_replicates"]}
bootstrap_seed: {report["bootstrap_seed"]}
cluster_key: {report["cluster_key"]}
confirmatory_hypotheses: {", ".join(report["confirmatory_hypotheses"])}
"""


def write_validation_report_and_update_lock(output_dir: Path, report: dict[str, Any]) -> None:
    report_path = output_dir / "VALIDATION_REPORT.md"
    report_path.write_text(validation_report_text(report), encoding="utf-8")
    lock_path = output_dir / "STAGE6K_STATISTICAL_LOCK.json"
    if not lock_path.is_file():
        return
    lock = read_json(lock_path)
    artifact_hashes = dict(lock.get("artifact_hashes") or {})
    artifact_hashes["VALIDATION_REPORT.md"] = sha256_file(report_path)
    lock["artifact_hashes"] = artifact_hashes
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate(output_dir: Path, stage6j_dir: Path | None = None, stage6e_dir: Path | None = None) -> dict[str, Any]:
    stage6j_dir = stage6j_dir or PROJECT_ROOT / "stage6_replay_evaluation"
    stage6e_dir = stage6e_dir or PROJECT_ROOT / "stage6_final_registration_revision"
    violations: list[str] = []
    recompute_checks = {
        "paired_table_recomputed": False,
        "mcnemar_h1_recomputed": False,
        "mcnemar_h2_recomputed": False,
        "holm_recomputed": False,
        "cluster_bootstrap_recomputed": False,
        "secondary_results_recomputed": False,
    }

    for rel in STAGE6K_REQUIRED_FILES:
        if not (output_dir / rel).is_file():
            violations.append(f"missing_stage6k_artifact:{rel}")
    if violations:
        return {
            "status": "FAIL",
            "violations": violations,
            "stage": STAGE,
            "final_n": FINAL_N,
            "confirmatory_hypotheses": list(CONFIRMATORY_HYPOTHESES),
            "bootstrap_seed": BOOTSTRAP_SEED,
            "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            "cluster_key": CLUSTER_KEY,
            "model_called": False,
            "gpu_called": False,
            **recompute_checks,
        }

    input_hashes = {
        **relative_hashes(stage6j_dir, STAGE6J_REQUIRED_FILES, violations, "stage6j"),
        **relative_hashes(stage6e_dir, STAGE6E_REQUIRED_FILES, violations, "stage6e"),
    }
    validate_stage6j_protocol(stage6j_dir, input_hashes, violations)

    manifest = read_json(output_dir / "FROZEN_OUTCOME_MANIFEST.json")
    lock = read_json(output_dir / "STAGE6K_STATISTICAL_LOCK.json")
    saved_paired = read_jsonl(output_dir / "PAIRED_OUTCOME_TABLE.jsonl")
    expected_ids = {str(row.get("stage6_sample_id")) for row in load_final_manifest(stage6e_dir, violations)}
    validate_saved_paired_table(saved_paired, expected_ids, violations)
    rebuilt_paired = rebuild_paired_table(stage6j_dir, stage6e_dir, violations)
    compare("paired_table", saved_paired, rebuilt_paired, violations)
    recompute_checks["paired_table_recomputed"] = True

    protocol_fields = {
        "final_n": FINAL_N,
        "primary_metric": PRIMARY_METRIC,
        "confirmatory_hypotheses": list(CONFIRMATORY_HYPOTHESES),
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "cluster_key": CLUSTER_KEY,
        "ci_level": CI_LEVEL,
    }
    for key, expected in protocol_fields.items():
        if manifest.get(key) != expected:
            violations.append(f"manifest_protocol_mismatch:{key}")
        if lock.get(key) != expected:
            violations.append(f"lock_protocol_mismatch:{key}")
    if manifest.get("input_hashes") != input_hashes:
        violations.append("manifest_input_hashes_mismatch")
    if lock.get("input_hashes") != input_hashes:
        violations.append("lock_input_hashes_mismatch")
    if lock.get("upstream_dependency_hashes") != {
        "stage6j/STAGE6J_REPLAY_EVALUATION_LOCK.json": input_hashes.get("stage6j/STAGE6J_REPLAY_EVALUATION_LOCK.json"),
        "stage6e/STAGE6E_FINAL_REGISTRATION_LOCK.json": input_hashes.get("stage6e/STAGE6E_FINAL_REGISTRATION_LOCK.json"),
    }:
        violations.append("upstream_dependency_hashes_mismatch")
    if manifest.get("model_called") is not False or lock.get("model_called") is not False:
        violations.append("stage6k_model_call_not_false")
    if manifest.get("gpu_called") is not False or lock.get("gpu_called") is not False:
        violations.append("stage6k_gpu_call_not_false")
    for frozen_flag in ("raw_generation_rewritten", "gold_changed", "sample_exclusion_added", "new_hypothesis_added"):
        if lock.get(frozen_flag) is not False:
            violations.append(f"lock_frozen_flag_not_false:{frozen_flag}")

    current_artifact_hashes = {rel: sha256_file(output_dir / rel) for rel in STAGE6K_HASHED_ARTIFACTS}
    if lock.get("artifact_hashes") != current_artifact_hashes:
        violations.append("stage6k_lock_artifact_hashes_mismatch")

    if len(rebuilt_paired) == FINAL_N and not violations:
        h1 = mcnemar_recompute(
            [row["d_f_g1_correct"] for row in rebuilt_paired],
            [row["original_mp_fs_plus_correct"] for row in rebuilt_paired],
            "H1",
            "D+F+G1",
            "Original MP-FS+",
        )
        recompute_checks["mcnemar_h1_recomputed"] = True
        h2 = mcnemar_recompute(
            [row["d_f_g1_correct"] for row in rebuilt_paired],
            [row["d_g1_correct"] for row in rebuilt_paired],
            "H2",
            "D+F+G1",
            "D+G1",
        )
        recompute_checks["mcnemar_h2_recomputed"] = True
        compare("mcnemar_h1", read_json(output_dir / "MCNEMAR_H1.json"), h1, violations)
        compare("mcnemar_h2", read_json(output_dir / "MCNEMAR_H2.json"), h2, violations)
        compare("holm", read_json(output_dir / "HOLM_CORRECTION.json"), holm_recompute([h1, h2]), violations)
        recompute_checks["holm_recomputed"] = True
        compare("cluster_bootstrap", read_json(output_dir / "CLUSTER_BOOTSTRAP.json"), cluster_bootstrap_recompute(rebuilt_paired), violations)
        recompute_checks["cluster_bootstrap_recomputed"] = True
        compare("secondary_results", read_json(output_dir / "SECONDARY_RESULTS.json"), secondary_recompute(rebuilt_paired, stage6j_dir), violations)
        recompute_checks["secondary_results_recomputed"] = True

        if h1["contingency"]["n10_a_correct_b_incorrect"] != 0 or h1["contingency"]["n01_a_incorrect_b_correct"] != 0:
            violations.append("h1_expected_zero_discordant_not_met")
        if h2["contingency"]["n10_a_correct_b_incorrect"] != 0 or h2["contingency"]["n01_a_incorrect_b_correct"] != 0:
            violations.append("h2_expected_zero_discordant_not_met")
        if h1["raw_p_value"] != 1.0 or h2["raw_p_value"] != 1.0:
            violations.append("expected_mcnemar_p_values_not_one")

    holm_saved = read_json(output_dir / "HOLM_CORRECTION.json")
    if holm_saved.get("confirmatory_family") != list(CONFIRMATORY_HYPOTHESES) or holm_saved.get("family_size") != 2:
        violations.append("confirmatory_family_not_h1_h2_only")
    secondary = read_json(output_dir / "SECONDARY_RESULTS.json")
    if secondary.get("no_secondary_p_values") is not True or contains_forbidden_p_value_key(secondary):
        violations.append("secondary_results_contains_p_value")
    bootstrap = read_json(output_dir / "CLUSTER_BOOTSTRAP.json")
    if bootstrap.get("bootstrap_replicates") != BOOTSTRAP_REPLICATES:
        violations.append("bootstrap_replicates_mismatch")
    if bootstrap.get("bootstrap_seed") != BOOTSTRAP_SEED:
        violations.append("bootstrap_seed_mismatch")
    if bootstrap.get("cluster_key") != CLUSTER_KEY:
        violations.append("bootstrap_cluster_key_mismatch")

    return {
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "stage": STAGE,
        "final_n": FINAL_N,
        "confirmatory_hypotheses": list(CONFIRMATORY_HYPOTHESES),
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "cluster_key": CLUSTER_KEY,
        "model_called": False,
        "gpu_called": False,
        **recompute_checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "stage6_frozen_statistical_analysis")
    parser.add_argument("--stage6j-dir", type=Path, default=PROJECT_ROOT / "stage6_replay_evaluation")
    parser.add_argument("--stage6e-dir", type=Path, default=PROJECT_ROOT / "stage6_final_registration_revision")
    parser.add_argument("--no-write-report", action="store_true")
    args = parser.parse_args()
    report = validate(args.output_dir, args.stage6j_dir, args.stage6e_dir)
    if not args.no_write_report:
        write_validation_report_and_update_lock(args.output_dir, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
