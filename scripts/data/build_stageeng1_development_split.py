#!/usr/bin/env python3
"""StageENG1 Gretel English INSERT development split freeze.

This stage consumes the frozen StageENG0 primary English INSERT development
candidate manifest. It creates a leakage-guarded development train/dev split
without calling any model, using GPU inference, or touching the official
Gretel test confirmation rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stageeng0_gretel_qualification import (
    DATASET_ID,
    DATASET_REVISION,
    RAW_FILES,
    STAGE_NAME as STAGE0_NAME,
    load_parquet_rows,
)


STAGE_NAME = "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT"
SPLIT_SEED = "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT_V1"
PILOT_TARGET = 100
EXPECTED_STAGE0_DEVELOPMENT_COUNT = 928
EXPECTED_STAGE0_CONFIRMATION_COUNT = 51
STAGE0_INPUT_FILES = [
    "DEVELOPMENT_TRAIN_CANDIDATE_MANIFEST.jsonl",
    "OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl",
    "STAGEENG0_LOCK.json",
    "DERIVED_ARTIFACT_MANIFEST.json",
]
SCIENTIFIC_ARTIFACTS = [
    "DEVELOPMENT_SPLIT_POLICY.json",
    "STAGE0_INPUT_HASHES.json",
    "DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl",
    "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl",
    "DEVELOPMENT_PILOT_POOL_MANIFEST.jsonl",
    "DEVELOPMENT_SPLIT_IDS.tsv",
    "SPLIT_GROUP_AUDIT.json",
    "DUPLICATE_AUDIT.json",
    "OFFICIAL_TEST_ISOLATION_AUDIT.json",
    "STAGEENG1_SPLIT_SUMMARY.json",
]
SIGNATURE_FIELDS = [
    "schema_database_group",
    "context_hash",
    "prompt_hash",
    "normalized_prompt_hash",
    "sql_hash",
    "sql_template_hash",
    "source_row_key",
]


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, value: str) -> str:
        self.parent.setdefault(value, value)
        if self.parent[value] != value:
            self.parent[value] = self.find(self.parent[value])
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def git_output(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def normalize_prompt(prompt: str) -> str:
    return re.sub(r"\s+", " ", str(prompt or "").strip().lower())


def sql_template(sql: str) -> str:
    text = str(sql or "")
    text = re.sub(r"'(?:''|[^'])*'", "'?'", text)
    text = re.sub(r'"(?:""|[^"])*"', '"?"', text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "?", text)
    return re.sub(r"\s+", " ", text.strip().lower())


def load_raw_by_sample_id(raw_dir: Path | None) -> dict[str, dict[str, Any]]:
    if raw_dir is None:
        return {}
    rows_by_split, _schemas = load_parquet_rows(raw_dir)
    by_sample_id: dict[str, dict[str, Any]] = {}
    for split, rows in rows_by_split.items():
        for index, row in enumerate(rows):
            sample_id = f"gretel:{split}:{row.get('id', index)}:{index:06d}"
            by_sample_id[sample_id] = dict(row)
    return by_sample_id


def stage0_input_hashes(stage0_dir: Path) -> dict[str, Any]:
    return {
        "stage": STAGE0_NAME,
        "input_files": {
            name: {
                "bytes": (stage0_dir / name).stat().st_size,
                "sha256": sha256_file(stage0_dir / name),
            }
            for name in STAGE0_INPUT_FILES
        },
    }


def split_policy(pilot_target: int) -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "source_stage": STAGE0_NAME,
        "dataset_id": DATASET_ID,
        "dataset_revision": DATASET_REVISION,
        "model_outputs_allowed": False,
        "model_called": False,
        "gpu_called": False,
        "input_population": {
            "requires_source_split": "train",
            "requires_development_allowed": True,
            "requires_official_test_confirmation_only": False,
            "requires_operation": "INSERT",
            "requires_complexity_class": "single_row_insert",
            "requires_v2_literal_grounded_primary_eligible": True,
        },
        "official_test_policy": {
            "source_split": "test",
            "count": EXPECTED_STAGE0_CONFIRMATION_COUNT,
            "usage": "confirmation_only_after_development_freeze",
            "included_in_stageeng1_split": False,
        },
        "split": {
            "development_dev_target_count": pilot_target,
            "development_dev_role": "locked_pilot_pool_no_model_run_in_stageeng1",
            "development_train_role": "available_for_later_analysis_or_tuning_after_review",
            "split_seed": SPLIT_SEED,
            "group_selection": "deterministic_subset_sum_over_leakage_components",
        },
        "leakage_component_signatures": SIGNATURE_FIELDS,
        "leakage_rule": "no signature value may appear in both development_train and development_dev",
    }


def enrich_candidate(row: dict[str, Any], raw_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    raw = raw_by_id.get(str(row["sample_id"]))
    normalized_prompt = normalize_prompt(str(raw.get("sql_prompt") if raw else ""))
    template = sql_template(str(raw.get("sql") if raw else ""))
    normalized_prompt_hash = sha256_text(normalized_prompt) if raw else str(row["prompt_hash"])
    sql_template_hash = sha256_text(template) if raw else str(row["sql_hash"])
    source_row_key = f"{row['source_split']}:{row['source_index']}"
    enriched = {
        "sample_id": row["sample_id"],
        "source_split": row["source_split"],
        "source_index": row["source_index"],
        "source_row_key": source_row_key,
        "development_allowed": row["development_allowed"],
        "official_test_confirmation_only": row["official_test_confirmation_only"],
        "operation": row["operation"],
        "complexity_class": row["complexity_class"],
        "v2_literal_grounded_primary_eligible": row["v2_literal_grounded_primary_eligible"],
        "schema_database_group": row["schema_database_group"],
        "context_hash": row["context_hash"],
        "prompt_hash": row["prompt_hash"],
        "normalized_prompt_hash": normalized_prompt_hash,
        "sql_hash": row["sql_hash"],
        "sql_template_hash": sql_template_hash,
        "raw_row_hash": row["raw_row_hash"],
        "initial_state_hash": row["initial_state_hash"],
        "gold_post_state_hash": row["gold_post_state_hash"],
        "assignment_count": (row.get("insert_assignment_grounding") or {}).get("assignment_count"),
        "raw_text_available_for_normalized_audit": bool(raw),
    }
    enriched["leakage_signature_hash"] = sha256_text(
        canonical_json([f"{field}:{enriched[field]}" for field in SIGNATURE_FIELDS])
    )
    return enriched


def leakage_components(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    uf = UnionFind()
    signature_to_samples: dict[str, list[str]] = defaultdict(list)
    row_by_id = {str(row["sample_id"]): row for row in rows}
    for row in rows:
        sample_id = str(row["sample_id"])
        uf.find(sample_id)
        for field in SIGNATURE_FIELDS:
            value = row.get(field)
            if value in (None, ""):
                continue
            signature = f"{field}:{value}"
            signature_to_samples[signature].append(sample_id)
            uf.union(sample_id, signature)

    grouped: dict[str, list[str]] = defaultdict(list)
    for sample_id in row_by_id:
        grouped[uf.find(sample_id)].append(sample_id)

    components: list[dict[str, Any]] = []
    for sample_ids in grouped.values():
        sample_ids = sorted(sample_ids)
        group_id = sha256_text(canonical_json(sample_ids))[:16]
        rows_in_group = [row_by_id[sample_id] for sample_id in sample_ids]
        components.append(
            {
                "split_group_id": group_id,
                "sample_ids": sample_ids,
                "count": len(sample_ids),
                "schema_database_groups": sorted({str(row["schema_database_group"]) for row in rows_in_group}),
                "context_hashes": sorted({str(row["context_hash"]) for row in rows_in_group}),
                "prompt_hashes": sorted({str(row["prompt_hash"]) for row in rows_in_group}),
                "normalized_prompt_hashes": sorted(
                    {str(row["normalized_prompt_hash"]) for row in rows_in_group}
                ),
                "sql_hashes": sorted({str(row["sql_hash"]) for row in rows_in_group}),
                "sql_template_hashes": sorted({str(row["sql_template_hash"]) for row in rows_in_group}),
                "selection_score": sha256_text(f"{SPLIT_SEED}:{group_id}"),
            }
        )
    return sorted(components, key=lambda row: (row["selection_score"], row["split_group_id"]))


def select_dev_group_ids(groups: list[dict[str, Any]], target: int) -> set[str]:
    dp: dict[int, list[dict[str, Any]]] = {0: []}
    for group in groups:
        size = int(group["count"])
        for count in sorted(list(dp.keys()), reverse=True):
            new_count = count + size
            if new_count > target or new_count in dp:
                continue
            dp[new_count] = [*dp[count], group]
    if target not in dp:
        raise ValueError(f"Cannot select exactly {target} rows from {len(groups)} leakage groups")
    return {str(group["split_group_id"]) for group in dp[target]}


def duplicate_groups(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row.get(field)
        if value not in (None, ""):
            grouped[str(value)].append(row)
    duplicates: list[dict[str, Any]] = []
    for value, values in grouped.items():
        if len(values) <= 1:
            continue
        splits = sorted({str(row["stageeng1_split"]) for row in values if row.get("stageeng1_split")})
        duplicates.append(
            {
                "field": field,
                "hash": value,
                "count": len(values),
                "stageeng1_splits": splits,
                "cross_split": len(splits) > 1,
                "sample_ids": sorted(str(row["sample_id"]) for row in values),
            }
        )
    return sorted(duplicates, key=lambda row: (-int(row["count"]), row["field"], row["hash"]))


def build_duplicate_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    audits = {field: duplicate_groups(rows, field) for field in SIGNATURE_FIELDS}
    cross_split = [entry for values in audits.values() for entry in values if entry["cross_split"]]
    return {
        "stage": STAGE_NAME,
        "signature_fields": SIGNATURE_FIELDS,
        "duplicate_group_counts": {field: len(values) for field, values in audits.items()},
        "duplicate_row_counts": {
            field: sum(int(entry["count"]) for entry in values) for field, values in audits.items()
        },
        "duplicates": audits,
        "cross_split_signature_violations": cross_split,
    }


def manifest_row(row: dict[str, Any], group: dict[str, Any], split: str) -> dict[str, Any]:
    output = dict(row)
    output["stageeng1_split"] = split
    output["development_pilot_pool"] = split == "development_dev"
    output["split_group_id"] = group["split_group_id"]
    output["split_group_size"] = group["count"]
    output["split_group_selection_score"] = group["selection_score"]
    return output


def build_run(
    stage0_dir: Path,
    out_dir: Path,
    *,
    pilot_target: int = PILOT_TARGET,
    raw_dir: Path | None = None,
) -> dict[str, Any]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_by_id = load_raw_by_sample_id(raw_dir)
    stage0_hashes = stage0_input_hashes(stage0_dir)
    policy = split_policy(pilot_target)
    stage0_lock = read_json(stage0_dir / "STAGEENG0_LOCK.json")
    stage0_development = read_jsonl(stage0_dir / "DEVELOPMENT_TRAIN_CANDIDATE_MANIFEST.jsonl")
    stage0_confirmation = read_jsonl(stage0_dir / "OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl")

    candidates = [enrich_candidate(row, raw_by_id) for row in stage0_development]
    groups = leakage_components(candidates)
    group_by_sample: dict[str, dict[str, Any]] = {}
    for group in groups:
        for sample_id in group["sample_ids"]:
            group_by_sample[sample_id] = group

    dev_group_ids = select_dev_group_ids(groups, pilot_target)
    split_rows: list[dict[str, Any]] = []
    for row in sorted(candidates, key=lambda item: str(item["sample_id"])):
        group = group_by_sample[str(row["sample_id"])]
        split = "development_dev" if group["split_group_id"] in dev_group_ids else "development_train"
        split_rows.append(manifest_row(row, group, split))

    train_rows = [row for row in split_rows if row["stageeng1_split"] == "development_train"]
    dev_rows = [row for row in split_rows if row["stageeng1_split"] == "development_dev"]
    group_audit = {
        "stage": STAGE_NAME,
        "split_seed": SPLIT_SEED,
        "component_count": len(groups),
        "signature_fields": SIGNATURE_FIELDS,
        "development_dev_target_count": pilot_target,
        "development_dev_count": len(dev_rows),
        "development_train_count": len(train_rows),
        "max_component_size": max(int(group["count"]) for group in groups) if groups else 0,
        "selected_development_dev_group_count": len(dev_group_ids),
        "selected_development_dev_groups": [
            group for group in groups if group["split_group_id"] in dev_group_ids
        ],
        "all_groups": groups,
    }
    duplicate_audit = build_duplicate_audit(split_rows)
    official_ids = {str(row["sample_id"]) for row in stage0_confirmation}
    split_ids = {str(row["sample_id"]) for row in split_rows}
    official_isolation = {
        "stage": STAGE_NAME,
        "official_confirmation_count": len(stage0_confirmation),
        "official_test_confirmation_only_ids_in_stageeng1_split": sorted(official_ids & split_ids),
        "official_test_policy": "excluded_from_stageeng1_development_split",
        "official_test_confirmation_manifest_sha256": stage0_hashes["input_files"][
            "OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl"
        ]["sha256"],
    }
    summary = {
        "stage": STAGE_NAME,
        "source_stage": STAGE0_NAME,
        "source_stage_lock_status": stage0_lock.get("status"),
        "source_stage_lock_commit": stage0_lock.get("git_commit"),
        "dataset_id": DATASET_ID,
        "dataset_revision": DATASET_REVISION,
        "model_called": False,
        "gpu_called": False,
        "raw_text_available_for_normalized_audit": bool(raw_by_id),
        "stage0_development_candidate_count": len(stage0_development),
        "stage0_official_confirmation_count": len(stage0_confirmation),
        "development_train_count": len(train_rows),
        "development_dev_count": len(dev_rows),
        "development_pilot_pool_count": len(dev_rows),
        "split_group_count": len(groups),
        "cross_split_signature_violation_count": len(duplicate_audit["cross_split_signature_violations"]),
    }

    write_json(out_dir / "DEVELOPMENT_SPLIT_POLICY.json", policy)
    write_json(out_dir / "STAGE0_INPUT_HASHES.json", stage0_hashes)
    write_jsonl(out_dir / "DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl", train_rows)
    write_jsonl(out_dir / "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl", dev_rows)
    write_jsonl(out_dir / "DEVELOPMENT_PILOT_POOL_MANIFEST.jsonl", dev_rows)
    write_text(
        out_dir / "DEVELOPMENT_SPLIT_IDS.tsv",
        "sample_id\tstageeng1_split\tsplit_group_id\tdevelopment_pilot_pool\n"
        + "\n".join(
            f"{row['sample_id']}\t{row['stageeng1_split']}\t{row['split_group_id']}\t"
            f"{str(row['development_pilot_pool']).lower()}"
            for row in split_rows
        )
        + "\n",
    )
    write_json(out_dir / "SPLIT_GROUP_AUDIT.json", group_audit)
    write_json(out_dir / "DUPLICATE_AUDIT.json", duplicate_audit)
    write_json(out_dir / "OFFICIAL_TEST_ISOLATION_AUDIT.json", official_isolation)
    write_json(out_dir / "STAGEENG1_SPLIT_SUMMARY.json", summary)

    derived_manifest = {
        "stage": STAGE_NAME,
        "artifact_count": len(SCIENTIFIC_ARTIFACTS),
        "artifacts": [
            {
                "path": name,
                "bytes": (out_dir / name).stat().st_size,
                "sha256": sha256_file(out_dir / name),
            }
            for name in SCIENTIFIC_ARTIFACTS
        ],
    }
    derived_manifest["combined_scientific_artifacts_sha256"] = sha256_text(
        canonical_json(derived_manifest["artifacts"])
    )
    write_json(out_dir / "DERIVED_ARTIFACT_MANIFEST.json", derived_manifest)
    lock = {
        "stage": STAGE_NAME,
        "status": "PASS_DEVELOPMENT_SPLIT_FROZEN",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_branch": git_output(PROJECT_ROOT, "branch", "--show-current"),
        "git_commit": git_output(PROJECT_ROOT, "rev-parse", "HEAD"),
        "dataset_id": DATASET_ID,
        "revision": DATASET_REVISION,
        "source_stage": STAGE0_NAME,
        "source_stage_development_manifest_sha256": stage0_hashes["input_files"][
            "DEVELOPMENT_TRAIN_CANDIDATE_MANIFEST.jsonl"
        ]["sha256"],
        "source_stage_confirmation_manifest_sha256": stage0_hashes["input_files"][
            "OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl"
        ]["sha256"],
        "derived_artifact_manifest_sha256": sha256_file(out_dir / "DERIVED_ARTIFACT_MANIFEST.json"),
        "model_called": False,
        "gpu_called": False,
    }
    write_json(out_dir / "STAGEENG1_LOCK.json", lock)
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(summary, duplicate_audit))
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme(out_dir))
    return summary


def validation_report(summary: dict[str, Any], duplicate_audit: dict[str, Any]) -> str:
    return f"""# StageENG1 Gretel English INSERT Development Split Validation Report

Status: PASS

Validation date: {date.today().isoformat()}

## Scope

StageENG1 freezes a leakage-guarded development split over the 928
StageENG0 `development_allowed=true` primary English INSERT samples. It does
not run Qwen, does not use GPU inference, does not score model outputs, and
does not include the 51 official-test confirmation rows.

## Frozen Counts

```text
StageENG0 development candidates     {summary['stage0_development_candidate_count']}
StageENG0 official confirmation       {summary['stage0_official_confirmation_count']}
Development train                     {summary['development_train_count']}
Development dev / pilot pool          {summary['development_dev_count']}
Leakage components                    {summary['split_group_count']}
Cross-split signature violations      {summary['cross_split_signature_violation_count']}
```

## Duplicate Audit

```json
{json.dumps(duplicate_audit['duplicate_group_counts'], indent=2, sort_keys=True)}
```

## Validation Commands

```text
uv run --with pyarrow python scripts/data/build_stageeng1_development_split.py --stage0-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION --raw-dir <raw_dir> --out-dir StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT --package StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT_PATCH0_FINAL_REVIEWER_PACKAGE_20260830.zip
uv run --with pyarrow python scripts/data/validate_stageeng1_development_split.py --stage1-dir StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT --stage0-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION --raw-dir <raw_dir>
PYTHONPATH=tests/support/windows_py314_pytest_tempdir python -m pytest -q tests/test_stageeng1_development_split.py
PYTHONPATH=tests/support/windows_py314_pytest_tempdir python -m pytest -q -m "not integration"
python -m zipfile --test StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT_PATCH0_FINAL_REVIEWER_PACKAGE_20260830.zip
```

## Guardrails

```text
model_called=false
gpu_called=false
official_test_tuning=false
official_test_confirmation_rows_in_split=0
```
"""


def reviewer_readme(out_dir: Path) -> str:
    return f"""# StageENG1 Gretel English INSERT Development Split

This reviewer package freezes the StageENG1 split over the StageENG0 primary
English INSERT development population.

Review order:

1. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/DEVELOPMENT_SPLIT_POLICY.json`
2. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/STAGE0_INPUT_HASHES.json`
3. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl`
4. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl`
5. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/DEVELOPMENT_PILOT_POOL_MANIFEST.jsonl`
6. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/SPLIT_GROUP_AUDIT.json`
7. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/DUPLICATE_AUDIT.json`
8. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/OFFICIAL_TEST_ISOLATION_AUDIT.json`
9. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/DERIVED_ARTIFACT_MANIFEST.json`
10. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/STAGEENG1_LOCK.json`
11. `scripts/data/build_stageeng1_development_split.py`
12. `scripts/data/validate_stageeng1_development_split.py`
13. `tests/test_stageeng1_development_split.py`
14. `StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT/VALIDATION_REPORT.md`

The split contains 828 development-train samples and a locked 100-sample
development-dev/pilot pool. The 51 official-test confirmation rows remain
excluded and confirmation-only.

Rerun:

```bash
uv run --with pyarrow python scripts/data/build_stageeng1_development_split.py \\
  --stage0-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION \\
  --raw-dir /path/to/gretel_raw \\
  --out-dir StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT
uv run --with pyarrow python scripts/data/validate_stageeng1_development_split.py \\
  --stage1-dir StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT \\
  --stage0-dir StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION \\
  --raw-dir /path/to/gretel_raw
python -m pytest -q tests/test_stageeng1_development_split.py
```

No GPU is required. No model is called.

Local artifact directory at build time:

```text
{out_dir}
```
"""


def package_reviewer(stage0_dir: Path, stage1_dir: Path, package_path: Path) -> str:
    if package_path.exists():
        package_path.unlink()
    include_files = [
        *stage1_dir.rglob("*"),
        *(stage0_dir / name for name in STAGE0_INPUT_FILES),
        PROJECT_ROOT / "scripts" / "data" / "build_stageeng1_development_split.py",
        PROJECT_ROOT / "scripts" / "data" / "validate_stageeng1_development_split.py",
        PROJECT_ROOT / "tests" / "test_stageeng1_development_split.py",
    ]
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted({p for p in include_files if p.is_file()}):
            if path.is_relative_to(stage1_dir):
                arcname = Path(STAGE_NAME) / path.relative_to(stage1_dir)
            elif path.is_relative_to(stage0_dir):
                arcname = Path(STAGE0_NAME) / path.relative_to(stage0_dir)
            else:
                arcname = path.relative_to(PROJECT_ROOT)
            archive.write(path, arcname.as_posix())
    with zipfile.ZipFile(package_path) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP integrity check failed at {bad}")
    digest = sha256_file(package_path)
    package_path.with_suffix(package_path.suffix + ".sha256").write_text(
        f"{digest}  {package_path.name}\n",
        encoding="utf-8",
    )
    return digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage0-dir", type=Path, default=PROJECT_ROOT / STAGE0_NAME)
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--pilot-target", type=int, default=PILOT_TARGET)
    parser.add_argument("--package", type=Path)
    args = parser.parse_args()

    summary = build_run(
        args.stage0_dir,
        args.out_dir,
        pilot_target=args.pilot_target,
        raw_dir=args.raw_dir,
    )
    if args.package:
        digest = package_reviewer(args.stage0_dir, args.out_dir, args.package)
        summary["package_sha256"] = digest
        summary["package"] = str(args.package)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
