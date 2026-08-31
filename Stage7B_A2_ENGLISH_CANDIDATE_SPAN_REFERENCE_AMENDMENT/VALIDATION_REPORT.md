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
selected_variant=lexical_ngram2
assignment_candidate_coverage=2252/2256
full_sample_candidate_coverage=724/728
min_required_assignment_coverage=0.99
min_required_full_sample_coverage=0.99
candidate_count_min=11
candidate_count_median=45.0
candidate_count_p95=70
candidate_count_max=114
missing_assignments=4
```

## Pareto Selection

```text
selection_rule=first require assignment/full-sample coverage >= 0.99; then minimize p95 candidate_count, mean candidate_count, and max candidate_count
selected_variant=lexical_ngram2
baseline_variant=brute16
```

## Serialization Burden

```text
tokenizer_status=PASS
tokenizer=Qwen/Qwen2.5-Coder-7B-Instruct
tokenizer_revision=c03e6d358207e414f1eca0bb1891e29f1db0e242
serialization_chars_median=1518.0
serialization_chars_p95=2427
serialization_tokens_median=662.0
serialization_tokens_p95=1057
serialization_tokens_max=1722
```

## Method Decision

The selected compact deterministic source-only inventory satisfies the frozen
coverage requirements while sharply reducing candidate burden relative to the
PATCH0 brute-force baseline. Phase O should stop generating numeric character
offsets and instead select dynamically-enumerated `SPAN_...` references. Phase
M, typed materialization, completeness, compiler, and SQLite preflight remain
unchanged.
