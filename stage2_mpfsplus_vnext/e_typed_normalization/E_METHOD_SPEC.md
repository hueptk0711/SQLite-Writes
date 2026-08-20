# Stage 2 E — Free-text / Typed Normalization Method Specification

## Frozen parent

Stage E starts from the frozen checkpoint:

```text
Stage2-D-FINAL
0eba16b297100966d3635172456086db872166d6
```

A–D are frozen. E must consume their existing interfaces and must not retune them from
Stage-1 diagnostic examples.

## Failure hypothesis

Stage-1.1 manual audit separated date-related failures into:

```text
DATE_NORMALIZATION_POLICY_ERROR                9
DATE_NORMALIZATION_AND_EVIDENCE_SPAN_ERROR     1
EVIDENCE_SPAN_SELECTION_ERROR                  1
```

The date audit inspected 11 samples and observed 14 failing temporal-cell attempts:

- 12 values were explicit, valid year-first datetimes copied from the request and used in gold;
- `Attempt` and `For` were wrong evidence values and must remain rejected;
- only 3/11 samples were clean normalization-only blockers, so E does not claim whole-sample rescue.

The specific deterministic failure is that `iso_date_normalization` accepts DATE forms but
rejects explicit DATETIME values such as:

```text
2026-07-30 14:47:00
2024-10-29 17:30:55.954446
```

E tests the hypothesis that the free-text evidence-materialization boundary can safely
handle such values when three independent conditions hold:

1. the resolved reference-plan cell explicitly requested `iso_date_normalization`;
2. the enumerated verbatim evidence is deterministically typed DATE or DATETIME and matches
   a strict unambiguous year-first grammar;
3. the resolved target column is not clearly numeric/blob-incompatible.

If any condition is not met, E fails closed. It does not repair evidence, columns, groups,
or references.

## Intervention boundary

E runs only here:

```text
free-text request
    -> enumerated verbatim evidence
    -> resolved table/column IDs
    -> Stage-E typed normalization
    -> materialized Write Plan
    -> frozen verifier/compiler
```

It does **not** run on structured/semi-structured source parsing. D3 NULL semantics remain
frozen.

## V5 ablation

```text
V4 = A + B + C + D
V5 = A + B + C + D + E
```

V5 config:

```text
configs/stage2/v5_free_text_typed_normalization.json
```

The independent E block is:

```json
{
  "free_text_typed_normalization": {
    "enabled": true,
    "date_normalization": true,
    "datetime_normalization": true,
    "preserve_raw_evidence": true,
    "fail_closed_on_ambiguous_format": true
  }
}
```

E does not change the MP-FS+ prompt. V4 and V5 therefore use identical prompts for the same
sample; the causal difference is deterministic post-generation materialization only.

## Allowed deterministic temporal grammar

E accepts only year-first forms:

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

Calendar/time validity is checked with `datetime` construction. Fractional precision above
six digits is outside this checkpoint and fails closed.

Canonicalization is deterministic:

```text
YYYY/MM/DD            -> YYYY-MM-DD
YYYY-MM-DDTHH:MM:SS   -> YYYY-MM-DD HH:MM:SS
```

Fractional digits are preserved exactly.

## Sentence-boundary punctuation

E may remove **at most one** terminal `.` or `,`, and only if the remaining value passes the
strict temporal grammar. There is no global `rstrip(".,")` rule.

Therefore:

```text
DATE rule + 2026-08-19.     -> 2026-08-19
DATE rule + 2026-08-19..    -> reject
identity  + SC9081.         -> unchanged
```

Quotes are not stripped by E. A quoted/text evidence candidate is not reinterpreted as a
temporal candidate.

## Fail-closed ambiguity

The following remain unresolved/rejected under E:

```text
01/02/2026
01/02/03
2026-13-40
Attempt
For
'2026-08-19.'
```

E intentionally does not guess locale/date order and does not repair a wrong evidence span.

## Provenance contract

For a handled E cell, `normalization_audit` retains:

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
evidence_id
evidence_start
evidence_end
applied
lossless
```

The original request and evidence offsets are never rewritten.

## D3 boundary

Structured-source NULL handling remains exactly the frozen D behavior:

```text
unquoted NULL  -> null
unquoted None  -> text "None"
unquoted nil   -> text "nil"
quoted "NULL"  -> text "NULL"
quoted "None"  -> text "None"
JSON null      -> typed null
Python None    -> typed null
```

E does not add free-text NULL repair or modify D3.

## Explicit non-goals

Stage E does not implement:

- evidence-span repair;
- column/group mapping repair;
- constrained reference repair (F);
- diagnostic-driven LLM repair (G);
- verifier relaxation;
- prompt changes;
- GPU/model runs;
- 7B development runs;
- full causal replay;
- confirmatory claims.
