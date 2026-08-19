# MP-FS+ vNext Changelog — A–C Patch 3

## Patch 3 — exactness and semantic false-positive hardening

- separated loose control-alias canonicalization from exact database-identifier resolution;
- preserved underscores/punctuation in DB identifier keys and retained candidate lists to prevent silent dictionary overwrite;
- added fail-closed `AMBIGUOUS_IDENTIFIER`;
- split `CONFLICT_ACTION_CONTROL` from `CONFLICT_TARGET_CONTROL`;
- restricted structured operation parsing to exact aliases;
- masked quoted payload literals before free-text conflict-semantic detection;
- restricted free-text restoration to high-confidence instructional syntax;
- preserved V0 materialization artifact shape when Stage-2 roles are disabled;
- propagated `method_variant` / `method_version` through experiment provenance;
- added adversarial tests for identifier collision, semantic fabrication, quoted payload false positives, V0 artifact identity, and run provenance.

## Patch 2 retained

- valid `method_id="MP-FS+"` dispatch with separate ablation variants;
- direct inheritance from frozen MP-FS+ config;
- V1 isolation from B/C controls;
- group/table-scoped free-text B;
- unknown update-column fail-closed handling;
- SET-LHS update parsing;
- contradictory update-control error;
- semi-structured warning propagation.

## Still out of scope

- D structured parser / NULL handling;
- E free-text/date normalization;
- F constrained reference repair;
- G targeted diagnostic-driven repair;
- causal replay experiment;
- 3B/7B/14B end-to-end runs.
