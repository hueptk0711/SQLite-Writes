# Stage7C-A5 English Column-Conditioned Phase O Protocol Freeze Validation Report

Status: PASS

Validation date: 2026-09-01

## Scope

Stage7C-A5 freezes the one-call column-conditioned Phase O protocol. It does
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
```

## Synthetic Feasibility

```text
fresh_cases=12
assigned_column_decisions=52
omit_column_decisions=15
multi_table_oneof_cases=2
oracle_preflight=12/12 ADMITTED
canonical_target_state=12/12 exact
```

## Full Prompt Token Burden

```text
tokenizer_status=PASS
tokenizer=Qwen/Qwen2.5-Coder-7B-Instruct
tokenizer_revision=c03e6d358207e414f1eca0bb1891e29f1db0e242
rendered_prompt_chars_median=3252.0
rendered_prompt_chars_p95=3681
rendered_prompt_tokens_median=1100.5
rendered_prompt_tokens_p95=1322
rendered_prompt_tokens_max=1336
```

## Locked Failure Policy

Candidate-generator miss is a method failure, not OMIT, and may not exclude a
sample from pilot/dev/test denominators. Diagnostics are run after primary and cannot compensate primary failures.

```text
a4_derived_regression_diagnostics=12
diagnostic_oracle_preflight=12/12 ADMITTED
column_span_refs_mapping_equality=order_insensitive_by_object_key
duplicate_span_reuse_is_method_failure=true
```
