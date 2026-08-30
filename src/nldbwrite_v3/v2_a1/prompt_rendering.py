from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .types import SchemaInventory, SlotBundle


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def canonical_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_text(text: str) -> str:
    return hashlib.sha256(canonical_text(text).encode("utf-8")).hexdigest()


def serialize_prompt_object(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def rendered_prompt_sha256(text: str) -> str:
    return sha256_text(text)


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


def _read_spec(root: Path, rel_path: str) -> dict[str, Any]:
    return json.loads((root / rel_path).read_text(encoding="utf-8"))


def phase_o_prompt_spec(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    return _read_spec(root, "stage7c_a1_v2_development_protocol/PHASE_O_PROMPT_SPEC.json")


def phase_m_prompt_spec(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    return _read_spec(root, "stage7c_a1_v2_development_protocol/PHASE_M_PROMPT_SPEC.json")


def validate_frozen_prompt_hashes(root: Path = PROJECT_ROOT) -> None:
    phase_o = phase_o_prompt_spec(root)
    phase_m = phase_m_prompt_spec(root)
    hashes = phase_o["prompt_hashes"]
    checks = {
        "phase_o_system_prompt_sha256": sha256_text(phase_o["system_prompt"]),
        "phase_o_user_prompt_template_sha256": sha256_text(phase_o["user_prompt_template"]),
        "phase_m_system_prompt_sha256": sha256_text(phase_m["system_prompt"]),
        "phase_m_user_prompt_template_sha256": sha256_text(phase_m["user_prompt_template"]),
    }
    for key, digest in checks.items():
        if hashes.get(key) != digest:
            from .types import V2A1Error

            raise V2A1Error("frozen_prompt_hash_mismatch", "Frozen prompt hash does not match prompt text", details={"key": key})


def render_chat_prompt_with_tokenizer(tokenizer: Any, messages: list[dict[str, str]]) -> str:
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)


def render_phase_o_messages(question: str, inventory: SchemaInventory, *, root: Path = PROJECT_ROOT) -> tuple[list[dict[str, str]], str]:
    spec = phase_o_prompt_spec(root)
    user = spec["user_prompt_template"].format(
        question=question,
        offset_guide=offset_guide(question),
        schema_inventory=serialize_prompt_object(inventory_payload(inventory)),
    )
    messages = [{"role": "system", "content": spec["system_prompt"]}, {"role": "user", "content": user}]
    return messages, sha256_text(serialize_prompt_object(messages))


def render_phase_m_messages(operation: str, inventory: SchemaInventory, slots: SlotBundle, *, root: Path = PROJECT_ROOT) -> tuple[list[dict[str, str]], str]:
    evidence = [item.__dict__ for item in slots.evidence]
    slot_payload = [item.__dict__ for item in slots.slots]
    spec = phase_m_prompt_spec(root)
    user = spec["user_prompt_template"].format(
        operation=operation,
        schema_inventory=serialize_prompt_object(inventory_payload(inventory)),
        evidence_inventory=serialize_prompt_object(evidence),
        semantic_slot_inventory=serialize_prompt_object(slot_payload),
    )
    messages = [{"role": "system", "content": spec["system_prompt"]}, {"role": "user", "content": user}]
    return messages, sha256_text(serialize_prompt_object(messages))


def render_phase_o_prompt(question: str, inventory: SchemaInventory, *, root: Path = PROJECT_ROOT) -> tuple[list[dict[str, str]], str]:
    return render_phase_o_messages(question, inventory, root=root)


def render_phase_m_prompt(operation: str, inventory: SchemaInventory, slots: SlotBundle, *, root: Path = PROJECT_ROOT) -> tuple[list[dict[str, str]], str]:
    return render_phase_m_messages(operation, inventory, slots, root=root)
