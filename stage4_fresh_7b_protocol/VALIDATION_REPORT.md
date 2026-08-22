# Stage 4 Protocol Validation Report

Status: PASS
Fresh samples: 300
Prompt rows: 2100
Model calls: 0
GPU required for this protocol package: no
Exact tokenizer count: deferred to mandatory GPU preflight before generation

Patch-1 execution-lock validation:

- generation graph invariant: PASS (`direct`, `j_fs`, `mp_fs_plus_shared`)
- frozen sample IDs SHA-256: `5c8bd5cf2e5b79088322289f795beb95c33a1fa0de214f129eb0cf6680319a25`
- D_G1 primary config SHA-256: `1ec5d19768fd1bc4c1814c0e2e02d3205007d2d53db9dab8ff0e8123e9e11fdf`
- source-group audit: 300 samples, 194 groups, 87 multi-sample groups, max group size 4
- source-group key source: `source_group_id` for 300/300 samples
- D parser opportunity audit: semantic parser output changed on 1/300 samples
- exact 4-bit BitsAndBytes config: locked
- runtime provenance assertion: accepted protocol commit must equal execution commit and working tree must be clean
- authoritative runner dry-run: PASS
- deterministic repeat build: PASS

CPU validation logs:

- `validation/protocol_validator.txt`
- `validation/dedicated_stage4_tests.txt`
- `validation/compatibility_A_to_G2_stage3_stage3b_stage4.txt`
- `validation/full_fast_suite.txt`
- `validation/runner_dry_run.txt`
- `validation/deterministic_repeat.txt`
