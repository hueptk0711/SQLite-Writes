# Stage 2 Checkpoint A–C Report

## Status

Implementation checkpoint only. No new model inference and no confirmatory accuracy claim is made.

## Stage-1 hypotheses addressed

| Intervention | Stage-1 evidence | Intended boundary |
|---|---|---|
| A — control semantics | 22 materialization-bound operation/control cases | source provenance/materialization |
| B — conflict preservation | 20/20 conflict audits resolvable from input | upstream semantic planning |
| C — update columns | 4 executed-but-wrong omission cases plus latent expansion cases | plan/materialization/compilation semantic consistency |

A is not claimed to recover 22/22. Stage 1 showed only a subset were clean single blockers. The purpose of this checkpoint is to make the interventions explicit and ablatable before causal replay.

## Validation performed

- Dedicated Stage-2 A–C regression suite: 13 tests passed.
- Existing source/planner + MP-FS+ compatibility tests plus A–C tests: 53 passed.
- Full repository fast suite (`pytest -q -m "not integration"`): passed at 100% on the full code state matching branch HEAD `34ef629b4b0e49ba88dc37c783d500d94a925f7e`.

## Baseline preservation checks

- all flags default to `false`;
- `original.json` explicitly disables A/B/C;
- intervention functions return without semantic rewrites when their flags are disabled;
- baseline unresolved fields remain fail-closed;
- compiled statement serialization omits Stage-2 semantic trace when no trace is present.

## Safety checks

- a control-like payload field is not consumed solely because of its name;
- a `consumed_control` record without `consumed_by` remains invalid;
- unresolvable explicit conflict target remains an error;
- unresolvable explicit update column remains an error;
- explicit update exclusions remove forbidden relationship columns;
- no fuzzy reference matching or LLM repair was added.

## Next checkpoint

Do not run 7B yet. Submit A–C for review first. If accepted, proceed to D–G (parser/value fixes, free-text/date normalization, constrained reference repair, targeted repair), then causal replay and only later one controlled 7B development run.
