from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .compiler import compile_sqlite_program
from .completeness import verify_completeness
from .inventories import build_schema_inventory
from .phase_m_output import parse_phase_m_output
from .phase_o_output import parse_phase_o_output
from .preflight import preflight_sqlite
from .prompt_rendering import render_phase_m_prompt, render_phase_o_prompt
from .slot_inventory import build_slot_bundle
from .span_validation import validate_and_sort_spans


STATES = (
    "PREPARED",
    "PHASE_O_RENDERED",
    "PHASE_O_OUTPUT_RECEIVED",
    "PHASE_O_VALIDATED",
    "SLOTS_BUILT",
    "PHASE_M_RENDERED",
    "PHASE_M_OUTPUT_RECEIVED",
    "PHASE_M_VALIDATED",
    "MATERIALIZED",
    "COMPLETENESS_VERIFIED",
    "COMPILED",
    "PREFLIGHTED",
    "ADMITTED",
    "REJECTED",
)


@dataclass(frozen=True)
class MockedPipelineResult:
    state: str
    phase_o_rendered_prompt_sha256: str
    phase_m_rendered_prompt_sha256: str | None
    sql: str | None
    admitted: bool
    reason_code: str


def run_mocked_pipeline(
    *,
    question: str,
    model_side_input: dict,
    phase_o_output_json: str,
    phase_m_output_json: str,
    phase_o_system_prompt: str,
    phase_m_system_prompt: str,
    db_path: Path | None = None,
) -> MockedPipelineResult:
    inventory = build_schema_inventory(model_side_input)
    _, phase_o_hash = render_phase_o_prompt(phase_o_system_prompt, question, inventory)
    phase_o = parse_phase_o_output(phase_o_output_json)
    spans = validate_and_sort_spans(question, phase_o["value_spans"])
    slots = build_slot_bundle(spans)
    _, phase_m_hash = render_phase_m_prompt(phase_m_system_prompt, phase_o["operation"], inventory, slots)
    ir = parse_phase_m_output(phase_m_output_json, phase_o["operation"], inventory, slots)
    verify_completeness(ir, slots)
    program = compile_sqlite_program(ir, inventory, slots)
    if db_path is None:
        return MockedPipelineResult("COMPILED", phase_o_hash, phase_m_hash, program.sql, True, "compiled_without_preflight")
    preflight = preflight_sqlite(db_path, program)
    return MockedPipelineResult("ADMITTED" if preflight.admitted else "REJECTED", phase_o_hash, phase_m_hash, program.sql, preflight.admitted, preflight.reason_code)
