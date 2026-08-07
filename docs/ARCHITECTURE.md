# Architecture decisions

## 1. Dual-mode planning

The input router distinguishes two representations:

```text
semi-structured -> deterministic SourcePayload -> Mapping Plan -> materialize
free text       -> evidence-grounded Write Plan extraction
both            -> verifier -> compiler -> executor
```

Free text is never forced through an empty mapping payload. Semi-structured
input is never regenerated cell by cell by the model.

## 2. Multi-collection source contract

`SourcePayload` separates `instruction_text` from one or more collections.
Each collection carries:

- a stable `collection_id`;
- its source path and detected format;
- rows and their original field names;
- row and field counts.

Each Mapping Plan target group names `source_collection`. The prompt exposes
only instruction text and collection metadata, never the source cell values.
Materialization later joins the predicted mapping to the untouched source
rows.

## 3. Representation responsibilities

The Mapping Plan contains semantic decisions: source collection, table,
field mappings, constants, conflict policy, and dependencies. It contains no
copied source cells.

The Write Plan contains actual rows plus provenance and is the sole input to
the verifier. This isolates:

- semantic errors in table, field, conflict, or dependency choices;
- payload errors such as missing rows, changed cells, and invented values;
- compiler errors in identifiers, SQL construction, ordering, or execution.

## 4. Evidence and constants

Every materialized cell comes from exactly one source location or from a
grounded constant.

An instruction-derived constant must store a verbatim `exact_span`; the span
must occur in `instruction_text`, and the constant value must be supported by
that span. A schema-derived constant is accepted only when the profiled column
contains that exact default. A free-text extracted value follows the same
verbatim-evidence rule.

## 5. Explicit write semantics

`action=insert` describes the write. `conflict.action` independently describes
uniqueness handling:

- `error`: plain insert;
- `do_nothing`: ignore the conflicting row;
- `do_update`: update only the declared `update_columns`.

A key-only `do_update` has no legal update mask and is normalized to
`do_nothing` with a structured warning.

## 6. Validation and compiler trust boundary

Raw LLM output passes through JSON extraction and JSON-schema validation
before materialization. Diagnostics include a parse status, JSON path, and
message.

Verification is mandatory for MP-ZS, MP-FS, and MP-FS-R-semi.
`compile_verified_plan()` accepts a successful verification result and does
not re-run the verifier. The compiler:

- accepts only verified tables and columns;
- performs limited case/punctuation normalization, not semantic guessing;
- uses placeholders for every value;
- defaults to no type coercion;
- builds the entire program before execution;
- rejects the whole program in strict mode if any group fails.

## 7. Repair boundary

Repair is one restricted JSON-Patch attempt over mapping/policy paths for
semi-structured inputs only. The method is therefore named `MP-FS-R-semi`;
eligibility, attempts, and accepted repairs are reported separately. It cannot
patch source collections or materialized values. A repair is accepted only
when all of the following hold:

1. Mapping Plan schema is valid.
2. Materialization succeeds.
3. Source row and cell coverage do not decrease.
4. No value outside source/evidence/default provenance is introduced.
5. Verification has no errors.
6. The entire program compiles.
7. A transactional dry-run succeeds.
8. The payload hash is unchanged.
9. Write scope is not expanded without evidence.

## 8. State evaluation

Gold oracle evaluation compares:

```text
Gold Write Plan -> verifier -> compiler -> isolated SQLite state
Gold SQL -------------------------------> isolated SQLite state
```

For ordinary inserts/updates, exact write deltas are compared. When triggers or
cascading key updates can change related tables, the evaluator expands the
affected-table closure and compares full table states. Target-state and strict
affected-state results are reported separately.

## 9. Experimental lifecycle

The required order is:

```text
data audit -> full oracle -> snapshot review -> freeze -> source analysis
-> dev pilot -> error analysis -> config lock -> one locked test
-> ablations/robustness -> paired statistics
```

The runner creates and validates `run_lock.json` before reading any generation
checkpoint. The lock covers dataset, split, selected profiles/databases,
method and resolved inference config, prompt set/templates, source trees,
dependency lock, environment manifest, and model/tokenizer identities. Each
raw row also stores its prompt hash.

Development uses an independent frozen 120-sample package. The fixed
real-model sequence is 5 samples, then 20, then all 120. Only a signed
go-decision for the exact locked configuration enables `stage=locked-test`.
Successful locked runs create a one-use consumed marker; truncation creates an
invalid marker instead.
