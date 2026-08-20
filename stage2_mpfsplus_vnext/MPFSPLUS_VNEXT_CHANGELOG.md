# MP-FS+ vNext Changelog — Stage 2 E

## Patch 4 — unified free-text instruction/payload boundary

- introduced a shared payload-literal masking boundary for free-text operation, conflict-target, and update-column extraction;
- masks quoted payload RHS literals such as `description='conflict_target=other'` and `note='update_columns=other'`;
- preserves quoted SQL identifiers in `ON CONFLICT("user_id")` and assignment LHS/RHS such as `"name" = excluded."name"`;
- preserves explicit quoted control values such as `conflict_target: "id"`;
- prevents quoted payload text such as `note='ON CONFLICT(other) DO NOTHING'` from creating deterministic conflict semantics;
- added six adversarial tests for target/update payload isolation and quoted-identifier/control preservation.

## Patch 3 retained

- exact database-identifier resolution separate from loose control aliases;
- ambiguous exact identifier fail-closed behavior;
- conflict action / conflict target separation;
- strict structured operation aliases;
- high-confidence free-text operation semantics;
- V0 materialization artifact compatibility;
- `method_variant` / `method_version` experiment provenance.

## Patch 2 retained

- valid `method_id="MP-FS+"` dispatch with separate ablation variants;
- direct inheritance from frozen MP-FS+ config;
- V1 isolation from B/C controls;
- group/table-scoped free-text B;
- unknown update-column fail-closed handling;
- SET-LHS update parsing;
- contradictory update-control error;
- semi-structured warning propagation.

## A–C frozen checkpoint

A–C are frozen at `Stage2-A-C-FINAL` (`f1fa49ddb8e6b920fb5a4237e088b6603579ae23`). D uses their existing interfaces and does not tune their logic.

## D Patch 4 — internal namespace representation hardening

- replaces the user-representable string sentinel `"__UNPREFIXED__"` with tagged row namespaces;
- represents named prefixes as `("named", prefix)` and unprefixed syntax as `("unprefixed", "")`;
- prevents a real source prefix named `__UNPREFIXED__` from colliding with the internal unprefixed namespace;
- adds one adversarial regression test and CPU-smoke assertion for the sentinel-collision case;
- does not modify D2 control separation, D3 NULL handling, D4 provenance, A–C, or V4 integration.

## D Patch 3 — mixed row-namespace hardening

- treats the empty/unprefixed dotted-row namespace as a real row namespace;
- explicit D row segmentation now commits only when all row fields belong to one unambiguous namespace;
- named-prefix + unprefixed dotted rows defer instead of merging by row label;
- named-prefix dotted rows mixed with unprefixed row-heading/row-marker syntax also defer;
- all-unprefixed dotted rows and single named-prefix dotted rows remain supported;
- adds three targeted regression tests and CPU-smoke assertions for the final D1 namespace blocker.

## D Patch 2 — parser trust-boundary hardening

- multi-prefix dotted repeated rows now defer to the historical path instead of grouping across prefixes by row label;
- strong D control context now requires a high-confidence field/value semantic signal, so invalid `operation=login` cannot reclassify `table=audit`;
- numbered, bulleted, and equals key-value parser paths now retain payload-cell value provenance;
- added adversarial regression coverage for multi-prefix safety, single-prefix retention, invalid operation aliases, and provenance completeness across textual parser forms.

## D — structured parser / NULL handling

- added independently ablatable `structured_source_parser` config and V4 variant;
- preserves explicit repeated row boundaries for `rowN:`, `row = N`, and `row_N.field=value`;
- separates strong control-only blocks from payload rows;
- preserves ambiguous textual `None`/`nil` while keeping explicit unquoted `NULL` as null;
- records value coercion provenance and stable `SRC_ROW_*` row IDs;
- propagates the same parser config through prompt-time and pipeline-time parsing;
- added exact regression fixtures for the seven Stage-1 manually audited parser failures.

## Still out of scope

- E free-text/date normalization;
- F constrained reference repair;
- G targeted diagnostic-driven repair;
- causal replay experiment;
- 3B/7B/14B end-to-end runs.


## E Patch 1 — free-text typed temporal normalization

- starts from frozen `Stage2-D-FINAL` (`0eba16b297100966d3635172456086db872166d6`);
- adds independently ablatable V5 `free_text_typed_normalization`;
- runs only after free-text evidence and target-column references are resolved;
- accepts strict unambiguous year-first DATE/DATETIME values under an explicit `iso_date_normalization` request;
- extends the date-only rule to Stage-1-style datetimes, including up to six fractional-second digits;
- canonicalizes year-first date separators and datetime `T` deterministically;
- removes at most one sentence-boundary `.`/`,` only when the remaining temporal value is strictly valid;
- rejects ambiguous/invalid temporal forms and wrong evidence tokens instead of guessing or repairing;
- records raw evidence, offsets, semantic type, normalized value, rule, confidence, and requested normalization;
- preserves V4 prompt identity and all frozen A–D behavior when E is disabled;
- adds 14 Stage-1 diagnostic temporal-cell fixtures (12 expected accepts, 2 expected rejects), dedicated adversarial tests, and CPU smoke.

## E still out of scope

- evidence-span repair;
- column/group mapping repair;
- F constrained reference repair;
- G diagnostic-driven targeted repair;
- full causal replay;
- GPU / 3B / 5B / 7B / 14B runs.
