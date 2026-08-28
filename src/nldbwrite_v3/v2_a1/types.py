from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class V2A1Error(ValueError):
    def __init__(self, reason_code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.details = details or {}


class Operation(str, Enum):
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    UPSERT = "UPSERT"


@dataclass(frozen=True)
class TableRef:
    ref: str
    name: str


@dataclass(frozen=True)
class ColumnRef:
    ref: str
    name: str
    source_type: str


@dataclass(frozen=True)
class ConstraintRef:
    ref: str
    column_refs: tuple[str, ...]


@dataclass(frozen=True)
class SchemaInventory:
    tables: tuple[TableRef, ...]
    columns: tuple[ColumnRef, ...]
    constraints: tuple[ConstraintRef, ...] = ()


@dataclass(frozen=True)
class AcceptedSpan:
    start_char: int
    end_char: int
    text: str


@dataclass(frozen=True)
class EvidenceItem:
    evidence_ref: str
    span_ref: str
    start_char: int
    end_char: int
    text: str


@dataclass(frozen=True)
class SlotItem:
    slot_ref: str
    evidence_ref: str
    required: bool
    start_char: int
    end_char: int
    text: str


@dataclass(frozen=True)
class SlotBundle:
    evidence: tuple[EvidenceItem, ...]
    slots: tuple[SlotItem, ...]


@dataclass(frozen=True)
class MaterializedValue:
    value: Any
    sqlite_affinity: str
    evidence_ref: str


@dataclass(frozen=True)
class MaterializedBinding:
    binding_key: str
    context: str
    index: int
    column_ref: str
    evidence_ref: str
    slot_ref: str
    value: Any
    sqlite_affinity: str


@dataclass(frozen=True)
class SQLiteProgram:
    operation: str
    sql: str
    parameters: tuple[Any, ...]
    normalized: str


@dataclass(frozen=True)
class PreflightResult:
    admitted: bool
    reason_code: str
    message: str
