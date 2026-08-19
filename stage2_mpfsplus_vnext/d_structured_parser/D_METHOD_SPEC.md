# Stage 2 D — Structured Parser + NULL Handling

## Status

Checkpoint implementation for reviewer inspection. A–C are frozen at tag `Stage2-A-C-FINAL` and are not modified by this intervention.

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
