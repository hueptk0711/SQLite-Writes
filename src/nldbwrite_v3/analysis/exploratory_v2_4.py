from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import shutil
import statistics
import tarfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from nldbwrite_v3.compiler import compile_verified_plan, preflight_program
from nldbwrite_v3.evaluator import evaluate_candidate_sample, find_database
from nldbwrite_v3.verifier import verify_write_plan

from .common_safety_replay import (
    _extract_holdout,
    _load_jsonl,
    run_common_safety_replay,
)
from .reporting_v2_3 import (
    _discover_extracted_root,
    _discover_run_root,
    load_json,
    sha256_file,
)
from .statistics import holm_bonferroni


METHODS = {
    "D-FS-M": "d_fs_m",
    "J-FS-M": "j_fs_m",
    "S-FS-v2-M": "s_fs_v2_m",
    "MP-FS-M": "mp_fs_m",
    "MP-FS+": "mp_fs_plus",
    "Gold-MP": "gold_mp",
}
EXPLORATORY_METHODS = ("D-FS-M", "J-FS-M", "MP-FS+")
SECOND_MODEL_METHODS = {
    "D-FS-M": "d_fs_m",
    "J-FS-M": "j_fs_m",
    "MP-FS+": "mp_fs_plus",
}


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
        newline="\n",
    )


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"Cannot write empty CSV: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def wilson_interval(successes: int, trials: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if trials <= 0 or successes < 0 or successes > trials:
        raise ValueError("Wilson interval requires 0 <= successes <= trials")
    estimate = successes / trials
    z2 = z * z
    denominator = 1.0 + z2 / trials
    center = (estimate + z2 / (2.0 * trials)) / denominator
    radius = (
        z
        * math.sqrt(
            estimate * (1.0 - estimate) / trials + z2 / (4.0 * trials * trials)
        )
        / denominator
    )
    return max(0.0, center - radius), min(1.0, center + radius)


def zero_event_upper_bound(trials: int, alpha: float = 0.05) -> float:
    if trials <= 0 or not 0.0 < alpha < 1.0:
        raise ValueError("Invalid one-sided exact-binomial parameters")
    return 1.0 - alpha ** (1.0 / trials)


def exact_mcnemar_from_counts(left_only: int, right_only: int) -> float:
    discordant = left_only + right_only
    if discordant == 0:
        return 1.0
    tail = sum(
        math.comb(discordant, index)
        for index in range(min(left_only, right_only) + 1)
    ) / (2**discordant)
    return min(1.0, 2.0 * tail)


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _summary_stats(values: Iterable[Any]) -> dict[str, float | None]:
    clean = [float(value) for value in values if value is not None]
    return {
        "mean": statistics.fmean(clean) if clean else None,
        "median": statistics.median(clean) if clean else None,
        "q1": _quantile(clean, 0.25),
        "q3": _quantile(clean, 0.75),
        "p90": _quantile(clean, 0.90),
        "p95": _quantile(clean, 0.95),
    }


def _load_primary(workspace: Path) -> tuple[Path, dict[str, list[dict[str, Any]]]]:
    import_report = load_json(
        workspace / "07_reproducibility" / "server_final_run" / "IMPORT_REPORT.json"
    )
    archive = Path(str(import_report["archive"]))
    if not archive.is_file():
        archive = workspace / "04_results" / "00_incoming_from_server" / archive.name
    extracted = _discover_extracted_root(workspace, import_report, archive)
    run_root = _discover_run_root(extracted)
    rows = {
        method: _load_jsonl(run_root / slug / "evaluation.jsonl")
        for method, slug in METHODS.items()
    }
    return run_root, rows


def _off_target(row: dict[str, Any], sample: dict[str, Any]) -> tuple[bool, list[str]]:
    targets = set(sample.get("gold_tables") or [])
    strict = list(row.get("strict_mismatched_tables") or [])
    mismatches = sorted(table for table in strict if table not in targets)
    return bool(mismatches), mismatches


def build_cascade(
    common_rows: list[dict[str, Any]],
    primary_rows: dict[str, list[dict[str, Any]]],
    samples: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    common = {
        (str(row["method_id"]), str(row["sample_id"])): row
        for row in common_rows
    }
    primary = {
        method: {str(row["sample_id"]): row for row in rows}
        for method, rows in primary_rows.items()
    }
    output: list[dict[str, Any]] = []
    for sample_id in sorted(samples):
        j_gate = common[("J-FS-M", sample_id)]
        d_gate = common[("D-FS-M", sample_id)]
        if j_gate["preflight_accepted"]:
            selected = "J-FS-M"
            accepted = True
        elif d_gate["preflight_accepted"]:
            selected = "D-FS-M"
            accepted = True
        else:
            selected = None
            accepted = False
        selected_row = primary[selected][sample_id] if selected else None
        target_correct = bool(
            accepted and selected_row and selected_row.get("target_state_correct")
        )
        off_target, off_target_tables = (
            _off_target(selected_row, samples[sample_id])
            if selected_row is not None
            else (False, [])
        )
        output.append(
            {
                "sample_id": sample_id,
                "db_id": samples[sample_id]["db_id"],
                "analysis_class": "post_hoc_exploratory_structured_fallback",
                "policy": "J_then_D_common_transactional_preflight",
                "j_preflight_accepted": bool(j_gate["preflight_accepted"]),
                "d_preflight_accepted": bool(d_gate["preflight_accepted"]),
                "selected_method": selected,
                "admitted": accepted,
                "target_state_correct": target_correct,
                "false_accept": bool(accepted and not target_correct),
                "any_off_target_change": bool(accepted and off_target),
                "off_target_mismatched_tables": off_target_tables if accepted else [],
            }
        )
    total = len(output)
    admitted = sum(row["admitted"] for row in output)
    correct = sum(row["target_state_correct"] for row in output)
    false_accepts = sum(row["false_accept"] for row in output)
    off_target = sum(row["any_off_target_change"] for row in output)
    d_correct = {sid for sid, row in primary["D-FS-M"].items() if row.get("target_state_correct")}
    j_correct = {sid for sid, row in primary["J-FS-M"].items() if row.get("target_state_correct")}
    mp_correct = {sid for sid, row in primary["MP-FS+"].items() if row.get("target_state_correct")}
    cascade_correct = {row["sample_id"] for row in output if row["target_state_correct"]}
    db_rows: list[dict[str, Any]] = []
    for db_id in sorted({row["db_id"] for row in output}):
        subset = [row for row in output if row["db_id"] == db_id]
        db_rows.append(
            {
                "db_id": db_id,
                "samples": len(subset),
                "target_state_accuracy": sum(row["target_state_correct"] for row in subset) / len(subset),
                "coverage": sum(row["admitted"] for row in subset) / len(subset),
                "accepted_output_accuracy": (
                    sum(row["target_state_correct"] for row in subset)
                    / sum(row["admitted"] for row in subset)
                ),
            }
        )
    d_wins = len(cascade_correct - d_correct)
    d_losses = len(d_correct - cascade_correct)
    j_wins = len(cascade_correct - j_correct)
    j_losses = len(j_correct - cascade_correct)
    summary = {
        "analysis_id": "post_hoc_j_then_d_common_preflight_cascade_v1",
        "status": "pass",
        "analysis_class": "post_hoc_exploratory_structured_fallback",
        "predictions_modified": False,
        "model_inference_rerun": False,
        "gold_label_used_by_policy": False,
        "policy_order": ["J-FS-M", "D-FS-M", "abstain"],
        "samples": total,
        "admitted": admitted,
        "correct": correct,
        "false_accepts": false_accepts,
        "off_target_modifications": off_target,
        "coverage": admitted / total,
        "target_state_accuracy": correct / total,
        "accepted_output_accuracy": correct / admitted,
        "false_accept_rate": false_accepts / admitted,
        "abstention_rate": (total - admitted) / total,
        "representation_complementarity": {
            "d_correct": len(d_correct),
            "j_correct": len(j_correct),
            "both_correct": len(d_correct & j_correct),
            "d_only_correct": len(d_correct - j_correct),
            "j_only_correct": len(j_correct - d_correct),
            "both_wrong": total - len(d_correct | j_correct),
            "d_or_j_correct": len(d_correct | j_correct),
            "d_or_j_accuracy": len(d_correct | j_correct) / total,
            "mp_plus_unique_beyond_d_or_j": len(mp_correct - (d_correct | j_correct)),
        },
        "paired_vs_d": {
            "wins": d_wins,
            "losses": d_losses,
            "exact_mcnemar_p": exact_mcnemar_from_counts(d_wins, d_losses),
        },
        "paired_vs_j": {
            "wins": j_wins,
            "losses": j_losses,
            "exact_mcnemar_p": exact_mcnemar_from_counts(j_wins, j_losses),
        },
        "by_database": db_rows,
    }
    return summary, output


def build_uncertainty(
    primary_rows: dict[str, list[dict[str, Any]]],
    cascade: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for method in METHODS:
        evaluations = primary_rows[method]
        successes = sum(bool(row.get("target_state_correct")) for row in evaluations)
        low, high = wilson_interval(successes, len(evaluations))
        rows.append(
            {
                "analysis_family": "primary",
                "metric": "target_state_accuracy",
                "method": method,
                "successes": successes,
                "trials": len(evaluations),
                "estimate": successes / len(evaluations),
                "wilson_95_low": low,
                "wilson_95_high": high,
            }
        )
    for metric, successes, trials in (
        ("target_state_accuracy", cascade["correct"], cascade["samples"]),
        ("coverage", cascade["admitted"], cascade["samples"]),
        ("accepted_output_accuracy", cascade["correct"], cascade["admitted"]),
    ):
        low, high = wilson_interval(successes, trials)
        rows.append(
            {
                "analysis_family": "post_hoc_exploratory",
                "metric": metric,
                "method": "J->D cascade",
                "successes": successes,
                "trials": trials,
                "estimate": successes / trials,
                "wilson_95_low": low,
                "wilson_95_high": high,
            }
        )
    rows.append(
        {
            "analysis_family": "rare_event_bound",
            "metric": "off_target_event_probability_one_sided_95_upper",
            "method": "zero observed events in 300 trials",
            "successes": 0,
            "trials": 300,
            "estimate": 0.0,
            "wilson_95_low": None,
            "wilson_95_high": zero_event_upper_bound(300),
        }
    )
    return rows


def _read_tar_jsonl(bundle: tarfile.TarFile, member_name: str) -> list[dict[str, Any]]:
    member = bundle.getmember(member_name)
    handle = bundle.extractfile(member)
    if handle is None:
        raise ValueError(f"Cannot read tar member: {member_name}")
    return [json.loads(line) for line in handle.read().decode("utf-8").splitlines() if line]


def paired_model_scale(
    workspace: Path,
    primary_rows: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    archive_dir = workspace / "04_results" / "00_incoming_from_server" / "second_model_qwen14b"
    archive = archive_dir / "mp_fs_plus_second_model_qwen25_coder_14b_v1_20260801T064530Z.tar.gz"
    checksum = archive.with_name(archive.name + ".sha256")
    expected = checksum.read_text(encoding="utf-8").split()[0].lower()
    if sha256_file(archive) != expected:
        raise ValueError("Second-model archive checksum mismatch")
    seven = {
        method: {row["sample_id"]: bool(row.get("target_state_correct")) for row in primary_rows[method]}
        for method in SECOND_MODEL_METHODS
    }
    raw: list[dict[str, Any]] = []
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            posix = PurePosixPath(member.name)
            if posix.is_absolute() or ".." in posix.parts or member.issym() or member.islnk():
                raise ValueError(f"Unsafe second-model archive member: {member.name}")
        for method, slug in SECOND_MODEL_METHODS.items():
            member = (
                "experiments/second_model/qwen25_coder_14b_final300_posthoc_v1/"
                f"{slug}/evaluation.jsonl"
            )
            fourteen = {
                row["sample_id"]: bool(row.get("target_state_correct"))
                for row in _read_tar_jsonl(bundle, member)
            }
            if set(seven[method]) != set(fourteen):
                raise ValueError(f"7B/14B sample mismatch for {method}")
            improved = sum(not seven[method][sid] and fourteen[sid] for sid in fourteen)
            degraded = sum(seven[method][sid] and not fourteen[sid] for sid in fourteen)
            raw.append(
                {
                    "method": method,
                    "improved_7b_wrong_14b_correct": improved,
                    "degraded_7b_correct_14b_wrong": degraded,
                    "exact_mcnemar_p": exact_mcnemar_from_counts(improved, degraded),
                }
            )
    adjusted = holm_bonferroni([row["exact_mcnemar_p"] for row in raw])
    for row, value in zip(raw, adjusted):
        row["holm_adjusted_p"] = value
        row["analysis_class"] = "post_hoc_same_family_model_scale"
    return raw


def efficiency_quantiles(primary_rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for method, rows in primary_rows.items():
        if method == "Gold-MP":
            continue
        record: dict[str, Any] = {"method": method, "samples": len(rows)}
        for field in ("input_tokens", "output_tokens", "latency_sec", "preflight_latency_sec"):
            for statistic_name, value in _summary_stats(row.get(field) for row in rows).items():
                record[f"{field}_{statistic_name}"] = value
        record["output_limit_hit_rate"] = sum(bool(row.get("hit_max_new_tokens")) for row in rows) / len(rows)
        output.append(record)
    return output


def _normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(value.split())


def _scalar_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        result: list[str] = []
        for nested in value.values():
            result.extend(_scalar_values(nested))
        return result
    if isinstance(value, list):
        result = []
        for nested in value:
            result.extend(_scalar_values(nested))
        return result
    if value is None or isinstance(value, bool):
        return []
    text = str(value).strip()
    return [text] if len(text) >= 2 else []


def mask_gold_values(text: str, sample: dict[str, Any]) -> str:
    masked = _normalize_text(text)
    values = sorted(
        {_normalize_text(item) for item in _scalar_values(sample.get("gold_records"))},
        key=lambda item: (-len(item), item),
    )
    for value in values:
        if len(value) >= 2:
            masked = masked.replace(value, " <value> ")
    masked = re.sub(r"https?://\S+", " <value> ", masked)
    masked = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " <value> ", masked)
    masked = re.sub(r"\b(?=\w*[a-z])(?=\w*\d)[\w.-]+\b", " <value> ", masked)
    masked = re.sub(r"\b[+-]?(?:\d+(?:[.,]\d+)*)\b", " <value> ", masked)
    return " ".join(masked.split())


def _char_ngrams(text: str, low: int = 3, high: int = 5) -> Counter[str]:
    padded = f"  {_normalize_text(text)}  "
    return Counter(
        padded[index : index + size]
        for size in range(low, high + 1)
        for index in range(max(0, len(padded) - size + 1))
    )


def tfidf_vectors(texts: list[str]) -> list[dict[str, float]]:
    counts = [_char_ngrams(text) for text in texts]
    document_frequency: Counter[str] = Counter()
    for row in counts:
        document_frequency.update(row)
    total = len(texts)
    vectors: list[dict[str, float]] = []
    for row in counts:
        weighted = {
            term: (1.0 + math.log(count)) * (math.log((1.0 + total) / (1.0 + document_frequency[term])) + 1.0)
            for term, count in row.items()
        }
        norm = math.sqrt(sum(value * value for value in weighted.values())) or 1.0
        vectors.append({term: value / norm for term, value in weighted.items()})
    return vectors


def cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(term, 0.0) for term, value in left.items())


def _extract_calibration_requests(workspace: Path) -> list[str]:
    archive = workspace / "03_protocol_and_data" / "calibration_evidence" / "mp_fs_plus_calibration60_20260729T141353Z.tar.gz"
    member = "experiments/calibration/full_locked_v3_in28672_out4096/d_fs_m/prompts.jsonl"
    with tarfile.open(archive, "r:gz") as bundle:
        rows = _read_tar_jsonl(bundle, member)
    requests: list[str] = []
    for row in rows:
        prompt = str(row.get("prompt") or "")
        marker = "\n\nREQUEST:\n"
        requests.append(prompt.split(marker, 1)[1] if marker in prompt else prompt)
    return requests


def dataset_redundancy_audit(
    workspace: Path,
    samples: dict[str, dict[str, Any]],
    primary_rows: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = [samples[sample_id] for sample_id in sorted(samples)]
    texts = [str(sample["input_text"]) for sample in ordered]
    normalized = [_normalize_text(text) for text in texts]
    masked = [mask_gold_values(text, sample) for text, sample in zip(texts, ordered)]
    vectors = tfidf_vectors(texts)
    nearest_rows: list[dict[str, Any]] = []
    for index, sample in enumerate(ordered):
        best_index = -1
        best_score = -1.0
        for other in range(len(ordered)):
            if other == index:
                continue
            score = cosine(vectors[index], vectors[other])
            if score > best_score or (math.isclose(score, best_score) and ordered[other]["id"] < ordered[best_index]["id"]):
                best_index = other
                best_score = score
        neighbor = ordered[best_index]
        if sample["db_id"] == neighbor["db_id"]:
            relation = "same_db_same_format" if sample["input_format"] == neighbor["input_format"] else "same_db_different_format"
        else:
            relation = "different_db"
        similarity_bin = "low" if best_score < 0.50 else "medium" if best_score < 0.80 else "high"
        nearest_rows.append(
            {
                "sample_id": sample["id"],
                "db_id": sample["db_id"],
                "input_format": sample["input_format"],
                "nearest_sample_id": neighbor["id"],
                "nearest_db_id": neighbor["db_id"],
                "nearest_input_format": neighbor["input_format"],
                "relation": relation,
                "char_ngram_tfidf_similarity": best_score,
                "similarity_bin": similarity_bin,
            }
        )
    calibration = _extract_calibration_requests(workspace)
    demos = load_json(
        workspace
        / "configs"
        / "demonstrations"
        / "matched_semantic_bank.json"
    )
    demo_texts = [str(row["input"]) for row in demos["examples"]]
    cross_texts = texts + calibration + demo_texts
    cross_vectors = tfidf_vectors(cross_texts)
    final_vectors = cross_vectors[: len(texts)]
    cal_vectors = cross_vectors[len(texts) : len(texts) + len(calibration)]
    demo_vectors = cross_vectors[len(texts) + len(calibration) :]
    cross_rows: list[dict[str, Any]] = []
    for sample, vector in zip(ordered, final_vectors):
        cal_score = max((cosine(vector, other) for other in cal_vectors), default=0.0)
        demo_score = max((cosine(vector, other) for other in demo_vectors), default=0.0)
        cross_rows.append(
            {
                "sample_id": sample["id"],
                "nearest_calibration_similarity": cal_score,
                "nearest_demonstration_similarity": demo_score,
            }
        )
    exact_groups = Counter(normalized)
    masked_groups = Counter(masked)
    method_lookup = {
        method: {row["sample_id"]: bool(row.get("target_state_correct")) for row in rows}
        for method, rows in primary_rows.items()
    }
    bin_rows: list[dict[str, Any]] = []
    for label in ("low", "medium", "high"):
        ids = [row["sample_id"] for row in nearest_rows if row["similarity_bin"] == label]
        for method in ("D-FS-M", "J-FS-M", "MP-FS+"):
            correct = sum(method_lookup[method][sample_id] for sample_id in ids)
            low, high = wilson_interval(correct, len(ids)) if ids else (None, None)
            bin_rows.append(
                {
                    "similarity_bin": label,
                    "definition": "low<0.50; medium=0.50-<0.80; high>=0.80",
                    "method": method,
                    "samples": len(ids),
                    "correct": correct,
                    "target_state_accuracy": correct / len(ids) if ids else None,
                    "wilson_95_low": low,
                    "wilson_95_high": high,
                }
            )
    revisions = Counter(str(sample.get("revision")) for sample in ordered)
    authors = sorted({str(sample.get("author_id")) for sample in ordered})
    reviewer_ids = sorted(
        {
            str(review.get("reviewer_id"))
            for sample in ordered
            for review in (sample.get("qa_reviews") or [])
            if review.get("reviewer_id") is not None
        }
    )
    scores = [row["char_ngram_tfidf_similarity"] for row in nearest_rows]
    report = {
        "analysis_id": "dataset_redundancy_and_contamination_audit_v1",
        "status": "pass",
        "method": {
            "normalization": "Unicode NFKC, casefold, whitespace collapse",
            "similarity": "character 3-5 gram TF-IDF with sublinear TF and smoothed IDF, cosine",
            "bins": {"low": "<0.50", "medium": "0.50-<0.80", "high": ">=0.80"},
            "value_masking": "replace scalar gold-record values plus URL/date/alphanumeric/numeric literals",
        },
        "samples": len(ordered),
        "exact_duplicate_rows": sum(count for count in exact_groups.values() if count > 1),
        "exact_duplicate_groups": sum(count > 1 for count in exact_groups.values()),
        "value_masked_duplicate_rows": sum(count for count in masked_groups.values() if count > 1),
        "value_masked_duplicate_groups": sum(count > 1 for count in masked_groups.values()),
        "nearest_similarity": {
            "median": statistics.median(scores),
            "p95": _quantile(scores, 0.95),
            "maximum": max(scores),
            "relation_counts": dict(Counter(row["relation"] for row in nearest_rows)),
        },
        "cross_split_similarity": {
            "calibration_median_nearest": statistics.median(row["nearest_calibration_similarity"] for row in cross_rows),
            "calibration_max_nearest": max(row["nearest_calibration_similarity"] for row in cross_rows),
            "demonstration_median_nearest": statistics.median(row["nearest_demonstration_similarity"] for row in cross_rows),
            "demonstration_max_nearest": max(row["nearest_demonstration_similarity"] for row in cross_rows),
        },
        "authorship_and_review": {
            "author_count": len(authors),
            "author_ids": authors,
            "reviewer_count": len(reviewer_ids),
            "reviewer_ids": reviewer_ids,
            "revision_distribution": dict(sorted(revisions.items())),
            "cohens_kappa_reported": False,
            "reason_kappa_not_reported": "frozen records contain adjudicated approvals rather than a complete pre-adjudication independent decision matrix",
        },
        "accuracy_by_similarity_bin": bin_rows,
    }
    return report, nearest_rows, cross_rows


def downstream_ablation(
    workspace: Path,
    run_root: Path,
    holdout: Path,
    samples: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    mp_root = run_root / "mp_fs_plus"
    materialized = {
        row["sample_id"]: row.get("write_plan")
        for row in _load_jsonl(mp_root / "materialized_write_plans.jsonl")
    }
    variants = {
        "V0_no_verifier_no_provenance_no_preflight": {"hard": False, "provenance": False, "preflight": False},
        "V1_hard_verifier_only": {"hard": True, "provenance": False, "preflight": False},
        "V2_hard_verifier_plus_provenance": {"hard": True, "provenance": True, "preflight": False},
        "V3_full_with_transactional_preflight": {"hard": True, "provenance": True, "preflight": True},
    }
    rows: list[dict[str, Any]] = []
    for sample_id in sorted(samples):
        sample = samples[sample_id]
        plan = materialized.get(sample_id)
        profile = load_json(holdout / "profiles" / f"{sample['db_id']}.json")
        db_path = find_database(holdout / "databases", str(sample["db_id"]))
        for variant, flags in variants.items():
            verification_status = "not_run"
            program = None
            if plan is not None:
                if flags["hard"]:
                    verification = verify_write_plan(
                        plan,
                        profile,
                        check_provenance=bool(flags["provenance"]),
                    )
                    verification_status = verification.status
                    candidate_plan = verification.normalized_plan if verification.valid else None
                else:
                    candidate_plan = plan
                if candidate_plan is not None:
                    program = compile_verified_plan(
                        candidate_plan,
                        profile,
                        normalization_mode="lossless",
                    )
                    # Production MP-FS+ carries verifier/grounding warnings
                    # into preflight, where designated semantic-risk warnings
                    # are fail-closed. Preserve that boundary for V3 so the
                    # ablation anchor exactly reproduces primary admission.
                    if flags["hard"]:
                        program.warnings.extend(verification.warnings)
            build_success = bool(program is not None and program.status == "success")
            preflight = (
                preflight_program(db_path, program)
                if build_success and flags["preflight"]
                else None
            )
            admitted = bool(
                build_success
                and (not flags["preflight"] or preflight.get("accepted"))
            )
            evaluation = evaluate_candidate_sample(
                sample,
                db_path,
                program=program,
                parse_status="success" if plan is not None else "not_available",
                build_status="success" if build_success else "error",
                preflight=preflight if flags["preflight"] else None,
            )
            target_correct = bool(admitted and evaluation["target_state_correct"])
            rows.append(
                {
                    "sample_id": sample_id,
                    "db_id": sample["db_id"],
                    "variant": variant,
                    "materialized_plan_available": plan is not None,
                    "verification_status": verification_status,
                    "build_success": build_success,
                    "preflight_accepted": bool(preflight.get("accepted")) if preflight else None,
                    "admitted": admitted,
                    "execution_success": bool(evaluation["execution_success"]),
                    "target_state_correct": target_correct,
                    "false_accept": bool(admitted and not target_correct),
                    "constraint_or_execution_failure": bool(admitted and not evaluation["execution_success"]),
                    "any_off_target_change": bool(admitted and evaluation.get("any_off_target_change")),
                    "off_target_mismatched_tables": evaluation.get("off_target_mismatched_tables") or [],
                    "error_type": evaluation.get("error_type"),
                }
            )
    summaries: list[dict[str, Any]] = []
    for variant in variants:
        subset = [row for row in rows if row["variant"] == variant]
        admitted = sum(row["admitted"] for row in subset)
        correct = sum(row["target_state_correct"] for row in subset)
        false_accepts = sum(row["false_accept"] for row in subset)
        summaries.append(
            {
                "variant": variant,
                "samples": len(subset),
                "materialized_plan_coverage": sum(row["materialized_plan_available"] for row in subset) / len(subset),
                "program_build_rate": sum(row["build_success"] for row in subset) / len(subset),
                "coverage": admitted / len(subset),
                "target_state_accuracy": correct / len(subset),
                "accepted_output_accuracy": correct / admitted if admitted else None,
                "false_accept_count": false_accepts,
                "false_accept_rate": false_accepts / admitted if admitted else None,
                "invalid_program_rate": 1.0 - sum(row["build_success"] for row in subset) / len(subset),
                "constraint_or_execution_failure_count": sum(row["constraint_or_execution_failure"] for row in subset),
                "off_target_modification_count": sum(row["any_off_target_change"] for row in subset),
                "abstention_rate": 1.0 - admitted / len(subset),
            }
        )
    primary_evaluation = _load_jsonl(mp_root / "evaluation.jsonl")
    expected_anchor = {
        "coverage": sum(bool(row.get("preflight_accepted")) for row in primary_evaluation) / len(primary_evaluation),
        "target_state_accuracy": sum(bool(row.get("target_state_correct")) for row in primary_evaluation) / len(primary_evaluation),
    }
    observed_anchor = next(
        item for item in summaries
        if item["variant"] == "V3_full_with_transactional_preflight"
    )
    anchor_pass = all(
        math.isclose(float(observed_anchor[key]), float(value), abs_tol=1e-12)
        for key, value in expected_anchor.items()
    )
    if not anchor_pass:
        raise AssertionError(
            "V3 must reproduce the frozen MP-FS+ admission and target-state anchors"
        )
    report = {
        "analysis_id": "mp_fs_plus_downstream_deterministic_ablation_v1",
        "status": "pass",
        "analysis_class": "post_hoc_frozen_plan_downstream_ablation",
        "scope": "verification/provenance/preflight boundaries after deterministic materialization",
        "not_claimed": [
            "full MP-FS+ component ablation",
            "reference-grounding ablation",
            "prompt or generation ablation",
        ],
        "predictions_modified": False,
        "model_inference_rerun": False,
        "database_execution_replayed_on_isolated_copies": True,
        "primary_anchor": {
            "status": "pass",
            "expected": expected_anchor,
            "observed": {
                key: observed_anchor[key]
                for key in expected_anchor
            },
        },
        "variants": summaries,
    }
    return report, rows


def run_exploratory_v2_4(
    workspace: Path,
    output_dir: Path,
    *,
    keep_temp_on_failure: bool = False,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_root, primary_rows = _load_primary(workspace)
    run_common_safety_replay(workspace, output_dir)
    common_rows = _load_jsonl(output_dir / "common_safety_replay.jsonl")
    # Use a deterministic work directory. On Windows, TemporaryDirectory can
    # inherit restrictive ACLs that make the just-created extraction tree
    # unreadable to the same process. This mirrors the already validated
    # common-safety replay extraction path and is removed after analysis.
    work_root = output_dir / "_exploratory_holdout"
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True)
    completed = False
    try:
        holdout = _extract_holdout(workspace, work_root)
        samples = {
            str(row["id"]): row
            for row in load_json(holdout / "dataset.final.json")
        }
        cascade, cascade_rows = build_cascade(common_rows, primary_rows, samples)
        redundancy, nearest_rows, cross_rows = dataset_redundancy_audit(
            workspace,
            samples,
            primary_rows,
        )
        ablation, ablation_rows = downstream_ablation(
            workspace,
            run_root,
            holdout,
            samples,
        )
        completed = True
    finally:
        if completed or not keep_temp_on_failure:
            shutil.rmtree(work_root, ignore_errors=True)
    uncertainty = build_uncertainty(primary_rows, cascade)
    paired_scale = paired_model_scale(workspace, primary_rows)
    efficiency = efficiency_quantiles(primary_rows)

    _write_json(output_dir / "cascade_results.json", cascade)
    _write_jsonl(output_dir / "cascade_per_sample.jsonl", cascade_rows)
    _write_csv(output_dir / "cascade_by_database.csv", cascade["by_database"])
    _write_csv(output_dir / "statistical_uncertainty.csv", uncertainty)
    _write_csv(output_dir / "paired_model_scale_tests.csv", paired_scale)
    _write_csv(output_dir / "efficiency_quantiles.csv", efficiency)
    _write_json(output_dir / "dataset_redundancy_audit.json", redundancy)
    _write_csv(output_dir / "dataset_nearest_neighbors.csv", nearest_rows)
    _write_csv(output_dir / "dataset_cross_split_similarity.csv", cross_rows)
    _write_csv(output_dir / "accuracy_by_similarity_bin.csv", redundancy["accuracy_by_similarity_bin"])
    _write_json(output_dir / "downstream_ablation_results.json", ablation)
    _write_jsonl(output_dir / "downstream_ablation_per_sample.jsonl", ablation_rows)
    _write_csv(output_dir / "downstream_ablation_summary.csv", ablation["variants"])

    summary = {
        "report_version": "2.4",
        "status": "pass",
        "analysis_class": "corrective_plus_post_hoc_exploratory",
        "primary_results_modified": False,
        "predictions_modified": False,
        "gpu_required": False,
        "cascade": cascade,
        "paired_model_scale": paired_scale,
        "dataset_redundancy": redundancy,
        "downstream_ablation": ablation,
    }
    _write_json(output_dir / "reporting_v2_4_results.json", summary)
    output_names = [
        "common_safety_replay.jsonl",
        "common_safety_summary.csv",
        "common_safety_results.json",
        "cascade_results.json",
        "cascade_per_sample.jsonl",
        "cascade_by_database.csv",
        "statistical_uncertainty.csv",
        "paired_model_scale_tests.csv",
        "efficiency_quantiles.csv",
        "dataset_redundancy_audit.json",
        "dataset_nearest_neighbors.csv",
        "dataset_cross_split_similarity.csv",
        "accuracy_by_similarity_bin.csv",
        "downstream_ablation_results.json",
        "downstream_ablation_per_sample.jsonl",
        "downstream_ablation_summary.csv",
        "reporting_v2_4_results.json",
    ]
    source_root = Path(__file__).resolve().parents[3]
    manifest = {
        "analysis_id": "reporting_and_exploratory_extension_v2_4",
        "status": "frozen_post_hoc_analysis",
        "predictions_modified": False,
        "primary_results_modified": False,
        "gpu_required": False,
        "source_sha256": {
            "src/nldbwrite_v3/analysis/exploratory_v2_4.py": sha256_file(Path(__file__)),
            "src/nldbwrite_v3/verifier/verify.py": sha256_file(Path(__file__).parents[1] / "verifier" / "verify.py"),
            "run_exploratory_v2_4.py": sha256_file(source_root / "run_exploratory_v2_4.py"),
            "reproduce_paper.py": sha256_file(source_root / "reproduce_paper.py"),
            "tests/test_exploratory_v2_4.py": sha256_file(source_root / "tests" / "test_exploratory_v2_4.py"),
        },
        "output_sha256": {
            name: sha256_file(output_dir / name) for name in output_names
        },
    }
    _write_json(output_dir / "REPORTING_V2_4_MANIFEST.json", manifest)
    return {
        "status": "pass",
        "samples": len(cascade_rows),
        "cascade_accuracy": cascade["target_state_accuracy"],
        "cascade_coverage": cascade["coverage"],
        "cascade_admitted_accuracy": cascade["accepted_output_accuracy"],
        "off_target_events": cascade["off_target_modifications"],
        "downstream_ablation_variants": len(ablation["variants"]),
        "dataset_audit_status": redundancy["status"],
        "primary_results_modified": False,
    }
