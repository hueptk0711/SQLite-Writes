# Stage 4 Protocol Validation Report

Status: PASS
Fresh samples: 300
Prompt rows: 2100
Model calls: 0
GPU required for this protocol package: no
Exact tokenizer count: deferred to mandatory GPU preflight before generation

Patch-2 execution-hardening validation:

- generation graph invariant: PASS (`direct`, `j_fs`, `mp_fs_plus_shared`)
- frozen sample IDs SHA-256: `5c8bd5cf2e5b79088322289f795beb95c33a1fa0de214f129eb0cf6680319a25`
- D_G1 primary config SHA-256: `1ec5d19768fd1bc4c1814c0e2e02d3205007d2d53db9dab8ff0e8123e9e11fdf`
- source-group audit: 300 samples, 194 groups, 87 multi-sample groups, max group size 4
- source-group key source counts: `{'source_group_id': 300}`
- D parser opportunity audit: semantic parser output changed on 1/300 samples
- exact 4-bit BitsAndBytes config: locked
- exact Python/package environment enforcement: locked
- strict raw generation completion: status==success and input_truncated==false for 300/300
- explicit resume with execution-lock drift rejection: locked
- frozen statistical analysis script: `scripts/analysis/analyze_stage4_fresh_7b.py`
- runtime provenance assertion: accepted protocol commit must equal execution commit and working tree must be clean
- authoritative runner dry-run: recorded in validation/runner_dry_run.txt after CPU validation
- deterministic repeat build: recorded in validation/deterministic_repeat.txt after CPU validation

Patch-3 frozen-analysis validation:

- complete executable analysis implementation: locked
- required method outputs: exact frozen sample-ID set, duplicates rejected, missing rows rejected
- primary metrics separated: target-state accuracy and strict full-state accuracy
- safety/selective metrics: coverage, accepted-output accuracy, false accept, execution success, constraint failure, off-target state change
- paired Original MP-FS+ vs D_G1 analysis: cluster bootstrap and McNemar exact test for both primary metrics
- subgroup outputs: input type, operation type, database, dependency-sensitive
- diagnostic outputs: first failure stage, D activation, G1 attempt/application/revalidation/final-state after application
- sample-level audit table: one sample × method row for all predeclared methods

Environment compatibility patch validation:

- expected GPU Python major/minor corrected: `3.14` → `3.12`
- historical verified GPU Python: `3.12.7`
- historical verified environment manifest: `07_reproducibility/server_final_run/environment_manifest_final_server.json`
- dependency lock unchanged; current SHA-256 equals historical lock SHA-256: `861a24b179b5edd1245aba33109402dd4ab82a634098bd8d81fcb666f5bdf9f1`
- locked GPU packages unchanged: `torch==2.6.0+cu124`, `transformers==5.5.3`, `accelerate==1.14.0`, `bitsandbytes==0.47.0`, `tokenizers==0.22.2`, `safetensors==0.5.3`
- model calls after environment correction: 0

CPU validation logs:

- `validation/protocol_validator.txt`
- `validation/analysis_freeze_tests.txt`
- `validation/dedicated_stage4_tests.txt`
- `validation/compatibility_A_to_G2_stage3_stage3b_stage4.txt`
- `validation/full_fast_suite.txt`
- `validation/runner_dry_run.txt`
- `validation/deterministic_repeat.txt`
