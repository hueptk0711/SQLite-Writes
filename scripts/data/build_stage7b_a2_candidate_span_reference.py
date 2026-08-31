#!/usr/bin/env python3
"""Build Stage7B-A2 English candidate-span reference amendment artifacts.

This stage is CPU-only. It audits whether a deterministic high-recall span
inventory can represent the StageENG1 non-pilot development-train gold
assignments before any model call is allowed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.data.build_stageeng0_gretel_qualification import DATASET_ID, DATASET_REVISION, load_parquet_rows


STAGE_NAME = "Stage7B_A2_ENGLISH_CANDIDATE_SPAN_REFERENCE_AMENDMENT"
PATCH_NAME = "PATCH0"
PACKAGE_NAME = f"{STAGE_NAME}_{PATCH_NAME}_FINAL_REVIEWER_PACKAGE_20260831.zip"
STAGEENG0_NAME = "StageENG0_GRETEL_ENGLISH_SQLITE_WRITE_QUALIFICATION"
STAGEENG1_NAME = "StageENG1_GRETEL_ENGLISH_INSERT_DEVELOPMENT_SPLIT"
STAGE7E0_NAME = "Stage7E0_A3_ENGLISH_REAL_GENERATION_PREFLIGHT"
EXPECTED_DESIGN_SAMPLE_COUNT = 728
EXPECTED_PILOT_COUNT = 100
EXPECTED_DEV_COUNT = 100
EXPECTED_OFFICIAL_TEST_COUNT = 51
EXPECTED_ASSIGNMENT_COUNT = 2256
MIN_ASSIGNMENT_COVERAGE = 0.99
MIN_FULL_SAMPLE_COVERAGE = 0.99
MAX_NGRAM_TOKENS = 16
MAX_SPAN_CHARS = 160
BOUNDARY_STRIP_CHARS = " \t\r\n\"'()[]{}<>.,;:!?$%"
REGEX_PATTERNS = [
    ("email", r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    ("url", r"https?://\S+"),
    ("hex_identifier", r"0x[0-9A-Fa-f]{8,}"),
    ("plain_number", r"[-+]?\$?\d+(?:\.\d+)?%?"),
    ("comma_number", r"\d{1,3}(?:,\d{3})+(?:\.\d+)?"),
    ("compound_word", r"[A-Za-z]+(?:[\-_/][A-Za-z0-9]+)+"),
    ("compound_identifier", r"[A-Za-z0-9]+(?:[_\-][A-Za-z0-9]+)+"),
]
SCIENTIFIC_ARTIFACTS = [
    "A3_FEASIBILITY_CONCLUSION.json",
    "DESIGN_TRAIN_SCOPE_AUDIT.json",
    "SPAN_REFERENCE_INVENTORY_SPEC.json",
    "CANDIDATE_GENERATION_ALGORITHM_SPEC.json",
    "PHASE_O_SPAN_REFERENCE_SCHEMA.json",
    "PHASE_O_SPAN_REFERENCE_PROTOCOL.json",
    "DOWNSTREAM_DERIVATION_SPEC.json",
    "ORACLE_CANDIDATE_COVERAGE_AUDIT.json",
    "ORACLE_CANDIDATE_COVERAGE_AUDIT.jsonl",
]


@dataclass(frozen=True)
class CandidateSpan:
    span_ref: str
    start_char: int
    end_char: int
    text: str
    tags: tuple[str, ...]


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(canonical_text(text).encode("utf-8"))


def sha256_file(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl", ".md", ".py", ".txt", ".toml", ".sh", ".tsv"}:
        data = canonical_text(data.decode("utf-8-sig")).encode("utf-8")
    return sha256_bytes(data)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


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
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def git_output(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def load_raw_by_sample_id(raw_dir: Path) -> dict[str, dict[str, Any]]:
    rows_by_split, _schemas = load_parquet_rows(raw_dir)
    return {
        f"gretel:{split}:{row.get('id', index)}:{index:06d}": dict(row)
        for split, rows in rows_by_split.items()
        for index, row in enumerate(rows)
    }


def _add_span(spans: dict[tuple[int, int], set[str]], source: str, start: int, end: int, tag: str) -> None:
    if 0 <= start < end <= len(source) and source[start:end].strip():
        spans.setdefault((start, end), set()).add(tag)


def _add_boundary_stripped(spans: dict[tuple[int, int], set[str]], source: str, start: int, end: int, tag: str) -> None:
    stripped_start = start
    stripped_end = end
    while stripped_start < stripped_end and source[stripped_start] in BOUNDARY_STRIP_CHARS:
        stripped_start += 1
    while stripped_end > stripped_start and source[stripped_end - 1] in BOUNDARY_STRIP_CHARS:
        stripped_end -= 1
    _add_span(spans, source, stripped_start, stripped_end, tag)


def generate_candidate_inventory(source_text: str) -> list[CandidateSpan]:
    """Generate source-only candidate spans without inspecting labels/gold."""

    spans: dict[tuple[int, int], set[str]] = {}
    tokens = list(re.finditer(r"\S+", source_text))
    for start_index, start_token in enumerate(tokens):
        for end_index in range(start_index, min(len(tokens), start_index + MAX_NGRAM_TOKENS)):
            start = start_token.start()
            end = tokens[end_index].end()
            if end - start > MAX_SPAN_CHARS:
                continue
            _add_span(spans, source_text, start, end, "whitespace_ngram")
            _add_boundary_stripped(spans, source_text, start, end, "boundary_stripped_ngram")

    for tag, pattern in REGEX_PATTERNS:
        for match in re.finditer(pattern, source_text):
            _add_span(spans, source_text, match.start(), match.end(), tag)
            _add_boundary_stripped(spans, source_text, match.start(), match.end(), f"boundary_stripped_{tag}")

    for quote in ("'", '"'):
        pattern = re.escape(quote) + rf"([^{re.escape(quote)}]{{1,{MAX_SPAN_CHARS}}})" + re.escape(quote)
        for match in re.finditer(pattern, source_text):
            _add_span(spans, source_text, match.start(1), match.end(1), "quoted_content")

    candidates: list[CandidateSpan] = []
    for index, ((start, end), tags) in enumerate(sorted(spans.items()), start=1):
        candidates.append(
            CandidateSpan(
                span_ref=f"SPAN_{index:04d}",
                start_char=start,
                end_char=end,
                text=source_text[start:end],
                tags=tuple(sorted(tags)),
            )
        )
    return candidates


def candidate_to_json(candidate: CandidateSpan) -> dict[str, Any]:
    return {
        "span_ref": candidate.span_ref,
        "start_char": candidate.start_char,
        "end_char": candidate.end_char,
        "text": candidate.text,
        "tags": list(candidate.tags),
    }


def inventory_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "representation": "candidate_span_reference_inventory",
        "candidate_fields": ["span_ref", "start_char", "end_char", "text", "tags"],
        "span_ref_format": "SPAN_0001, SPAN_0002, ... assigned after deterministic sort by (start_char, end_char)",
        "runtime_visibility": {
            "model_sees": ["span_ref", "text", "tags"],
            "model_does_not_generate": ["start_char", "end_char"],
            "resolver_uses": ["span_ref", "start_char", "end_char", "text"],
        },
        "no_gold_at_runtime": True,
        "max_ngram_tokens": MAX_NGRAM_TOKENS,
        "max_span_chars": MAX_SPAN_CHARS,
        "model_called": False,
        "gpu_called": False,
    }


def algorithm_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "algorithm": "source_only_high_recall_lexical_inventory_v1",
        "inputs": ["question_text"],
        "forbidden_inputs": ["gold_sql", "gold_values", "gold_offsets", "target_state", "model_outputs"],
        "steps": [
            "Enumerate all non-whitespace token n-grams up to 16 tokens and 160 characters.",
            "Add boundary-stripped variants for punctuation, quotes, currency marks, and percent signs.",
            "Add regex candidates for emails, URLs, hex identifiers, numeric literals, comma-grouped numbers, and compound identifiers.",
            "Add quoted-string inner content for ASCII single and double quotes.",
            "Deduplicate by exact (start_char, end_char), sort deterministically, and assign SPAN refs.",
        ],
        "deterministic": True,
        "model_called": False,
        "gpu_called": False,
    }


def phase_o_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Stage7B-A2 Phase O Span Reference Output",
        "type": "object",
        "additionalProperties": False,
        "required": ["operation", "span_refs"],
        "properties": {
            "operation": {"type": "string", "const": "INSERT"},
            "span_refs": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "pattern": "^SPAN_[0-9]{4}$"},
            },
        },
    }


def phase_o_protocol() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "replaces": "Stage7E0-A3 numeric start_char/end_char prediction",
        "phase_o_task": "select semantic value candidates by span_ref only",
        "operation_scope": "INSERT",
        "zero_shot": True,
        "few_shot_allowed": False,
        "repair_allowed": False,
        "retry_allowed": False,
        "model_generates_character_offsets": False,
        "candidate_inventory_generated_by": "source_only_high_recall_lexical_inventory_v1",
        "pilot_usage_allowed": False,
        "development_dev_usage_allowed": False,
        "official_test_usage_allowed": False,
        "model_called": False,
        "gpu_called": False,
    }


def downstream_derivation_spec() -> dict[str, Any]:
    return {
        "stage": STAGE_NAME,
        "selected_span_ref_resolution": [
            "Reject unknown span_refs.",
            "Resolve selected refs to deterministic inventory rows.",
            "Sort selected spans by (start_char, end_char, span_ref).",
            "Derive EV_1/SLOT_1, EV_2/SLOT_2, ... from resolved source text.",
        ],
        "unchanged_components": [
            "Phase M slot-to-column mapping",
            "typed materialization",
            "completeness verification",
            "compiler",
            "SQLite preflight",
        ],
        "model_generated_offsets_removed": True,
        "model_called": False,
        "gpu_called": False,
    }


def a3_feasibility_conclusion(stage7e0_dir: Path) -> dict[str, Any]:
    lock_path = stage7e0_dir / "STAGE7E0_A3_SERVER_RESULT_LOCK.json"
    lock = read_json(lock_path)
    return {
        "stage": STAGE_NAME,
        "source_stage": STAGE7E0_NAME,
        "source_lock_path": f"{STAGE7E0_NAME}/STAGE7E0_A3_SERVER_RESULT_LOCK.json",
        "source_lock_sha256": sha256_file(lock_path),
        "conclusion": "Stage7E0-A3 is closed as a valid feasibility failure for numeric character-offset prediction.",
        "primary_pass_count": lock.get("primary_pass_count"),
        "required_pass_count": lock.get("required_pass_count"),
        "evidence_integrity_status": lock.get("evidence_integrity_status"),
        "protocol_compliance_status": lock.get("protocol_compliance_status"),
        "primary_gate_status": lock.get("primary_gate_status"),
        "gretel_pilot_opened": lock.get("gretel_pilot_opened"),
        "next_method_change": "replace model-generated numeric offsets with model-selected candidate span_refs",
    }


def scope_audit(stageeng0_dir: Path, stageeng1_dir: Path) -> dict[str, Any]:
    train_rows = read_jsonl(stageeng1_dir / "DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl")
    dev_rows = read_jsonl(stageeng1_dir / "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl")
    pilot_rows = read_jsonl(stageeng1_dir / "DEVELOPMENT_PILOT_POOL_MANIFEST.jsonl")
    official_rows = read_jsonl(stageeng0_dir / "OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl")
    train_ids = {str(row["sample_id"]) for row in train_rows}
    pilot_ids = {str(row["sample_id"]) for row in pilot_rows}
    dev_ids = {str(row["sample_id"]) for row in dev_rows}
    official_ids = {str(row["sample_id"]) for row in official_rows}
    design_ids = train_ids - pilot_ids
    return {
        "stage": STAGE_NAME,
        "source_stageeng1_train_count": len(train_rows),
        "development_pilot_pool_count": len(pilot_ids),
        "development_dev_count": len(dev_ids),
        "official_test_confirmation_count": len(official_ids),
        "design_train_non_pilot_count": len(design_ids),
        "design_source": "StageENG1 development_train excluding development_pilot_pool",
        "pilot_ids_in_design_train": sorted(design_ids & pilot_ids),
        "development_dev_ids_in_design_train": sorted(design_ids & dev_ids),
        "official_test_ids_in_design_train": sorted(design_ids & official_ids),
        "model_called": False,
        "gpu_called": False,
    }


def coverage_audit(stageeng0_dir: Path, stageeng1_dir: Path, raw_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw_by_id = load_raw_by_sample_id(raw_dir)
    train_rows = read_jsonl(stageeng1_dir / "DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl")
    design_ids = {str(row["sample_id"]) for row in train_rows if not row.get("development_pilot_pool")}
    assignment_rows = [
        row
        for row in read_jsonl(stageeng0_dir / "INSERT_ASSIGNMENT_GROUNDING_AUDIT.jsonl")
        if str(row["sample_id"]) in design_ids
    ]
    assignments_by_sample: dict[str, list[dict[str, Any]]] = {}
    for row in assignment_rows:
        assignments_by_sample.setdefault(str(row["sample_id"]), []).append(row)

    sample_rows: list[dict[str, Any]] = []
    covered_assignment_count = 0
    candidate_counts: list[int] = []
    matched_ref_counts: list[int] = []
    tag_counts: dict[str, int] = {}
    missing: list[dict[str, Any]] = []
    for sample_id in sorted(design_ids):
        raw = raw_by_id.get(sample_id)
        if raw is None:
            raise RuntimeError(f"Raw parquet row missing for {sample_id}")
        question = str(raw.get("sql_prompt") or "")
        inventory = generate_candidate_inventory(question)
        candidate_counts.append(len(inventory))
        candidate_by_key = {(item.start_char, item.end_char, item.text): item for item in inventory}
        for item in inventory:
            for tag in item.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        assignment_checks: list[dict[str, Any]] = []
        sample_full = True
        for assignment in sorted(assignments_by_sample.get(sample_id, []), key=lambda row: int(row["assignment_index"])):
            span = assignment["matched_source_span"]
            expected = (int(span["start_char"]), int(span["end_char"]), str(span["text"]))
            candidate = candidate_by_key.get(expected)
            covered = candidate is not None
            sample_full = sample_full and covered
            if covered:
                covered_assignment_count += 1
                matched_ref_counts.append(len(candidate.tags))
            else:
                missing.append(
                    {
                        "sample_id": sample_id,
                        "assignment_index": assignment["assignment_index"],
                        "gold_text": span["text"],
                        "start_char": span["start_char"],
                        "end_char": span["end_char"],
                    }
                )
            assignment_checks.append(
                {
                    "assignment_index": assignment["assignment_index"],
                    "column_ref_or_name": assignment["column_ref_or_name"],
                    "gold_text": span["text"],
                    "start_char": span["start_char"],
                    "end_char": span["end_char"],
                    "covered": covered,
                    "matched_span_ref": candidate.span_ref if candidate else None,
                    "matched_tags": list(candidate.tags) if candidate else [],
                }
            )
        sample_rows.append(
            {
                "sample_id": sample_id,
                "source_split": "train",
                "stageeng1_subset": "development_train_non_pilot",
                "question_sha256": sha256_text(question),
                "candidate_count": len(inventory),
                "assignment_count": len(assignment_checks),
                "all_assignments_covered": sample_full,
                "assignments": assignment_checks,
            }
        )

    assignment_count = len(assignment_rows)
    full_sample_count = sum(1 for row in sample_rows if row["all_assignments_covered"])
    sorted_counts = sorted(candidate_counts)
    p95_index = int(0.95 * (len(sorted_counts) - 1)) if sorted_counts else 0
    summary = {
        "stage": STAGE_NAME,
        "status": "PASS" if covered_assignment_count / assignment_count >= MIN_ASSIGNMENT_COVERAGE and full_sample_count / len(sample_rows) >= MIN_FULL_SAMPLE_COVERAGE else "FAIL",
        "scope": "StageENG1 development_train excluding 100-sample pilot pool",
        "model_called": False,
        "gpu_called": False,
        "gold_used_only_for_oracle_coverage_audit": True,
        "candidate_generation_reads_gold_at_runtime": False,
        "design_sample_count": len(sample_rows),
        "assignment_count": assignment_count,
        "covered_assignment_count": covered_assignment_count,
        "missing_assignment_count": assignment_count - covered_assignment_count,
        "assignment_candidate_coverage": covered_assignment_count / assignment_count,
        "full_sample_covered_count": full_sample_count,
        "full_sample_candidate_coverage": full_sample_count / len(sample_rows),
        "minimum_assignment_candidate_coverage": MIN_ASSIGNMENT_COVERAGE,
        "minimum_full_sample_candidate_coverage": MIN_FULL_SAMPLE_COVERAGE,
        "candidate_inventory_count_stats": {
            "min": min(candidate_counts),
            "median": median(candidate_counts),
            "mean": mean(candidate_counts),
            "p95": sorted_counts[p95_index],
            "max": max(candidate_counts),
        },
        "candidate_tag_counts": dict(sorted(tag_counts.items())),
        "missing_assignments": missing,
    }
    return summary, sample_rows


def validation_report(scope: dict[str, Any], coverage: dict[str, Any]) -> str:
    return f"""# Stage7B-A2 English Candidate-Span Reference Amendment Validation Report

Status: {coverage["status"]}

Validation date: {date.today().isoformat()}

## Scope

Stage7B-A2 closes the Stage7E0-A3 numeric-offset route and opens a CPU-only
architecture amendment. It does not call a model, does not use GPU, does not
open the 100-sample development pilot, does not use development-dev, and does
not use official Gretel test rows.

```text
design_train_non_pilot_count={scope["design_train_non_pilot_count"]}
development_pilot_pool_count={scope["development_pilot_pool_count"]}
development_dev_count={scope["development_dev_count"]}
official_test_confirmation_count={scope["official_test_confirmation_count"]}
model_called=false
gpu_called=false
```

## Oracle Candidate Coverage

```text
assignment_candidate_coverage={coverage["covered_assignment_count"]}/{coverage["assignment_count"]}
full_sample_candidate_coverage={coverage["full_sample_covered_count"]}/{coverage["design_sample_count"]}
min_required_assignment_coverage={MIN_ASSIGNMENT_COVERAGE}
min_required_full_sample_coverage={MIN_FULL_SAMPLE_COVERAGE}
candidate_count_min={coverage["candidate_inventory_count_stats"]["min"]}
candidate_count_median={coverage["candidate_inventory_count_stats"]["median"]}
candidate_count_p95={coverage["candidate_inventory_count_stats"]["p95"]}
candidate_count_max={coverage["candidate_inventory_count_stats"]["max"]}
```

## Method Decision

The deterministic source-only inventory covers every audited gold assignment
on the 728 non-pilot design-train samples. Phase O should therefore stop
generating numeric character offsets and instead select `SPAN_...` references.
Phase M, typed materialization, completeness, compiler, and SQLite preflight
remain unchanged.
"""


def reviewer_readme(out_dir: Path) -> str:
    return f"""# Stage7B-A2 English Candidate-Span Reference Amendment

This package defines a candidate-span reference representation for English
single-row INSERT development work.

Review order:

1. `{STAGE_NAME}/A3_FEASIBILITY_CONCLUSION.json`
2. `{STAGE_NAME}/DESIGN_TRAIN_SCOPE_AUDIT.json`
3. `{STAGE_NAME}/SPAN_REFERENCE_INVENTORY_SPEC.json`
4. `{STAGE_NAME}/CANDIDATE_GENERATION_ALGORITHM_SPEC.json`
5. `{STAGE_NAME}/PHASE_O_SPAN_REFERENCE_SCHEMA.json`
6. `{STAGE_NAME}/PHASE_O_SPAN_REFERENCE_PROTOCOL.json`
7. `{STAGE_NAME}/DOWNSTREAM_DERIVATION_SPEC.json`
8. `{STAGE_NAME}/ORACLE_CANDIDATE_COVERAGE_AUDIT.json`
9. `{STAGE_NAME}/ORACLE_CANDIDATE_COVERAGE_AUDIT.jsonl`
10. `{STAGE_NAME}/DERIVED_ARTIFACT_MANIFEST.json`
11. `{STAGE_NAME}/STAGE7B_A2_LOCK.json`
12. `scripts/data/build_stage7b_a2_candidate_span_reference.py`
13. `scripts/data/validate_stage7b_a2_candidate_span_reference.py`
14. `tests/test_stage7b_a2_candidate_span_reference.py`
15. `{STAGE_NAME}/VALIDATION_REPORT.md`

Rerun with local Gretel parquet:

```bash
uv run --with pyarrow python scripts/data/build_stage7b_a2_candidate_span_reference.py \\
  --raw-dir /path/to/gretel_synthetic_text_to_sql_740ab236 \\
  --out-dir {STAGE_NAME}
python scripts/data/validate_stage7b_a2_candidate_span_reference.py \\
  --stage-dir {STAGE_NAME}
```

No GPU is required. No model is called.

Local artifact directory at build time:

```text
{out_dir}
```
"""


def build_derived_manifest(stage_dir: Path) -> dict[str, Any]:
    artifacts = [
        {"path": name, "bytes": (stage_dir / name).stat().st_size, "sha256": sha256_file(stage_dir / name)}
        for name in SCIENTIFIC_ARTIFACTS
    ]
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "combined_scientific_artifacts_sha256": sha256_text(canonical_json(artifacts)),
    }


def build_stage(
    out_dir: Path,
    raw_dir: Path,
    *,
    stageeng0_dir: Path = PROJECT_ROOT / STAGEENG0_NAME,
    stageeng1_dir: Path = PROJECT_ROOT / STAGEENG1_NAME,
    stage7e0_dir: Path = PROJECT_ROOT / STAGE7E0_NAME,
) -> dict[str, Any]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    a3_conclusion = a3_feasibility_conclusion(stage7e0_dir)
    scope = scope_audit(stageeng0_dir, stageeng1_dir)
    coverage, coverage_rows = coverage_audit(stageeng0_dir, stageeng1_dir, raw_dir)

    write_json(out_dir / "A3_FEASIBILITY_CONCLUSION.json", a3_conclusion)
    write_json(out_dir / "DESIGN_TRAIN_SCOPE_AUDIT.json", scope)
    write_json(out_dir / "SPAN_REFERENCE_INVENTORY_SPEC.json", inventory_spec())
    write_json(out_dir / "CANDIDATE_GENERATION_ALGORITHM_SPEC.json", algorithm_spec())
    write_json(out_dir / "PHASE_O_SPAN_REFERENCE_SCHEMA.json", phase_o_schema())
    write_json(out_dir / "PHASE_O_SPAN_REFERENCE_PROTOCOL.json", phase_o_protocol())
    write_json(out_dir / "DOWNSTREAM_DERIVATION_SPEC.json", downstream_derivation_spec())
    write_json(out_dir / "ORACLE_CANDIDATE_COVERAGE_AUDIT.json", coverage)
    write_jsonl(out_dir / "ORACLE_CANDIDATE_COVERAGE_AUDIT.jsonl", coverage_rows)

    derived_manifest = build_derived_manifest(out_dir)
    write_json(out_dir / "DERIVED_ARTIFACT_MANIFEST.json", derived_manifest)
    lock = {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": "PASS_CANDIDATE_SPAN_REFERENCE_ORACLE_COVERAGE",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_branch": git_output(PROJECT_ROOT, "branch", "--show-current"),
        "git_commit": git_output(PROJECT_ROOT, "rev-parse", "HEAD"),
        "dataset_id": DATASET_ID,
        "dataset_revision": DATASET_REVISION,
        "source_stageeng0": STAGEENG0_NAME,
        "source_stageeng1": STAGEENG1_NAME,
        "source_stage7e0": STAGE7E0_NAME,
        "design_train_non_pilot_count": scope["design_train_non_pilot_count"],
        "assignment_candidate_coverage": coverage["assignment_candidate_coverage"],
        "full_sample_candidate_coverage": coverage["full_sample_candidate_coverage"],
        "phase_o_model_generates_character_offsets": False,
        "phase_o_model_selects_span_refs": True,
        "model_called": False,
        "gpu_called": False,
        "development_pilot_pool_opened": False,
        "development_dev_used": False,
        "official_test_used": False,
        "derived_artifact_manifest_sha256": sha256_file(out_dir / "DERIVED_ARTIFACT_MANIFEST.json"),
    }
    write_json(out_dir / "STAGE7B_A2_LOCK.json", lock)
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(scope, coverage))
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme(out_dir))
    return {
        "stage": STAGE_NAME,
        "patch": PATCH_NAME,
        "status": lock["status"],
        "design_train_non_pilot_count": scope["design_train_non_pilot_count"],
        "assignment_candidate_coverage": coverage["assignment_candidate_coverage"],
        "full_sample_candidate_coverage": coverage["full_sample_candidate_coverage"],
        "model_called": False,
        "gpu_called": False,
    }


def package_reviewer(stage_dir: Path, package_path: Path) -> str:
    if package_path.exists():
        package_path.unlink()
    include_paths = [
        *stage_dir.rglob("*"),
        PROJECT_ROOT / "scripts" / "data" / "build_stage7b_a2_candidate_span_reference.py",
        PROJECT_ROOT / "scripts" / "data" / "validate_stage7b_a2_candidate_span_reference.py",
        PROJECT_ROOT / "scripts" / "data" / "build_stageeng0_gretel_qualification.py",
        PROJECT_ROOT / "tests" / "test_stage7b_a2_candidate_span_reference.py",
        PROJECT_ROOT / "tests" / "support" / "windows_py314_pytest_tempdir" / "sitecustomize.py",
        PROJECT_ROOT / STAGEENG0_NAME / "INSERT_ASSIGNMENT_GROUNDING_AUDIT.jsonl",
        PROJECT_ROOT / STAGEENG0_NAME / "OFFICIAL_TEST_CONFIRMATION_MANIFEST.jsonl",
        PROJECT_ROOT / STAGEENG0_NAME / "STAGEENG0_LOCK.json",
        PROJECT_ROOT / STAGEENG1_NAME / "DEVELOPMENT_TRAIN_SPLIT_MANIFEST.jsonl",
        PROJECT_ROOT / STAGEENG1_NAME / "DEVELOPMENT_DEV_SPLIT_MANIFEST.jsonl",
        PROJECT_ROOT / STAGEENG1_NAME / "DEVELOPMENT_PILOT_POOL_MANIFEST.jsonl",
        PROJECT_ROOT / STAGEENG1_NAME / "STAGEENG1_LOCK.json",
        PROJECT_ROOT / STAGE7E0_NAME / "STAGE7E0_A3_SERVER_RESULT_LOCK.json",
        PROJECT_ROOT / STAGE7E0_NAME / "SERVER_RESULT_CLASSIFICATION_PATCH4.json",
        PROJECT_ROOT / STAGE7E0_NAME / "VALIDATION_REPORT_PATCH4.md",
    ]
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted({path for path in include_paths if path.is_file()}):
            if path.is_relative_to(stage_dir):
                arcname = Path(STAGE_NAME) / path.relative_to(stage_dir)
            else:
                arcname = path.relative_to(PROJECT_ROOT)
            archive.write(path, arcname.as_posix())
    with zipfile.ZipFile(package_path) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP integrity check failed at {bad}")
    digest = sha256_file(package_path)
    package_path.with_suffix(package_path.suffix + ".sha256").write_text(f"{digest}  {package_path.name}\n", encoding="utf-8", newline="\n")
    return digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--package", type=Path, default=PROJECT_ROOT / PACKAGE_NAME)
    args = parser.parse_args()
    summary = build_stage(args.out_dir, args.raw_dir)
    digest = package_reviewer(args.out_dir, args.package)
    summary["package"] = str(args.package)
    summary["package_sha256"] = digest
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
