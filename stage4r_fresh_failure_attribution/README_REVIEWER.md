# Stage4R Fresh Failure Attribution Reviewer README

## Scope

Stage4R is a CPU-only post-hoc audit of the accepted Stage 4 fresh 7B run.

It does not rerun the model, regenerate prompts, change configs, modify raw
generations, or change evaluations. The purpose is to archive the reviewer-
requested failure attribution after Stage 4 execution was accepted/frozen.

Accepted Stage 4 execution commit:

```text
d984e9815c13da5490b73b097181c563b5a1c534
```

## Key findings

- Primary D_G1 generalization remains null:
  Original MP-FS+ and D_G1 are identical on 300/300 fresh samples.
- The Stage 4 analysis reporting bug is fixed:
  `G1_trace_sample_count` is now `0`, matching `G1_attempts = 0`.
- FULL vs D_G1 has a clean secondary paired improvement:
  `5` rescues, `0` regressions.
- Constrained reference repair F accounts for the FULL rescues:
  `9` activated samples, `38` exact-name repairs, `5` rescues,
  `4` fail-closed cases, `0` regressions.
- D_G1 has `201/300` incorrect samples on the fresh set.
- Output-length failure is preserved as a separate artifact:
  Direct `15`, J-FS `33`, MP-FS+/D_G1/FULL/NO-C `9`.

## Important interpretation constraint

Do not promote FULL or F to the new primary method using these same 300 fresh
samples as confirmatory evidence. FULL/F should be reported here only as a
pre-specified secondary ablation/audit result. A future D+F+G1 method would need
a new holdout/public benchmark.

## Main artifacts

```text
artifacts/stage4r_summary.json
artifacts/f_activation_sample_level.csv
artifacts/f_exact_name_repairs.csv
artifacts/full_vs_dg1_paired_summary.csv
artifacts/full_vs_dg1_paired_sample_level.csv
artifacts/d_g1_failure_sample_level.csv
artifacts/d_g1_failure_taxonomy.csv
artifacts/hit_max_new_tokens_summary.csv
artifacts/hit_max_new_tokens_samples.csv
corrected_frozen_analysis/intervention_summary.csv
corrected_frozen_analysis/variant_metrics.csv
corrected_frozen_analysis/primary_paired_analysis.json
```

## Code

```text
scripts/analysis/analyze_stage4_fresh_7b.py
scripts/analysis/run_stage4r_fresh_failure_attribution.py
tests/test_stage4_analysis_freeze.py
tests/test_stage4r_fresh_failure_attribution.py
tests/support/windows_py314_pytest_tempdir/sitecustomize.py
```

The `tests/support/windows_py314_pytest_tempdir/sitecustomize.py` shim is used
only for local Windows Python 3.14 pytest tempdir validation. It is not used by
server/GPU execution.
