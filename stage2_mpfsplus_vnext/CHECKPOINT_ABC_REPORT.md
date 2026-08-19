# Stage 2 Checkpoint A–C Patch 3 Report

## Scope

Patch 3 is a deterministic hardening pass over A–C only. It does not implement D–G, causal replay, prompt changes, or model inference.

## Reviewer blockers addressed

1. **Exact DB identifier boundary.** Control aliases still use loose canonicalization, but schema identifiers now use quote-stripped, case-insensitive exact keys that preserve underscores and punctuation. `user_id` and `userid` remain distinct. Candidate maps retain all exact candidates and ambiguous identifiers fail closed with `AMBIGUOUS_IDENTIFIER`.
2. **Conflict action/target separation.** `CONFLICT_ACTION_CONTROL` and `CONFLICT_TARGET_CONTROL` are distinct. Target/key values are never passed to the operation/action parser.
3. **Strict structured operation aliases.** Structured control values use exact alias sets; substring values such as `skip_validation` do not become `insert_ignore`.
4. **High-confidence free-text B.** Quoted literals are masked before instruction parsing. Deterministic restoration requires explicit syntax such as `ON CONFLICT ... DO ...`, `INSERT OR IGNORE/UPDATE`, typed `operation:` assignments, or an explicit conditional conflict/duplicate cue with a nearby action. Bare payload words do not change semantics.
5. **V0 artifact compatibility.** When Stage-2 control roles are off and there is no deterministic consumption, unresolved-field records do not gain Stage-2-only `role` metadata. A frozen materialization fixture checks full-structure equality.
6. **Experiment provenance.** `method_variant` and `method_version` are recorded in run lock, manifest, sample-level processed artifacts, summary metadata, and final-consumed identity.

## Validation

- Stage-2 A–C adversarial/integration suite: **30/30 passed**.
- Compatibility subset: **100%, no failures**.
- Full fast suite (`-m "not integration"`): **100%, no failures**.
- CPU smoke: **PASS** using the mock backend on one synthetic sample; run lock, manifest, summary metadata, and evaluation sample all carried the expected variant/version.
- No GPU/model inference performed.

## Decision requested

Review whether A–C can now be frozen as `Stage2 A-C FINAL` and work can proceed to D–G.
