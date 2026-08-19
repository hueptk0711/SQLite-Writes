# Candidate Fixes

## Issue ID: MPF-ERR-001

Affected stage: reference_resolution

Affected samples: 28

Observed behavior: LLM emits non-existent enumerated target-column IDs.

Likely cause: frozen v2.1 output shows this as a recurring first-order failure class.

Possible fix: Strengthen schema-ID constraints and add a repair pass for nearest valid column IDs.

Expected benefit: bounded by 28 currently affected incorrect samples before interaction with other fixes.

Risk: changes planner/repair behavior and therefore requires a fresh Stage 2 evaluation.

## Issue ID: MPF-ERR-002

Affected stage: materialization

Affected samples: 27

Observed behavior: Source fields remain unmapped or invalid source-field references are produced.

Likely cause: frozen v2.1 output shows this as a recurring first-order failure class.

Possible fix: Separate control/instruction fields from payload fields before materialization.

Expected benefit: bounded by 27 currently affected incorrect samples before interaction with other fixes.

Risk: changes planner/repair behavior and therefore requires a fresh Stage 2 evaluation.

## Issue ID: MPF-ERR-003

Affected stage: verification

Affected samples: 21

Observed behavior: Plans omit required target columns after grounding.

Likely cause: frozen v2.1 output shows this as a recurring first-order failure class.

Possible fix: Add planner repair for required-column coverage using available evidence.

Expected benefit: bounded by 21 currently affected incorrect samples before interaction with other fixes.

Risk: changes planner/repair behavior and therefore requires a fresh Stage 2 evaluation.

## Issue ID: MPF-ERR-004

Affected stage: execution/state_comparison

Affected samples: 16

Observed behavior: Candidates pass checks but produce target-state mismatch.

Likely cause: frozen v2.1 output shows this as a recurring first-order failure class.

Possible fix: Introduce post-execution semantic repair candidates in Stage 2.

Expected benefit: bounded by 16 currently affected incorrect samples before interaction with other fixes.

Risk: changes planner/repair behavior and therefore requires a fresh Stage 2 evaluation.

## Issue ID: MPF-ERR-005

Affected stage: semantic_gate/preflight

Affected samples: 0

Observed behavior: Candidate is rejected after compilation by semantic or SQLite safety gate.

Likely cause: frozen v2.1 output shows this as a recurring first-order failure class.

Possible fix: Use oracle-bypass evidence to decide relax-vs-repair policy.

Expected benefit: bounded by 0 currently affected incorrect samples before interaction with other fixes.

Risk: changes planner/repair behavior and therefore requires a fresh Stage 2 evaluation.
