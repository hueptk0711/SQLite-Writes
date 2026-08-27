# Stage7C Validation Report

Status: PASS

violations: []

train_create_count: 1760
dev_create_count: 240

slot_quality: {"dev_candidate_gold_value_coverage_min": 0.95, "dev_candidate_gold_value_coverage_rate": 0.959763, "dev_spurious_required_slot_rate": 0.0, "dev_spurious_required_slot_rate_max": 0.01}

input_hashes_recomputed: true
raw_crudsql_hashes_recomputed: true
train_dev_create_manifests_recomputed: true
model_input_leakage_recomputed: true
split_contamination_recomputed: true
semantic_slot_derivation_audit_recomputed: true
gold_program_derivation_audit_recomputed: true
operation_mapping_validated: true
generation_config_validated: true
selection_policy_validated: true
reserved_benchmarks_validated: true

model_called: false
gpu_called: false
v2_implemented: false
experiment_run: false
live_sql_bench_gt_opened: false
