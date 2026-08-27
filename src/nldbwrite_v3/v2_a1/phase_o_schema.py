from __future__ import annotations

ALLOWED_OPERATIONS = {"INSERT", "UPDATE", "DELETE", "UPSERT"}
PHASE_O_REQUIRED_KEYS = {"operation", "value_spans"}
PHASE_O_SPAN_KEYS = {"start_char", "end_char"}
