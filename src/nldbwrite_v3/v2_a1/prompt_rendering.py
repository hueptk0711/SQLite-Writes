from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .types import SchemaInventory, SlotBundle


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def serialize_prompt_object(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def rendered_prompt_sha256(text: str) -> str:
    return hashlib.sha256(canonical_text(text).encode("utf-8")).hexdigest()


def offset_guide(question: str) -> str:
    return "\n".join(f"{index}\t{char}" for index, char in enumerate(question))


def _ref_number(ref: str) -> int:
    match = re.fullmatch(r"[A-Z]+_(\d+)", ref)
    return int(match.group(1)) if match else 0


def inventory_payload(inventory: SchemaInventory) -> dict[str, Any]:
    return {
        "tables": [item.__dict__ for item in sorted(inventory.tables, key=lambda item: _ref_number(item.ref))],
        "columns": [item.__dict__ for item in sorted(inventory.columns, key=lambda item: _ref_number(item.ref))],
        "constraints": [{"ref": item.ref, "column_refs": list(item.column_refs)} for item in sorted(inventory.constraints, key=lambda item: _ref_number(item.ref))],
    }


def render_phase_o_prompt(system_prompt: str, question: str, inventory: SchemaInventory) -> tuple[str, str]:
    user = "\n".join(
        [
            "Exact original question:",
            question,
            "",
            "Python code-point offset guide:",
            offset_guide(question),
            "",
            "Schema inventory JSON:",
            serialize_prompt_object(inventory_payload(inventory)),
        ]
    )
    rendered = f"<system>\n{system_prompt}\n</system>\n<user>\n{user}\n</user>\n<assistant>\n"
    return rendered, rendered_prompt_sha256(rendered)


def render_phase_m_prompt(system_prompt: str, operation: str, inventory: SchemaInventory, slots: SlotBundle) -> tuple[str, str]:
    evidence = [item.__dict__ for item in slots.evidence]
    slot_payload = [item.__dict__ for item in slots.slots]
    user = "\n".join(
        [
            f"Predicted operation: {operation}",
            "Schema inventory JSON:",
            serialize_prompt_object(inventory_payload(inventory)),
            "Evidence inventory JSON:",
            serialize_prompt_object(evidence),
            "Semantic slot inventory JSON:",
            serialize_prompt_object(slot_payload),
        ]
    )
    rendered = f"<system>\n{system_prompt}\n</system>\n<user>\n{user}\n</user>\n<assistant>\n"
    return rendered, rendered_prompt_sha256(rendered)
