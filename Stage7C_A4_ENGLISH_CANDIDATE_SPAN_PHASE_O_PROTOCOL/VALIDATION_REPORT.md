# Stage7C-A4 English Candidate-Span Phase O Protocol Validation Report

Status: PASS

Validation date: 2026-08-31

## Scope

Stage7C-A4 freezes the model-facing Phase O protocol for the Stage7B-A2
candidate-span architecture. It does not call a model, does not use GPU, does
not open the Gretel pilot, does not use development-dev, and does not use
official test rows.

## Frozen Protocol

```text
phase_o_output_keys=operation,span_refs
model_generates_character_offsets=false
model_generates_values=false
model_generates_column_refs=false
runtime_schema=dynamic per-sample enum over exact candidate refs
candidate_serialization=SPAN_0001 | TAG[,TAG...] | exact source text
candidate_generator_variant=lexical_ngram2
```

## Synthetic Feasibility

```text
fresh_cases=10
gold_values=35
oracle_preflight=10/10 ADMITTED
canonical_target_state=10/10 exact
candidate_inventory_contains_all_gold_spans=10/10
```

## Full Prompt Token Burden

```text
tokenizer_status=PASS
tokenizer=Qwen/Qwen2.5-Coder-7B-Instruct
tokenizer_revision=c03e6d358207e414f1eca0bb1891e29f1db0e242
rendered_prompt_chars_median=2181.5
rendered_prompt_chars_p95=2648
rendered_prompt_tokens_median=701.5
rendered_prompt_tokens_p95=968
rendered_prompt_tokens_max=1019
```

## Locked Failure Policy

Candidate-generator miss is locked as a method failure. It must remain in every
pilot/dev/test denominator and may not be used as a sample-exclusion rule.
