# Stage7C-A6 English Column-Conditioned Phase O Protocol Freeze Validation Report

Status: PASS

Validation date: 2026-09-02

## Scope

Stage7C-A6 freezes the one-call column-conditioned Phase O protocol. It does
not call a model, does not use GPU, does not open the Gretel pilot, does not
use development-dev, and does not use official test rows.

## Frozen Protocol

```text
phase_o_output_keys=operation,table_ref,column_span_refs
phase_m_primary_pipeline_removed=true
runtime_schema=single-table const TAB_1 or multi-table oneOf branch
candidate_serialization=SPAN_0001 | TAG[,TAG...] | exact source text
candidate_generator_variant=lexical_ngram2
type_based_candidate_pruning_enabled=false
candidate_domain_filter_enabled=true
runtime_order=lexical_ngram2 inventory -> schema-label + conservative-alias atomic suppression + context-aware omission-cue suppression -> filtered candidate inventory -> dynamic per-column SPAN | OMIT schema
```

## Synthetic Feasibility

```text
fresh_cases=12
assigned_column_decisions=52
omit_column_decisions=14
multi_table_oneof_cases=2
oracle_preflight=12/12 ADMITTED
canonical_target_state=12/12 exact
gold_suppressed_by_candidate_domain_filter=0
unfiltered_candidate_total=554
filtered_candidate_total=483
suppressed_candidate_total=71
```

## Full Prompt Token Burden

```text
tokenizer_status=PASS
tokenizer=Qwen/Qwen2.5-Coder-7B-Instruct
tokenizer_revision=c03e6d358207e414f1eca0bb1891e29f1db0e242
rendered_prompt_chars_median=3103.0
rendered_prompt_tokens_min=863
rendered_prompt_chars_p95=3493
rendered_prompt_tokens_median=998.5
rendered_prompt_tokens_p95=1194
rendered_prompt_tokens_max=1226
```

## Locked Failure Policy

Candidate-generator miss is a method failure, not OMIT, and may not exclude a
sample from pilot/dev/test denominators. Diagnostics are run after primary and cannot compensate primary failures.

```text
a5_corrected_regression_diagnostics=12
a5_diagnostic_oracle_preflight=12/12 ADMITTED
a6_method_stress_regression_diagnostics=12
method_stress_oracle_preflight=12/12 ADMITTED
exact_prior_design_literal_reuse_case_count=0
exact_synthetic_fixture_reuse_case_count=0
column_span_refs_mapping_equality=order_insensitive_by_object_key
duplicate_span_reuse_is_method_failure=true
```
