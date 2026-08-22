# Stage 2-G1 Method Specification — Evidence-Span Boundary Targeted Repair

## Scope

G1 is a deterministic, post-diagnostic repair for one narrow free-text evidence-boundary defect. It does not invoke a model, regenerate a plan, search request text, create evidence, select a semantically different span, or repair any A–F responsibility.

The only eligible transformation is:

```text
selected pre-enumerated identifier span: SC9081.
unique pre-enumerated bounded span:       SC9081
diagnosed value_from:                     e3
repaired value_from:                      e7
```

## Frozen parent

```text
tag:    Stage2-F-FINAL
commit: 48b36e06a0ccef2bee69120029820f99f9fa6af5
```

A–F are frozen. G1 consumes the plan and evidence candidate set produced by those components through their existing interfaces.

## G1 contract

```text
frozen A–F free-text materialization succeeds
    ↓
deterministic G1 boundary diagnostic
    ↓ exactly one diagnosed value_from slot
closed pre-enumerated candidate set
    ↓ exactly one compatible bounded candidate
deep copy of reference plan
    ↓ change only diagnosed value_from
same frozen materializer, one retry maximum
    ↓ same G1 diagnostic boundary + frozen verifier
PASS or FAIL CLOSED
```

## Deterministic diagnostic

G1 diagnoses a slot only when all conditions hold:

1. G1 is enabled.
2. The selected evidence already exists in the frozen enumerated candidate set.
3. Its type is `number_or_identifier`.
4. Its text ends with exactly one allowed terminal character: `.` or `,`.
5. Removing that final character yields a strict identifier containing both alphabetic and numeric content.
6. The frozen candidate set already contains a candidate with:
   - the same start offset;
   - end offset exactly one less;
   - text exactly equal to the bounded text;
   - type `number_or_identifier`.

No substring scan, regex search over the request, fuzzy match, edit distance, schema-value lookup, database-state lookup, or model call is permitted.

## Closed-set selection

The candidate set is derived only by filtering the already enumerated evidence candidates against the exact `(start, end - 1, text_without_terminal)` boundary. Exactly one candidate is required.

Zero candidates means no G1 diagnosis. More than one candidate means `FAIL CLOSED`; G1 does not choose by ordering or candidate priority.

## One-slot isolation and atomicity

Exactly one diagnosed semantic slot is required per invocation. Multiple diagnosed slots fail closed without mutation.

The reference plan is deep-copied. The only permitted mutation is:

```text
/write_groups/<group>/rows/<row>/<column_id>/value_from
```

The operation, conflict action/target, update columns, table/column/group references, normalization rule, dependencies, unresolved fields, prompt, request, values, and every other evidence reference remain unchanged.

If the replacement evidence reference is already assigned to another semantic slot, G1 reports `replacement_evidence_reference_collision` and fails closed. It never collapses or merges evidence assignments.

## One revalidation

After the deep-copy mutation, the same frozen free-text materializer is called once. G1 then runs its deterministic diagnostic boundary once on the repaired plan, followed by the existing frozen verifier. No second repair or recursive retry is allowed.

`repair_succeeded=true` is recorded only when the materializer, G1 diagnostic boundary, and verifier all pass. Any error yields `revalidation_result=FAIL_CLOSED` and no compilation.

## Provenance

Each applied/failed attempt records:

```text
diagnostic
diagnosed_slot
semantic_slot
target_column_reference
old_reference
old_value
candidate_set
selected_repair
repair_rule
repair_reason
repair_attempted
repair_applied
repair_succeeded
revalidation_result
revalidation_attempts
revalidation_error_codes
atomic_rollback
max_revalidation_attempts
```

Successful materialized plans store the trace under:

```text
write_groups[*].reference_trace.diagnostic_targeted_repairs
```

## Ablation

```text
V6 = A+B+C+D+E+F
V7 = A+B+C+D+E+F+G1
```

G1 is controlled only by `diagnostic_targeted_repair.enabled`. With G1 disabled, pipeline output is byte-structure compatible with frozen V6 behavior and the planner prompt is unchanged.

## Stage-1 fixture status

`final_archeology_032` is the single manually audited Stage-1 G1 regression fixture. It is development diagnostic evidence, not a new experiment, confirmatory test, or headline rescue claim. No model was run for Stage 2-G1.

## Explicitly out of scope

- G2 evidence-span selection repair;
- changing a valid non-boundary evidence choice;
- free-text mapping/group repair;
- operation/conflict/update repair;
- reference repair owned by F;
- normalization owned by E;
- parser behavior owned by D;
- arbitrary punctuation stripping;
- quoted-string stripping;
- plan regeneration or LLM repair;
- Stage 3 replay or any model experiment.
