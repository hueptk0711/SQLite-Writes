from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .phase_m_schema import PROJECT_ROOT, validate_phase_m_ir
from .types import SchemaInventory, SlotBundle, V2A1Error


def parse_phase_m_output(text: str, operation: str, inventory: SchemaInventory, slots: SlotBundle, *, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    try:
        obj: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise V2A1Error("phase_m_parse", "Phase M output is not valid JSON", details={"error": str(exc)}) from exc
    return validate_phase_m_ir(obj, operation, inventory, slots, root=root)
