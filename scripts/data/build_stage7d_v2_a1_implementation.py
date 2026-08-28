from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "stage7d_v2_a1_implementation"
STAGE = "Stage7D_V2_A1_IMPLEMENTATION"
FROZEN_TIMESTAMP_UTC = "2026-08-27T00:00:00+00:00"
FROZEN_MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"
FROZEN_MODEL_REVISION = "c03e6d358207e414f1eca0bb1891e29f1db0e242"
FROZEN_TOKENIZER_CONFIG_SHA256 = "959e7f1d9a1b7641a6d6ce05ca97b75c7894fcb66cbe5a040406458fb1128ee4"
FROZEN_CHAT_TEMPLATE_SHA256 = "cd8e9439f0570856fd70470bf8889ebd8b5d1107207f67a5efb46e342330527f"

UPSTREAM_INPUTS = [
    "stage7b_v2_method_specification/STAGE7B_V2_SPECIFICATION_LOCK.json",
    "stage7b_v2_method_specification/V2_ARCHITECTURE_SPEC.json",
    "stage7b_v2_method_specification/DESIGN_TO_EVIDENCE_TRACEABILITY.json",
    "stage7b_a1_free_text_slot_discovery_amendment/STAGE7B_A1_LOCK.json",
    "stage7b_a1_free_text_slot_discovery_amendment/PHASE_O_JSON_SCHEMA.json",
    "stage7b_a1_free_text_slot_discovery_amendment/PHASE_O_SEMANTIC_SPAN_SPEC.json",
    "stage7b_a1_free_text_slot_discovery_amendment/SPAN_VALIDATION_SPEC.json",
    "stage7b_a1_free_text_slot_discovery_amendment/COMPLETENESS_AMENDED_SPEC.json",
    "stage7b_a1_free_text_slot_discovery_amendment/SOURCE_SPAN_ORACLE_AUDIT.json",
    "stage7c_a1_v2_development_protocol/STAGE7C_A1_PROTOCOL_LOCK.json",
    "stage7c_a1_v2_development_protocol/GENERATION_PROTOCOL_A1.json",
    "stage7c_a1_v2_development_protocol/PHASE_O_PROMPT_SPEC.json",
    "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json",
    "stage7c_a1_v2_development_protocol/QUESTION_OFFSET_GUIDE_SPEC.json",
    "stage7c_a1_v2_development_protocol/PHASE_O_OUTPUT_VALIDATION_SPEC.json",
    "stage7c_a1_v2_development_protocol/DATA_LEAKAGE_AUDIT_A1.json",
    "stage7c_a1_v2_development_protocol/RESERVED_BENCHMARK_POLICY.json",
]

CODE_FILES = [
    "src/nldbwrite_v3/v2_a1/__init__.py",
    "src/nldbwrite_v3/v2_a1/types.py",
    "src/nldbwrite_v3/v2_a1/protocol.py",
    "src/nldbwrite_v3/v2_a1/inventories.py",
    "src/nldbwrite_v3/v2_a1/json_schema.py",
    "src/nldbwrite_v3/v2_a1/prompt_rendering.py",
    "src/nldbwrite_v3/v2_a1/phase_o_schema.py",
    "src/nldbwrite_v3/v2_a1/phase_o_output.py",
    "src/nldbwrite_v3/v2_a1/span_validation.py",
    "src/nldbwrite_v3/v2_a1/slot_inventory.py",
    "src/nldbwrite_v3/v2_a1/phase_m_schema.py",
    "src/nldbwrite_v3/v2_a1/phase_m_output.py",
    "src/nldbwrite_v3/v2_a1/reference_validation.py",
    "src/nldbwrite_v3/v2_a1/typed_materializer.py",
    "src/nldbwrite_v3/v2_a1/completeness.py",
    "src/nldbwrite_v3/v2_a1/compiler.py",
    "src/nldbwrite_v3/v2_a1/preflight.py",
    "src/nldbwrite_v3/v2_a1/pipeline.py",
    "src/nldbwrite_v3/v2_a1/diagnostics.py",
]

TEST_FILES = ["tests/v2_a1/test_stage7d_v2_a1.py"]
SCRIPT_FILES = [
    "scripts/data/build_stage7d_v2_a1_implementation.py",
    "scripts/data/validate_stage7d_v2_a1_implementation.py",
]


def canonical_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".jsonl", ".md", ".py", ".txt", ".toml", ".yaml", ".yml"}:
        text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        return text.encode("utf-8")
    return data


def sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(canonical_bytes(path)).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(rel_path: str) -> dict[str, Any]:
    return json.loads((ROOT / rel_path).read_text(encoding="utf-8"))


def rel_hashes(paths: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    missing = [p for p in paths if not (ROOT / p).exists()]
    if missing:
        raise FileNotFoundError(f"Missing required paths: {missing}")
    for path in paths:
        hashes[path] = sha256_file(ROOT / path)
    return hashes


def declared_test_count() -> int:
    text = (ROOT / TEST_FILES[0]).read_text(encoding="utf-8")
    return text.count("\ndef test_")


def collected_test_count() -> int:
    import re
    import subprocess

    result = subprocess.run(
        ["python", "-m", "pytest", "--collect-only", "-q", TEST_FILES[0]],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(r":\s*(\d+)\s*$", result.stdout.strip())
    if not match:
        raise RuntimeError(f"Could not parse pytest collection output: {result.stdout!r}")
    return int(match.group(1))


def artifact_paths() -> list[Path]:
    return sorted(p for p in OUT_DIR.iterdir() if p.is_file() and p.name != "STAGE7D_IMPLEMENTATION_LOCK.json")


def build(force: bool = False) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    if any(OUT_DIR.iterdir()) and not force:
        raise SystemExit(f"{OUT_DIR} is not empty; use --force to rebuild Stage7D artifacts")

    stage7c_lock = read_json("stage7c_a1_v2_development_protocol/STAGE7C_A1_PROTOCOL_LOCK.json")
    generation = read_json("stage7c_a1_v2_development_protocol/GENERATION_PROTOCOL_A1.json")

    write_json(
        OUT_DIR / "STAGE7D_INPUT_MANIFEST.json",
        {
            "stage": STAGE,
            "created_utc": FROZEN_TIMESTAMP_UTC,
            "upstream_stage7b_status": read_json("stage7b_v2_method_specification/STAGE7B_V2_SPECIFICATION_LOCK.json")["status"],
            "upstream_stage7b_a1_status": read_json("stage7b_a1_free_text_slot_discovery_amendment/STAGE7B_A1_LOCK.json")["status"],
            "upstream_stage7c_a1_status": stage7c_lock["status"],
            "upstream_inputs": UPSTREAM_INPUTS,
            "input_hashes": rel_hashes(UPSTREAM_INPUTS),
            "model_config_frozen_for_later_execution": generation["model_config"],
            "phase_o_model_calls": stage7c_lock["phase_o_model_calls"],
            "phase_m_model_calls": stage7c_lock["phase_m_model_calls"],
            "forbidden_inputs": ["gold_columns", "gold_sql_conds", "gold_spans", "gold_operation", "481_confirmation_test", "LiveSQLBench_ground_truth"],
        },
    )

    write_json(
        OUT_DIR / "IMPLEMENTATION_COMPONENT_MANIFEST.json",
        {
            "stage": STAGE,
            "implemented_components": {
                "protocol": "Loads locked Stage7B, Stage7B-A1, Stage7C-A1, and Stage7D protocols and fail-fast verifies upstream input hashes.",
                "inventories": "Builds model-side schema inventories and rejects gold-side leakage keys.",
                "prompt_rendering": "Renders exact frozen Stage7C-A1 role/content messages and exposes the tokenizer.apply_chat_template contract adapter.",
                "json_schema": "Validates the frozen Draft 2020-12 schema subset needed by Phase O and dynamic Phase M schemas without adding runtime dependencies.",
                "phase_o_schema_and_output": "Validates against frozen Phase O JSON Schema, parses operation plus value-span offsets, and forbids span_ref/text emission.",
                "span_validation": "Validates Python Unicode code-point [start,end) offsets and rejects duplicate/overlapping spans.",
                "slot_inventory": "Derives deterministic SPAN/EV/SLOT IDs after sorted accepted offsets.",
                "phase_m_schema_and_output": "Instantiates real operation-specific Draft 2020-12 schemas with dynamic enums and enforces SLOT/EVIDENCE coherence.",
                "typed_materializer": "Applies conservative SQLite-affinity materialization without implicit date/unit normalization.",
                "completeness": "Checks missing required slots and duplicate SLOT/COL bindings after materialization, scoped by mapping context.",
                "compiler": "Compiles binding-specific materialized values to parameterized SQLite write programs.",
                "preflight": "Runs SQLite execution checks on a copied DB with rollback and a wall-clock progress-handler deadline.",
                "pipeline": "Orchestrates the V2-A1 state machine with injected mocked Phase O/M outputs only.",
                "diagnostics": "Audits primary pipeline oracle isolation.",
            },
            "code_files": CODE_FILES,
            "code_hashes": rel_hashes(CODE_FILES),
            "script_files": SCRIPT_FILES,
            "script_hashes": rel_hashes(SCRIPT_FILES),
            "test_files": TEST_FILES,
            "test_hashes": rel_hashes(TEST_FILES),
            "not_implemented_in_stage7d": ["model_runner", "qwen_generation", "train_dev_experiment", "accuracy_reporting", "ablation_execution"],
        },
    )

    write_json(
        OUT_DIR / "SPEC_TO_CODE_TRACEABILITY.json",
        {
            "stage": STAGE,
            "traceability": [
                {"spec": "Phase O operation + grounded spans", "code": ["phase_o_schema.py", "phase_o_output.py", "span_validation.py"], "tests": ["Phase O schema/output and Unicode span tests"]},
                {"spec": "Deterministic EV/SLOT inventory creation", "code": ["slot_inventory.py"], "tests": ["sorted SPAN/EV/SLOT derivation tests"]},
                {"spec": "Dynamic inventory membership validation", "code": ["inventories.py", "reference_validation.py", "phase_m_schema.py"], "tests": ["TAB/COL/EV/SLOT out-of-inventory rejection tests"]},
                {"spec": "Structured predicates for UPDATE/DELETE", "code": ["phase_m_schema.py", "compiler.py"], "tests": ["multi-predicate selector and compiler tests"]},
                {"spec": "UPSERT conditional semantics", "code": ["phase_m_schema.py", "compiler.py"], "tests": ["DO_NOTHING/DO_UPDATE policy regression tests"]},
                {"spec": "Conservative typed materialization", "code": ["typed_materializer.py"], "tests": ["INTEGER/REAL/TEXT/BLOB/NUMERIC strictness tests"]},
                {"spec": "Semantic completeness after materialization", "code": ["typed_materializer.py", "completeness.py"], "tests": ["materialization-before-completeness and context-scoped duplicate tests"]},
                {"spec": "Binding-specific UPSERT values", "code": ["typed_materializer.py", "compiler.py"], "tests": ["same EV/SLOT reused across UPSERT insert/update contexts"]},
                {"spec": "No oracle spans in primary path", "code": ["pipeline.py", "diagnostics.py"], "tests": ["oracle isolation tests"]},
            ],
        },
    )

    write_json(
        OUT_DIR / "IMPLEMENTATION_INVARIANTS.json",
        {
            "stage": STAGE,
            "invariants": [
                "Phase O model output contains operation and value_spans only.",
                "Model-generated span_ref, evidence_ref, slot_ref, or value text is forbidden in Phase O.",
                "Offsets use Python Unicode code-point indexing over the exact original question.",
                "Accepted spans are sorted by start_char then end_char before deterministic ID assignment.",
                "Every Phase O semantic slot is required and must be mapped at least once, with duplicate use rejected within each mapping context.",
                "Duplicate SLOT and target-column checks run after materialization so value conversion failures keep precedence.",
                "SLOT reuse is forbidden within a single mapping context but permitted across distinct UPSERT insert/update contexts.",
                "Materialized values are keyed by binding occurrence, not evidence_ref alone.",
                "All table, column, evidence, slot, and constraint references must be members of the supplied dynamic inventory.",
                "Typed materialization does not perform implicit date, currency, percentage, or unit conversion.",
                "SQLite programs are parameterized; identifiers are quoted only after inventory validation.",
                "Preflight runs against a copied database and rolls back.",
                "Primary Stage7D pipeline accepts mocked outputs only and does not call a model or GPU.",
            ],
        },
    )

    write_json(
        OUT_DIR / "ORACLE_ISOLATION_AUDIT.json",
        {
            "stage": STAGE,
            "primary_pipeline_uses_oracle_spans": False,
            "oracle_artifacts_allowed_only_for": ["diagnostic_protocol", "label_side_evaluation_after_generation"],
            "forbidden_primary_imports": ["SOURCE_SPAN_LABEL_MANIFEST", "oracle_span_provider", "gold_sql_conds", "gold_spans"],
            "status": "PASS_BUILT_PENDING_VALIDATION",
        },
    )

    write_json(
        OUT_DIR / "NO_MODEL_EXECUTION_AUDIT.json",
        {
            "stage": STAGE,
            "model_called": False,
            "gpu_called": False,
            "qwen_generation_run": False,
            "train_dev_generation_run": False,
            "experiment_run": False,
            "live_sql_bench_gt_opened": False,
            "confirmation_481_evaluated": False,
            "stage_scope": "implementation with synthetic fixtures and mocked LLM outputs only",
        },
    )

    write_json(
        OUT_DIR / "CHAT_TEMPLATE_PREFLIGHT.json",
        {
            "stage": STAGE,
            "status": "PASS_CHAT_TEMPLATE_PREFLIGHT_LOCKED",
            "model_id": FROZEN_MODEL_ID,
            "model_revision": FROZEN_MODEL_REVISION,
            "tokenizer_revision": FROZEN_MODEL_REVISION,
            "tokenizer_config_file_sha256": FROZEN_TOKENIZER_CONFIG_SHA256,
            "actual_chat_template_string_sha256": FROZEN_CHAT_TEMPLATE_SHA256,
            "source": "HuggingFace tokenizer_config.json at the frozen tokenizer revision",
            "source_url": f"https://huggingface.co/{FROZEN_MODEL_ID}/raw/{FROZEN_MODEL_REVISION}/tokenizer_config.json",
            "hash_method": "sha256(canonical_lf(tokenizer_config_json['chat_template']).encode('utf-8'))",
            "apply_chat_template_parameters": {"tokenize": False, "add_generation_prompt": True},
            "model_called": False,
            "gpu_called": False,
            "generation_run": False,
        },
    )

    write_json(
        OUT_DIR / "TEST_SUMMARY.json",
        {
            "stage": STAGE,
            "pytest_command": "python -m pytest -q tests/v2_a1/test_stage7d_v2_a1.py",
            "py_compile_command": "python -m py_compile <all Stage7D modules and tests>",
            "declared_test_functions": declared_test_count(),
            "collected_test_cases": collected_test_count(),
            "last_observed_result": "not_run_by_builder",
            "test_execution_evidence_policy": "Builder records collected test cases only; reviewer must run pytest and validation commands independently.",
            "test_scope": ["unit", "synthetic integration", "mocked Phase O/M pipeline", "no model execution"],
        },
    )

    (OUT_DIR / "REVIEWER_README.md").write_text(
        "# Stage7D V2-A1 Implementation\n\n"
        "This package implements the locked V2-A1 protocol as executable Python modules with synthetic fixtures and mocked LLM outputs only.\n\n"
        "Run:\n\n"
        "```bash\n"
        "python scripts/data/build_stage7d_v2_a1_implementation.py --force\n"
        "python scripts/data/validate_stage7d_v2_a1_implementation.py\n"
        "python -m pytest -q tests/v2_a1/test_stage7d_v2_a1.py\n"
        "```\n\n"
        "Stage7D intentionally does not run Qwen, GPU generation, train/dev evaluation, ablations, the 481 confirmation set, or LiveSQLBench.\n",
        encoding="utf-8",
    )

    (OUT_DIR / "VALIDATION_REPORT.md").write_text(
        "# Stage7D Validation Report\n\n"
        "Status: BUILT_PENDING_VALIDATION\n\n"
        "Run `python scripts/data/validate_stage7d_v2_a1_implementation.py` to recompute hashes and close the lock.\n",
        encoding="utf-8",
    )

    artifact_hashes = {p.name: sha256_file(p) for p in artifact_paths()}
    write_json(
        OUT_DIR / "STAGE7D_IMPLEMENTATION_LOCK.json",
        {
            "stage": STAGE,
            "status": "BUILT_PENDING_VALIDATION",
            "created_utc": FROZEN_TIMESTAMP_UTC,
            "input_hashes": rel_hashes(UPSTREAM_INPUTS),
            "code_hashes": rel_hashes(CODE_FILES),
            "script_hashes": rel_hashes(SCRIPT_FILES),
            "test_hashes": rel_hashes(TEST_FILES),
            "artifact_hashes": artifact_hashes,
            "model_called": False,
            "gpu_called": False,
            "experiment_run": False,
            "v2_generation_run": False,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    build(force=args.force)
    print("PASS_BUILT")


if __name__ == "__main__":
    main()
