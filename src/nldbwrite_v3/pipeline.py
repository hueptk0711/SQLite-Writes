from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from typing import Any, Mapping

from nldbwrite_v3.compiler import compile_verified_plan
from nldbwrite_v3.ir import (
    CompiledProgram,
    Diagnostic,
    SourcePayload,
    VerificationResult,
)
from nldbwrite_v3.planner import (
    MaterializationError,
    ambiguous_conflict_policy_diagnostic,
    ground_reference_mapping_plan,
    ground_mapping_plan,
    materialize_mapping_plan,
    materialize_reference_free_text_plan,
    resolve_reference_mapping_plan,
)
from nldbwrite_v3.source_parser import parse_source_payload
from nldbwrite_v3.verifier import verify_write_plan
from nldbwrite_v3.vnext import (
    Stage2InterventionConfig,
    apply_free_text_reference_interventions,
)


@dataclass(slots=True)
class PipelineResult:
    source_payload: SourcePayload
    write_plan: dict[str, Any] | None
    verification: VerificationResult | None
    program: CompiledProgram | None
    stage: str

    @property
    def success(self) -> bool:
        return bool(self.program and self.program.status == "success")

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stage": self.stage,
            "source_payload": self.source_payload.to_dict(),
            "write_plan": self.write_plan,
            "verification": (
                self.verification.to_dict() if self.verification else None
            ),
            "compiled_program": self.program.to_dict() if self.program else None,
        }


class MappingFirstPipeline:
    def __init__(
        self,
        profile: dict[str, Any],
        *,
        strict_atomic: bool = True,
        normalize_values: bool = False,
        normalization_mode: str = "legacy",
        reference_planning: bool = False,
        stage2_interventions: Mapping[str, Any] | None = None,
    ):
        self.profile = profile
        self.strict_atomic = strict_atomic
        self.normalize_values = normalize_values
        self.normalization_mode = normalization_mode
        self.reference_planning = reference_planning
        self.stage2_interventions = Stage2InterventionConfig.from_mapping(
            stage2_interventions
        )

    def run(
        self,
        request: str,
        predicted_plan: dict[str, Any],
    ) -> PipelineResult:
        payload = parse_source_payload(request)
        grounding_warnings: list[Diagnostic] = []
        reference_plan = self.reference_planning or (
            predicted_plan.get("plan_kind") == "reference_write_plan"
        ) or any(
            isinstance(group, dict) and "source_collection_id" in group
            for group in predicted_plan.get("target_groups") or []
        )
        if reference_plan:
            policy_diagnostic = ambiguous_conflict_policy_diagnostic(request)
            if policy_diagnostic is not None:
                verification = VerificationResult(
                    "invalid",
                    None,
                    [policy_diagnostic],
                    [],
                )
                return PipelineResult(
                    payload,
                    None,
                    verification,
                    None,
                    "policy_resolution",
                )
        if (
            reference_plan
            and payload.mode == "semi_structured"
            and "target_groups" in predicted_plan
        ):
            predicted_plan, grounding_warnings = (
                ground_reference_mapping_plan(
                    predicted_plan,
                    payload,
                    self.profile,
                )
            )
            grounded_plan, reference_errors = resolve_reference_mapping_plan(
                predicted_plan,
                payload,
                self.profile,
                stage2_interventions=self.stage2_interventions.to_dict(),
                warning_sink=grounding_warnings,
            )
            if reference_errors:
                verification = VerificationResult(
                    "invalid",
                    None,
                    reference_errors,
                    grounding_warnings,
                )
                return PipelineResult(
                    payload,
                    None,
                    verification,
                    None,
                    "reference_resolution",
                )
            try:
                write_plan = materialize_mapping_plan(
                    grounded_plan,
                    payload,
                    control_field_roles=self.stage2_interventions.control_field_roles,
                )
            except MaterializationError as exc:
                verification = VerificationResult(
                    "invalid",
                    None,
                    exc.diagnostics,
                    grounding_warnings,
                )
                return PipelineResult(
                    payload,
                    None,
                    verification,
                    None,
                    "materialization",
                )
        elif reference_plan and payload.mode == "free_text":
            predicted_plan, intervention_diagnostics = (
                apply_free_text_reference_interventions(
                    predicted_plan,
                    request,
                    self.profile,
                    self.stage2_interventions,
                )
            )
            intervention_errors = [
                item
                for item in intervention_diagnostics
                if item.severity == "error"
            ]
            grounding_warnings.extend(
                item
                for item in intervention_diagnostics
                if item.severity == "warning"
            )
            if intervention_errors:
                verification = VerificationResult(
                    "invalid",
                    None,
                    intervention_errors,
                    grounding_warnings,
                )
                return PipelineResult(
                    payload,
                    None,
                    verification,
                    None,
                    "semantic_preservation",
                )
            try:
                write_plan = materialize_reference_free_text_plan(
                    predicted_plan,
                    request,
                    self.profile,
                )
            except MaterializationError as exc:
                verification = VerificationResult(
                    "invalid",
                    None,
                    exc.diagnostics,
                    [],
                )
                return PipelineResult(
                    payload,
                    None,
                    verification,
                    None,
                    "evidence_materialization",
                )
        elif payload.mode == "semi_structured" and "target_groups" in predicted_plan:
            grounded_plan, grounding_warnings = ground_mapping_plan(
                predicted_plan,
                payload,
                self.profile,
            )
            try:
                write_plan = materialize_mapping_plan(
                    grounded_plan,
                    payload,
                    control_field_roles=self.stage2_interventions.control_field_roles,
                )
            except MaterializationError as exc:
                verification = VerificationResult(
                    "invalid",
                    None,
                    exc.diagnostics,
                    grounding_warnings,
                )
                return PipelineResult(
                    payload,
                    None,
                    verification,
                    None,
                    "materialization",
                )
        elif payload.mode == "free_text" and "target_groups" in predicted_plan:
            verification = VerificationResult(
                "invalid",
                None,
                [
                    Diagnostic(
                        "FREE_TEXT_REQUIRES_EXTRACTION_PLAN",
                        (
                            "Free-text input has no deterministic source rows; "
                            "the planner must return write_groups with value evidence."
                        ),
                        path="/target_groups",
                    )
                ],
                [],
            )
            return PipelineResult(
                payload,
                None,
                verification,
                None,
                "materialization",
            )
        else:
            write_plan = deepcopy(predicted_plan)
            if payload.mode == "free_text":
                write_plan.setdefault("version", "3.0")
                write_plan["plan_kind"] = "free_text_write_plan"
                write_plan["source"] = {
                    "mode": "free_text",
                    "format": "free_text",
                    "instruction_text": payload.instruction_text,
                    "row_count": 0,
                    "collections": [],
                    "evidence_required": True,
                }
                write_plan.setdefault("dependencies", [])
                write_plan.setdefault("unresolved_fields", [])
        verification = verify_write_plan(write_plan, self.profile)
        verification.warnings.extend(grounding_warnings)
        if not verification.valid:
            return PipelineResult(
                payload,
                write_plan,
                verification,
                None,
                "verification",
            )
        program = compile_verified_plan(
            verification.normalized_plan,
            self.profile,
            normalize_values=self.normalize_values,
            normalization_mode=self.normalization_mode,
        )
        program.warnings.extend(verification.warnings)
        return PipelineResult(
            payload,
            write_plan,
            verification,
            program,
            "compiled" if program.status == "success" else "compilation",
        )
