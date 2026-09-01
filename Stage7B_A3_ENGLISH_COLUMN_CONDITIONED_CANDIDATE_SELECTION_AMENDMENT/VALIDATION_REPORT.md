# Stage7B-A3 English Column-Conditioned Candidate Selection Validation Report

Status: PASS

Validation date: 2026-09-01

## Scope

Stage7B-A3 is a CPU-only architecture amendment. It freezes Stage7E0-A4 as a
valid scientific feasibility failure and audits a column-conditioned
candidate-selection representation on the 728 non-pilot design-train samples.
It does not call a model, does not use GPU, does not open the 100-sample
development pilot, does not use development-dev, and does not use official
Gretel test rows.

```text
design_train_non_pilot_count=728
development_pilot_pool_count=100
development_dev_count=100
official_test_confirmation_count=51
model_called=false
gpu_called=false
```

## Frozen A4 Result

```text
stage7e0_a4_status=STAGE7E0_A4_VALID_FEASIBILITY_FAIL_CLOSED
primary_pass_count=6/10
required_pass_count=10/10
primary_gate_status=FAIL
scientific_result_eligible=true
gretel_pilot_opened=false
```

## Root Cause

```text
phase_o_severe_under_selection=3
phase_o_non_atomic_broader_span_selection=1
phase_m_primary_root_cause=0
compiler_or_materializer_bug=0
```

## Column-Conditioned Audit

```text
parsed_sample_count=728
target_table_column_decision_count=2548
assigned_column_decision_count=2256
omit_decision_count=292
omitted_required_without_default_count=0
assignment_candidate_coverage=2252/2256
full_sample_candidate_coverage=724/728
gold_assignment_type_compatible_coverage=2251/2256
candidate_count_p95=70
type_compatible_candidate_count_p95=68
```

## Decision

Column-conditioned candidate selection directly addresses the A4 failure mode:
the schema requires one SPAN/OMIT decision for every target-table column, so
stopping after the first selected value is no longer schema-valid. This stage
does not run a new primary feasibility experiment and does not authorize
opening Gretel pilot/dev/test rows.
