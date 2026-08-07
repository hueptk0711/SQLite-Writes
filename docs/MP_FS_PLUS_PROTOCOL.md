# MP-FS+ engineering and experiment protocol

## Scope

`paper_v3_release_20260726` is immutable development evidence. This working
tree implements the next method but does not overwrite, rerun, or relabel the
consumed 677-sample result.

MP-FS+ changes only the failure-prone interfaces:

1. Source collections and selectors use deterministic `c*`/`s*` IDs.
2. Tables, columns, and unique constraints use deterministic schema IDs.
3. Free-text values use enumerated `e*` evidence spans.
4. Type conversion uses an allow-list of lossless normalization rules.
5. Execution is admitted only after a rolled-back transactional preflight.
6. LLM repair is excluded from the final method.

## Development ablations

Run on DEV-120 and the diagnostic failures from the consumed 677 set:

| Variant | Components |
|---|---|
| MP-0 | Frozen MP-FS behavior |
| MP-1 | + source collection/selector IDs |
| MP-2 | + table/column/constraint IDs |
| MP-3 | + free-text evidence IDs |
| MP-4 | + lossless normalization |
| MP-FS+ | + transactional preflight and fail-closed admission |

Diagnostic data may guide implementation but may not be described as an
untouched evaluation of MP-FS+.

## Matched comparison

Use these frozen configs:

- `configs/final/d_fs_m.json`
- `configs/final/j_fs_m.json`
- `configs/final/s_fs_v2_m.json`
- `configs/final/mp_fs_m.json`
- `configs/final/mp_fs_plus.json`
- `configs/oracles/gold_mp.json`

Every method receives the same four semi-structured and two free-text semantic
examples. Only the output representation changes.

## Calibration gate

Create 50-80 new samples on databases excluded from the final holdout. Resolve
`configs/experiments/calibration_protocol.template.json` and freeze it before
the pilot.

MP-FS+ is eligible for final freeze only if:

- Gold-MP is 100%.
- Parse success is at least 98%.
- Plan/build success is at least 85%.
- Execution success is at least 75%.
- Accepted-output accuracy is at least 95%.
- Side-effect and input-truncation rates are 0%.
- Missing predictions are 0.
- Invalid selector and unknown-column errors are near zero.

## External holdout

Create 300 independently authored requests on 3-5 unseen SQLite databases.
Required balance and review gates are encoded in
`configs/experiments/final_protocol.template.json` and enforced by
`scripts/data/audit_external_holdout.py`.

Each conflict-sensitive sample must state the policy in the request or cite a
frozen system policy. Each sample requires two independent QA approvals.
Format variants and other augmentation belong in a separate stress set.

## Freeze and final run

Freeze code, data, split IDs, gold plans, prompts, demonstration IDs, model
revision, tokenizer, generation config, databases, profiles, dependency lock,
and environment. Generate:

- `run_lock.json`
- `SHA256SUMS.txt`
- `final_protocol.json`

Run the six-method matrix once. Do not change a prompt after inspecting final
outputs.

## Statistics

Pre-register:

1. MP-FS+ vs D-FS-M
2. MP-FS+ vs J-FS-M
3. MP-FS+ vs MP-FS-M
4. J-FS-M vs D-FS-M

Report absolute paired difference, exact McNemar wins/losses, source-group
clustered 95% bootstrap interval, database-macro bootstrap interval, and one
Holm correction across the four comparisons.

## Claims

Choose the method-paper narrative only if MP-FS+ improves correctness with
appropriate uncertainty. Otherwise report the controlled empirical result and
the reliability-coverage/error-attribution trade-off. Never report accepted
accuracy without coverage, abstention, overall accuracy, and side effects.
