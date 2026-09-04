from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from .eng2b_candidate_domains import build_column_specific_domains, dynamic_schema_with_column_domains
from .types import V2A1Error


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return sha256(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")).hexdigest()


def schema_branches(schema: dict[str, Any]) -> list[dict[str, Any]]:
    branches = schema.get("oneOf") or [schema]
    specs: list[dict[str, Any]] = []
    for branch in branches:
        properties = branch["properties"]
        column_node = properties["column_span_refs"]
        table_ref_node = properties["table_ref"]
        operation_node = properties["operation"]
        table_ref = table_ref_node.get("const") or table_ref_node.get("enum", [None])[0]
        operation_choices = operation_node.get("enum") or [operation_node.get("const")]
        required_columns = list(column_node["required"])
        specs.append(
            {
                "operation_choices": [str(value) for value in operation_choices if value is not None],
                "table_ref": str(table_ref),
                "columns": required_columns,
                "column_domains": {
                    column: [str(value) for value in column_node["properties"][column]["enum"]]
                    for column in required_columns
                },
            }
        )
    return specs


def _literal_transition(text: str, literal: str, pos: int, used: frozenset[str]) -> list[tuple[str, int, frozenset[str]]]:
    if pos >= len(text):
        return [("prefix", pos, used)]
    end = min(len(text), pos + len(literal))
    fragment = text[pos:end]
    if not literal.startswith(fragment):
        return []
    if len(fragment) < len(literal):
        return [("prefix", len(text), used)]
    return [("complete", pos + len(literal), used)]


def _enum_transition(text: str, values: list[str], pos: int, used: frozenset[str]) -> list[tuple[str, int, frozenset[str]]]:
    if pos >= len(text):
        return [("prefix", pos, used)]
    results: list[tuple[str, int, frozenset[str]]] = []
    for value in values:
        if value != "OMIT" and value in used:
            continue
        end = min(len(text), pos + len(value))
        fragment = text[pos:end]
        if value.startswith(fragment):
            if len(fragment) < len(value):
                results.append(("prefix", len(text), used))
            else:
                next_used = used if value == "OMIT" else frozenset([*used, value])
                results.append(("complete", pos + len(value), next_used))
    return results


def _advance(
    text: str,
    states: list[tuple[str, int, frozenset[str]]],
    part: str | list[str],
) -> list[tuple[str, int, frozenset[str]]]:
    output: list[tuple[str, int, frozenset[str]]] = []
    for _status, pos, used in states:
        if isinstance(part, str):
            output.extend(_literal_transition(text, part, pos, used))
        else:
            output.extend(_enum_transition(text, part, pos, used))
    return output


def branch_status(text: str, branch: dict[str, Any]) -> str:
    states: list[tuple[str, int, frozenset[str]]] = [("complete", 0, frozenset())]
    parts: list[str | list[str]] = ['{"column_span_refs":{']
    for index, column in enumerate(branch["columns"]):
        if index:
            parts.append(",")
        parts.extend([f'"{column}":"', branch["column_domains"][column], '"'])
    parts.extend(['},"operation":"', branch["operation_choices"], '","table_ref":"', [branch["table_ref"]], '"}'])
    for part in parts:
        states = _advance(text, states, part)
        if not states:
            return "invalid"
        if any(status == "prefix" for status, _pos, _used in states):
            return "prefix"
    if any(status == "complete" and pos == len(text) for status, pos, _used in states):
        return "complete"
    return "invalid"


@dataclass
class Eng2BConstraintGrammar:
    schema: dict[str, Any]

    def __post_init__(self) -> None:
        self.schema_sha256 = sha256_text(canonical_json(self.schema))
        self.branches = schema_branches(self.schema)
        self.fingerprint = sha256_text(canonical_json({"schema_sha256": self.schema_sha256, "branches": self.branches, "stateful_unique_non_omit_span_refs": True}))

    def is_prefix(self, text: str) -> bool:
        return any(branch_status(text, branch) in {"prefix", "complete"} for branch in self.branches)

    def is_complete(self, text: str) -> bool:
        return any(branch_status(text, branch) == "complete" for branch in self.branches)

    def metadata(self) -> dict[str, Any]:
        return {
            "phase": "phase_o",
            "schema_sha256": self.schema_sha256,
            "constraint_grammar_sha256": self.fingerprint,
            "constraint_fingerprint": self.fingerprint,
            "constraint_source": "ENG2B_dynamic_column_specific_json_schema",
            "stateful_unique_non_omit_span_refs": True,
            "token_level_enforcement": True,
            "finite_complete_object_enumeration": False,
            "finite_known_answer_candidates": False,
            "label_side_data_used_for_constraints": False,
        }


def build_eng2b_constraint_grammar(schema: dict[str, Any]) -> Eng2BConstraintGrammar:
    return Eng2BConstraintGrammar(schema)


def prepare_eng2b_runtime_row(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    domain_result = build_column_specific_domains(
        model_side_input=row["model_side_input"],
        runtime_constraints=row["runtime_constraints"],
    )
    dynamic_schema = dynamic_schema_with_column_domains(
        model_side_input=row["model_side_input"],
        runtime_constraints=row["runtime_constraints"],
        domains=domain_result["domains"],
    )
    grammar = build_eng2b_constraint_grammar(dynamic_schema)
    runtime_row = copy.deepcopy(row)
    runtime_row["runtime_constraints"]["eng2b_original_phase_o_schema_sha256"] = row["runtime_constraints"].get("phase_o_schema_sha256") or sha256_text(canonical_json(row["runtime_constraints"]["phase_o_schema"]))
    runtime_row["runtime_constraints"]["phase_o_schema"] = dynamic_schema
    runtime_row["runtime_constraints"]["phase_o_schema_sha256"] = grammar.schema_sha256
    runtime_row["runtime_constraints"]["eng2b_per_column_domains"] = domain_result["domains"]
    runtime_row["runtime_constraints"]["eng2b_per_column_domain_sha256"] = sha256_text(canonical_json(domain_result["domains"]))
    metadata = {
        "method_id": "M2_FINAL_ENG2B",
        "global_schema_sha256": row["runtime_constraints"].get("phase_o_schema_sha256") or sha256_text(canonical_json(row["runtime_constraints"]["phase_o_schema"])),
        "eng2b_dynamic_schema_sha256": grammar.schema_sha256,
        "generation_schema_sha256": grammar.schema_sha256,
        "parser_schema_sha256": grammar.schema_sha256,
        "per_column_domain_sha256": runtime_row["runtime_constraints"]["eng2b_per_column_domain_sha256"],
        "constraint_grammar_sha256": grammar.fingerprint,
        "stateful_unique_non_omit_span_refs": True,
        "domain_construction_uses_gold": False,
    }
    if metadata["global_schema_sha256"] == metadata["eng2b_dynamic_schema_sha256"]:
        raise V2A1Error("eng2b_schema_not_dynamic", "ENG2B dynamic schema must differ from the frozen global A7 schema")
    return runtime_row, metadata
