# Stage7B-A4 Atomic Candidate Domain and Omission-Cue Amendment Validation Report

Status: PASS

Validation date: 2026-09-02

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

## PATCH0 Generic Atomic Domain

```text
assignment_representability=2249/2256
full_sample_representability=721/728
candidate_count_median=35.0
candidate_count_p95=54
candidate_count_max=85
broader_containing_gold_total=1536
reviewer_blocker=generic atomic-child suppression creates three additional gold losses
```

## PATCH1 Schema-Label-Aware Domain

```text
assignment_representability=2252/2256
full_sample_representability=724/728
candidate_count_median=43.0
candidate_count_p95=68
candidate_count_max=114
suppressed_candidate_total=1166
broader_containing_gold_total=5420
```

## PATCH2 Schema-Label-Alias Domain

```text
assignment_representability=2252/2256
full_sample_representability=724/728
candidate_count_median=43.0
candidate_count_p95=67
candidate_count_max=114
suppressed_candidate_total=1346
broader_containing_gold_total=5255
threshold_decision=PASS_AUDIT_THRESHOLDS_READY_FOR_REVIEW
additional_assignment_losses=0
additional_full_sample_losses=0
preferred_freeze_gate_passed=true
method_freeze_authorized=false
```

## Omission Cues

```text
cue_phrases=omitted,missing,not provided,not supplied,blank,absent,left empty
true_assigned_value_exact_cue_count=0
true_assigned_value_contains_cue_count=0
question_cue_occurrence_count=0
candidate_containing_cue_count=0
synthetic_omission_safety_status=PASS
synthetic_positive_fixtures=4
synthetic_negative_literal_fixtures=4
```

## A5 Observed Error Counterfactual

This is a development diagnostic over the already-closed A5 UET outputs, not an
independent evaluation.

```text
corrected_a5_wrong_decision_count=23
patch1_wrong_decisions_suppressed=16
patch1_correct_gold_suppressed=0
patch2_wrong_decisions_suppressed=23
patch2_correct_gold_suppressed=0
```

## Decision

The PATCH2 schema-label-alias candidate-domain amendment preserves the current
baseline representability while removing more observed A5 label-plus-value
distractors. This package does not freeze a new runtime protocol and does not
authorize a model rerun; it provides the evidence needed for reviewer approval
of a later protocol freeze.
