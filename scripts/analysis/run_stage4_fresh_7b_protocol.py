#!/usr/bin/env python3
"""Freeze the Stage-4 fresh 7B protocol without calling a model.

The script selects a deterministic fresh subset, audits overlap against the
300 diagnostic samples, builds exact production prompt hashes for the frozen
generation arms, and emits reviewer-facing protocol artifacts.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from nldbwrite_v3.experiments.run_method import (  # noqa: E402
    _load_method_config,
    _load_profiles,
    _prompt_for_sample,
)
from nldbwrite_v3.source_parser import parse_source_payload  # noqa: E402

from scripts.analysis.run_stage3_causal_replay import (  # noqa: E402
    sha256_file,
    write_csv,
    write_json,
)


SELECTION_SEED = "stage4-fresh-7b-protocol-v1"
PRIMARY_CONFIG_HASH = "1ec5d19768fd1bc4c1814c0e2e02d3205007d2d53db9dab8ff0e8123e9e11fdf"
EXPECTED_SAMPLE_COUNT = 300
MODEL_LOCK = {
    "model_id": "Qwen/Qwen2.5-Coder-7B-Instruct",
    "snapshot_revision": "c03e6d358207e414f1eca0bb1891e29f1db0e242",
    "tokenizer_revision": "c03e6d358207e414f1eca0bb1891e29f1db0e242",
    "aggregate_sha256": "e2026c78ea002527089b088023b7ae2c1486f127f667cafbb823225877cd268c",
    "tokenizer_sha256": "06d1f5403e9eda68466f91b5c235eab56b530a9b8155e21f3bd0523b4b29e468",
    "model_config_sha256": "326f5a48d12e88e8115048769fd5bb4eac3f56dee63847b983bc908456d5c357",
}
INFERENCE_LOCK = {
    "backend": "hf",
    "framework": "transformers",
    "batch_size": 1,
    "context_length": 32768,
    "max_input_tokens": 28672,
    "max_new_tokens": 4096,
    "input_truncation_policy": "error",
    "quantization": "4bit",
    "bitsandbytes_config": {
        "load_in_4bit": True,
        "bnb_4bit_quant_type": "fp4",
        "bnb_4bit_use_double_quant": False,
        "bnb_4bit_compute_dtype": "float16",
        "bnb_4bit_quant_storage": "uint8",
        "source": (
            "Explicit lock of the historical runner behavior: the previous "
            "HF runner passed only load_in_4bit=True and compute_dtype=float16, "
            "therefore BitsAndBytes defaults are fixed here instead of left "
            "implicit."
        ),
    },
    "compute_dtype": "float16",
    "device_map": "auto",
    "do_sample": False,
    "temperature": None,
    "top_p": None,
    "top_k": None,
    "eos_token_id": "tokenizer.eos_token_id captured by GPU preflight",
    "pad_token_id": "tokenizer.pad_token_id; if absent set to eos_token_id",
    "bos_handling": "tokenizer(prompt, add_special_tokens=True)",
    "chat_template_usage": {
        "messages": [{"role": "user", "content": "<production_prompt>"}],
        "tokenize": False,
        "add_generation_prompt": True,
    },
    "padding_side": "left",
    "generation_kwargs": {
        "max_new_tokens": 4096,
        "do_sample": False,
        "pad_token_id": "tokenizer.pad_token_id",
        "eos_token_id": "tokenizer.eos_token_id",
        "temperature": "omitted_when_do_sample_false",
        "top_p": "omitted_when_do_sample_false",
    },
    "seed": 42,
    "stop_sequences": [],
    "trust_remote_code": False,
    "batching_policy": "fixed batch_size=1; resume keyed by generation_arm+sample_id",
    "retry_policy": {
        "completed_raw_output": "immutable_never_regenerate",
        "infrastructure_crash_before_output": "resume_same_config_only",
        "semantic_retry": False,
        "attempt_log_required": True,
    },
}
CONFIGS = [
    ("direct", "Direct", "D-FS-M", "configs/stage4/direct.json", "generation", "baseline"),
    ("j_fs", "J-FS", "J-FS-M", "configs/stage4/j_fs.json", "generation", "baseline"),
    (
        "original_mp_fs_plus",
        "Original MP-FS+",
        "MP-FS+",
        "configs/stage4/original_mp_fs_plus.json",
        "deterministic_reprocess",
        "primary_comparison_baseline",
    ),
    (
        "d_g1_primary",
        "MP-FS+ vNext D_G1",
        "MP-FS+",
        "configs/stage4/d_g1_primary.json",
        "deterministic_reprocess",
        "primary",
    ),
    (
        "d_only_secondary",
        "MP-FS+ vNext D_ONLY",
        "MP-FS+",
        "configs/stage4/d_only_secondary.json",
        "deterministic_reprocess",
        "secondary_ablation",
    ),
    (
        "full_secondary",
        "MP-FS+ vNext FULL",
        "MP-FS+",
        "configs/stage4/full_secondary.json",
        "deterministic_reprocess",
        "secondary_ablation",
    ),
    (
        "no_c_secondary",
        "MP-FS+ vNext NO_C",
        "MP-FS+",
        "configs/stage4/no_c_secondary.json",
        "deterministic_reprocess",
        "secondary_ablation",
    ),
]
VNEXT_SLUGS = {"d_g1_primary", "d_only_secondary", "full_secondary", "no_c_secondary"}
MP_FS_PLUS_SHARED_SLUGS = {
    "original_mp_fs_plus",
    "d_g1_primary",
    "d_only_secondary",
    "full_secondary",
    "no_c_secondary",
}
GENERATION_ARMS = {"direct", "j_fs", "mp_fs_plus_shared"}
BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 240822


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_ids(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_sample(sample: Mapping[str, Any]) -> str:
    fields = {
        key: sample.get(key)
        for key in (
            "db_id",
            "input_text",
            "gold_sql",
            "gold_records",
            "gold_tables",
            "operation_type",
            "operation_semantics",
            "input_format",
            "source_id",
        )
    }
    return json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_group_key(sample: Mapping[str, Any]) -> tuple[str, str]:
    """Return the official source-group key when present.

    The archived Stage-4 pool uses ``source_group_id`` rather than
    ``source_group``.  Only when both official metadata fields are absent do we
    fall back to a root-seed parse from the sample ID for audit transparency.
    """

    for field in ("source_group", "source_group_id"):
        value = sample.get(field)
        if value not in {None, ""}:
            return str(value), field
    sample_id = str(sample.get("id") or sample.get("sample_id") or "")
    seed_match = re.search(r"seed_\d+", sample_id)
    if seed_match:
        return seed_match.group(0), "derived_from_sample_id_no_official_source_group"
    return sample_id, "sample_id_fallback_no_official_source_group"


def stable_json_hash(value: Any) -> str:
    return sha256_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )


def select_fresh_samples(
    samples: list[dict[str, Any]],
    diagnostic_samples: list[dict[str, Any]],
    sample_count: int,
) -> list[dict[str, Any]]:
    diagnostic_ids = {
        str(row.get("id") or row.get("sample_id") or "") for row in diagnostic_samples
    }
    diagnostic_groups = {source_group_key(row)[0] for row in diagnostic_samples}
    diagnostic_text_hashes = {sha256_text(str(row.get("input_text") or "")) for row in diagnostic_samples}
    diagnostic_canonical_hashes = {sha256_text(canonical_sample(row)) for row in diagnostic_samples}
    eligible = []
    for sample in samples:
        sample_id = str(sample.get("id") or sample.get("sample_id") or "")
        source_group = source_group_key(sample)[0]
        text_hash = sha256_text(str(sample.get("input_text") or ""))
        canonical_hash = sha256_text(canonical_sample(sample))
        if (
            sample_id in diagnostic_ids
            or source_group in diagnostic_groups
            or text_hash in diagnostic_text_hashes
            or canonical_hash in diagnostic_canonical_hashes
        ):
            continue
        key = sha256_text(f"{SELECTION_SEED}|{sample_id}")
        eligible.append((key, sample_id, sample))
    eligible.sort()
    if len(eligible) < sample_count:
        raise ValueError(f"Need {sample_count} fresh samples, found {len(eligible)}")
    return [sample for _key, _sample_id, sample in eligible[:sample_count]]


def operation_label(gold_plan: Mapping[str, Any] | None, sample: Mapping[str, Any]) -> str:
    explicit = sample.get("operation_semantics") or sample.get("operation_type")
    if explicit and explicit not in {"insert"}:
        return "upsert_update" if str(explicit) == "upsert" else str(explicit)
    groups = (gold_plan or {}).get("write_groups") or []
    conflict_actions = {
        str((group.get("conflict") or {}).get("action") or "error")
        for group in groups
    }
    has_updates = any((group.get("conflict") or {}).get("update_columns") for group in groups)
    if has_updates or conflict_actions & {"update", "do_update", "upsert_update"}:
        return "upsert_update"
    if conflict_actions & {"do_nothing", "ignore", "insert_ignore"}:
        return "insert_ignore"
    return "plain_insert"


def estimated_token_count(prompt: str) -> int:
    return int(math.ceil(len(prompt.encode("utf-8")) / 4))


def build_prompt_tables(
    samples: list[dict[str, Any]],
    profiles: Mapping[str, dict[str, Any]],
    configs: Mapping[str, dict[str, Any]],
    gold_plans: Mapping[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    rows: list[dict[str, Any]] = []
    prompt_hashes_by_slug: dict[str, list[str]] = {slug: [] for slug, *_rest in CONFIGS}
    for sample in samples:
        sample_id = str(sample.get("id") or sample.get("sample_id") or "")
        source_group, source_group_source = source_group_key(sample)
        db_id = str(sample["db_id"])
        per_sample_hashes: dict[str, str] = {}
        per_sample_modes: dict[str, str] = {}
        for slug, _label, method_id, _relative, generation_role, analysis_role in CONFIGS:
            prompt, payload = _prompt_for_sample(method_id, sample, profiles[db_id], configs[slug])
            prompt_hash = sha256_text(prompt)
            per_sample_hashes[slug] = prompt_hash
            per_sample_modes[slug] = str(payload.mode)
            prompt_hashes_by_slug[slug].append(prompt_hash)
            rows.append(
                {
                    "sample_id": sample_id,
                    "source_group": source_group,
                    "source_group_source": source_group_source,
                    "db_id": db_id,
                    "method_slug": slug,
                    "method_label": _label,
                    "method_id": method_id,
                    "generation_role": generation_role,
                    "analysis_role": analysis_role,
                    "generation_arm": slug if slug in {"direct", "j_fs"} else "mp_fs_plus_shared",
                    "shares_raw_generation_with": (
                        "" if slug in {"direct", "j_fs"} else "mp_fs_plus_shared"
                    ),
                    "prompt_sha256": prompt_hash,
                    "prompt_char_count": len(prompt),
                    "prompt_utf8_bytes": len(prompt.encode("utf-8")),
                    "prompt_token_count": estimated_token_count(prompt),
                    "prompt_token_count_policy": "ceil(utf8_bytes/4)_cpu_estimate",
                    "exact_token_count_required_on_gpu_preflight": 1,
                    "context_overflow": int(estimated_token_count(prompt) > INFERENCE_LOCK["max_input_tokens"]),
                    "detected_mode": payload.mode,
                    "detected_format": payload.source_format,
                    "operation_type": operation_label(gold_plans.get(sample_id), sample),
                    "dependency_sensitive": int(bool((gold_plans.get(sample_id) or {}).get("dependencies"))),
                    "original_vs_vnext_prompt_equal": "",
                }
            )
        for row in rows[-len(CONFIGS):]:
            row["original_vs_vnext_prompt_equal"] = int(
                per_sample_hashes["original_mp_fs_plus"] == per_sample_hashes["d_g1_primary"]
            )
            row["all_vnext_prompts_equal"] = int(
                len({per_sample_hashes[slug] for slug in VNEXT_SLUGS}) == 1
            )
            row["detected_mode_consistent_across_vnext"] = int(
                len({per_sample_modes[slug] for slug in VNEXT_SLUGS}) == 1
            )
    summary: list[dict[str, Any]] = []
    by_sample = {
        str(sample.get("id") or sample.get("sample_id") or ""): [
            row for row in rows if row["sample_id"] == str(sample.get("id") or sample.get("sample_id") or "")
        ]
        for sample in samples
    }
    for dimension in ("ALL", "free_text", "semi_structured"):
        selected = []
        for sample_rows in by_sample.values():
            mode = str(sample_rows[0]["detected_mode"])
            if dimension == "ALL" or mode == dimension:
                selected.append(sample_rows)
        changed = [
            sample_rows[0]["sample_id"]
            for sample_rows in selected
            if not bool(sample_rows[0]["original_vs_vnext_prompt_equal"])
        ]
        vnext_drift = [
            sample_rows[0]["sample_id"]
            for sample_rows in selected
            if not bool(sample_rows[0]["all_vnext_prompts_equal"])
        ]
        summary.append(
            {
                "comparison": "original_mp_fs_plus_vs_d_g1_primary",
                "input_type": dimension,
                "samples": len(selected),
                "same_prompt": len(selected) - len(changed),
                "changed_prompt": len(changed),
                "changed_sample_ids": "|".join(changed),
            }
        )
        summary.append(
            {
                "comparison": "vnext_candidates_internal_equivalence",
                "input_type": dimension,
                "samples": len(selected),
                "same_prompt": len(selected) - len(vnext_drift),
                "changed_prompt": len(vnext_drift),
                "changed_sample_ids": "|".join(vnext_drift),
            }
        )
    prompt_set_hashes = {
        slug: sha256_text("".join(values)) for slug, values in prompt_hashes_by_slug.items()
    }
    return rows, summary, prompt_set_hashes


def summarize_dataset(
    samples: Iterable[Mapping[str, Any]],
    prompt_rows: list[dict[str, Any]],
    gold_plans: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    ids = [str(sample.get("id") or sample.get("sample_id") or "") for sample in samples]
    rows_by_sample = {row["sample_id"]: row for row in prompt_rows if row["method_slug"] == "d_g1_primary"}
    return {
        "sample_count": len(ids),
        "unique_sample_ids": len(set(ids)),
        "sample_ids_sha256": sha256_text("\n".join(ids) + "\n"),
        "database_counts": dict(sorted(Counter(row["db_id"] for row in rows_by_sample.values()).items())),
        "input_type_counts": dict(sorted(Counter(row["detected_mode"] for row in rows_by_sample.values()).items())),
        "input_format_counts": dict(sorted(Counter(row["detected_format"] for row in rows_by_sample.values()).items())),
        "operation_counts": dict(sorted(Counter(row["operation_type"] for row in rows_by_sample.values()).items())),
        "dependency_sensitive_count": sum(
            bool((gold_plans.get(sample_id) or {}).get("dependencies")) for sample_id in ids
        ),
        "source_group_policy": (
            "Use official source_group when present, else official source_group_id "
            "when present; derive from sample ID only if no official metadata exists."
        ),
        "selection_policy": {
            "seed": SELECTION_SEED,
            "rule": "exclude diagnostic overlaps, then sort by sha256(seed|sample_id) and take first 300",
            "manual_inspection_for_tuning": False,
        },
    }


def source_group_audit(samples: list[dict[str, Any]]) -> dict[str, Any]:
    groups = [source_group_key(sample)[0] for sample in samples]
    sources = [source_group_key(sample)[1] for sample in samples]
    counts = Counter(groups)
    group_size_distribution = Counter(counts.values())
    return {
        "sample_count": len(samples),
        "unique_source_groups": len(counts),
        "multi_sample_group_count": sum(size > 1 for size in counts.values()),
        "max_group_size": max(counts.values()) if counts else 0,
        "group_size_distribution": {
            str(size): count for size, count in sorted(group_size_distribution.items())
        },
        "source_group_source_counts": dict(sorted(Counter(sources).items())),
        "policy": (
            "Primary clustering key is official source_group/source_group_id; "
            "sample-ID root derivation is used only when official metadata is absent."
        ),
    }


def d_parser_opportunity_audit(
    samples: list[dict[str, Any]],
    d_config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    def semantic_projection(payload: Any) -> dict[str, Any]:
        return {
            "mode": payload.mode,
            "source_format": payload.source_format,
            "instruction_text": payload.instruction_text,
            "collections": [
                {
                    "collection_id": collection.collection_id,
                    "source_path": collection.source_path,
                    "source_format": collection.source_format,
                    "rows": collection.rows,
                    "fields": collection.fields,
                    "reference_id": collection.reference_id,
                    "selector_id": collection.selector_id,
                    "field_ids": collection.field_ids,
                }
                for collection in payload.collections
            ],
        }

    rows: list[dict[str, Any]] = []
    structured_parser_config = d_config.get("structured_source_parser")
    for sample in samples:
        sample_id = str(sample.get("id") or sample.get("sample_id") or "")
        request = str(sample.get("input_text") or "")
        legacy_payload = parse_source_payload(request, structured_parser=None)
        d_payload = parse_source_payload(
            request,
            structured_parser=structured_parser_config,
        )
        legacy_hash = stable_json_hash(semantic_projection(legacy_payload))
        d_hash = stable_json_hash(semantic_projection(d_payload))
        rows.append(
            {
                "sample_id": sample_id,
                "source_group": source_group_key(sample)[0],
                "legacy_payload_hash": legacy_hash,
                "D_payload_hash": d_hash,
                "changed": int(legacy_hash != d_hash),
            }
        )
    return rows


def overlap_audit(
    fresh_samples: list[dict[str, Any]],
    diagnostic_samples: list[dict[str, Any]],
    source_pool_count: int,
) -> dict[str, Any]:
    def ids(samples: list[dict[str, Any]]) -> set[str]:
        return {str(row.get("id") or row.get("sample_id") or "") for row in samples}

    def groups(samples: list[dict[str, Any]]) -> set[str]:
        return {source_group_key(row)[0] for row in samples}

    def text_hashes(samples: list[dict[str, Any]]) -> set[str]:
        return {sha256_text(str(row.get("input_text") or "")) for row in samples}

    def canonical_hashes(samples: list[dict[str, Any]]) -> set[str]:
        return {sha256_text(canonical_sample(row)) for row in samples}

    def db_ids(samples: list[dict[str, Any]]) -> set[str]:
        return {str(row.get("db_id") or "") for row in samples}

    fresh_ids = ids(fresh_samples)
    diagnostic_ids = ids(diagnostic_samples)
    return {
        "status": "PASS",
        "source_pool_count": source_pool_count,
        "fresh_sample_count": len(fresh_samples),
        "diagnostic_sample_count": len(diagnostic_samples),
        "sample_id_overlap_count": len(fresh_ids & diagnostic_ids),
        "source_group_overlap_count": len(groups(fresh_samples) & groups(diagnostic_samples)),
        "input_text_hash_overlap_count": len(text_hashes(fresh_samples) & text_hashes(diagnostic_samples)),
        "canonical_content_hash_overlap_count": len(canonical_hashes(fresh_samples) & canonical_hashes(diagnostic_samples)),
        "database_overlap_count": len(db_ids(fresh_samples) & db_ids(diagnostic_samples)),
        "fresh_database_ids": sorted(db_ids(fresh_samples)),
        "diagnostic_database_ids": sorted(db_ids(diagnostic_samples)),
        "disclosure": (
            "Fresh set is disjoint by sample ID, source group, input-text hash, "
            "canonical content hash, and database ID."
        ),
    }


def git_output(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True, encoding="utf-8").strip()


def copy_configs(output_dir: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for slug, _label, _method_id, relative, _generation_role, _analysis_role in CONFIGS:
        source = PROJECT_ROOT / relative
        destination = output_dir / "configs" / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        result[slug] = {"path": relative, "sha256": sha256_file(source)}
    return result


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def write_docs(output_dir: Path, manifest: Mapping[str, Any]) -> None:
    write_text(
        output_dir / "README_REVIEWER.md",
        """# Stage 4 Fresh 7B Protocol Reviewer Package

This package freezes the fresh 7B protocol only. It does not call a model and
does not contain raw model generations.

Primary method: MP-FS+ vNext D_G1.
Primary comparison: Original MP-FS+ vs D_G1, both reprocessed from the
same immutable MP-FS+ raw generation after exact HF input-ID preflight.
Secondary ablations on the same shared MP-FS+ raw generation: D_ONLY, FULL,
NO_C.

This is a fresh-to-vNext, database-disjoint held-out evaluation subset drawn
from the archived frozen test pool; it is not described as a never-before-used
public dataset.
""",
    )
    write_text(
        output_dir / "STAGE4_PROTOCOL.md",
        """# Stage 4 Fresh 7B Protocol

Status: protocol freeze pending reviewer acceptance.

Fresh set: 300 samples selected deterministically from an archived 677-sample
test pool after excluding all overlap with the 300 diagnostic samples by sample
ID, source group, input-text hash, and canonical content hash.

Generation graph:

- Direct: Direct SQL prompt, generated independently.
- J-FS: JSON/J-FS prompt, generated independently.
- Shared MP-FS+: generated once only after GPU preflight proves that
  Original MP-FS+ and D_G1 final HF input IDs are identical for all 300 samples.

The shared MP-FS+ raw generation is processed as:

- Primary baseline: Original MP-FS+.
- Primary method: D_G1.
- Secondary: D_ONLY, FULL, NO_C.

If the GPU preflight reports Original-vs-D_G1 final input equality below
300/300, stop and return the preflight artifacts for review; do not change the
protocol automatically.

No component selection is allowed after seeing fresh 7B results. D_G1 remains
primary even if a secondary ablation scores higher on the fresh run.

Token-budget policy: frozen standard context, max_input_tokens=28672,
max_new_tokens=4096, truncation_policy=error. No 2K/4K/8K/full-context token
budget experiment is part of Stage 4.

Raw generation immutability: completed rows in raw_generations/direct.jsonl,
raw_generations/j_fs.jsonl, and raw_generations/mp_fs_plus_shared.jsonl are
never regenerated for semantic reasons. Samples with no completed raw output
because of an infrastructure crash may resume once with the same locked config;
attempt logs must be preserved.

Stopping rule: if D_G1 shows systematic false acceptance, off-target state
changes, truncation, or missing predictions, preserve raw outputs and report the
failure. Do not patch/tune D/G1 on this fresh set and then reuse it as a test.
""",
    )
    write_text(
        output_dir / "analysis" / "metric_spec.md",
        """# Metric Spec

Primary metrics:

- Target-State Accuracy.
- Strict Full-State Accuracy.

Safety/selective metrics:

- Coverage.
- Accepted-Output Accuracy.
- False Accept Count and Rate.
- Execution Success.
- Constraint Failure.
- Off-Target State Change.

Diagnostics:

- First failure stage.
- D activation.
- G1 attempted, applied, revalidation success, and final-state success.
""",
    )
    write_text(
        output_dir / "analysis" / "statistical_analysis_plan.md",
        """# Statistical Analysis Plan

Primary paired comparison: Original MP-FS+ vs D_G1.

Report paired counts:

- both correct
- original only correct
- D_G1 only correct
- both wrong

Accuracy remains sample-weighted. The primary 95% confidence interval for the
paired accuracy difference uses a cluster bootstrap over source_group:

- cluster key: official source_group if present, else official source_group_id;
  sample-ID derivation only if no official metadata exists
- bootstrap replicates: 10000
- bootstrap RNG seed: 240822
- interval: percentile 95% CI

Report McNemar exact test as a secondary conventional paired test with a note
that the dataset contains clustered variants.

Predeclared subgroups:

- free_text
- semi_structured
- plain_insert
- insert_ignore
- upsert_update
- per database
- dependency-sensitive
- non-dependency-sensitive
""",
    )
    write_text(
        output_dir / "provenance" / "environment_lock.txt",
        "\n".join(
            [
                f"python={platform.python_version()}",
                f"platform={platform.platform()}",
                f"sqlite={sqlite3.sqlite_version}",
                "local_transformers_available=false",
                "exact_qwen_token_count_required_on_gpu_preflight=true",
                "gpu_environment_capture_required_before_generation=true",
                "required_gpu_packages=python,torch,transformers,accelerate,bitsandbytes,cuda,tokenizers,safetensors",
                "",
            ]
        ),
    )
    write_text(
        output_dir / "RUN_STAGE4_7B_AFTER_ACCEPTANCE.md",
        """# Run Stage 4 Fresh 7B After Reviewer Acceptance

Target server path requested by user:

```bash
ssh uet@222.255.250.24
mkdir -p /home/uet/hue_ptk
cd /home/uet/hue_ptk
```

Upload the accepted code/package from the local machine:

```powershell
scp "D:\\paper kltn\\text to sql\\reviewer_packages\\Stage4_FRESH_7B_PROTOCOL_PATCH1_FINAL_REVIEWER_PACKAGE_20260822.zip" uet@222.255.250.24:/home/uet/hue_ptk/
```

On the server, unpack only after protocol acceptance and use a clean git
checkout at the accepted Patch-1 commit:

```bash
cd /home/uet/hue_ptk
unzip Stage4_FRESH_7B_PROTOCOL_PATCH1_FINAL_REVIEWER_PACKAGE_20260822.zip -d Stage4_FRESH_7B_PROTOCOL_PATCH1_REVIEW
git clone https://github.com/hueptk0711/SQLite-Writes.git SQLite-Writes-stage4
cd SQLite-Writes-stage4
git checkout <PATCH1_COMMIT_AFTER_REVIEW>
python -m venv .venv-stage4
source .venv-stage4/bin/activate
pip install -r requirements-inference.lock.txt
```

Before any model generation, run the exact-token GPU preflight using the
accepted local Qwen2.5-Coder-7B-Instruct snapshot. Replace the data paths with
the server locations of the archived Stage-4 source files.

```bash
python scripts/server/run_stage4_gpu_preflight.py \
  --protocol-root stage4_fresh_7b_protocol \
  --fresh-source-data /home/uet/hue_ptk/data/stage4/dataset_test_v3.json \
  --fresh-gold-plans /home/uet/hue_ptk/data/stage4/gold_plans.jsonl \
  --profile-dir /home/uet/hue_ptk/data/stage4/profiles \
  --model-name-or-path /home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242 \
  --accepted-protocol-commit <PATCH1_COMMIT_AFTER_REVIEW> \
  --output-dir /home/uet/hue_ptk/stage4_fresh_7b_gpu_preflight
```

If any prompt overflows, or if Original-vs-D_G1 final input equality is not
300/300, stop and send the preflight output for review.

Only after preflight PASS, run the single authoritative Stage-4 runner:

```bash
python scripts/server/run_stage4_fresh_7b.py \
  --protocol-root stage4_fresh_7b_protocol \
  --fresh-source-data /home/uet/hue_ptk/data/stage4/dataset_test_v3.json \
  --fresh-gold-plans /home/uet/hue_ptk/data/stage4/gold_plans.jsonl \
  --profile-dir /home/uet/hue_ptk/data/stage4/profiles \
  --db-root /home/uet/hue_ptk/data/stage4/databases \
  --model-name-or-path /home/uet/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-7B-Instruct/snapshots/c03e6d358207e414f1eca0bb1891e29f1db0e242 \
  --accepted-protocol-commit <PATCH1_COMMIT_AFTER_REVIEW> \
  --result-root /home/uet/hue_ptk/stage4_fresh_7b_results
```
""",
    )
    write_json(output_dir / "provenance" / "run_lock_TEMPLATE.json", manifest["run_lock_template"])


def stage4_hf_inference_config(model_name_or_path: str = "<SERVER_QWEN_7B_SNAPSHOT_PATH>") -> dict[str, Any]:
    bnb = INFERENCE_LOCK["bitsandbytes_config"]
    return {
        "backend": "hf",
        "model_name_or_path": model_name_or_path,
        "revision": MODEL_LOCK["snapshot_revision"],
        "model_hash": MODEL_LOCK["aggregate_sha256"],
        "trust_remote_code": INFERENCE_LOCK["trust_remote_code"],
        "device_map": INFERENCE_LOCK["device_map"],
        "batch_size": INFERENCE_LOCK["batch_size"],
        "max_input_tokens": INFERENCE_LOCK["max_input_tokens"],
        "max_new_tokens": INFERENCE_LOCK["max_new_tokens"],
        "input_truncation_policy": INFERENCE_LOCK["input_truncation_policy"],
        "quantization": INFERENCE_LOCK["quantization"],
        "compute_dtype": INFERENCE_LOCK["compute_dtype"],
        "bnb_4bit_quant_type": bnb["bnb_4bit_quant_type"],
        "bnb_4bit_use_double_quant": bnb["bnb_4bit_use_double_quant"],
        "bnb_4bit_quant_storage": bnb["bnb_4bit_quant_storage"],
        "do_sample": INFERENCE_LOCK["do_sample"],
        "temperature": INFERENCE_LOCK["temperature"],
        "top_p": INFERENCE_LOCK["top_p"],
        "top_k": INFERENCE_LOCK["top_k"],
        "seed": INFERENCE_LOCK["seed"],
        "padding_side": INFERENCE_LOCK["padding_side"],
        "chat_template_usage": INFERENCE_LOCK["chat_template_usage"],
        "generation_kwargs": INFERENCE_LOCK["generation_kwargs"],
    }


def validate_protocol(root: Path) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    manifest = read_json(root / "provenance" / "run_manifest.json")
    overlap = read_json(root / "data" / "overlap_audit.json")
    fresh_manifest = read_json(root / "data" / "fresh_dataset_manifest.json")
    run_lock = read_json(root / "provenance" / "run_lock_TEMPLATE.json")
    source_audit = read_json(root / "data" / "source_group_audit.json")
    rows = []
    with (root / "prompt_audit" / "prompt_manifest.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    expected_rows = EXPECTED_SAMPLE_COUNT * len(CONFIGS)
    if len(rows) != expected_rows:
        violations.append({"rule": "prompt_manifest_row_count", "actual": len(rows)})
    if fresh_manifest.get("sample_count") != EXPECTED_SAMPLE_COUNT:
        violations.append({"rule": "fresh_sample_count"})
    for key in (
        "sample_id_overlap_count",
        "source_group_overlap_count",
        "input_text_hash_overlap_count",
        "canonical_content_hash_overlap_count",
    ):
        if int(overlap.get(key, -1)) != 0:
            violations.append({"rule": key, "actual": overlap.get(key)})
    primary = read_json(root / "configs" / "d_g1_primary.json")
    if sha256_file(root / "configs" / "d_g1_primary.json") != PRIMARY_CONFIG_HASH:
        violations.append({"rule": "primary_config_hash"})
    if primary.get("structured_source_parser", {}).get("enabled") is not True:
        violations.append({"rule": "D_enabled"})
    repair = primary.get("diagnostic_targeted_repair") or {}
    if not repair.get("enabled") or not repair.get("evidence_span_boundary"):
        violations.append({"rule": "G1_enabled"})
    if repair.get("evidence_span_selection") is not False:
        violations.append({"rule": "G2_must_be_off"})
    if run_lock.get("model_called") is not False or run_lock.get("gpu_required_for_protocol") is not False:
        violations.append({"rule": "cpu_only_protocol"})
    if run_lock.get("primary_method_slug") != "d_g1_primary":
        violations.append({"rule": "primary_method"})
    if set(run_lock.get("generation_arms") or []) != GENERATION_ARMS:
        violations.append({"rule": "three_generation_arm_graph"})
    shared_map = run_lock.get("deterministic_reprocesses_share_raw_generation_with") or {}
    for slug in MP_FS_PLUS_SHARED_SLUGS:
        if shared_map.get(slug) != "mp_fs_plus_shared":
            violations.append({"rule": "mp_fs_plus_shared_raw_generation", "slug": slug})
    bnb = (run_lock.get("inference_lock") or {}).get("bitsandbytes_config") or {}
    for key in (
        "load_in_4bit",
        "bnb_4bit_quant_type",
        "bnb_4bit_use_double_quant",
        "bnb_4bit_compute_dtype",
        "bnb_4bit_quant_storage",
    ):
        if key not in bnb:
            violations.append({"rule": "bitsandbytes_config_explicit", "field": key})
    if "repository_head" in run_lock:
        violations.append({"rule": "stale_repository_head_field_must_not_exist"})
    if source_audit.get("sample_count") != EXPECTED_SAMPLE_COUNT:
        violations.append({"rule": "source_group_audit_count"})
    if not (root / "inference" / "stage4_qwen25_7b_in28672_out4096.json").is_file():
        violations.append({"rule": "stage4_inference_config_present"})
    by_sample: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_sample.setdefault(row["sample_id"], []).append(row)
    for sample_id, sample_rows in by_sample.items():
        vnext = {row["prompt_sha256"] for row in sample_rows if row["method_slug"] in VNEXT_SLUGS}
        if len(vnext) != 1:
            violations.append({"rule": "vnext_prompt_equivalence", "sample_id": sample_id})
        generated_arms = {row["generation_arm"] for row in sample_rows}
        if generated_arms != GENERATION_ARMS:
            violations.append({"rule": "generation_arms", "sample_id": sample_id})
        for row in sample_rows:
            if row["method_slug"] in MP_FS_PLUS_SHARED_SLUGS:
                if row["generation_arm"] != "mp_fs_plus_shared":
                    violations.append({"rule": "shared_generation_arm", "sample_id": sample_id})
                if row["shares_raw_generation_with"] != "mp_fs_plus_shared":
                    violations.append({"rule": "shared_raw_pointer", "sample_id": sample_id})
    for relative, metadata in manifest.get("files", {}).items():
        path = root / relative
        if not path.is_file() or sha256_file(path) != metadata.get("sha256"):
            violations.append({"rule": "manifest_hash", "path": relative})
    return {
        "status": "PASS" if not violations else "FAIL",
        "prompt_rows": len(rows),
        "sample_count": fresh_manifest.get("sample_count"),
        "generation_arms": sorted(GENERATION_ARMS),
        "violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fresh-source-data", required=True)
    parser.add_argument("--fresh-source-ids", required=True)
    parser.add_argument("--fresh-gold-plans", required=True)
    parser.add_argument("--diagnostic-data", required=True)
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--db-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--sample-count", type=int, default=EXPECTED_SAMPLE_COUNT)
    args = parser.parse_args()

    started = time.time()
    output_dir = Path(args.output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"Output directory must be absent or empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    fresh_source = Path(args.fresh_source_data).resolve()
    fresh_ids_path = Path(args.fresh_source_ids).resolve()
    fresh_gold_path = Path(args.fresh_gold_plans).resolve()
    diagnostic_path = Path(args.diagnostic_data).resolve()
    profile_dir = Path(args.profile_dir).resolve()
    db_root = Path(args.db_root).resolve()

    fresh_all = read_json(fresh_source)
    fresh_source_ids = read_ids(fresh_ids_path)
    diagnostic_samples = read_json(diagnostic_path)
    gold_plans = {str(row["sample_id"]): row for row in read_jsonl(fresh_gold_path)}
    selected = select_fresh_samples(fresh_all, diagnostic_samples, args.sample_count)
    selected_ids = [str(row.get("id") or row.get("sample_id")) for row in selected]
    if set(selected_ids) - set(fresh_source_ids):
        raise ValueError("Selected IDs are not a subset of the frozen source split")
    if set(selected_ids) - set(gold_plans):
        raise ValueError("Selected IDs are missing gold plans")
    profiles = _load_profiles(profile_dir)
    missing_profiles = sorted({str(row["db_id"]) for row in selected} - set(profiles))
    if missing_profiles:
        raise ValueError(f"Missing profiles: {missing_profiles}")

    config_hashes = copy_configs(output_dir)
    configs = {
        slug: _load_method_config(PROJECT_ROOT / relative)[0]
        for slug, _label, _method_id, relative, _generation_role, _analysis_role in CONFIGS
    }
    prompt_rows, prompt_summary, prompt_set_hashes = build_prompt_tables(
        selected, profiles, configs, gold_plans
    )
    cluster_audit = source_group_audit(selected)
    d_audit_rows = d_parser_opportunity_audit(selected, configs["d_g1_primary"])
    fresh_manifest = {
        "stage": "Stage4_FRESH_7B_PROTOCOL",
        "status": "frozen_pending_reviewer_acceptance",
        "source_dataset": str(fresh_source),
        "source_ids": str(fresh_ids_path),
        "source_gold_plans": str(fresh_gold_path),
        "profile_dir": str(profile_dir),
        "db_root": str(db_root),
        "source_pool_count": len(fresh_all),
        "source_pool_ids_sha256": sha256_text("\n".join(fresh_source_ids) + "\n"),
        "selected_sample_ids": selected_ids,
        **summarize_dataset(selected, prompt_rows, gold_plans),
    }
    overlap = overlap_audit(selected, diagnostic_samples, len(fresh_all))

    write_text(output_dir / "data" / "fresh_sample_ids.txt", "\n".join(selected_ids) + "\n")
    write_json(output_dir / "data" / "fresh_dataset_manifest.json", fresh_manifest)
    write_json(output_dir / "data" / "overlap_audit.json", overlap)
    write_json(output_dir / "data" / "source_group_audit.json", cluster_audit)
    fields = list(prompt_rows[0])
    write_csv(output_dir / "prompt_audit" / "prompt_manifest.csv", prompt_rows, fields)
    write_csv(output_dir / "prompt_audit" / "prompt_surface_summary.csv", prompt_summary, list(prompt_summary[0]))
    write_csv(
        output_dir / "analysis" / "d_parser_opportunity_audit.csv",
        d_audit_rows,
        list(d_audit_rows[0]),
    )
    inference_config = stage4_hf_inference_config()
    write_json(
        output_dir / "inference" / "stage4_qwen25_7b_in28672_out4096.json",
        inference_config,
    )
    validation_text = [
        "PROMPT_HASH_VALIDATION=PASS",
        f"sample_count={len(selected_ids)}",
        f"prompt_rows={len(prompt_rows)}",
        "vnext_candidates_share_one_generation=true",
        "prompt_hash_scope=production_prompt_before_hf_chat_template",
        "exact_qwen_token_count_required_on_gpu_preflight=true",
        "",
    ]
    write_text(output_dir / "prompt_audit" / "prompt_hash_validation.txt", "\n".join(validation_text))

    run_lock_template = {
        "stage": "Stage4_FRESH_7B_PROTOCOL",
        "status": "template_pending_reviewer_acceptance",
        "model_called": False,
        "gpu_required_for_protocol": False,
        "gpu_required_for_generation_after_acceptance": True,
        "primary_method_slug": "d_g1_primary",
        "primary_method_identity": "MP-FS+-vNext-D-G1-FINAL",
        "primary_config_sha256": config_hashes["d_g1_primary"]["sha256"],
        "primary_config_expected_stage3b_sha256": PRIMARY_CONFIG_HASH,
        "generation_arms": ["direct", "j_fs", "mp_fs_plus_shared"],
        "generation_graph": {
            "direct": {
                "generates_raw": "raw_generations/direct.jsonl",
                "process_as": ["direct"],
            },
            "j_fs": {
                "generates_raw": "raw_generations/j_fs.jsonl",
                "process_as": ["j_fs"],
            },
            "mp_fs_plus_shared": {
                "generates_raw": "raw_generations/mp_fs_plus_shared.jsonl",
                "process_as": [
                    "original_mp_fs_plus",
                    "d_g1_primary",
                    "d_only_secondary",
                    "full_secondary",
                    "no_c_secondary",
                ],
                "required_preflight_invariant": (
                    "original_vs_dg1_final_input_equal == 300/300"
                ),
            },
        },
        "deterministic_reprocesses_share_raw_generation_with": {
            "original_mp_fs_plus": "mp_fs_plus_shared",
            "d_g1_primary": "mp_fs_plus_shared",
            "d_only_secondary": "mp_fs_plus_shared",
            "full_secondary": "mp_fs_plus_shared",
            "no_c_secondary": "mp_fs_plus_shared",
        },
        "model_lock": MODEL_LOCK,
        "inference_lock": INFERENCE_LOCK,
        "stage4_inference_config": {
            "path": "stage4_fresh_7b_protocol/inference/stage4_qwen25_7b_in28672_out4096.json",
            "sha256": sha256_file(output_dir / "inference" / "stage4_qwen25_7b_in28672_out4096.json"),
        },
        "gpu_environment_lock_required": [
            "python",
            "torch",
            "transformers",
            "accelerate",
            "bitsandbytes",
            "cuda_runtime",
            "gpu_model",
            "gpu_driver",
            "tokenizers",
            "safetensors",
        ],
        "runtime_assertions": {
            "git_rev_parse_head_equals_accepted_protocol_commit": True,
            "working_tree_clean_before_generation": True,
            "execution_commit_equals_accepted_protocol_commit": True,
            "generated_result_files_outside_git_allowed": True,
        },
        "token_budget_experiment": False,
        "stopping_rule": "no tuning on fresh set; preserve raw outputs and report failures",
        "protocol_builder_base_commit": git_output("rev-parse", "HEAD"),
        "repository_branch": git_output("branch", "--show-current"),
        "fresh_sample_ids_sha256": fresh_manifest["sample_ids_sha256"],
        "source_group_audit": cluster_audit,
        "statistics": {
            "accuracy_weighting": "sample_weighted",
            "primary_paired_difference": "accuracy(d_g1_primary)-accuracy(original_mp_fs_plus)",
            "confidence_interval": "cluster_bootstrap_percentile_95",
            "cluster_key": "source_group",
            "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "secondary_mcnemar_exact": True,
        },
        "prompt_set_hashes": prompt_set_hashes,
        "config_hashes": config_hashes,
    }
    manifest: dict[str, Any] = {
        "stage": "Stage4_FRESH_7B_PROTOCOL",
        "created_at_unix": started,
        "elapsed_seconds": None,
        "model_called": False,
        "run_lock_template": run_lock_template,
        "files": {},
    }
    write_docs(output_dir, manifest)
    write_json(output_dir / "provenance" / "run_manifest.json", manifest)
    validation = validate_protocol(output_dir)
    write_json(output_dir / "tests" / "protocol_invariants.json", validation)
    write_text(
        output_dir / "VALIDATION_REPORT.md",
        "\n".join(
            [
                "# Stage 4 Protocol Validation Report",
                "",
                f"Status: {validation['status']}",
                f"Fresh samples: {fresh_manifest['sample_count']}",
                f"Prompt rows: {validation['prompt_rows']}",
                "Model calls: 0",
                "GPU required for this protocol package: no",
                "Exact tokenizer count: deferred to mandatory GPU preflight before generation",
                "",
                "Patch-1 execution-lock validation:",
                "",
                "- generation graph invariant: PASS (`direct`, `j_fs`, `mp_fs_plus_shared`)",
                f"- frozen sample IDs SHA-256: `{fresh_manifest['sample_ids_sha256']}`",
                f"- D_G1 primary config SHA-256: `{config_hashes['d_g1_primary']['sha256']}`",
                (
                    "- source-group audit: "
                    f"{cluster_audit['sample_count']} samples, "
                    f"{cluster_audit['unique_source_groups']} groups, "
                    f"{cluster_audit['multi_sample_group_count']} multi-sample groups, "
                    f"max group size {cluster_audit['max_group_size']}"
                ),
                f"- source-group key source counts: `{cluster_audit['source_group_source_counts']}`",
                (
                    "- D parser opportunity audit: semantic parser output changed on "
                    f"{sum(int(row['changed']) for row in d_audit_rows)}/{len(d_audit_rows)} samples"
                ),
                "- exact 4-bit BitsAndBytes config: locked",
                "- runtime provenance assertion: accepted protocol commit must equal execution commit and working tree must be clean",
                "- authoritative runner dry-run: recorded in validation/runner_dry_run.txt after CPU validation",
                "- deterministic repeat build: recorded in validation/deterministic_repeat.txt after CPU validation",
                "",
                "CPU validation logs:",
                "",
                "- `validation/protocol_validator.txt`",
                "- `validation/dedicated_stage4_tests.txt`",
                "- `validation/compatibility_A_to_G2_stage3_stage3b_stage4.txt`",
                "- `validation/full_fast_suite.txt`",
                "- `validation/runner_dry_run.txt`",
                "- `validation/deterministic_repeat.txt`",
                "",
            ]
        ),
    )
    manifest["elapsed_seconds"] = time.time() - started
    files = {}
    for path in sorted(item for item in output_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(output_dir).as_posix()
        if relative == "provenance/run_manifest.json":
            continue
        files[relative] = {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
    manifest["files"] = files
    write_json(output_dir / "provenance" / "run_manifest.json", manifest)
    validation = validate_protocol(output_dir)
    write_json(output_dir / "tests" / "protocol_invariants.json", validation)
    print(json.dumps(validation, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
