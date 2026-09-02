# Stage7E0-A6 UET Primary Result Failure Analysis

Status: REAL_CONSTRAINED_PRIMARY_FAIL_DO_NOT_OPEN_GRETEL

The Qwen GPU output is preserved as server evidence. The PATCH0
UET constrained A6 run satisfies evidence and protocol checks, but the
primary gate did not reach the required 12/12 pass count. Diagnostics
and the Gretel development-train pilot remain unopened.

```text
backend=constrained_hf
protocol_backend=incremental_json_schema_grammar
primary_pass_count=2/12
required_pass_count=12/12
evidence_integrity_status=PASS
protocol_compliance_status=PASS
primary_gate_status=FAIL
scientific_result_eligible=true
phase_o_raw_rows=12
failure_stage_counts={'acceptance_gate': 8, 'materialization_failure': 1, 'A6_deterministic_oracle': 1}
```

## Primary-Failure Case Evidence

### stage7c_a6_primary_english_001

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0005","COL_2":"SPAN_0015","COL_3":"SPAN_0022","COL_4":"SPAN_0028","COL_5":"SPAN_0036","COL_6":"SPAN_0049","COL_7":"OMIT"},"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a6_primary_english_002

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0013","COL_2":"SPAN_0020","COL_3":"SPAN_0026","COL_4":"SPAN_0033","COL_5":"OMIT"},"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a6_primary_english_003

```text
failure_stage=materialization_failure
error=INTEGER evidence must be a strict lossless integer literal
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0007","COL_2":"SPAN_0017","COL_3":"SPAN_0022","COL_4":"SPAN_0027","COL_5":"SPAN_0039","COL_6":"SPAN_0049"},"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a6_primary_english_004

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0008","COL_2":"SPAN_0018","COL_3":"SPAN_0026","COL_4":"SPAN_0036","COL_5":"SPAN_0042","COL_6":"OMIT"},"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a6_primary_english_005

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0008","COL_2":"SPAN_0017","COL_3":"SPAN_0024","COL_4":"SPAN_0029","COL_5":"OMIT"},"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a6_primary_english_007

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0010","COL_2":"SPAN_0019","COL_3":"SPAN_0026","COL_4":"SPAN_0033","COL_5":"SPAN_0042"},"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a6_primary_english_008

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0010","COL_2":"SPAN_0017","COL_3":"SPAN_0024","COL_4":"SPAN_0030","COL_5":"SPAN_0037","COL_6":"SPAN_0051"},"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a6_primary_english_009

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0009","COL_2":"SPAN_0020","COL_3":"SPAN_0028","COL_4":"SPAN_0039","COL_5":"SPAN_0046","COL_6":"SPAN_0055"},"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a6_primary_english_011

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0010","COL_2":"SPAN_0019","COL_3":"SPAN_0026","COL_4":"SPAN_0033","COL_5":"OMIT","COL_6":"OMIT"},"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a6_primary_english_012

```text
failure_stage=A6_deterministic_oracle
error=NOT NULL constraint failed: theater_prop_checkouts.checkout_id
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"OMIT","COL_2":"SPAN_0019","COL_3":"OMIT","COL_4":"SPAN_0027","COL_5":"OMIT","COL_6":"OMIT","COL_7":"OMIT"},"operation":"INSERT","table_ref":"TAB_1"}
```
