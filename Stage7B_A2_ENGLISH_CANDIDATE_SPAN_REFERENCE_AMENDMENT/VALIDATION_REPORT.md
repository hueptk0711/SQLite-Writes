# Stage7B-A2 English Candidate-Span Reference Amendment Validation Report

Status: PASS

Validation date: 2026-08-31

## Scope

Stage7B-A2 closes the Stage7E0-A3 numeric-offset route and opens a CPU-only
architecture amendment. It does not call a model, does not use GPU, does not
open the 100-sample development pilot, does not use development-dev, and does
not use official Gretel test rows.

```text
design_train_non_pilot_count=728
development_pilot_pool_count=100
development_dev_count=100
official_test_confirmation_count=51
model_called=false
gpu_called=false
```

## Oracle Candidate Coverage

```text
assignment_candidate_coverage=2256/2256
full_sample_candidate_coverage=728/728
min_required_assignment_coverage=0.99
min_required_full_sample_coverage=0.99
candidate_count_min=20
candidate_count_median=215.0
candidate_count_p95=401
candidate_count_max=704
```

## Method Decision

The deterministic source-only inventory covers every audited gold assignment
on the 728 non-pilot design-train samples. Phase O should therefore stop
generating numeric character offsets and instead select `SPAN_...` references.
Phase M, typed materialization, completeness, compiler, and SQLite preflight
remain unchanged.
