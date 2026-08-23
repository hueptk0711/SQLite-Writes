# Stage4R.2.1 Actual D+F+G1 Replay Accounting Patch

## Scope

Stage4R.2 addressed the remaining Stage4R.1 reviewer blocker: the prior
`D_F_G1_DIAGNOSTIC` artifact was a frozen-output projection, not an actual
deterministic replay.

Stage4R.2.1 is a CPU-only reporting/provenance patch. It does not alter the
method, prompt, dataset, gold labels, metric, protocol, raw generations, or
state evaluation. It fixes F accounting by reading repair traces from
`verification.jsonl` instead of only from `materialized_write_plans.jsonl`.

The actual replay path remains:

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
Stage4R.2 result commit: 47055424e6f2acae18293c867e65c5015ed98299
Stage4R.2.1 result commit: see package provenance
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

## Clean replay provenance

Stage4R.2.1 reran the D+F+G1 deterministic replay once from a clean detached
worktree of the patch commit. The clean replay reproduced the same state-level
result:

```text
D_G1_correct = 99/300
ACTUAL_D_F_G1_correct = 104/300
FULL_correct = 104/300

D_G1 → ACTUAL_D_F_G1: 5 rescues, 0 regressions
ACTUAL_D_F_G1 → FULL: 0 rescues, 0 regressions
```

Key clean run hashes:

```text
source_code_tree_sha256 = 0e49c394a91dc81faef89e2db97beb891d9c73ff396520d60928ea786bc1e669
method_config_sha256 = 3036949521b25ea6d5767500a13ce91206e73cf94dc3576497d96562e0cc48af
raw_generation_source_sha256 = b293f52dfb95acea23aa4a8d80a03a0cd1adea592de46908e346bd8b74fe5330
```

The clean replay paired summary, sample-level comparison, F-attempt CSVs,
materialized write plans, and verification traces are byte-identical to the
reported Stage4R.2.1 artifacts. Evaluation/metrics JSON files are not expected
to be byte-identical because they include timing/path metadata.

## F repair evidence in the actual replay

```text
F_attempt_sample_count = 27
F_attempt_count = 102

F_exact_name_attempt_sample_count = 21
F_exact_name_attempt_count = 73

F_applied_sample_count = 17
F_applied_exact_name_repair_count = 62

F_materialized_sample_count = 9
F_materialized_exact_name_repair_count = 38

F_state_rescue_count = 5
F_state_regression_count = 0
```

The old 9/38 number is retained only as the materialized-plan subset:
successful exact-name F repairs on samples that survived later verification
far enough to produce `materialized_write_plans.jsonl`.

Repair attempt rule counts from `verification.jsonl`:

```text
unique_exact_identifier_name = 73
ambiguous_closed_set = 25
replacement_target_assignment_collision = 4
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
artifacts/f_attempts.csv
artifacts/f_applied_repairs.csv
artifacts/f_materialized_repairs.csv
artifacts/f_sample_outcomes.csv
artifacts/d_f_g1_actual_metrics.json
artifacts/d_f_g1_actual_manifest.json
actual_run/
clean_replay/
```

## Related fixes included in this patch

- Internal EOL stability for `stage4r_fresh_failure_attribution/**`.
- Internal checksum regeneration for Stage4R.1 artifacts.
- Taxonomy family renamed from `output_length` to
  `max_token_hit_associated`.
- Stage4R.2 artifacts are covered by `CHECKSUMS_SHA256.txt`.
