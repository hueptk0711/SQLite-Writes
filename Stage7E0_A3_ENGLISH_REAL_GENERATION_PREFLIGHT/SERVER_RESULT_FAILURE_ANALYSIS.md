# Stage7E0-A3 English Real Server Result Failure Analysis

Status: REAL_CONSTRAINED_PRIMARY_FAIL_DO_NOT_OPEN_GRETEL

The Qwen GPU output is preserved as server evidence. The PATCH3
constrained run satisfies evidence and protocol checks, but the primary
gate did not reach the required 8/8 pass count. Diagnostics and the
Gretel development-train pilot remain unopened.

```text
backend=constrained_hf
primary_pass_count=1/8
required_pass_count=8/8
evidence_integrity_status=PASS
protocol_compliance_status=PASS
primary_gate_status=FAIL
scientific_result_eligible=true
phase_o_raw_rows=8
phase_m_raw_rows=8
failure_stage_counts={'materialization_failure': 6, 'acceptance_gate': 1}
```

## Primary-Failure Case Evidence

### stage7c_fresh_english_002

```text
failure_stage=materialization_failure
error=INTEGER evidence must be a strict lossless integer literal
```

Phase O raw output:

```text
{"operation":"INSERT","value_spans":[{"end_char":28,"start_char":18},{"end_char":58,"start_char":30},{"end_char":60,"start_char":59}]}
```

Phase M raw output:

```text
{"assignments":[{"column_ref":"COL_1","evidence_ref":"EV_1","slot_ref":"SLOT_1"},{"column_ref":"COL_2","evidence_ref":"EV_2","slot_ref":"SLOT_2"},{"column_ref":"COL_3","evidence_ref":"EV_3","slot_ref":"SLOT_3"}],"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_fresh_english_003

```text
failure_stage=materialization_failure
error=Numeric evidence must be a strict finite numeric literal
```

Phase O raw output:

```text
{"operation":"INSERT","value_spans":[{"end_char":28,"start_char":15},{"end_char":52,"start_char":46},{"end_char":64,"start_char":53},{"end_char":79,"start_char":66}]}
```

Phase M raw output:

```text
{"assignments":[{"column_ref":"COL_1","evidence_ref":"EV_1","slot_ref":"SLOT_1"},{"column_ref":"COL_2","evidence_ref":"EV_2","slot_ref":"SLOT_2"},{"column_ref":"COL_3","evidence_ref":"EV_3","slot_ref":"SLOT_3"},{"column_ref":"COL_4","evidence_ref":"EV_4","slot_ref":"SLOT_4"}],"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_fresh_english_004

```text
failure_stage=materialization_failure
error=INTEGER evidence must be a strict lossless integer literal
```

Phase O raw output:

```text
{"operation":"INSERT","value_spans":[{"end_char":31,"start_char":16},{"end_char":51,"start_char":42},{"end_char":65,"start_char":61},{"end_char":72,"start_char":70},{"end_char":90,"start_char":80}]}
```

Phase M raw output:

```text
{"assignments":[{"column_ref":"COL_1","evidence_ref":"EV_1","slot_ref":"SLOT_1"},{"column_ref":"COL_2","evidence_ref":"EV_2","slot_ref":"SLOT_2"},{"column_ref":"COL_3","evidence_ref":"EV_3","slot_ref":"SLOT_3"},{"column_ref":"COL_4","evidence_ref":"EV_4","slot_ref":"SLOT_4"},{"column_ref":"COL_5","evidence_ref":"EV_5","slot_ref":"SLOT_5"}],"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_fresh_english_005

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"operation":"INSERT","value_spans":[{"end_char":26,"start_char":14},{"end_char":72,"start_char":49}]}
```

Phase M raw output:

```text
{"assignments":[{"column_ref":"COL_1","evidence_ref":"EV_1","slot_ref":"SLOT_1"},{"column_ref":"COL_2","evidence_ref":"EV_2","slot_ref":"SLOT_2"}],"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_fresh_english_006

```text
failure_stage=materialization_failure
error=INTEGER evidence must be a strict lossless integer literal
```

Phase O raw output:

```text
{"operation":"INSERT","value_spans":[{"end_char":38,"start_char":22},{"end_char":51,"start_char":50},{"end_char":71,"start_char":63}]}
```

Phase M raw output:

```text
{"assignments":[{"column_ref":"COL_1","evidence_ref":"EV_1","slot_ref":"SLOT_1"},{"column_ref":"COL_2","evidence_ref":"EV_2","slot_ref":"SLOT_2"},{"column_ref":"COL_3","evidence_ref":"EV_3","slot_ref":"SLOT_3"}],"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_fresh_english_007

```text
failure_stage=materialization_failure
error=INTEGER evidence must be a strict lossless integer literal
```

Phase O raw output:

```text
{"operation":"INSERT","value_spans":[{"end_char":20,"start_char":12},{"end_char":37,"start_char":29},{"end_char":52,"start_char":43},{"end_char":73,"start_char":60}]}
```

Phase M raw output:

```text
{"assignments":[{"column_ref":"COL_1","evidence_ref":"EV_1","slot_ref":"SLOT_1"},{"column_ref":"COL_2","evidence_ref":"EV_2","slot_ref":"SLOT_2"},{"column_ref":"COL_3","evidence_ref":"EV_3","slot_ref":"SLOT_3"},{"column_ref":"COL_4","evidence_ref":"EV_4","slot_ref":"SLOT_4"}],"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_fresh_english_008

```text
failure_stage=materialization_failure
error=Numeric evidence must be a strict finite numeric literal
```

Phase O raw output:

```text
{"operation":"INSERT","value_spans":[{"end_char":32,"start_char":22},{"end_char":49,"start_char":40},{"end_char":52,"start_char":51},{"end_char":55,"start_char":54},{"end_char":58,"start_char":57},{"end_char":61,"start_char":60},{"end_char":64,"start_char":63},{"end_char":67,"start_char":66},{"end_char":70,"start_char":69},{"end_char":73,"start_char":72},{"end_char":76,"start_char":75}]}
```

Phase M raw output:

```text
{"assignments":[{"column_ref":"COL_1","evidence_ref":"EV_1","slot_ref":"SLOT_1"},{"column_ref":"COL_2","evidence_ref":"EV_2","slot_ref":"SLOT_2"},{"column_ref":"COL_3","evidence_ref":"EV_3","slot_ref":"SLOT_3"},{"column_ref":"COL_4","evidence_ref":"EV_4","slot_ref":"SLOT_4"},{"column_ref":"COL_5","evidence_ref":"EV_5","slot_ref":"SLOT_5"}],"operation":"INSERT","table_ref":"TAB_1"}
```
