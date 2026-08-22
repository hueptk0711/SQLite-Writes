# Stage 2-G2 Patch 3 Method Specification — Temporal Evidence Selection

## Scope and frozen parent

G2 Patch 3 is a deterministic, temporal-only evidence-reference repair. Its frozen method parent and patch base are:

```text
tag:    Stage2-G1-FINAL
commit: b3d6e721b5d3c1ea9a5fd7e117692a807815dcb7
Patch 3 base commit: 7b3a52e2ea0015f765eec825b1e5cdb6a9c7524d
```

A–F and G1 are unchanged. G2 does not call a model, regenerate a plan, create evidence, scan the request for new spans, rank candidates, or repair generic text evidence.

## Contract

```text
frozen A–F materializer rejects one temporal-normalization slot
    ↓ exact deterministic Stage-E type-mismatch diagnostic
G2 identifies exactly one diagnosed value_from slot
    ↓ old evidence grounding is none or the diagnosed target
old effective target is safe
    ↓ filter the pre-enumerated evidence candidate set
frozen exact-column grounding preserves diagnosed target
    ↓ exactly one compatible candidate remains
exactly one compatible temporal candidate
    ↓ deep copy
change only that value_from
    ↓ same frozen materializer exactly once
same frozen G1 diagnostic boundary, without applying G1
    ↓ same frozen verifier exactly once
PASS or FAIL CLOSED
```

## Diagnostic gate

G2 creates `TEMPORAL_EVIDENCE_SELECTION_INCOMPATIBLE` only from an existing materializer diagnostic satisfying all of these conditions:

1. error code is `TYPED_NORMALIZATION_REJECTED`;
2. typed error code is `TEMPORAL_EVIDENCE_TYPE_MISMATCH`;
3. the diagnosed normalization rule is `iso_date_normalization`;
4. the diagnostic path resolves to the current plan slot;
5. its evidence ID and raw value still equal the selected frozen candidate;
6. the selected candidate is non-temporal;
7. the table and column references resolve exactly in the frozen profile;
8. frozen exact-column grounding for the rejected evidence resolves no explicit target or the same diagnosed target.

No diagnosis is inferred from lexical similarity or a desired gold value.

## Closed compatible candidate set

G2 starts only from `extract_evidence_candidates(request)`, the frozen enumerated set already used by the pipeline. A candidate remains compatible only when:

- its candidate type is `date` or `datetime`;
- its frozen candidate role is `primary`;
- it is a maximal temporal span, excluding a date component contained by an enclosing datetime;
- it starts after the rejected selection ends;
- it stays inside the deterministic sentence containing the rejected selection;
- the frozen exact-column grounding rule resolves no explicit column or resolves the same diagnosed column;
- the existing frozen Stage-E target/type validator accepts it for the diagnosed column and normalization.

Sentence boundaries are only newline or `.`, `!`, `?`, `;` followed by whitespace/end. Decimal and fractional-second dots are therefore not boundaries.

Exactly one compatible candidate is required. Zero or multiple candidates produce `non_unique_compatible_candidate_set` and no mutation. G2 never breaks a tie by distance, order, score, embedding, fuzzy match, edit distance, schema contents, database state, or a model.

## Rejected-evidence target grounding

Frozen materialization performs exact-column grounding before Stage-E normalization, while its error path still names the original plan column. Patch 3 therefore grounds the rejected evidence itself before evaluating any replacement.

```text
old evidence: no explicit column       -> continue
old evidence: diagnosed column         -> continue
old evidence: different local column   -> FAIL CLOSED
old evidence: other-table-only column  -> FAIL CLOSED
```

The diagnostic records `selected_evidence_effective_target_grounding` with one of `no_explicit_column`, `same_diagnosed_column`, `different_same_table_column`, or `cross_table_only_column`. Unsafe cases create an empty compatible set and the secondary repair guard returns `invalid_source_grounding_provenance`; the materializer is not retried.

This policy is deliberately conservative instead of simulating row-level remap collision suppression.

## Replacement target grounding

The frozen materializer may use a candidate's `left_context` to remap the predicted column when an immediately preceding exact schema identifier names a column. Patch 2 reuses that exact deterministic resolver through the read-only `resolve_explicit_column_grounding` adapter before candidate admission. It does not change the resolver or materializer.

```text
no explicit column signal             -> eligible for remaining filters
explicit diagnosed column             -> eligible for remaining filters
explicit different same-table column  -> exclude
explicit other-table-only column       -> exclude
```

A different same-table target is recorded as `candidate_target_grounding_mismatch`. An other-table-only signal is recorded as `candidate_cross_table_grounding_conflict`. Both are retained under `target_grounding_rejections` provenance, and neither candidate enters the compatible closed set.

This guard is conservative even when another row-level collision might later suppress the materializer remap. G2 admits only replacements whose standalone frozen grounding cannot change the diagnosed target column.

## One-slot mutation and collision safety

Exactly one G2 diagnostic is allowed per invocation. Multiple diagnosed slots fail closed.

The reference plan is deep-copied. The only permitted mutation is:

```text
/write_groups/<group-index>/rows/<row-index>/<column-id>/value_from
```

The normalization rule, operation, table/group/column references, conflict semantics, update columns, dependencies, values, request, prompt, and all other evidence references remain unchanged. If the selected replacement evidence is already assigned to another semantic slot, G2 fails closed with `replacement_evidence_reference_collision`.

## One revalidation and atomic rollback

After mutation, G2 invokes the same frozen materializer exactly once. A materialization error is returned as `diagnostic_targeted_revalidation`; no new repair is attempted.

After successful materialization, G2 checks the frozen G1 diagnostic boundary. If G1 would be required, the invocation fails closed rather than chaining G2 and G1. The frozen verifier then runs once. A verifier failure rolls back to the pre-repair state; because that state did not materialize, no write plan is compiled or returned.

`repair_succeeded=true` is recorded only after the materializer, residual G1 check, and verifier pass.

## Provenance

Every attempted G2 trace records at least:

```text
diagnostic
diagnostic_source
diagnosed_slot
target table/column/type context
old_reference
old_value
old_candidate_type
selected_evidence_effective_target_grounding
candidate_set
target_grounding_rejections
selected_repair
repair_rule
selection_policy
context_window
repair_attempted
repair_applied
repair_succeeded
revalidation_result
revalidation_attempts
revalidation_error_codes
atomic_rollback
```

Successful traces are stored under `write_groups[*].reference_trace.diagnostic_targeted_repairs`.

## Ablation and compatibility

```text
V7 = A+B+C+D+E+F+G1
V8 = A+B+C+D+E+F+G1+G2
```

G2 is controlled by `diagnostic_targeted_repair.evidence_span_selection`. The safety invariant `preserve_effective_target_grounding` is mandatory and cannot be disabled. With evidence-span selection disabled, the frozen G1 materialization failure behavior is preserved. V7 and V8 planner prompts are identical.

## Stage-1 diagnostic fixtures

- `final_vaccine_002`: temporal G2-eligible diagnostic fixture;
- `final_vaccine_048`: temporal G2-eligible diagnostic fixture;
- `final_virtual_046`: non-temporal evidence-selection control, explicitly ineligible.

These are development diagnostic/regression cases, not confirmatory test data or claims of complete sample rescue. Other latent failures may remain. No model is run in Stage 2-G2.

## Explicitly out of scope

- generic text, quoted text, label-value, or semantic evidence selection;
- backward or cross-sentence selection;
- candidate generation or request-wide search;
- plan regeneration or LLM repair;
- operation/conflict/update repair owned by A–C;
- parser behavior owned by D;
- normalization policy owned by E;
- structural reference repair owned by F;
- G1 boundary mutation during the same G2 invocation;
- Stage 3 replay or any model experiment.
