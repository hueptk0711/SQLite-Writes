# Stage6K Validation Report

Status: PASS

Frozen protocol checks:
- final_n: 481
- primary_metric: target_state_correct
- confirmatory_family: H1, H2 only
- bootstrap_seed: 240824
- bootstrap_replicates: 10000
- cluster_key: source_group
- model_called: false
- gpu_called: false

The validator recomputes the paired table, exact McNemar tests, Holm correction, and cluster bootstrap from frozen Stage6J outcomes and Stage6E final denominator before comparing to saved Stage6K artifacts.
