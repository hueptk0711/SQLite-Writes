# Stage7E0-A3 English Invalid Run 001 Classification

Status: INVALID_RUN_001_BACKEND_PROTOCOL_VIOLATION_DO_NOT_OPEN_GRETEL

The prior server files are preserved as evidence with integrity status
`PASS`, but they are not a scientific
A3 primary result because the run used `plain_hf_unconstrained`
instead of `patch9_incremental_json_schema_grammar`.

```text
observed_primary_pass_count=0/8
protocol_compliance_status=FAIL
primary_gate_status=INVALID_NOT_EVALUATED
scientific_result_eligible=false
gretel_pilot_opened=false
```

Decision: preserve the raw server evidence, classify it as invalid, and rerun the
same eight A3 cases only with the PATCH9 constrained backend.
