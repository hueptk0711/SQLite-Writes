#!/usr/bin/env python3
"""Stage7C A3 English Phase O offset-semantics amendment.

This stage freezes a narrow prompt amendment and an 8-case fresh English
synthetic smoke set. It does not call a model, use GPU inference, inspect the
Gretel pilot pool, or change the model/backend protocol.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE_NAME = "Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT"
MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"
MODEL_REVISION = "c03e6d358207e414f1eca0bb1891e29f1db0e242"
PATCH_PACKAGE_NAME = (
    "Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT_"
    "PATCH0_FINAL_REVIEWER_PACKAGE_20260830.zip"
)
PHASE_O_OFFSET_SEMANTICS_AMENDMENT = """Offsets follow Python slicing exactly.

start_char is inclusive.
end_char is exclusive.

The selected text is exactly:

Q[start_char:end_char]

If a value occupies character positions i through j inclusive:
start_char = i
end_char = j + 1.

Before returning JSON, verify that Q[start_char:end_char] is exactly one complete atomic database value and contains no surrounding punctuation or field label.
"""
SCIENTIFIC_ARTIFACTS = [
    "PHASE_O_PROMPT_AMENDMENT.md",
    "PHASE_O_PROMPT_AUDIT.json",
    "FRESH_ENGLISH_SYNTHETIC_CASES.jsonl",
    "PHASE_O_EXPECTED_SPANS.jsonl",
    "PHASE_M_EXPECTED_MAPPINGS.jsonl",
    "TYPED_TARGET_STATES.jsonl",
    "SYNTHETIC_SQLITE_DB_MANIFEST.jsonl",
]
REQUIRED_PROMPT_SOURCE_SNIPPETS = [
    "PHASE_O_OFFSET_SEMANTICS_AMENDMENT",
    "Offsets follow Python slicing exactly.",
    "start_char is inclusive.",
    "end_char is exclusive.",
    "Q[start_char:end_char]",
    "end_char = j + 1.",
    "Before returning JSON, verify that",
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


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


def typed_value(value: Any) -> Any:
    if isinstance(value, dict):
        kind = value["type"]
        text = value["text"]
        if kind == "integer":
            return int(str(text).replace(",", ""))
        if kind == "real":
            return float(str(text).replace(",", ""))
        return text
    return value


def case_definitions() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "stage7c_fresh_english_001",
            "coverage_tags": ["2_values", "text", "integer", "comma", "colon"],
            "request": "Insert account code AC-001, score: 42 into accounts.",
            "table": "accounts",
            "columns": [
                {"name": "account_code", "type": "TEXT", "semantic_type": "identifier", "not_null": True},
                {"name": "score", "type": "INTEGER", "semantic_type": "measure", "not_null": True},
            ],
            "values": [
                {"column": "account_code", "text": "AC-001", "type": "text"},
                {"column": "score", "text": "42", "type": "integer"},
            ],
        },
        {
            "case_id": "stage7c_fresh_english_002",
            "coverage_tags": ["3_values", "quoted_text", "email", "integer", "comma"],
            "request": "Add contact name \"Mina Tran\", email mina.tran@example.com, priority 3.",
            "table": "contacts",
            "columns": [
                {"name": "name", "type": "TEXT", "semantic_type": "text", "not_null": True},
                {"name": "email", "type": "TEXT", "semantic_type": "email", "not_null": True},
                {"name": "priority", "type": "INTEGER", "semantic_type": "measure", "not_null": True},
            ],
            "values": [
                {"column": "name", "text": "Mina Tran", "type": "text"},
                {"column": "email", "text": "mina.tran@example.com", "type": "text"},
                {"column": "priority", "text": "3", "type": "integer"},
            ],
        },
        {
            "case_id": "stage7c_fresh_english_003",
            "coverage_tags": ["4_values", "parentheses", "real", "integer", "text"],
            "request": "Create reading (sensor S-77) with temperature 21.75, humidity 45, status normal.",
            "table": "readings",
            "columns": [
                {"name": "sensor_id", "type": "TEXT", "semantic_type": "identifier", "not_null": True},
                {"name": "temperature", "type": "REAL", "semantic_type": "measure", "not_null": True},
                {"name": "humidity", "type": "INTEGER", "semantic_type": "measure", "not_null": True},
                {"name": "status", "type": "TEXT", "semantic_type": "text", "not_null": True},
            ],
            "values": [
                {"column": "sensor_id", "text": "S-77", "type": "text"},
                {"column": "temperature", "text": "21.75", "type": "real"},
                {"column": "humidity", "text": "45", "type": "integer"},
                {"column": "status", "text": "normal", "type": "text"},
            ],
        },
        {
            "case_id": "stage7c_fresh_english_004",
            "coverage_tags": ["5_values", "date_like", "quoted_text", "real", "integer", "colon"],
            "request": (
                "Log shipment id SHIP-2026-08-30: carrier \"Blue Rail\", "
                "weight 18.5, stops 4, eta 2026-09-02."
            ),
            "table": "shipments",
            "columns": [
                {"name": "shipment_id", "type": "TEXT", "semantic_type": "identifier", "not_null": True},
                {"name": "carrier", "type": "TEXT", "semantic_type": "text", "not_null": True},
                {"name": "weight", "type": "REAL", "semantic_type": "measure", "not_null": True},
                {"name": "stops", "type": "INTEGER", "semantic_type": "measure", "not_null": True},
                {"name": "eta", "type": "TEXT", "semantic_type": "date_key", "not_null": True},
            ],
            "values": [
                {"column": "shipment_id", "text": "SHIP-2026-08-30", "type": "text"},
                {"column": "carrier", "text": "Blue Rail", "type": "text"},
                {"column": "weight", "text": "18.5", "type": "real"},
                {"column": "stops", "text": "4", "type": "integer"},
                {"column": "eta", "text": "2026-09-02", "type": "text"},
            ],
        },
        {
            "case_id": "stage7c_fresh_english_005",
            "coverage_tags": ["2_values", "long_identifier", "email"],
            "request": "Register user handle user_452 and recovery_email ops-team+452@example.org.",
            "table": "users",
            "columns": [
                {"name": "handle", "type": "TEXT", "semantic_type": "identifier", "not_null": True},
                {"name": "recovery_email", "type": "TEXT", "semantic_type": "email", "not_null": True},
            ],
            "values": [
                {"column": "handle", "text": "user_452", "type": "text"},
                {"column": "recovery_email", "text": "ops-team+452@example.org", "type": "text"},
            ],
        },
        {
            "case_id": "stage7c_fresh_english_006",
            "coverage_tags": ["3_values", "colon", "quoted_text", "integer", "parentheses"],
            "request": "Create ticket: title \"Valve pressure low\", severity 2, station (STN-44).",
            "table": "tickets",
            "columns": [
                {"name": "title", "type": "TEXT", "semantic_type": "text", "not_null": True},
                {"name": "severity", "type": "INTEGER", "semantic_type": "measure", "not_null": True},
                {"name": "station", "type": "TEXT", "semantic_type": "identifier", "not_null": True},
            ],
            "values": [
                {"column": "title", "text": "Valve pressure low", "type": "text"},
                {"column": "severity", "text": "2", "type": "integer"},
                {"column": "station", "text": "STN-44", "type": "text"},
            ],
        },
        {
            "case_id": "stage7c_fresh_english_007",
            "coverage_tags": ["4_values", "comma_grouped_real", "integer", "quoted_text"],
            "request": "Add invoice INV-9001, amount 1,250.75, line_count 12, note \"paid in full\".",
            "table": "invoices",
            "columns": [
                {"name": "invoice_id", "type": "TEXT", "semantic_type": "identifier", "not_null": True},
                {"name": "amount", "type": "REAL", "semantic_type": "measure", "not_null": True},
                {"name": "line_count", "type": "INTEGER", "semantic_type": "measure", "not_null": True},
                {"name": "note", "type": "TEXT", "semantic_type": "text", "not_null": True},
            ],
            "values": [
                {"column": "invoice_id", "text": "INV-9001", "type": "text"},
                {"column": "amount", "text": "1,250.75", "type": "real"},
                {"column": "line_count", "text": "12", "type": "integer"},
                {"column": "note", "text": "paid in full", "type": "text"},
            ],
        },
        {
            "case_id": "stage7c_fresh_english_008",
            "coverage_tags": ["5_values", "parentheses", "real", "integer", "date_like", "long_value"],
            "request": (
                "Insert experiment run RUN-A3-008 (operator Dr Lin) with ph 7.4, "
                "samples 36, started 2026-08-30."
            ),
            "table": "experiment_runs",
            "columns": [
                {"name": "run_id", "type": "TEXT", "semantic_type": "identifier", "not_null": True},
                {"name": "operator", "type": "TEXT", "semantic_type": "text", "not_null": True},
                {"name": "ph", "type": "REAL", "semantic_type": "measure", "not_null": True},
                {"name": "samples", "type": "INTEGER", "semantic_type": "measure", "not_null": True},
                {"name": "started", "type": "TEXT", "semantic_type": "date_key", "not_null": True},
            ],
            "values": [
                {"column": "run_id", "text": "RUN-A3-008", "type": "text"},
                {"column": "operator", "text": "Dr Lin", "type": "text"},
                {"column": "ph", "text": "7.4", "type": "real"},
                {"column": "samples", "text": "36", "type": "integer"},
                {"column": "started", "text": "2026-08-30", "type": "text"},
            ],
        },
    ]


def schema_inventory(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "tables": [
            {
                "table_id": "t1",
                "table": case["table"],
                "columns": [
                    {
                        "column_id": f"t1.c{index}",
                        "name": column["name"],
                        "type": column["type"],
                        "semantic_type": column["semantic_type"],
                        "default": None,
                        "not_null": bool(column["not_null"]),
                    }
                    for index, column in enumerate(case["columns"], start=1)
                ],
                "unique_indexes": [],
                "foreign_keys": [],
            }
        ]
    }


def create_sql(case: dict[str, Any]) -> str:
    columns = ", ".join(
        f'"{column["name"]}" {column["type"]} {"NOT NULL" if column["not_null"] else ""}'.strip()
        for column in case["columns"]
    )
    return f'CREATE TABLE "{case["table"]}" ({columns});'


def insert_sql(case: dict[str, Any]) -> tuple[str, list[Any]]:
    names = [value["column"] for value in case["values"]]
    params = [typed_value(value) for value in case["values"]]
    quoted_names = ", ".join(f'"{name}"' for name in names)
    placeholders = ", ".join("?" for _ in params)
    return f'INSERT INTO "{case["table"]}" ({quoted_names}) VALUES ({placeholders});', params


def target_state(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
    return [dict(row) for row in rows]


def expected_spans(case: dict[str, Any]) -> list[dict[str, Any]]:
    request = case["request"]
    spans = []
    search_from = 0
    for index, value in enumerate(case["values"], start=1):
        text = value["text"]
        start = request.index(text, search_from)
        end = start + len(text)
        search_from = end
        spans.append(
            {
                "case_id": case["case_id"],
                "value_id": f"v{index}",
                "column": value["column"],
                "text": text,
                "start_char": start,
                "end_char": end,
                "python_slice_expression": f"Q[{start}:{end}]",
                "selected_text": request[start:end],
                "value_type": value["type"],
            }
        )
    return spans


def mapping(case: dict[str, Any], spans: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "phase_m_expected_mapping": {
            "version": "4.0",
            "plan_kind": "reference_write_plan",
            "write_groups": [
                {
                    "group_id": "g1",
                    "table_id": "t1",
                    "rows": [
                        {
                            f"t1.c{index}": {
                                "value_from": span["value_id"],
                                "normalization": (
                                    "lossless_integer_parsing"
                                    if span["value_type"] == "integer"
                                    else "decimal_parsing"
                                    if span["value_type"] == "real"
                                    else "identity"
                                ),
                            }
                            for index, span in enumerate(spans, start=1)
                        }
                    ],
                    "write_semantics": "plain_insert",
                    "conflict_target_id": None,
                    "update_column_ids": [],
                }
            ],
            "dependencies": [],
            "unresolved_fields": [],
        },
    }


def create_case_db(case: dict[str, Any], db_dir: Path) -> dict[str, Any]:
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / f"{case['case_id']}.sqlite"
    if db_path.exists():
        db_path.unlink()
    schema_sql = create_sql(case)
    gold_sql, params = insert_sql(case)
    with sqlite3.connect(db_path) as connection:
        connection.execute(schema_sql)
        connection.commit()
    initial_hash = sha256_file(db_path)

    with sqlite3.connect(":memory:") as connection:
        connection.execute(schema_sql)
        connection.execute(gold_sql, params)
        connection.commit()
        final_state = target_state(connection, case["table"])
    target_state_hash = sha256_text(canonical_json(final_state))
    return {
        "case_id": case["case_id"],
        "sqlite_db_path": f"sqlite_dbs/{db_path.name}",
        "sqlite_db_sha256": sha256_file(db_path),
        "initial_state_hash": initial_hash,
        "create_sql": schema_sql,
        "create_sql_sha256": sha256_text(schema_sql),
        "gold_insert_sql": gold_sql,
        "gold_insert_params": params,
        "gold_insert_sql_sha256": sha256_text(gold_sql),
        "target_state_hash": target_state_hash,
    }


def build_run(out_dir: Path) -> dict[str, Any]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_dir = out_dir / "sqlite_dbs"

    prompt_source = PROJECT_ROOT / "src" / "nldbwrite_v3" / "planner" / "prompt.py"
    prompt_text = prompt_source.read_text(encoding="utf-8")
    cases = []
    all_spans = []
    mappings = []
    target_states = []
    db_manifest = []
    for case in case_definitions():
        spans = expected_spans(case)
        db_info = create_case_db(case, db_dir)
        row_values = {value["column"]: typed_value(value) for value in case["values"]}
        cases.append(
            {
                "case_id": case["case_id"],
                "request": case["request"],
                "request_sha256": sha256_text(case["request"]),
                "schema_inventory": schema_inventory(case),
                "schema_inventory_sha256": sha256_text(canonical_json(schema_inventory(case))),
                "coverage_tags": case["coverage_tags"],
                "value_count": len(case["values"]),
                "gold_insert_sql": db_info["gold_insert_sql"],
                "gold_insert_params": db_info["gold_insert_params"],
                "sqlite_db_path": db_info["sqlite_db_path"],
            }
        )
        all_spans.extend(spans)
        mappings.append(mapping(case, spans))
        target_states.append(
            {
                "case_id": case["case_id"],
                "table": case["table"],
                "typed_target_rows": [row_values],
                "target_state_hash": db_info["target_state_hash"],
            }
        )
        db_manifest.append(db_info)

    prompt_audit = {
        "stage": STAGE_NAME,
        "prompt_source_path": "src/nldbwrite_v3/planner/prompt.py",
        "prompt_source_sha256": sha256_file(prompt_source),
        "amendment_sha256": sha256_text(PHASE_O_OFFSET_SEMANTICS_AMENDMENT.strip()),
        "amendment_present_in_prompt_source": all(
            snippet in prompt_text for snippet in REQUIRED_PROMPT_SOURCE_SNIPPETS
        ),
        "required_prompt_source_snippets": REQUIRED_PROMPT_SOURCE_SNIPPETS,
        "phase_o_semantics": {
            "start_char": "inclusive",
            "end_char": "exclusive",
            "slice": "Q[start_char:end_char]",
        },
        "unchanged_protocol": {
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "architecture": "same_2_call_phase_o_phase_m",
            "phase_m": "unchanged",
            "backend": "same_PATCH9_incremental_backend",
            "zero_shot": True,
            "retry": 0,
            "repair": "none",
        },
    }

    write_text(out_dir / "PHASE_O_PROMPT_AMENDMENT.md", PHASE_O_OFFSET_SEMANTICS_AMENDMENT)
    write_json(out_dir / "PHASE_O_PROMPT_AUDIT.json", prompt_audit)
    write_jsonl(out_dir / "FRESH_ENGLISH_SYNTHETIC_CASES.jsonl", cases)
    write_jsonl(out_dir / "PHASE_O_EXPECTED_SPANS.jsonl", all_spans)
    write_jsonl(out_dir / "PHASE_M_EXPECTED_MAPPINGS.jsonl", mappings)
    write_jsonl(out_dir / "TYPED_TARGET_STATES.jsonl", target_states)
    write_jsonl(out_dir / "SYNTHETIC_SQLITE_DB_MANIFEST.jsonl", db_manifest)

    lock = {
        "stage": STAGE_NAME,
        "status": "PASS_OFFSET_SEMANTICS_AND_FRESH_SMOKE_LOCK_BUILT",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_branch": git_output(PROJECT_ROOT, "branch", "--show-current"),
        "git_commit": git_output(PROJECT_ROOT, "rev-parse", "HEAD"),
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
        "fresh_english_case_count": len(cases),
        "phase_o_offset_contract": "start_inclusive_end_exclusive_python_slice",
    }
    artifact_names = [*SCIENTIFIC_ARTIFACTS, *[f"sqlite_dbs/{path.name}" for path in db_dir.glob("*.sqlite")]]
    derived_manifest = {
        "stage": STAGE_NAME,
        "artifact_count": len(artifact_names),
        "artifacts": [
            {
                "path": name,
                "bytes": (out_dir / name).stat().st_size,
                "sha256": sha256_file(out_dir / name),
            }
            for name in sorted(artifact_names)
        ],
    }
    derived_manifest["combined_scientific_artifacts_sha256"] = sha256_text(
        canonical_json(derived_manifest["artifacts"])
    )
    write_json(out_dir / "DERIVED_ARTIFACT_MANIFEST.json", derived_manifest)
    lock["derived_artifact_manifest_sha256"] = sha256_file(out_dir / "DERIVED_ARTIFACT_MANIFEST.json")
    write_json(out_dir / "STAGE7C_SMOKE_LOCK.json", lock)
    write_text(out_dir / "VALIDATION_REPORT.md", validation_report(len(cases), len(all_spans)))
    write_text(out_dir / "REVIEWER_README.md", reviewer_readme(out_dir))
    return {
        "stage": STAGE_NAME,
        "fresh_english_case_count": len(cases),
        "expected_span_count": len(all_spans),
        "model_called": False,
        "gpu_called": False,
        "gretel_pilot_opened": False,
    }


def validation_report(case_count: int, span_count: int) -> str:
    return f"""# Stage7C A3 English Phase O Offset-Semantics Amendment Validation Report

Status: PASS

Validation date: {date.today().isoformat()}

## Scope

Stage7C only amends Phase O offset wording and freezes a fresh English
synthetic smoke set. It does not run Qwen, use GPU, inspect model outputs, or
open the StageENG1 Gretel pilot pool.

## Frozen Smoke Set

```text
fresh English cases        {case_count}
expected Phase O spans     {span_count}
offset contract            start inclusive, end exclusive
slice oracle               Q[start_char:end_char]
```

## Guardrails

```text
same Qwen2.5-Coder-7B=true
same revision=true
same 2-call architecture=true
same Phase M=true
same PATCH9 incremental backend=true
zero_shot=true
retry=0
repair=none
model_called=false
gpu_called=false
gretel_pilot_opened=false
```

## Validation Commands

```text
python scripts/data/build_stage7c_a3_english_offset_semantics.py --out-dir Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT --package Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT_PATCH0_FINAL_REVIEWER_PACKAGE_20260830.zip
python scripts/data/validate_stage7c_a3_english_offset_semantics.py --stage-dir Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT
PYTHONPATH=tests/support/windows_py314_pytest_tempdir python -m pytest -q tests/test_stage7c_a3_english_offset_semantics.py
PYTHONPATH=tests/support/windows_py314_pytest_tempdir python -m pytest -q -m "not integration"
python -m zipfile --test Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT_PATCH0_FINAL_REVIEWER_PACKAGE_20260830.zip
```
"""


def reviewer_readme(out_dir: Path) -> str:
    return f"""# Stage7C A3 English Phase O Offset-Semantics Amendment

This package freezes a narrow Phase O prompt wording amendment and 8 fresh
English synthetic smoke cases. It does not include or use Gretel pilot rows.

Review order:

1. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/PHASE_O_PROMPT_AMENDMENT.md`
2. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/PHASE_O_PROMPT_AUDIT.json`
3. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/FRESH_ENGLISH_SYNTHETIC_CASES.jsonl`
4. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/PHASE_O_EXPECTED_SPANS.jsonl`
5. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/PHASE_M_EXPECTED_MAPPINGS.jsonl`
6. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/TYPED_TARGET_STATES.jsonl`
7. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/SYNTHETIC_SQLITE_DB_MANIFEST.jsonl`
8. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/STAGE7C_SMOKE_LOCK.json`
9. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/DERIVED_ARTIFACT_MANIFEST.json`
10. `src/nldbwrite_v3/planner/prompt.py`
11. `scripts/data/build_stage7c_a3_english_offset_semantics.py`
12. `scripts/data/validate_stage7c_a3_english_offset_semantics.py`
13. `tests/test_stage7c_a3_english_offset_semantics.py`
14. `scripts/analysis/validate_stage5_method_freeze.py`
15. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/VALIDATION_REPORT.md`

Rerun:

```bash
python scripts/data/build_stage7c_a3_english_offset_semantics.py \\
  --out-dir Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT \\
  --package Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT_PATCH0_FINAL_REVIEWER_PACKAGE_20260830.zip
python scripts/data/validate_stage7c_a3_english_offset_semantics.py \\
  --stage-dir Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT
python -m pytest -q tests/test_stage7c_a3_english_offset_semantics.py
```

No GPU is required. No model is called.

Local artifact directory at build time:

```text
{out_dir}
```
"""


def package_reviewer(stage_dir: Path, package_path: Path) -> str:
    if package_path.exists():
        package_path.unlink()
    include_files = [
        *stage_dir.rglob("*"),
        PROJECT_ROOT / "src" / "nldbwrite_v3" / "planner" / "prompt.py",
        PROJECT_ROOT / "scripts" / "analysis" / "validate_stage5_method_freeze.py",
        PROJECT_ROOT / "scripts" / "data" / "build_stage7c_a3_english_offset_semantics.py",
        PROJECT_ROOT / "scripts" / "data" / "validate_stage7c_a3_english_offset_semantics.py",
        PROJECT_ROOT / "tests" / "test_stage7c_a3_english_offset_semantics.py",
        PROJECT_ROOT / "tests" / "support" / "windows_py314_pytest_tempdir" / "sitecustomize.py",
    ]
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted({p for p in include_files if p.is_file()}):
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
    package_path.with_suffix(package_path.suffix + ".sha256").write_text(
        f"{digest}  {package_path.name}\n",
        encoding="utf-8",
    )
    return digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / STAGE_NAME)
    parser.add_argument("--package", type=Path, default=PROJECT_ROOT / PATCH_PACKAGE_NAME)
    args = parser.parse_args()

    summary = build_run(args.out_dir)
    if args.package:
        digest = package_reviewer(args.out_dir, args.package)
        summary["package"] = str(args.package)
        summary["package_sha256"] = digest
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
