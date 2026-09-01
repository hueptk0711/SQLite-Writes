# Stage7E0-A5 UET Primary Result Failure Analysis

Status: REAL_CONSTRAINED_PRIMARY_FAIL_DO_NOT_OPEN_GRETEL

The Qwen GPU output is preserved as server evidence. The PATCH4
UET constrained run satisfies evidence and protocol checks, but the
primary gate did not reach the required 12/12 pass count. Diagnostics
and the Gretel development-train pilot remain unopened.

```text
backend=constrained_hf
protocol_backend=incremental_json_schema_grammar
primary_pass_count=1/12
required_pass_count=12/12
evidence_integrity_status=PASS
protocol_compliance_status=PASS
primary_gate_status=FAIL
scientific_result_eligible=true
phase_o_raw_rows=12
failure_stage_counts={'acceptance_gate': 8, 'materialization_failure': 3}
```

## Primary-Failure Case Evidence

### stage7c_a5_primary_english_001

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0010","COL_2":"SPAN_0021","COL_3":"SPAN_0028","COL_4":"SPAN_0037","COL_5":"SPAN_0041","COL_6":"SPAN_0056"},"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a5_primary_english_002

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0011","COL_2":"SPAN_0019","COL_3":"SPAN_0028","COL_4":"SPAN_0034","COL_5":"SPAN_0040"},"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a5_primary_english_003

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0008","COL_2":"SPAN_0017","COL_3":"SPAN_0024","COL_4":"SPAN_0030","COL_5":"SPAN_0036","COL_6":"OMIT"},"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a5_primary_english_004

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0011","COL_2":"SPAN_0019","COL_3":"SPAN_0028","COL_4":"OMIT","COL_5":"OMIT"},"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a5_primary_english_005

```text
failure_stage=materialization_failure
error=Numeric evidence must be a strict finite numeric literal
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0011","COL_2":"SPAN_0019","COL_3":"SPAN_0029","COL_4":"SPAN_0035","COL_5":"OMIT","COL_6":"OMIT","COL_7":"OMIT"},"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a5_primary_english_006

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0006","COL_2":"SPAN_0014","COL_3":"SPAN_0020","COL_4":"SPAN_0029","COL_5":"OMIT"},"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a5_primary_english_007

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0010","COL_2":"SPAN_0015","COL_3":"SPAN_0025","COL_4":"SPAN_0032","COL_5":"SPAN_0036","COL_6":"SPAN_0045"},"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a5_primary_english_008

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0007","COL_2":"SPAN_0017","COL_3":"SPAN_0028","COL_4":"SPAN_0033","COL_5":"SPAN_0037","COL_6":"OMIT"},"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a5_primary_english_009

```text
failure_stage=materialization_failure
error=Numeric evidence must be a strict finite numeric literal
```

Phase O raw output:

```text
{"column_span_refs":{"TAB_2_COL_1":"SPAN_0014","TAB_2_COL_2":"SPAN_0025","TAB_2_COL_3":"SPAN_0030","TAB_2_COL_4":"SPAN_0036","TAB_2_COL_5":"OMIT"},"operation":"INSERT","table_ref":"TAB_2"}
```

### stage7c_a5_primary_english_010

```text
failure_stage=materialization_failure
error=Numeric evidence must be a strict finite numeric literal
```

Phase O raw output:

```text
{"column_span_refs":{"TAB_2_COL_1":"SPAN_0014","TAB_2_COL_2":"SPAN_0022","TAB_2_COL_3":"SPAN_0029","TAB_2_COL_4":"SPAN_0035","TAB_2_COL_5":"OMIT"},"operation":"INSERT","table_ref":"TAB_2"}
```

### stage7c_a5_primary_english_011

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"column_span_refs":{"COL_1":"SPAN_0010","COL_2":"SPAN_0019","COL_3":"SPAN_0024","COL_4":"SPAN_0032","COL_5":"SPAN_0037"},"operation":"INSERT","table_ref":"TAB_1"}
```
