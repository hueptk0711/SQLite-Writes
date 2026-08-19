# MP-FS+ vNext Changelog — Checkpoint A–C

## Added

- `nldbwrite_v3.vnext.interventions` with typed field roles and Stage-2 feature configuration.
- A: typed control/instruction provenance (`consumed_control`, `role`, `consumed_by`).
- B: deterministic preservation of explicit conflict operation/target semantics.
- C: deterministic preservation of explicit update-column sets and explicit exclusions.
- Fail-closed diagnostics:
  - `EXPLICIT_CONFLICT_SEMANTICS_DROPPED`
  - `REQUIRED_UPDATE_COLUMNS_DROPPED`
  - `REQUIRED_UPDATE_COLUMNS_UNRESOLVED`
- Stage-2 semantic traces carried through reference materialization and, when present, compilation.
- Four cumulative configs (`original`, `v1_control`, `v2_conflict`, `v3_update`).
- Intervention registry.
- Dedicated A–C regression tests.

## Changed

- `MappingFirstPipeline` accepts `stage2_interventions`; default is all disabled.
- Reference and free-text paths apply only enabled deterministic interventions before final materialization/verification.
- `materialize_mapping_plan` can distinguish typed consumed controls from payload provenance when A is enabled.
- Verifier recognizes only well-formed typed `consumed_control` records; arbitrary unresolved fields remain errors.
- `CompiledStatement` can expose a Stage-2 semantic trace, but omits the field entirely from serialization when absent so baseline output shape is preserved.

## Not changed in this checkpoint

- Structured parser row segmentation / NULL semantics.
- Date/time normalization.
- Reference repair.
- Diagnostic-driven LLM repair.
- LLM prompt/model/decoding.
- Verifier thresholds or preflight rules.
- Stage-1 frozen results.
