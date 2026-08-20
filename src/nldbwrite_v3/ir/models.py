from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


Severity = Literal["error", "warning"]


@dataclass(slots=True)
class Diagnostic:
    error_code: str
    message: str
    severity: Severity = "error"
    path: str = ""
    group_id: str | None = None
    table: str | None = None
    candidates: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceCollection:
    collection_id: str
    source_path: str
    source_format: str
    rows: list[dict[str, Any]]
    fields: list[str]
    reference_id: str = ""
    selector_id: str = ""
    field_ids: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourcePayload:
    mode: Literal["free_text", "semi_structured"]
    source_format: str
    collections: list[SourceCollection]
    instruction_text: str
    raw_text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def rows(self) -> list[dict[str, Any]]:
        """Backward-compatible flattened rows; use collections for new code."""
        return [
            row
            for collection in self.collections
            for row in collection.rows
        ]

    @property
    def fields(self) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for collection in self.collections:
            for field_name in collection.fields:
                if field_name not in seen:
                    seen.add(field_name)
                    output.append(field_name)
        return output

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "source_format": self.source_format,
            "instruction_text": self.instruction_text,
            "collections": [
                collection.to_dict() for collection in self.collections
            ],
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class PlanParseResult:
    parse_status: Literal["success", "json_error", "schema_error"]
    plan: dict[str, Any] | None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    extracted_json: str | None = None

    @property
    def success(self) -> bool:
        return self.parse_status == "success"

    def to_dict(self) -> dict[str, Any]:
        return {
            "parse_status": self.parse_status,
            "plan": self.plan,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "extracted_json": self.extracted_json,
        }


@dataclass(slots=True)
class VerificationResult:
    status: Literal["valid", "invalid"]
    normalized_plan: dict[str, Any] | None
    errors: list[Diagnostic] = field(default_factory=list)
    warnings: list[Diagnostic] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.status == "valid"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "normalized_plan": self.normalized_plan,
            "errors": [item.to_dict() for item in self.errors],
            "warnings": [item.to_dict() for item in self.warnings],
        }


@dataclass(slots=True)
class CompiledStatement:
    sql: str
    params: list[Any]
    group_id: str
    table: str
    row_count: int
    normalizations: list[dict[str, Any]] = field(default_factory=list)
    semantic_trace: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        output = asdict(self)
        # Preserve baseline serialization when Stage-2 tracing is absent.
        if output.get("semantic_trace") is None:
            output.pop("semantic_trace", None)
        return output


@dataclass(slots=True)
class CompiledProgram:
    status: Literal["success", "partial", "error"]
    statements: list[CompiledStatement] = field(default_factory=list)
    errors: list[Diagnostic] = field(default_factory=list)
    warnings: list[Diagnostic] = field(default_factory=list)
    strict_atomic: bool = True

    @property
    def success(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "strict_atomic": self.strict_atomic,
            "statements": [item.to_dict() for item in self.statements],
            "errors": [item.to_dict() for item in self.errors],
            "warnings": [item.to_dict() for item in self.warnings],
        }
