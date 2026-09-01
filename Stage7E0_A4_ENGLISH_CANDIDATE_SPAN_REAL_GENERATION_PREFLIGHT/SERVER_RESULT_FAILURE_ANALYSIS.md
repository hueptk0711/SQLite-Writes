# Stage7E0-A4 English Kaggle Primary Result Failure Analysis

Status: REAL_CONSTRAINED_PRIMARY_FAIL_DO_NOT_OPEN_GRETEL

The Qwen GPU output is preserved as server evidence. The PATCH3
constrained run satisfies evidence and protocol checks, but the primary
gate did not reach the required 10/10 pass count. Diagnostics and the
Gretel development-train pilot remain unopened.

```text
backend=constrained_hf
primary_pass_count=6/10
required_pass_count=10/10
evidence_integrity_status=PASS
protocol_compliance_status=PASS
primary_gate_status=FAIL
scientific_result_eligible=true
phase_o_raw_rows=10
phase_m_raw_rows=10
failure_stage_counts={'acceptance_gate': 3, 'materialization_failure': 1}
```

## Primary-Failure Case Evidence

### stage7c_a4_fresh_english_002

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"operation":"INSERT","span_refs":["SPAN_0009"]}
```

Phase M raw output:

```text
{"assignments":[{"column_ref":"COL_1","evidence_ref":"EV_1","slot_ref":"SLOT_1"}],"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a4_fresh_english_004

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"operation":"INSERT","span_refs":["SPAN_0026"]}
```

Phase M raw output:

```text
{"assignments":[{"column_ref":"COL_3","evidence_ref":"EV_1","slot_ref":"SLOT_1"}],"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a4_fresh_english_006

```text
failure_stage=acceptance_gate
error=None
```

Phase O raw output:

```text
{"operation":"INSERT","span_refs":["SPAN_0008"]}
```

Phase M raw output:

```text
{"assignments":[{"column_ref":"COL_1","evidence_ref":"EV_1","slot_ref":"SLOT_1"}],"operation":"INSERT","table_ref":"TAB_1"}
```

### stage7c_a4_fresh_english_009

```text
failure_stage=materialization_failure
error=INTEGER evidence must be a strict lossless integer literal
```

Phase O raw output:

```text
{"operation":"INSERT","span_refs":["SPAN_0010","SPAN_0017","SPAN_0021"]}
```

Phase M raw output:

```text
{"assignments":[{"column_ref":"COL_1","evidence_ref":"EV_1","slot_ref":"SLOT_1"},{"column_ref":"COL_2","evidence_ref":"EV_2","slot_ref":"SLOT_2"},{"column_ref":"COL_3","evidence_ref":"EV_3","slot_ref":"SLOT_3"}],"operation":"INSERT","table_ref":"TAB_1"}
```
