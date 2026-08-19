# MP-FS+ vNext Changelog — A–C Patch 2

## Fixed reviewer blockers

- Stage-2 V0/V1/V2/V3 now keep `method_id="MP-FS+"` and use `method_variant` for ablation identity.
- Every Stage-2 config inherits directly from `configs/final/mp_fs_plus.json`, preventing one-level inheritance loss.
- V1 is isolated to typed operation controls; it no longer auto-consumes conflict/update/metadata controls.
- Materialization uses exact `consumed_control_refs` emitted by semantic components instead of field-name/context suppression.
- Free-text conflict preservation is group/table scoped for multi-group requests.
- Free-text update parsing resolves explicit names after parsing and preserves unknown names as fail-closed errors.
- SQL-like `DO UPDATE SET` parsing uses assignment LHS only.
- Contradictory requested/excluded update controls emit `CONTRADICTORY_UPDATE_CONTROL`.
- Semi-structured intervention warnings propagate through the pipeline warning stream.

## Preserved from checkpoint 1

- exact conflict target resolution;
- exact update-column closed-set restoration;
- compiler semantic trace;
- fail-closed verifier/preflight behavior;
- no fuzzy repair and no LLM repair in A–C.

## Still out of scope

- D structured parser / NULL handling;
- E free-text/date normalization;
- F constrained reference repair;
- G diagnostic-driven targeted repair;
- causal replay experiment;
- 7B end-to-end development run.
