#!/usr/bin/env python3
"""Build Stage7C-A2 Phase O prompt feasibility amendment artifacts.

This stage formalizes a narrow Phase O prompt amendment after Stage7E0 PATCH9
localized the remaining failure to semantic span selection. It does not run a
model, call a GPU, change datasets, or update gold labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / "stage7c_a2_phase_o_prompt_feasibility_amendment"
STAGE = "Stage7C_A2_PHASE_O_PROMPT_FEASIBILITY_AMENDMENT"
DATE = "20260829"
HASH_POLICY = "sha256_canonical_lf_for_utf8_text_raw_bytes_for_binary_inputs"
LOCK_FILE = "STAGE7C_A2_LOCK.json"
PASS_STATUS = "PASS_STAGE7C_A2_PHASE_O_PROMPT_FEASIBILITY_AMENDMENT_LOCKED"

MODEL_CALLED = False
GPU_CALLED = False
TRAIN_DEV_GENERATION_RUN = False
CONFIRMATION_481_EVALUATED = False
LIVESQLBENCH_GT_OPENED = False
ARCHITECTURE_CHANGED = False
PHASE_M_CHANGED = False
BACKEND_CHANGED = False

INPUTS = (
    "stage7c_a1_v2_development_protocol/STAGE7C_A1_PROTOCOL_LOCK.json",
    "stage7c_a1_v2_development_protocol/PHASE_O_PROMPT_SPEC.json",
    "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json",
    "stage7c_a1_v2_development_protocol/GENERATION_PROTOCOL_A1.json",
    "stage7c_a1_v2_development_protocol/PROMPT_SERIALIZATION_SPEC.json",
    "stage7c_a1_v2_development_protocol/CHAT_TEMPLATE_RENDERING_SPEC.json",
    "stage7c_a1_v2_development_protocol/QUESTION_OFFSET_GUIDE_SPEC.json",
    "stage7c_a1_v2_development_protocol/PHASE_O_OUTPUT_VALIDATION_SPEC.json",
    "stage7d_v2_a1_implementation/STAGE7D_IMPLEMENTATION_LOCK.json",
)

ARTIFACTS = (
    "STAGE7C_A2_INPUT_MANIFEST.json",
    "AMENDMENT_RATIONALE.json",
    "PATCH9_EVIDENCE_SUMMARY.json",
    "PHASE_O_PROMPT_SPEC_A2.json",
    "PROMPT_CHANGE_DIFF.json",
    "FRESH_SYNTHETIC_SMOKE_SET.jsonl",
    "SMOKE_SET_LOCK.json",
    "NO_TRAIN_DEV_TUNING_AUDIT.json",
    "REVIEWER_README.md",
    "VALIDATION_REPORT.md",
)

TEXT_SUFFIXES = {".json", ".jsonl", ".md", ".py", ".txt", ".toml", ".yaml", ".yml"}

A2_PHASE_O_SYSTEM_PROMPT = (
    "You select the SQLite write operation and the atomic semantic value spans from the exact original request. "
    "Return one JSON object that satisfies the supplied schema. Use Python Unicode code-point offsets over the exact original question string. "
    "A value span must be the smallest contiguous source span that corresponds to exactly one literal database value required by the write request. "
    "Do not select operation or instruction words, schema or field labels, surrounding punctuation, or a whole clause or sentence that contains multiple database values. "
    "If a phrase contains multiple database values, return one separate span per value. "
    "Select only text that appears verbatim in the original question."
)

A2_PHASE_O_USER_PROMPT_TEMPLATE = (
    "Original question Q, unchanged:\n{question}\n\n"
    "Python code-point offset guide derived from Q:\n{offset_guide}\n\n"
    "Schema inventory:\n{schema_inventory}\n\n"
    "Return JSON with operation and value_spans only. Do not generate SPAN, EV, SLOT ids or value text.\n"
    "For value_spans, select the smallest verbatim source span for each atomic database value. "
    "Exclude instruction words, field labels, punctuation, and any larger phrase containing more than one value."
)


def canonical_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(canonical_lf(text).encode("utf-8"))


def sha256_file(path: Path) -> str:
    if path.suffix.casefold() in TEXT_SUFFIXES:
        return sha256_text(path.read_text(encoding="utf-8"))
    return sha256_bytes(path.read_bytes())


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")


def input_hashes(root: Path = PROJECT_ROOT) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for rel in INPUTS:
        path = root / rel
        if not path.is_file():
            raise FileNotFoundError(f"Missing Stage7C-A2 input: {rel}")
        hashes[rel] = sha256_file(path)
    return hashes


def artifact_hashes(output_dir: Path) -> dict[str, str]:
    return {rel: sha256_file(output_dir / rel) for rel in ARTIFACTS}


def reset_output_dir(output_dir: Path, force: bool) -> None:
    if output_dir.exists():
        if not force:
            raise RuntimeError(f"{output_dir} exists; pass --force to rebuild.")
        resolved_output = output_dir.resolve()
        resolved_root = PROJECT_ROOT.resolve()
        if output_dir.name != OUT_DIR.name or resolved_root not in resolved_output.parents:
            raise RuntimeError(f"Refusing to remove unexpected output dir: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def prompt_hashes(phase_m_spec: dict[str, Any]) -> dict[str, str]:
    return {
        "phase_o_system_prompt_sha256": sha256_text(A2_PHASE_O_SYSTEM_PROMPT),
        "phase_o_user_prompt_template_sha256": sha256_text(A2_PHASE_O_USER_PROMPT_TEMPLATE),
        "phase_m_system_prompt_sha256": sha256_text(phase_m_spec["system_prompt"]),
        "phase_m_user_prompt_template_sha256": sha256_text(phase_m_spec["user_prompt_template"]),
    }


def span(question: str, text: str) -> dict[str, Any]:
    start = question.index(text)
    return {"start_char": start, "end_char": start + len(text)}


def schema_inventory(table_name: str, columns: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "tables": [{"table_ref": "TAB_1", "table_name": table_name}],
        "columns": [
            {"column_ref": f"COL_{index}", "table_ref": "TAB_1", "column_name": name, "source_type": source_type}
            for index, (name, source_type) in enumerate(columns, start=1)
        ],
        "constraints": [],
    }


def synthetic_db_spec(table_name: str, columns: list[tuple[str, str]]) -> dict[str, Any]:
    quoted_cols = ", ".join(f'"{name}" {source_type}' for name, source_type in columns)
    return {
        "engine": "sqlite",
        "initial_rows": [],
        "table": table_name,
        "columns": [{"name": name, "source_type": source_type, "nullable": True} for name, source_type in columns],
        "create_sql": f'CREATE TABLE "{table_name}" ({quoted_cols});',
        "deterministic_fixture_policy": "derive empty SQLite fixture directly from this locked spec before Stage7E0-A2 model run",
    }


def fresh_smoke_rows() -> list[dict[str, Any]]:
    specs = [
        {
            "sample_id": "stage7c_a2_fresh_en_two_value_0001",
            "language": "en",
            "question": "Create a record for Bob with salary 5000.",
            "table_name": "people",
            "columns": [("name", "TEXT"), ("salary", "INTEGER")],
            "expected_value_texts": ["Bob", "5000"],
        },
        {
            "sample_id": "stage7c_a2_fresh_zh_two_value_0002",
            "language": "zh",
            "question": "新增公司华为，员工数100。",
            "table_name": "company",
            "columns": [("company_name", "TEXT"), ("employee_count", "INTEGER")],
            "expected_value_texts": ["华为", "100"],
        },
        {
            "sample_id": "stage7c_a2_fresh_en_three_value_0003",
            "language": "en",
            "question": "Add Carol, age 31, city Paris.",
            "table_name": "people",
            "columns": [("name", "TEXT"), ("age", "INTEGER"), ("city", "TEXT")],
            "expected_value_texts": ["Carol", "31", "Paris"],
        },
        {
            "sample_id": "stage7c_a2_fresh_zh_three_value_0004",
            "language": "zh",
            "question": "新增员工王伟，年龄28岁，城市北京。",
            "table_name": "employee",
            "columns": [("name", "TEXT"), ("age", "INTEGER"), ("city", "TEXT")],
            "expected_value_texts": ["王伟", "28", "北京"],
        },
    ]
    rows = []
    for spec in specs:
        question = spec["question"]
        expected_texts = spec["expected_value_texts"]
        spans = [span(question, text) for text in expected_texts]
        assignments = [
            {"slot_ref": f"SLOT_{index}", "evidence_ref": f"EV_{index}", "column_ref": f"COL_{index}"}
            for index in range(1, len(expected_texts) + 1)
        ]
        rows.append(
            {
                "sample_id": spec["sample_id"],
                "language": spec["language"],
                "source": "fresh_synthetic_phase_o_prompt_amendment_acceptance_candidate",
                "locked_before_model_run": True,
                "model_side_input": {
                    "question": question,
                    "schema_inventory": schema_inventory(spec["table_name"], spec["columns"]),
                },
                "synthetic_db_spec": synthetic_db_spec(spec["table_name"], spec["columns"]),
                "label_side_expected": {
                    "model_side_visible": False,
                    "phase_o": {"operation": "INSERT", "value_spans": spans},
                    "phase_m": {"operation": "INSERT", "table_ref": "TAB_1", "assignments": assignments},
                    "target_state": {
                        "table": spec["table_name"],
                        "inserted_row": {
                            column_name: expected_texts[index]
                            for index, (column_name, _source_type) in enumerate(spec["columns"])
                        },
                    },
                },
            }
        )
    return rows


def patch9_evidence_summary() -> dict[str, Any]:
    return {
        "stage": STAGE,
        "source": "Stage7E0 PATCH9 reviewer/server-output evidence",
        "patch9_commit": "89d549c5d5560d256610639acd43d2883af32585",
        "server_output_sha256": "766c408f55daeac3fd77de2bd91232178df41018724312aecbce0c2a3d9a39d2",
        "backend_status": {
            "answer_injection_audit_status": "PASS",
            "constraint_capacity_audit_status": "PASS",
            "phase_m_diagnostic_status": "PASS",
            "backend_supports_more_than_two_spans": True,
            "finite_complete_object_enumeration": False,
            "hard_max_semantic_spans": None,
            "label_side_data_used_for_constraints": False,
        },
        "observed_failures": [
            {
                "sample_id": "stage7e0_ascii_smoke_0001",
                "question": "Add Alice, age 20.",
                "expected": {"operation": "INSERT", "value_spans": [{"start_char": 4, "end_char": 9}, {"start_char": 15, "end_char": 17}]},
                "generated": {"operation": "INSERT", "value_spans": [{"start_char": 0, "end_char": 17}]},
                "failure_type": "phase_o_semantic_span_not_atomic",
            },
            {
                "sample_id": "stage7e0_unicode_smoke_0002",
                "question": "添加员工爱丽丝，年龄20岁。",
                "expected": {"operation": "INSERT", "value_spans": [{"start_char": 4, "end_char": 7}, {"start_char": 10, "end_char": 12}]},
                "generated": {"operation": "INSERT", "value_spans": [{"start_char": 0, "end_char": 13}, {"start_char": 14, "end_char": 12}]},
                "failure_type": "phase_o_invalid_or_non_atomic_offsets_after_json_schema_validation",
            },
        ],
        "decision": "do_not_patch_backend_further; open formal zero-shot Phase O prompt amendment before any train/dev generation",
    }


def prompt_spec_a2(old_phase_o: dict[str, Any], phase_m: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": STAGE,
        "phase": "Phase O",
        "amends": "Stage7C_A1_V2_DEVELOPMENT_PROTOCOL/PHASE_O_PROMPT_SPEC.json",
        "amendment_type": "zero_shot_instruction_clarification_only",
        "system_prompt": A2_PHASE_O_SYSTEM_PROMPT,
        "user_prompt_template": A2_PHASE_O_USER_PROMPT_TEMPLATE,
        "prompt_hashes": prompt_hashes(phase_m),
        "old_phase_o_prompt_hashes": {
            "phase_o_system_prompt_sha256": old_phase_o["prompt_hashes"]["phase_o_system_prompt_sha256"],
            "phase_o_user_prompt_template_sha256": old_phase_o["prompt_hashes"]["phase_o_user_prompt_template_sha256"],
        },
        "phase_m_prompt_hashes_unchanged_from_a1": {
            "phase_m_system_prompt_sha256": phase_m["prompt_hashes"]["phase_m_system_prompt_sha256"],
            "phase_m_user_prompt_template_sha256": phase_m["prompt_hashes"]["phase_m_user_prompt_template_sha256"],
        },
        "json_schema_version": old_phase_o["json_schema_version"],
        "schema_sha256": old_phase_o["schema_sha256"],
        "character_offset_instructions": old_phase_o["character_offset_instructions"],
        "zero_shot": True,
        "few_shot_examples_in_prompt": False,
        "gold_visible": False,
        "atomicity_rules": [
            "select the smallest contiguous source span for exactly one literal database value",
            "exclude operation and instruction words",
            "exclude schema or field labels unless they are part of the literal value itself",
            "exclude surrounding punctuation",
            "never select a whole clause or sentence containing multiple database values",
            "return separate spans when a phrase contains multiple values",
            "select only text that appears verbatim in the original question",
        ],
        "unchanged_components": [
            "Phase M prompt",
            "Phase O JSON schema",
            "Phase O output validation order",
            "two-call architecture",
            "model id and revision",
            "decoder/backend",
            "materializer",
            "compiler",
            "metrics",
            "datasets and gold labels",
        ],
    }


def prompt_change_diff(old_phase_o: dict[str, Any], phase_m: dict[str, Any]) -> dict[str, Any]:
    hashes = prompt_hashes(phase_m)
    return {
        "stage": STAGE,
        "old_stage": old_phase_o["stage"],
        "changed_component": "Phase O prompt only",
        "phase_o_system_prompt": {
            "old_sha256": old_phase_o["prompt_hashes"]["phase_o_system_prompt_sha256"],
            "new_sha256": hashes["phase_o_system_prompt_sha256"],
            "old_text": old_phase_o["system_prompt"],
            "new_text": A2_PHASE_O_SYSTEM_PROMPT,
        },
        "phase_o_user_prompt_template": {
            "old_sha256": old_phase_o["prompt_hashes"]["phase_o_user_prompt_template_sha256"],
            "new_sha256": hashes["phase_o_user_prompt_template_sha256"],
            "old_text": old_phase_o["user_prompt_template"],
            "new_text": A2_PHASE_O_USER_PROMPT_TEMPLATE,
        },
        "phase_m_system_prompt_sha256": {
            "old": phase_m["prompt_hashes"]["phase_m_system_prompt_sha256"],
            "new": hashes["phase_m_system_prompt_sha256"],
            "changed": False,
        },
        "phase_m_user_prompt_template_sha256": {
            "old": phase_m["prompt_hashes"]["phase_m_user_prompt_template_sha256"],
            "new": hashes["phase_m_user_prompt_template_sha256"],
            "changed": False,
        },
        "examples_added_to_prompt": False,
        "schemas_changed": False,
        "backend_changed": False,
        "dataset_or_gold_changed": False,
    }


def no_train_dev_tuning_audit() -> dict[str, Any]:
    return {
        "stage": STAGE,
        "status": "PASS",
        "model_called": MODEL_CALLED,
        "gpu_called": GPU_CALLED,
        "train_dev_generation_run": TRAIN_DEV_GENERATION_RUN,
        "confirmation_481_evaluated": CONFIRMATION_481_EVALUATED,
        "live_sql_bench_gt_opened": LIVESQLBENCH_GT_OPENED,
        "crudsql_train_outputs_inspected_for_prompt_tuning": False,
        "crudsql_dev_outputs_inspected_for_prompt_tuning": False,
        "gold_labels_modified": False,
        "datasets_modified": False,
        "metrics_modified": False,
        "backend_modified": BACKEND_CHANGED,
        "phase_m_modified": PHASE_M_CHANGED,
        "architecture_modified": ARCHITECTURE_CHANGED,
        "amendment_scope": "Phase O zero-shot prompt wording and fresh synthetic smoke lock only",
    }


def reviewer_readme() -> str:
    return """# Stage7C-A2 Phase O Prompt Feasibility Amendment

This package opens a narrow prompt-protocol amendment after Stage7E0 PATCH9
showed that the constrained-generation backend is label-independent,
incremental, non-enumerative, and scalable, while the frozen zero-shot Phase O
prompt still fails atomic semantic span selection.

Scope:
- Revise Phase O prompt wording to define atomic, smallest, verbatim value spans.
- Keep Phase M, schemas, model, backend, materializer, compiler, datasets, gold labels, metrics, and protocol architecture unchanged.
- Lock four fresh synthetic smoke cases before any model/GPU run.
- Do not run Qwen, GPU generation, train/dev generation, 481 confirmation, or LiveSQLBench.

Commands:
```bash
python scripts/data/build_stage7c_a2_phase_o_prompt_amendment.py --force
python scripts/data/validate_stage7c_a2_phase_o_prompt_amendment.py
python -m pytest -q tests/test_stage7c_a2_phase_o_prompt_amendment.py
```

After reviewer approval, the next separate stage should run a Stage7E0-A2 real
generation preflight with the PATCH9 backend and the locked A2 Phase O prompt.
"""


def validation_report_text(status: str = "BUILT_PENDING_VALIDATION", violations: list[str] | None = None) -> str:
    return "# Stage7C-A2 Validation Report\n\n" + f"Status: {status}\n\nviolations: {json.dumps(violations or [], ensure_ascii=False, sort_keys=True)}\n"


def smoke_set_lock(rows: list[dict[str, Any]]) -> dict[str, Any]:
    payload = "".join(canonical_json(row) + "\n" for row in rows)
    return {
        "stage": STAGE,
        "status": "LOCKED_BEFORE_MODEL_RUN",
        "fresh_synthetic_smoke_count": len(rows),
        "languages": ["en", "zh"],
        "two_value_cases": 2,
        "three_value_cases": 2,
        "old_stage7e0_failed_smokes_are_diagnostic_regression_only": True,
        "acceptance_policy": "operation exact match, all atomic spans exact, no extra spans, deterministic validation PASS",
        "full_fixture_hash_scope": [
            "model_side_input.question",
            "model_side_input.schema_inventory",
            "synthetic_db_spec",
            "label_side_expected.phase_o",
            "label_side_expected.phase_m",
            "label_side_expected.target_state",
        ],
        "model_called": MODEL_CALLED,
        "gpu_called": GPU_CALLED,
        "train_dev_generation_run": TRAIN_DEV_GENERATION_RUN,
        "smoke_set_sha256": sha256_text(payload),
    }


def lock(output_dir: Path, inputs: dict[str, str]) -> dict[str, Any]:
    generation = read_json(PROJECT_ROOT / "stage7c_a1_v2_development_protocol/GENERATION_PROTOCOL_A1.json")
    phase_o = read_json(output_dir / "PHASE_O_PROMPT_SPEC_A2.json")
    phase_m = read_json(PROJECT_ROOT / "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json")
    return {
        "stage": STAGE,
        "status": "BUILT_PENDING_VALIDATION",
        "date": DATE,
        "hash_policy": HASH_POLICY,
        "input_hashes": inputs,
        "artifact_hashes": artifact_hashes(output_dir),
        "amends_stage": "Stage7C_A1_V2_DEVELOPMENT_PROTOCOL",
        "phase_o_prompt_amended": True,
        "phase_o_system_prompt_sha256": phase_o["prompt_hashes"]["phase_o_system_prompt_sha256"],
        "phase_o_user_prompt_template_sha256": phase_o["prompt_hashes"]["phase_o_user_prompt_template_sha256"],
        "phase_m_system_prompt_sha256": phase_m["prompt_hashes"]["phase_m_system_prompt_sha256"],
        "phase_m_user_prompt_template_sha256": phase_m["prompt_hashes"]["phase_m_user_prompt_template_sha256"],
        "phase_m_changed": PHASE_M_CHANGED,
        "architecture_changed": ARCHITECTURE_CHANGED,
        "backend_changed": BACKEND_CHANGED,
        "model_config": generation["model_config"],
        "model_called": MODEL_CALLED,
        "gpu_called": GPU_CALLED,
        "train_dev_generation_run": TRAIN_DEV_GENERATION_RUN,
        "confirmation_481_evaluated": CONFIRMATION_481_EVALUATED,
        "live_sql_bench_gt_opened": LIVESQLBENCH_GT_OPENED,
    }


def build(output_dir: Path = OUT_DIR, *, force: bool = False) -> dict[str, Any]:
    reset_output_dir(output_dir, force)
    inputs = input_hashes()
    old_phase_o = read_json(PROJECT_ROOT / "stage7c_a1_v2_development_protocol/PHASE_O_PROMPT_SPEC.json")
    phase_m = read_json(PROJECT_ROOT / "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json")
    rows = fresh_smoke_rows()

    write_json(output_dir / "STAGE7C_A2_INPUT_MANIFEST.json", {"stage": STAGE, "date": DATE, "hash_policy": HASH_POLICY, "input_hashes": inputs})
    write_json(
        output_dir / "AMENDMENT_RATIONALE.json",
        {
            "stage": STAGE,
            "status": "RATIONALE_LOCKED",
            "trigger": "Stage7E0 PATCH9 localized remaining failure to Phase O semantic span selection with backend audits passing.",
            "decision": "amend Phase O zero-shot prompt wording before any train/dev generation",
            "do_not_patch_backend_further": True,
            "do_not_run_train_dev_before_a2_review": True,
            "scientific_scope": "prompt clarification only; no answer injection, no examples, no data or label changes",
            "patch9_evidence_summary": "PATCH9_EVIDENCE_SUMMARY.json",
        },
    )
    write_json(output_dir / "PATCH9_EVIDENCE_SUMMARY.json", patch9_evidence_summary())
    write_json(output_dir / "PHASE_O_PROMPT_SPEC_A2.json", prompt_spec_a2(old_phase_o, phase_m))
    write_json(output_dir / "PROMPT_CHANGE_DIFF.json", prompt_change_diff(old_phase_o, phase_m))
    write_jsonl(output_dir / "FRESH_SYNTHETIC_SMOKE_SET.jsonl", rows)
    write_json(output_dir / "SMOKE_SET_LOCK.json", smoke_set_lock(rows))
    write_json(output_dir / "NO_TRAIN_DEV_TUNING_AUDIT.json", no_train_dev_tuning_audit())
    (output_dir / "REVIEWER_README.md").write_text(reviewer_readme(), encoding="utf-8")
    (output_dir / "VALIDATION_REPORT.md").write_text(validation_report_text(), encoding="utf-8")
    write_json(output_dir / LOCK_FILE, lock(output_dir, inputs))
    return {"stage": STAGE, "status": "PASS_BUILT", "output_dir": str(output_dir), "fresh_smoke_count": len(rows), "model_called": False, "gpu_called": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.output_dir, force=args.force), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
