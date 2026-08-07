# Calibration matrix decision

- Protocol: `mp_fs_plus_calibration_v3_in28672_out4096_locked_20260729`
- Decision: **GO**
- Matrix complete: `true`
- Paper-result eligible: `false` (calibration stage)

| Method | Parse | Build | Execute | Target | Strict | Accepted accuracy | Coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| D-FS-M | 0.9833 | 0.9833 | 0.8167 | 0.7667 | 0.7667 | 0.7797 | 0.9833 |
| J-FS-M | 1.0000 | 0.9667 | 0.9000 | 0.7833 | 0.7833 | 0.8103 | 0.9667 |
| MP-FS-M | 0.8333 | 0.4833 | 0.3167 | 0.2500 | 0.2500 | 0.5172 | 0.4833 |
| MP-FS+ | 1.0000 | 0.8500 | 0.7500 | 0.7333 | 0.7333 | 0.9778 | 0.7500 |
| Gold-MP | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

## Locked Go/No-Go checks

- PASS: `gold_mp_accuracy` — 1.0 == 1.0
- PASS: `mp_fs_plus_parse_success` — 1.0 >= 0.98
- PASS: `mp_fs_plus_build_success` — 0.85 >= 0.85
- PASS: `mp_fs_plus_execution_success` — 0.75 >= 0.75
- PASS: `mp_fs_plus_accepted_output_accuracy` — 0.9777777777777777 >= 0.95
- PASS: `mp_fs_plus_side_effect_rate` — 0.0 <= 0.0
- PASS: `mp_fs_plus_invalid_source_selector_count` — 0 <= 1
- PASS: `mp_fs_plus_unknown_column_count` — 0 <= 1
- PASS: `all_gpu_input_truncation_count` — 0 <= 0
- PASS: `all_gpu_output_limit_hit_count` — 0 <= 0
- PASS: `all_methods_missing_prediction_count` — 0 <= 0
- PASS: `all_gpu_generation_failure_count` — 0 == 0
