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

## E Patch 2 — typed trust-boundary and causal-provenance hardening

- replaces permissive target compatibility with a fail-closed semantic allowlist while preserving TEXT-backed temporal storage;
- rejects explicit identifier, boolean, JSON, numeric/blob-like and unknown incompatible target semantics/types;
- requires non-empty enumerated `candidate_type` and accepts only exact `date` / `datetime` typing;
- enforces candidate subtype-to-grammar consistency instead of reinterpreting DATE as DATETIME or vice versa;
- changes `applied` semantics to mean Stage-E intervention handling/activation and adds independent `value_changed`, `accepted`, and `outcome` audit fields;
- records target semantic/declared type in normalization audit for later causal replay;
- replaces Unicode `\\d` temporal grammar with ASCII `[0-9]` grammar;
- makes raw evidence preservation mandatory when E is enabled;
- updates the 14 Stage-1 diagnostic cells with typed evidence metadata and adds reviewer-requested adversarial regression coverage;
- does not modify A–D, evidence/reference repair, F/G, prompts, verifier policy, or model execution.

## E Patch 3 — target storage/subtype compatibility hardening

- removes `DATE` / `TIME` substring matching from target declared-type compatibility;
- recognizes exact `DATE`, `DATETIME`, and `TIMESTAMP` temporal declarations and treats `TIME` as unsupported at this checkpoint;
- preserves SQLite text-affinity temporal storage through `CHAR` / `CLOB` / `TEXT` classification;
- fails closed on unknown custom declared types such as `CANDIDATE`, `RUNTIME`, `SOMEDATECODE`, `DATELIKE`, and `TIMELIKE`;
- enforces target semantic subtype consistency: `date` / `date_key` require date evidence, while `datetime` / `timestamp` require datetime evidence;
- enforces declared temporal subtype consistency: `DATE` requires date evidence; `DATETIME` / `TIMESTAMP` require datetime evidence;
- adds `TEMPORAL_TARGET_SUBTYPE_MISMATCH` for deterministic target/evidence subtype disagreement;
- keeps Patch-2 candidate typing, ASCII grammar, causal provenance, raw-evidence invariant, E3 fail-closed ambiguity, E5 ablation, and all frozen A–D behavior unchanged.

## F Patch 1 — constrained closed-set reference repair

- starts from frozen `Stage2-E-FINAL` (`7b9c4ef616fc2414fccba2cbe6b22016a3ed39b4`);
- adds independently ablatable V6 `constrained_reference_repair`;
- repairs only non-empty references already found invalid at a deterministic reference boundary;
- selects replacements only from a slot-local closed set using a unique exact identifier-name anchor or a singleton closed set;
- forbids fuzzy/edit-distance repair, valid-reference rewriting, and missing-slot auto-fill;
- limits repair to exactly one attempt per slot and fails closed on empty/ambiguous candidate sets;
- preserves values, raw evidence, operation, conflict action, update semantics, prompt content, and frozen A-E behavior;
- records mandatory repair provenance including original/replacement reference, full candidate set, rule, and validation before/after;
- classifies 35 Stage-1 reference-resolution first failures into 14 reference-only repairable, 10 partial-but-not-sample-safe, and 11 non-repairable diagnostic cases without claiming end-to-end rescue;
- adds dedicated/adversarial tests, V5/V6 identity checks, and CPU smoke;
- leaves G, causal replay, and all GPU/model runs out of scope.

## F Patch 2 — replacement-slot collision safety and F-scope diagnostic correction

- fails closed before key mutation when a selected replacement already exists in the same structural container;
- covers source-field keys, constants keys, and free-text row target-column keys;
- atomically rolls back earlier repairs in the same batch when a later invalid reference would collapse onto an existing replacement key;
- records `replacement_slot_collision` with `repair_applied=false` instead of overwriting or merging semantic assignments;
- adds four adversarial collision regression tests;
- regenerates Stage-1 F classification under the actual F eligibility whitelist: 13 reference-only repairable, 10 partial-but-not-sample-safe, 12 non-repairable;
- reclassifies `final_vaccine_018` as non-repairable and excludes four protected update-column proposals from `final_vaccine_033`'s F-eligible count;
- records F-eligible diagnostic rule counts separately: 70 exact-name proposals and 0 singleton proposals;
- preserves all accepted Patch-1 architecture, V5/V6 isolation, protected semantic boundaries, one-retry behavior, and prompt identity.

## F Patch 3 — source-field semantic-alias collision safety

- adds frozen-resolver-equivalent source-field identity resolution for the dual valid
  representations `field name` and enumerated `field ID`;
- fails closed when an `UNKNOWN_SOURCE_FIELD_ID` replacement would create a second raw
  key resolving to an already represented semantic source-field slot;
- records `replacement_semantic_slot_collision` with `repair_applied=false` instead of
  allowing resolver-level overwrite;
- reuses Patch-2 atomic rollback when a later semantic-alias collision follows an
  earlier repair in the same batch;
- adds direct and batch alias-collision adversarial regression tests and CPU-smoke
  coverage;
- keeps F eligibility, 13/10/12 Stage-1 classification, 70/0 rule accounting, singleton
  policy, one-retry behavior, protected semantics, V5/V6 isolation, and all frozen A-E
  behavior unchanged.
