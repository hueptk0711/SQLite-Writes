# Stage4R.2.1 Validation Report

## Scope

- Model calls: none
- GPU calls: none
- Raw Stage-4 generations modified: no
- Fresh sample IDs modified: no
- Dataset/gold labels modified: no
- Protocol changed: no

## Actual replay command

```text
python scripts\analysis\run_stage4r2_actual_dfg1_replay.py --protocol-root stage4_fresh_7b_protocol --result-root <Stage4_FRESH_7B_ENVFINAL_RESULTS_FOR_REVIEW_20260823>\stage4_fresh_7b_results_envfinal --config configs\stage4\d_f_g1_diagnostic.json --actual-run-dir stage4r2_actual_d_f_g1_replay\actual_run --output-dir stage4r2_actual_d_f_g1_replay\artifacts
```

Status:

```text
PASS
```

## Actual replay summary

```text
fresh_sample_count = 300
D_G1_correct = 99
ACTUAL_D_F_G1_correct = 104
FULL_correct = 104
D_G1_to_ACTUAL_D_F_G1_rescue = 5
D_G1_to_ACTUAL_D_F_G1_regression = 0
ACTUAL_D_F_G1_to_FULL_rescue = 0
ACTUAL_D_F_G1_to_FULL_regression = 0
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

The 9/38 count is now explicitly labeled as the materialized-plan subset, not
the full F activation/attempt count.

## Clean replay provenance

A clean detached worktree of the Stage4R.2.1 patch commit reran the same
CPU-only replay from the frozen Stage-4 shared raw generations.

```text
clean_replay_exit = 0
clean_replay_status = actual_replay_completed
clean_D_G1_correct = 99
clean_ACTUAL_D_F_G1_correct = 104
clean_FULL_correct = 104
clean_D_G1_to_ACTUAL_D_F_G1_rescue = 5
clean_D_G1_to_ACTUAL_D_F_G1_regression = 0
clean_ACTUAL_D_F_G1_to_FULL_rescue = 0
clean_ACTUAL_D_F_G1_to_FULL_regression = 0
clean_source_code_tree_sha256 = 0e49c394a91dc81faef89e2db97beb891d9c73ff396520d60928ea786bc1e669
clean_method_config_sha256 = 3036949521b25ea6d5767500a13ce91206e73cf94dc3576497d96562e0cc48af
clean_raw_generation_source_sha256 = b293f52dfb95acea23aa4a8d80a03a0cd1adea592de46908e346bd8b74fe5330
```

The clean replay paired summary, sample-level comparison, F accounting CSVs,
materialized write plans, and verification traces were byte-identical to the
reported Stage4R.2.1 artifacts. See:

```text
stage4r2_actual_d_f_g1_replay/clean_replay/clean_replay_hash_comparison.csv
stage4r2_actual_d_f_g1_replay/validation/clean_replay_provenance.txt
```

## Test validation

Committed validation logs are under:

```text
stage4r2_actual_d_f_g1_replay/validation/
```

They include environment/git status, compile output, Stage4 protocol validator
output, Stage4R.2 analysis-from-actual-run output, actual replay summary,
dedicated pytest output, full-suite pytest output, and internal checksum
verification.

Expected exit statuses:

```text
python_compile_exit = 0
stage4r2_analysis_from_actual_run_exit = 0
dedicated_stage4r2_tests_exit = 0
stage4_protocol_validator_exit = 0
full_fast_suite_exit = 0
internal_checksum_verifier_exit = 0
clean_replay_exit = 0
```

Local note: Windows Python 3.14 creates pytest temp directories with an
unusable ACL when using mode `0700` on this host. Dedicated tests were run with
a process-local `os.mkdir(..., 0o777)` pytest wrapper; the wrapper was not
committed and is not used by project code or replay execution.
