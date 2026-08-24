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
    extract_evidence_candidates,
    ground_reference_mapping_plan,
    ground_mapping_plan,
    materialize_mapping_plan,
    materialize_reference_free_text_plan,
    resolve_reference_mapping_plan,
)
from nldbwrite_v3.source_parser import parse_source_payload
from nldbwrite_v3.verifier import verify_write_plan
from nldbwrite_v3.vnext import (
    ConstrainedReferenceRepairConfig,
    DiagnosticTargetedRepairConfig,
    Stage2InterventionConfig,
    annotate_reference_diagnostics,
    apply_free_text_reference_interventions,
    attach_repair_trace,
    attach_targeted_repair_trace,
    diagnose_evidence_span_boundaries,
    diagnose_temporal_evidence_selections,
    mark_revalidation_outcome,
    mark_targeted_revalidation,
    repair_evidence_span_boundary_after_diagnostic,
    repair_temporal_evidence_selection_after_diagnostic,
    repair_free_text_plan_after_diagnostics,
    repair_mapping_plan_after_diagnostics,
    repair_warnings_from_traces,
    targeted_repair_warnings,
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
        structured_source_parser: Mapping[str, Any] | None = None,
        free_text_typed_normalization: Mapping[str, Any] | None = None,
        constrained_reference_repair: Mapping[str, Any] | None = None,
        diagnostic_targeted_repair: Mapping[str, Any] | None = None,
    ):
        self.profile = profile
        self.strict_atomic = strict_atomic
        self.normalize_values = normalize_values
        self.normalization_mode = normalization_mode
        self.reference_planning = reference_planning
        self.stage2_interventions = Stage2InterventionConfig.from_mapping(
            stage2_interventions
        )
        self.structured_source_parser = dict(structured_source_parser or {})
        self.free_text_typed_normalization = dict(
            free_text_typed_normalization or {}
        )
        self.constrained_reference_repair = (
            ConstrainedReferenceRepairConfig.from_mapping(
                constrained_reference_repair
            )
        )
        self.diagnostic_targeted_repair = (
            DiagnosticTargetedRepairConfig.from_mapping(
                diagnostic_targeted_repair
            )
        )

    def run(
        self,
        request: str,
        predicted_plan: dict[str, Any],
    ) -> PipelineResult:
        payload = parse_source_payload(
            request,
            structured_parser=self.structured_source_parser,
        )
        grounding_warnings: list[Diagnostic] = []
        targeted_repair_traces: list[dict[str, Any]] = []
        targeted_rollback_plan: dict[str, Any] | None = None
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
            resolution_warnings: list[Diagnostic] = []
            grounded_plan, reference_errors = resolve_reference_mapping_plan(
                predicted_plan,
                payload,
                self.profile,
                stage2_interventions=self.stage2_interventions.to_dict(),
                warning_sink=resolution_warnings,
            )
            if reference_errors and self.constrained_reference_repair.enabled:
                repair_outcome = repair_mapping_plan_after_diagnostics(
                    predicted_plan,
                    payload,
                    self.profile,
                    reference_errors,
                    self.constrained_reference_repair,
                )
                if repair_outcome.applied:
                    retry_warnings: list[Diagnostic] = []
                    grounded_plan, retry_errors = resolve_reference_mapping_plan(
                        repair_outcome.plan,
                        payload,
                        self.profile,
                        stage2_interventions=self.stage2_interventions.to_dict(),
                        warning_sink=retry_warnings,
                    )
                    final_traces = mark_revalidation_outcome(
                        repair_outcome.traces,
                        retry_errors,
                    )
                    repair_warnings = repair_warnings_from_traces(final_traces)
                    if retry_errors:
                        verification = VerificationResult(
                            "invalid",
                            None,
                            annotate_reference_diagnostics(
                                retry_errors,
                                final_traces,
                            ),
                            [
                                *grounding_warnings,
                                *retry_warnings,
                                *repair_warnings,
                            ],
                        )
                        return PipelineResult(
                            payload,
                            None,
                            verification,
                            None,
                            "reference_resolution",
                        )
                    grounded_plan = attach_repair_trace(
                        grounded_plan,
                        final_traces,
                    )
                    grounding_warnings.extend(retry_warnings)
                    grounding_warnings.extend(repair_warnings)
                    reference_errors = []
                else:
                    reference_errors = annotate_reference_diagnostics(
                        reference_errors,
                        repair_outcome.traces,
                    )
                    grounding_warnings.extend(resolution_warnings)
            else:
                grounding_warnings.extend(resolution_warnings)
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
            materialized_reference_plan = predicted_plan
            final_reference_traces: list[dict[str, Any]] = []
            try:
                write_plan = materialize_reference_free_text_plan(
                    materialized_reference_plan,
                    request,
                    self.profile,
                    free_text_typed_normalization=(
                        self.free_text_typed_normalization
                    ),
                )
            except MaterializationError as exc:
                reference_repair_outcome = None
                if self.constrained_reference_repair.enabled:
                    reference_repair_outcome = (
                        repair_free_text_plan_after_diagnostics(
                            predicted_plan,
                            self.profile,
                            exc.diagnostics,
                            self.constrained_reference_repair,
                        )
                    )
                if (
                    reference_repair_outcome is not None
                    and reference_repair_outcome.applied
                ):
                    try:
                        materialized_reference_plan = (
                            reference_repair_outcome.plan
                        )
                        write_plan = materialize_reference_free_text_plan(
                            materialized_reference_plan,
                            request,
                            self.profile,
                            free_text_typed_normalization=(
                                self.free_text_typed_normalization
                            ),
                        )
                    except MaterializationError as retry_exc:
                        final_traces = mark_revalidation_outcome(
                            reference_repair_outcome.traces,
                            retry_exc.diagnostics,
                        )
                        verification = VerificationResult(
                            "invalid",
                            None,
                            annotate_reference_diagnostics(
                                retry_exc.diagnostics,
                                final_traces,
                            ),
                            repair_warnings_from_traces(final_traces),
                        )
                        return PipelineResult(
                            payload,
                            None,
                            verification,
                            None,
                            "evidence_materialization",
                        )
                    final_traces = mark_revalidation_outcome(
                        reference_repair_outcome.traces,
                        [],
                    )
                    write_plan = attach_repair_trace(
                        write_plan,
                        final_traces,
                    )
                    grounding_warnings.extend(
                        repair_warnings_from_traces(final_traces)
                    )
                    final_reference_traces = final_traces
                else:
                    evidence_candidates = extract_evidence_candidates(request)
                    selection_diagnostics = (
                        diagnose_temporal_evidence_selections(
                            predicted_plan,
                            request,
                            evidence_candidates,
                            self.profile,
                            exc.diagnostics,
                            self.free_text_typed_normalization,
                            self.diagnostic_targeted_repair,
                        )
                    )
                    selection_outcome = (
                        repair_temporal_evidence_selection_after_diagnostic(
                            predicted_plan,
                            selection_diagnostics,
                            self.diagnostic_targeted_repair,
                        )
                    )
                    if selection_diagnostics and not selection_outcome.applied:
                        for index, diagnostic in enumerate(
                            selection_diagnostics
                        ):
                            item = deepcopy(diagnostic)
                            if index < len(selection_outcome.traces):
                                item.details["targeted_repair"] = deepcopy(
                                    selection_outcome.traces[index]
                                )
                            selection_diagnostics[index] = item
                        verification = VerificationResult(
                            "invalid",
                            None,
                            selection_diagnostics,
                            grounding_warnings,
                        )
                        return PipelineResult(
                            payload,
                            None,
                            verification,
                            None,
                            "diagnostic_targeted_repair",
                        )
                    if selection_outcome.applied:
                        try:
                            write_plan = materialize_reference_free_text_plan(
                                selection_outcome.plan,
                                request,
                                self.profile,
                                free_text_typed_normalization=(
                                    self.free_text_typed_normalization
                                ),
                            )
                        except MaterializationError as retry_exc:
                            targeted_repair_traces = (
                                mark_targeted_revalidation(
                                    selection_outcome.traces,
                                    passed=False,
                                    error_codes=[
                                        item.error_code
                                        for item in retry_exc.diagnostics
                                    ],
                                )
                            )
                            verification = VerificationResult(
                                "invalid",
                                None,
                                retry_exc.diagnostics,
                                [
                                    *grounding_warnings,
                                    *targeted_repair_warnings(
                                        targeted_repair_traces
                                    ),
                                ],
                            )
                            return PipelineResult(
                                payload,
                                None,
                                verification,
                                None,
                                "diagnostic_targeted_revalidation",
                            )
                        residual_boundary_diagnostics = (
                            diagnose_evidence_span_boundaries(
                                selection_outcome.plan,
                                evidence_candidates,
                                self.diagnostic_targeted_repair,
                            )
                        )
                        if residual_boundary_diagnostics:
                            targeted_repair_traces = (
                                mark_targeted_revalidation(
                                    selection_outcome.traces,
                                    passed=False,
                                    error_codes=[
                                        item.error_code
                                        for item in residual_boundary_diagnostics
                                    ],
                                )
                            )
                            verification = VerificationResult(
                                "invalid",
                                None,
                                residual_boundary_diagnostics,
                                [
                                    *grounding_warnings,
                                    *targeted_repair_warnings(
                                        targeted_repair_traces
                                    ),
                                ],
                            )
                            return PipelineResult(
                                payload,
                                None,
                                verification,
                                None,
                                "diagnostic_targeted_revalidation",
                            )
                        materialized_reference_plan = selection_outcome.plan
                        targeted_repair_traces = selection_outcome.traces
                    else:
                        if reference_repair_outcome is not None:
                            errors = annotate_reference_diagnostics(
                                exc.diagnostics,
                                reference_repair_outcome.traces,
                            )
                        else:
                            errors = exc.diagnostics
                        verification = VerificationResult(
                            "invalid",
                            None,
                            errors,
                            [],
                        )
                        return PipelineResult(
                            payload,
                            None,
                            verification,
                            None,
                            "evidence_materialization",
                        )

            if (
                self.diagnostic_targeted_repair.enabled
                and not targeted_repair_traces
            ):
                evidence_candidates = extract_evidence_candidates(request)
                targeted_diagnostics = diagnose_evidence_span_boundaries(
                    materialized_reference_plan,
                    evidence_candidates,
                    self.diagnostic_targeted_repair,
                )
                if targeted_diagnostics:
                    original_write_plan = write_plan
                    targeted_rollback_plan = original_write_plan
                    targeted_outcome = (
                        repair_evidence_span_boundary_after_diagnostic(
                            materialized_reference_plan,
                            targeted_diagnostics,
                            self.diagnostic_targeted_repair,
                        )
                    )
                    if not targeted_outcome.applied:
                        for index, diagnostic in enumerate(targeted_diagnostics):
                            item = deepcopy(diagnostic)
                            if index < len(targeted_outcome.traces):
                                item.details["targeted_repair"] = deepcopy(
                                    targeted_outcome.traces[index]
                                )
                            targeted_diagnostics[index] = item
                        verification = VerificationResult(
                            "invalid",
                            None,
                            targeted_diagnostics,
                            grounding_warnings,
                        )
                        return PipelineResult(
                            payload,
                            original_write_plan,
                            verification,
                            None,
                            "diagnostic_targeted_repair",
                        )
                    try:
                        write_plan = materialize_reference_free_text_plan(
                            targeted_outcome.plan,
                            request,
                            self.profile,
                            free_text_typed_normalization=(
                                self.free_text_typed_normalization
                            ),
                        )
                    except MaterializationError as retry_exc:
                        targeted_repair_traces = mark_targeted_revalidation(
                            targeted_outcome.traces,
                            passed=False,
                            error_codes=[
                                item.error_code for item in retry_exc.diagnostics
                            ],
                        )
                        failed_plan = attach_targeted_repair_trace(
                            original_write_plan,
                            targeted_repair_traces,
                        )
                        verification = VerificationResult(
                            "invalid",
                            None,
                            retry_exc.diagnostics,
                            [
                                *grounding_warnings,
                                *targeted_repair_warnings(
                                    targeted_repair_traces
                                ),
                            ],
                        )
                        return PipelineResult(
                            payload,
                            failed_plan,
                            verification,
                            None,
                            "diagnostic_targeted_revalidation",
                        )
                    revalidation_diagnostics = (
                        diagnose_evidence_span_boundaries(
                            targeted_outcome.plan,
                            evidence_candidates,
                            self.diagnostic_targeted_repair,
                        )
                    )
                    if revalidation_diagnostics:
                        targeted_repair_traces = mark_targeted_revalidation(
                            targeted_outcome.traces,
                            passed=False,
                            error_codes=[
                                item.error_code
                                for item in revalidation_diagnostics
                            ],
                        )
                        failed_plan = attach_targeted_repair_trace(
                            original_write_plan,
                            targeted_repair_traces,
                        )
                        verification = VerificationResult(
                            "invalid",
                            None,
                            revalidation_diagnostics,
                            [
                                *grounding_warnings,
                                *targeted_repair_warnings(
                                    targeted_repair_traces
                                ),
                            ],
                        )
                        return PipelineResult(
                            payload,
                            failed_plan,
                            verification,
                            None,
                            "diagnostic_targeted_revalidation",
                        )
                    materialized_reference_plan = targeted_outcome.plan
                    targeted_repair_traces = targeted_outcome.traces
                    if final_reference_traces:
                        write_plan = attach_repair_trace(
                            write_plan,
                            final_reference_traces,
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
        if targeted_repair_traces:
            targeted_repair_traces = mark_targeted_revalidation(
                targeted_repair_traces,
                passed=verification.valid,
                error_codes=[item.error_code for item in verification.errors],
            )
            trace_plan = (
                write_plan if verification.valid else targeted_rollback_plan
            )
            write_plan = (
                attach_targeted_repair_trace(
                    trace_plan,
                    targeted_repair_traces,
                )
                if trace_plan is not None
                else None
            )
            if verification.normalized_plan is not None:
                verification.normalized_plan = attach_targeted_repair_trace(
                    verification.normalized_plan,
                    targeted_repair_traces,
                )
            grounding_warnings.extend(
                targeted_repair_warnings(targeted_repair_traces)
            )
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
