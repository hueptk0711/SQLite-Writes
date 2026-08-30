# Stage7E0-A3 English Real Server Result Failure Analysis

Status: FAIL

The real Qwen GPU run completed the primary Stage7E0-A3 gate and failed 0/8.
Diagnostics and the Gretel development-train pilot remain unopened.

```text
backend=hf
primary_pass_count=0/8
required_pass_count=8/8
phase_o_raw_rows=8
phase_m_raw_rows=5
failure_stage_counts={'phase_m_schema_failure': 5, 'phase_o_schema_failure': 3}
```

## Failed Cases

### stage7c_fresh_english_001

```text
failure_stage=phase_m_schema_failure
error=missing required keys: ['assignments', 'table_ref']
```

Phase O raw output:

```text
```json
{
  "operation": "INSERT",
  "value_spans": [
    {
      "start_char": 20,
      "end_char": 26
    },
    {
      "start_char": 35,
      "end_char": 37
    }
  ]
}
```
```

Phase M raw output:

```text
```json
{
  "operation": "INSERT",
  "table": "accounts",
  "columns": ["account_code", "score"],
  "values": ["AC-001", 42]
}
```
```

### stage7c_fresh_english_002

```text
failure_stage=phase_o_schema_failure
error=enum mismatch
```

Phase O raw output:

```text
```json
{
  "operation": "INSERT INTO contacts (name, email, priority) VALUES (?, ?, ?)",
  "value_spans": [
    [18, 24],
    [36, 58],
    [60, 61]
  ]
}
```
```

Phase M raw output:

```text

```

### stage7c_fresh_english_003

```text
failure_stage=phase_m_schema_failure
error=missing required keys: ['assignments', 'operation', 'table_ref']
```

Phase O raw output:

```text
```json
{
  "operation": "INSERT",
  "value_spans": [
    {
      "start_char": 26,
      "end_char": 29
    },
    {
      "start_char": 46,
      "end_char": 50
    },
    {
      "start_char": 52,
      "end_char": 54
    },
    {
      "start_char": 62,
      "end_char": 64
    }
  ]
}
```
```

Phase M raw output:

```text
```json
{
  "operation": "INSERT",
  "table": "TAB_1",
  "columns": [
    {"column": "sensor_id", "value": "7")},
    {"column": "temperature", "value": "21.7"},
    {"column": "humidity", "value": "45"}
  ]
}
```
```

### stage7c_fresh_english_004

```text
failure_stage=phase_m_schema_failure
error=missing required keys: ['assignments', 'table_ref']
```

Phase O raw output:

```text
```json
{
  "operation": "INSERT",
  "value_spans": [
    {
      "start_char": 16,
      "end_char": 29
    },
    {
      "start_char": 42,
      "end_char": 48
    },
    {
      "start_char": 61,
      "end_char": 64
    },
    {
      "start_char": 65,
      "end_char": 66
    },
    {
      "start_char": 67,
      "end_char": 68
    },
    {
      "start_char": 69,
      "end_char": 70
    },
    {
      "start_char": 71,
      "end_char": 72
    },
    {
      "start_char": 73,
      "end_char": 74
    },
    {
      "start_char": 80,
      "end_char": 83
    },
    {
      "start_char": 84,
      "end_char": 85
    },
    {
      "start_char": 86,
      "end_char": 87
    },
    {
      "start_char": 88,
      "end_char": 89
    },
    {
      "start_char": 90,
      "end_char": 91
    }
  ]
}
```
```

Phase M raw output:

```text
```json
{
  "operation": "INSERT",
  "table": "shipments",
  "columns": [
    {"column": "shipment_id", "value": "SHIP-2026-08-"},
    {"column": "carrier", "value": "Blue R"},
    {"column": "weight", "value": "18."},
    {"column": "stops", "value": "4"},
    {"column": "eta", "value": "2029-09-01"}
  ]
}
```
```

### stage7c_fresh_english_005

```text
failure_stage=phase_m_schema_failure
error=missing required keys: ['assignments', 'table_ref']
```

Phase O raw output:

```text
```json
{
  "operation": "INSERT",
  "value_spans": [
    {
      "start_char": 21,
      "end_char": 26
    },
    {
      "start_char": 58,
      "end_char": 72
    }
  ]
}
```
```

Phase M raw output:

```text
```json
{
  "operation": "INSERT",
  "table": "TAB_1",
  "columns": ["COL_1", "COL_2"],
  "values": ["user_", "452@example.or"]
}
```
```

### stage7c_fresh_english_006

```text
failure_stage=phase_o_schema_failure
error=enum mismatch
```

Phase O raw output:

```text
```json
{
  "operation": "INSERT INTO tickets (title, severity, station) VALUES (?, ?, ?)",
  "value_spans": [
    [22, 36],
    [52, 53],
    [64, 68]
  ]
}
```
```

Phase M raw output:

```text

```

### stage7c_fresh_english_007

```text
failure_stage=phase_o_schema_failure
error=enum mismatch
```

Phase O raw output:

```text
```json
{
  "operation": "INSERT INTO invoices (invoice_id, amount_literal, line_count, note) VALUES (?, ?, ?, ?)",
  "value_spans": [
    [12, 20],
    [22, 37],
    [39, 41],
    [43, 62]
  ]
}
```
```

Phase M raw output:

```text

```

### stage7c_fresh_english_008

```text
failure_stage=phase_m_schema_failure
error=missing required keys: ['assignments', 'table_ref']
```

Phase O raw output:

```text
```json
{
  "operation": "INSERT",
  "value_spans": [
    {
      "start_char": 22,
      "end_char": 29
    },
    {
      "start_char": 43,
      "end_char": 48
    },
    {
      "start_char": 56,
      "end_char": 59
    },
    {
      "start_char": 62,
      "end_char": 64
    },
    {
      "start_char": 66,
      "end_char": 68
    },
    {
      "start_char": 70,
      "end_char": 72
    }
  ]
}
```
```

Phase M raw output:

```text
```json
{
  "operation": "INSERT",
  "table": "experiment_runs",
  "columns": [
    {"column_ref": "COL_1", "value": "RUN-A3-"},
    {"column_ref": "COL_2", "value": "Dr Li"},
    {"column_ref": "COL_3", "value": null},
    {"column_ref": "COL_4", "value": null},
    {"column_ref": "COL_5", "value": null}
  ]
}
```
```
