# MP-FS+ vNext Changelog — A–C Patch 4

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

## Still out of scope

- D structured parser / NULL handling;
- E free-text/date normalization;
- F constrained reference repair;
- G targeted diagnostic-driven repair;
- causal replay experiment;
- 3B/7B/14B end-to-end runs.
