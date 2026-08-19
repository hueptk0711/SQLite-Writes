# Stage 2 Checkpoint A–C Patch 4 Report

## Scope

Patch 4 is a narrow deterministic trust-boundary hardening pass over A–C only. It does not change feature flags, planner architecture, compiler, verifier, prompts, repair logic, D–G, or model inference.

## Reviewer blocker addressed

Patch 3 protected free-text operation detection from quoted payload literals, but conflict-target and update-column extraction still scanned raw request text.

Patch 4 applies one shared rule:

> Quoted payload content must never become deterministic instruction semantics.

The implementation masks quoted literal spans when they are RHS values of ordinary payload assignments, while preserving:

- explicit quoted control values such as `conflict_target: "id"`;
- quoted conflict identifiers such as `ON CONFLICT("user_id")`;
- quoted update LHS/RHS identifiers such as `"name" = excluded."name"`.

This boundary is now used consistently before:

1. free-text operation extraction;
2. free-text conflict-target extraction;
3. free-text update-column extraction.

## Adversarial invariants

Patch 4 verifies that:

- `description='conflict_target=other'` cannot override an explicit conflict target;
- `description='update_columns=other'` cannot add an update column;
- `ON CONFLICT("user_id") DO NOTHING` still resolves the quoted identifier;
- `DO UPDATE SET "name" = excluded."name"` still resolves `name` from assignment LHS;
- `note='ON CONFLICT(other) DO NOTHING'` cannot create conflict semantics;
- `conflict_target: "id"` remains a valid quoted control value.

## Validation

- Stage-2 A–C adversarial/integration suite: **36/36 passed**.
- Compatibility subset: **100%, no failures**.
- Full fast suite (`-m "not integration"`): **100%, no failures**.
- CPU smoke: **PASS** using the existing mock-backend smoke path; provenance remained valid.
- No GPU/model inference performed.

## Decision requested

Review whether A–C can now be frozen as `Stage2 A-C FINAL` and work can proceed to D–G.
