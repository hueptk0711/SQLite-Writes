# Candidate Fixes

These are Stage-2 candidates, not established causal fixes. Stage 1.1 must finish the manual audit first.

## Issue ID: MPF-ERR-001

Affected stage: reference_resolution

Affected samples: 28

Observed behavior: LLM emits target-column references that are not members of the enumerated legal inventory.

Stage-2 candidate action: Detect the invalid reference, present only legal IDs plus relevant schema meanings, and run a targeted constrained repair. Do not auto-map to the nearest name because that can turn a fail-safe error into silent semantic corruption.

Risk: any method change requires a fresh Stage-2 evaluation on frozen predictions/protocol boundaries as appropriate.

## Issue ID: MPF-ERR-002

Affected stage: materialization/provenance

Affected samples: 22

Observed behavior: The recurring `operation` source field is treated as an unmapped payload field even when it functions as control/instruction metadata.

Stage-2 candidate action: Audit the affected samples, then introduce an explicit control-field versus payload-field policy only if the audit confirms that `operation` is non-payload metadata.

Risk: any method change requires a fresh Stage-2 evaluation on frozen predictions/protocol boundaries as appropriate.

## Issue ID: MPF-ERR-003

Affected stage: verification

Affected samples: 21

Observed behavior: Plans omit required target columns after grounding.

Stage-2 candidate action: After causal audit, test a constrained required-column coverage repair using only grounded evidence.

Risk: any method change requires a fresh Stage-2 evaluation on frozen predictions/protocol boundaries as appropriate.

## Issue ID: MPF-ERR-004

Affected stage: execution/state_comparison

Affected samples: 16

Observed behavior: Candidates execute successfully but do not match the gold target state.

Stage-2 candidate action: Use `state_mismatch_audit.csv` to inspect gold delta, predicted delta, and the deterministic state-diff class. Do not prescribe a post-execution repair until these mismatch subtypes have been manually reviewed.

Risk: any method change requires a fresh Stage-2 evaluation on frozen predictions/protocol boundaries as appropriate.

## Issue ID: MPF-ERR-005

Affected stage: semantic_gate/preflight

Affected samples: 7

Observed behavior: Candidates are rejected after compilation by the semantic-risk gate or SQLite preflight.

Stage-2 candidate action: Analyze semantic-gate and preflight failures separately. Use stage-matched ablations where available; do not infer verifier causality from V0.

Risk: any method change requires a fresh Stage-2 evaluation on frozen predictions/protocol boundaries as appropriate.

## Issue ID: MPF-ERR-006

Affected stage: free_text/materialization

Affected samples: 11

Observed behavior: Date normalization failures form a systematic free-text subgroup.

Stage-2 candidate action: Manually distinguish genuinely ambiguous dates, unsupported-but-valid formats, and incorrect evidence spans before changing the normalizer.

Risk: any method change requires a fresh Stage-2 evaluation on frozen predictions/protocol boundaries as appropriate.

## Issue ID: MPF-ERR-007

Affected stage: conflict_planning

Affected samples: 20

Observed behavior: Conflict behavior is marked ambiguous and the fail-closed policy abstains.

Stage-2 candidate action: Populate `conflict_ambiguity_gold_label` as TRULY_AMBIGUOUS, RESOLVABLE_FROM_INPUT, RESOLVABLE_FROM_SCHEMA, or UNKNOWN before treating these cases as representation failures.

Risk: any method change requires a fresh Stage-2 evaluation on frozen predictions/protocol boundaries as appropriate.
