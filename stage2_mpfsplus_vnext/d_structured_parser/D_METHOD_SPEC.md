# Stage 2 D — Structured Parser + NULL Handling

## Status

Patch-2 hardening candidate after reviewer inspection. A–C remain frozen at tag `Stage2-A-C-FINAL` and are not modified by this intervention. D3 NULL semantics, V4 config, row IDs, and prompt/pipeline integration remain unchanged.

## Failure hypothesis

Stage 1.1 manual audit isolated seven deterministic source-parser failures:

| Root cause | N |
|---|---:|
| `SOURCE_PARSER_ROW_SEGMENTATION_ERROR` | 3 |
| `SOURCE_PARSER_CONTROL_ROW_SEGMENTATION_ERROR` | 2 |
| `SOURCE_PARSER_NULL_LITERAL_COERCION_ERROR` | 2 |

The hypothesis is narrow: some failures attributed downstream to planning/materialization are already caused by loss or corruption of source structure before LLM-plan materialization. Therefore deterministic parsing should preserve explicit row boundaries, keep control-only blocks out of payload rows, and avoid converting ambiguous text tokens such as `None` into SQL NULL without an explicit source null convention.

The seven Stage-1 cases are diagnostic fixtures only. They are not used as confirmatory accuracy evidence.

## Ablation contract

D is enabled independently through:

```json
"structured_source_parser": {
  "enabled": true,
  "null_literal_policy": "explicit_only",
  "emit_value_provenance": true
}
```

`configs/stage2/v4_structured_parser.json` keeps all frozen A–C flags enabled and adds only this parser block.

When the block is omitted or `enabled=false`, `parse_source_payload()` follows the historical parser behavior. This preserves the A–C checkpoint and gives a clean V3 → V4 ablation.

## D1 — Explicit repeated-row segmentation

D recognizes only explicit repeated-row forms with at least two row identifiers:

```text
row1:
  field=value
row2:
  field=value
```

```text
row = 1
field=value
row = 2
field=value
```

```text
row_1.field=value
row_2.field=value
```

The parser emits one collection with separate rows and stable row IDs:

```text
SRC_ROW_0001
SRC_ROW_0002
...
```

A row marker is structural metadata and is not emitted as a payload field.

## D2 — Control-row separation

A block is considered deterministic control metadata only when:

1. every field is in the control alias set; and
2. at least one field is a strong control such as operation/conflict/update semantics.

Context-only names such as `table` or `policy` are not sufficient on their own. This avoids globally treating every field named `table` or `policy` as metadata.

A recognized control-only block is attached to the next payload collection through `metadata.control_metadata`; it is never materialized as an extra data row.

## D3 — Conservative textual NULL semantics

Legacy behavior treated these unquoted text values as SQL NULL:

```text
NULL
None
nil
```

D changes only the enabled V4 parser policy:

| source token | D result | rule |
|---|---|---|
| unquoted `NULL` | `None` / SQL NULL | `explicit_text_null_to_null` |
| unquoted `None` | string `"None"` | `ambiguous_text_null_preserved` |
| unquoted `nil` | string `"nil"` | `ambiguous_text_null_preserved` |
| quoted `"NULL"` | string `"NULL"` | `quoted_literal_preserved` |
| JSON `null` | typed `None` | typed source semantics |
| Python literal `None` | typed `None` | typed source semantics |

Thus D does not infer null from an ambiguous textual spelling merely because Python uses `None` as its null object.

## D4 — Value provenance

For textual structured cells, D can record:

```text
raw_value
parsed_value
normalized_value
coercion_rule
coercion_confidence
row_id
field
```

This makes NULL coercion auditable and keeps normalization separate from the source token.

## D Patch 2 — trust-boundary hardening

Reviewer inspection of the first D checkpoint identified three parser-boundary issues. Patch 2 changes only these points.

### D1.3 — Tagged internal row namespaces

Patch 4 removes the user-representable string sentinel previously used for unprefixed rows. Internal row namespaces are now tagged values:

```text
("named", prefix)
("unprefixed", "")
```

Thus a legitimate source prefix named `__UNPREFIXED__` is represented as:

```text
("named", "__UNPREFIXED__")
```

and remains distinct from the truly unprefixed namespace:

```text
("unprefixed", "")
```

This makes the row-namespace safety invariant independent of any source-representable identifier string.

### D1.2 — One unambiguous row namespace per explicit D result

Patch 3 closes the remaining mixed-namespace case. The empty prefix is treated as a real `__UNPREFIXED__` namespace.

Therefore:

```text
row_1.id + row_1.name
```

remains supported, and:

```text
parent.row_1.id + parent.row_1.name
```

remains supported, but:

```text
parent.row_1.id + row_1.name
```

defers.

Likewise, a named dotted prefix mixed with unprefixed `rowN:` or `row=N` syntax defers rather than being merged by row label.

The parser does not attempt to infer whether the two namespaces refer to the same logical collection. It preserves the checkpoint policy: ambiguous mixed namespaces are handled by the historical parser path.

### D1.1 — Multi-prefix dotted rows fail/defer conservatively

The explicit D repeated-row detector supports a single dotted collection prefix, for example:

```text
robot_record.row_1.id=R1
robot_record.row_2.id=R2
```

If more than one distinct prefix appears in the same explicit dotted-row request, for example `parent.row_N.*` and `child.row_N.*`, the D detector does not group rows by the row label alone. It returns no D-specific parse and defers to the historical parser path. The checkpoint deliberately does not add a new multi-collection grammar.

This invariant prevents:

```text
parent.row_1.id + child.row_1.name -> one fabricated row
```

### D2.1 — Strong control context requires semantic validity

D no longer treats the presence of a strong-looking field name as sufficient evidence for broad metadata reclassification. A neighboring context-only name such as `table`/`policy` is reclassified only when at least one field/value pair carries a high-confidence control signal.

For `operation`, Patch 2 mirrors the frozen A exact-alias contract. Thus:

```text
operation=plain_insert -> strong control signal
operation=login        -> not a strong control signal
```

The second case therefore cannot cause `table=audit` to be removed from payload merely because the field is named `operation`.

### D4.1 — Provenance completeness across textual parser forms

When `emit_value_provenance=true`, every payload cell emitted by the supported textual parsers now has exactly one value-provenance trace for:

- CSV/TSV;
- markdown tables;
- colon key-value sections;
- equals key-value sections;
- numbered key-value records;
- bulleted key-value records;
- explicit D repeated rows.

The trace is finalized with `row_id` after collection row IDs are assigned.

## Integration boundary

The same `structured_source_parser` config is passed to:

- prompt-time source parsing in `_prompt_for_sample()`; and
- execution-time parsing in `MappingFirstPipeline`.

This prevents prompt/pipeline parser drift.

## Explicitly out of scope

D does not modify:

- A/B/C semantics or feature flags;
- date/time normalization;
- free-text evidence normalization;
- constrained reference repair;
- diagnostic-driven LLM repair;
- verifier thresholds;
- compiler behavior;
- prompts or decoding;
- any model/GPU experiment.

Those remain later Stage-2 checkpoints.
