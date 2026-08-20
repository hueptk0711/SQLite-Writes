# Stage 2 E — Free-text / Typed Normalization Method Specification

## Frozen parent

Stage E starts from:

```text
Stage2-D-FINAL
0eba16b297100966d3635172456086db872166d6
```

A–D are frozen. E consumes their existing interfaces and does not retune them from
Stage-1 diagnostic examples.

## Failure hypothesis

Stage-1.1 date audit identified 11 reviewed samples / 14 failing temporal-cell attempts:

- 12 explicit valid year-first DATETIME values were rejected by the previous date-only rule;
- `Attempt` and `For` were wrong evidence and must remain rejected;
- only three samples were clean normalization-only blockers, so no whole-sample rescue claim is made.

E tests whether deterministic free-text typed normalization can safely handle these cells
when **all** trust guards hold:

1. the resolved plan cell explicitly requests `iso_date_normalization`;
2. the evidence enumerator deterministically types the selected span as exactly `date` or `datetime`;
3. the candidate subtype matches the corresponding strict grammar;
4. the resolved target semantic type is temporal/text-compatible;
5. the declared target storage type is compatible with temporal text.

If any guard fails, E fails closed. It does not repair evidence, columns, groups, or references.

## Intervention boundary

```text
free-text request
    -> verbatim evidence enumeration
    -> resolved table/column IDs
    -> Stage-E typed normalization
    -> materialized Write Plan
    -> frozen verifier/compiler
```

E does not run on structured/semi-structured source parsing. D3 NULL semantics remain frozen.

## V5 ablation

```text
V4 = A + B + C + D
V5 = A + B + C + D + E
```

V5 adds only `free_text_typed_normalization`. V4 and V5 prompts remain identical for the
same sample; E acts only during deterministic materialization.

## Candidate-type invariant

E requires the enumerated evidence metadata to contain exactly one of:

```text
date
datetime
```

Missing/empty type fails with `TEMPORAL_EVIDENCE_TYPE_MISSING`.
Other types (`text`, `quoted_text`, `email`, `url`, `literal`, etc.) fail with
`TEMPORAL_EVIDENCE_TYPE_MISMATCH`.

Subtype consistency is mandatory:

```text
candidate_type=date     -> DATE grammar only
candidate_type=datetime -> DATETIME grammar only
```

Cross-subtype reinterpretation fails with `TEMPORAL_EVIDENCE_SUBTYPE_MISMATCH`.

## Target semantic/type compatibility

Target semantic metadata is checked before declared storage affinity.

Allowed semantic values are intentionally narrow:

```text
(empty/unspecified)
text
date
datetime
timestamp
temporal
date_key
```

Any explicit semantic outside this allowlist fails closed with
`TEMPORAL_TARGET_SEMANTIC_MISMATCH`, including identifiers, booleans, JSON, numeric
semantics, UUID-like semantics, URLs, emails, and blobs.

This still permits benchmark temporal values stored in SQLite `TEXT` columns when their
semantic type is `text`.

Declared numeric/blob/boolean/JSON-like storage types fail with
`TEMPORAL_TARGET_TYPE_MISMATCH`. DATE/TIME and text-like declared types remain compatible.
Unknown custom declared types are not silently treated as temporal storage.

## Allowed deterministic temporal grammar

E accepts ASCII-digit, year-first forms only:

```text
DATE:
YYYY-MM-DD
YYYY/MM/DD

DATETIME:
YYYY-MM-DD HH:MM:SS
YYYY-MM-DDTHH:MM:SS
YYYY/MM/DD HH:MM:SS
YYYY/MM/DDTHH:MM:SS

optional fractional seconds:
.1 through .ffffff
```

All digit classes use `[0-9]`; Unicode/full-width digits are outside this checkpoint.
Calendar/time validity is checked with `datetime`. Fractional precision above six digits
fails closed.

Canonicalization is deterministic:

```text
YYYY/MM/DD            -> YYYY-MM-DD
YYYY-MM-DDTHH:MM:SS   -> YYYY-MM-DD HH:MM:SS
```

Fractional digits are preserved exactly.

## Sentence-boundary punctuation

E may remove at most one terminal `.` or `,`, and only if the remainder passes the strict
candidate subtype grammar. There is no global `rstrip(".,")` rule.

```text
DATE + 2026-08-19.   -> 2026-08-19
DATE + 2026-08-19..  -> reject
identity + SC9081.   -> unchanged
```

Quotes are never stripped. Quoted/text candidates are not retyped as temporal evidence.

## Fail-closed ambiguity

Examples that remain rejected:

```text
01/02/2026
01/02/03
2026-13-40
Attempt
For
'2026-08-19.'
２０２６-０８-１９
```

E does not guess locale/date order and does not repair wrong evidence.

## Causal provenance contract

For a cell not handled by E:

```text
intervention_applied = false
applied              = false
value_changed        = false
outcome              = NOT_APPLICABLE
```

For a successful E decision, `applied` means **the intervention handled and accepted the
cell**, not merely that the output bytes changed.

Example Stage-1 DATETIME:

```text
2026-07-30 14:47:00 -> 2026-07-30 14:47:00
intervention_applied = true
applied              = true
value_changed        = false
accepted             = true
outcome              = ACCEPT
```

Canonicalizing slash date:

```text
2026/08/19 -> 2026-08-19
intervention_applied = true
applied              = true
value_changed        = true
```

Handled rejects have `applied=true`, `accepted=false`, `outcome=REJECT` so later causal
replay can distinguish E activation from string mutation.

The audit retains:

```text
stage2_intervention
raw_evidence_span
parsed_candidate
semantic_type
normalized_value
normalization_rule
normalization_confidence
requested_normalization
candidate_type
target_semantic_type
target_declared_type
evidence_id
evidence_start
evidence_end
intervention_applied
applied
value_changed
accepted
outcome
lossless
```

Raw evidence preservation is a mandatory Stage-E invariant. The compatibility config key
`preserve_raw_evidence` must remain true whenever E is enabled; disabling it raises an
error rather than silently removing audit evidence.

## Stage-1 diagnostic fixture

Each fixture cell records its actual typed boundary metadata:

```text
candidate_type
target_semantic_type
```

The 12 valid datetime cells use `candidate_type=datetime`; `Attempt` and `For` use
`candidate_type=text` and are expected to reject. This remains diagnostic/regression
evidence only, not end-to-end rescue evidence.

## D3 boundary

Structured-source NULL handling remains exactly frozen under D. E does not add free-text
NULL repair or modify D3.

## Explicit non-goals

Stage E does not implement evidence repair, column/group mapping repair, F, G, verifier
relaxation, prompt changes, GPU/model runs, 7B development runs, full causal replay, or
confirmatory claims.
