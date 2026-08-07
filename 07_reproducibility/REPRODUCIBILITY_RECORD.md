# Reproducibility record

## Frozen source and data

- Final GPU source archive:
  `../02_code/frozen_archive/mp_fs_plus_final_gpu_20260731.tar.gz`
- External holdout archive:
  `../03_protocol_and_data/final_holdout_release/mp_fs_plus_external_holdout_300_20260731.zip`
- Calibration evidence:
  `../03_protocol_and_data/calibration_evidence/`

## Server preflight identity

- GPU: NVIDIA GeForce RTX 4090, device 0.
- Driver: 570.124.04.
- CUDA runtime reported by Torch: 12.4.
- Torch: 2.6.0+cu124.
- Model aggregate SHA256:
  `e2026c78ea002527089b088023b7ae2c1486f127f667cafbb823225877cd268c`.
- Dependency lock SHA256:
  `861a24b179b5edd1245aba33109402dd4ab82a634098bd8d81fcb666f5bdf9f1`.
- Final protocol SHA256:
  `41eee8d41c2205af9485e03fd654a9a46a28720f78511b61babe1443c7d3820a`.

The complete server manifests and final protocol will be copied into this
directory by the final-result import tool.

## Post-hoc Yi-Coder cross-family extension

- Analysis class: post-hoc external-model robustness; primary result: false.
- Frozen protocol SHA256:
  `a5e6fdbd7dcbb6621092ea94dbc57bc45a08fa2d41d16fc62ba43802b250c256`.
- Model: `01-ai/Yi-Coder-9B-Chat`, snapshot
  `356a1f8d4e4a606d0b879e54191ca809918576b8`.
- Model aggregate SHA256:
  `881ebc7b893a9e12d704c40d2bdc908ed7958e0f671f5ccc434f5303102e6904`.
- Result archive SHA256:
  `5e087344cea56d7401e7af57898aaaf9304bd7c03aef2352d61e197f007c441e`.
- Import audit: 95/95 checks PASS; 300 rows per method, identical IDs/order,
  no regeneration, no input truncation, no missing generation.
- Eleven output-limit hits are retained as target-incorrect by the frozen
  conservative policy. Corrected off-target counts are 0/300 for each method.
- Canonical files: `../04_results/03_analysis_work/cross_family_yi_20260802/`.
