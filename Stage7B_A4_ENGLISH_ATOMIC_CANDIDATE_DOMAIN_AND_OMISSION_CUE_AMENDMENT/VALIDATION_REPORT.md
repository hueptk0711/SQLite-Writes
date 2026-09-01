# Stage7B-A4 Atomic Candidate Domain and Omission-Cue Amendment Validation Report

Status: PASS

Validation date: 2026-09-01

## Scope

This is a CPU-only design-train audit. It does not call a model, does not use
GPU, and does not open Gretel pilot, development-dev, or official test rows.

```text
design_train_non_pilot_count=728
assignment_count=2256
baseline_candidate_generator=lexical_ngram2
model_called=false
gpu_called=false
```

## Current Domain

```text
assignment_representability=2252/2256
full_sample_representability=724/728
candidate_count_median=45.0
candidate_count_p95=70
candidate_count_max=114
broader_containing_gold_total=6541
```

## Atomic-Filtered Domain Under Review

```text
assignment_representability=2249/2256
full_sample_representability=721/728
candidate_count_median=35.0
candidate_count_p95=54
candidate_count_max=85
suppressed_candidate_total=7504
broader_containing_gold_total=1536
threshold_decision=PASS_AUDIT_THRESHOLDS_READY_FOR_REVIEW
method_freeze_authorized=false
```

## Omission Cues

```text
cue_phrases=omitted,missing,not provided,not supplied,blank,absent,left empty
true_assigned_value_exact_cue_count=0
true_assigned_value_contains_cue_count=0
question_cue_occurrence_count=0
candidate_containing_cue_count=0
```

## Decision

The candidate-domain amendment passes the 99% assignment and full-sample
representability audit on the 728 design-train samples. This package does not
freeze a new runtime protocol and does not authorize a model rerun; it provides
the evidence needed for reviewer approval of a later protocol freeze.
