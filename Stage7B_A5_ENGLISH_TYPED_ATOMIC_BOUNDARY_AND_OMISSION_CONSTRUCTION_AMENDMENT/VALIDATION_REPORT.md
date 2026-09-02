# Stage7B-A5 Typed Atomic Boundary and Omission-Construction Amendment Validation Report

Status: PASS

Validation date: 2026-09-02

## Scope

CPU-only audit. No model call, no GPU call, no Gretel pilot/dev/test rows.
A6 real failures are development diagnostics only.

```text
design_train_non_pilot_count=728
assignment_count=2256
baseline_a4_assignment_representability=2252/2256
baseline_a4_full_sample_representability=724/728
stage7b_a5_assignment_representability=2252/2256
stage7b_a5_full_sample_representability=724/728
additional_assignment_losses=0
additional_full_sample_losses=0
a6_primary_result=2/12 valid feasibility failure
a6_wrong_decisions=15
a6_wrong_decisions_suppressed_by_a5=14
a6_correct_gold_suppressed_by_a5=0
synthetic_safety=PASS
model_called=false
gpu_called=false
```

## Decision

The Stage7B-A5 audit is ready for reviewer inspection only if the strict design
gate remains zero additional gold losses. This package does not authorize an
A6 rerun and does not open Gretel.
