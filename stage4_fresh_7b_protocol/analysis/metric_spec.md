# Metric Spec

Primary metrics:

- Target-State Accuracy.
- Strict Full-State Accuracy.

Safety/selective metrics:

- Coverage.
- Accepted-Output Accuracy.
- False Accept Count and Rate.
- Execution Success.
- Constraint Failure.
- Off-Target State Change.

Diagnostics:

- First failure stage.
- D activation.
- G1 attempted, applied, revalidation success, and final-state success.

Frozen executable outputs from `scripts/analysis/analyze_stage4_fresh_7b.py`:

- `variant_metrics.csv`: one row per predeclared method with both primary
  accuracies plus coverage, accepted-output accuracy, false accepts, execution
  success, constraint failures, and off-target state-change rates.
- `primary_paired_analysis.json`: Original MP-FS+ vs D_G1 paired analysis for
  both target-state and strict full-state correctness.
- `subgroup_metrics.csv`: input type, operation type, database, and
  dependency-sensitive subgroups.
- `failure_stage_summary.csv`: first-failure-stage counts and rates.
- `intervention_summary.csv`: D activation and G1 repair trace summary.
- `sample_level_analysis.csv`: sample × method audit table backing aggregates.

Before any metric is computed, every predeclared method must have exactly the
same frozen sample-ID set as `data/fresh_sample_ids.txt`; missing or duplicate
rows cause `STOP`.
