# Stage4R.2 Actual D+F+G1 Deterministic Replay

## Scope

Stage4R.2 addresses the remaining Stage4R.1 reviewer blocker: the prior
`D_F_G1_DIAGNOSTIC` artifact was a frozen-output projection, not an actual
deterministic replay.

This patch runs an actual CPU-only replay:

```text
frozen Stage-4 mp_fs_plus_shared raw generation
        ↓
configs/stage4/d_f_g1_diagnostic.json
        ↓
materialization
        ↓
verification
        ↓
compilation
        ↓
transactional preflight
        ↓
execution on fresh DB copy
        ↓
state comparison
```

No model inference, GPU execution, new prompt generation for inference, new
sample selection, or dataset/gold-label edits were performed.

## Commits

```text
Stage4R.1 base commit: 67d313415a9ba0be4528b293df70e563ce3265de
Stage4R.2 result commit: see package provenance
Accepted Stage4 execution commit: d984e9815c13da5490b73b097181c563b5a1c534
```

## Main result

Actual deterministic replay matches the ideal reviewer case:

```text
D_G1_correct = 99/300
ACTUAL_D_F_G1_correct = 104/300
FULL_correct = 104/300

D_G1 → ACTUAL_D_F_G1: 5 rescues, 0 regressions
ACTUAL_D_F_G1 → FULL: 0 rescues, 0 regressions
```

Interpretation:

```text
Within the frozen Stage-4 outputs, adding F to the D+G1 configuration was
sufficient to reproduce all five FULL-vs-D_G1 rescues without observed
state-level regressions.
```

This is still diagnostic/development evidence for a revised method, not a new
confirmatory test result.

## F repair evidence in the actual replay

```text
F activation samples = 9
F repair count = 38
F exact-name repair count = 38
repair rule = unique_exact_identifier_name
repair attempted/applied/succeeded = true for all 38
```

## Main artifacts

```text
artifacts/stage4r2_actual_replay_summary.json
artifacts/d_g1_actual_full_paired_summary.csv
artifacts/d_g1_actual_full_sample_level.csv
artifacts/d_f_g1_actual_evaluation.jsonl
artifacts/d_f_g1_actual_materialized_write_plans.jsonl
artifacts/d_f_g1_actual_verification.jsonl
artifacts/d_f_g1_actual_compiled_programs.jsonl
artifacts/d_f_g1_actual_preflight.jsonl
artifacts/d_f_g1_actual_execution.jsonl
artifacts/d_f_g1_actual_raw_generations.jsonl
artifacts/d_f_g1_actual_f_repairs.csv
artifacts/d_f_g1_actual_metrics.json
artifacts/d_f_g1_actual_manifest.json
actual_run/
```

## Related fixes included in this patch

- Internal EOL stability for `stage4r_fresh_failure_attribution/**`.
- Internal checksum regeneration for Stage4R.1 artifacts.
- Taxonomy family renamed from `output_length` to
  `max_token_hit_associated`.
- Stage4R.2 artifacts are covered by `CHECKSUMS_SHA256.txt`.
