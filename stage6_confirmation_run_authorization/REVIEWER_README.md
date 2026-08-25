# Stage6G Confirmation Run Authorization Lock

This CPU-only stage authorizes the frozen Stage6 final confirmation set for
confirmatory generation after Stage6F reviewer acceptance. It does not create
predictions and does not call the model or GPU.

The confirmatory run must use exactly four LLM generation streams:

1. `direct` -> `raw_generations/direct.jsonl`
2. `j_fs` -> `raw_generations/j_fs.jsonl`
3. `original_mp_fs_plus` -> `raw_generations/original_mp_fs_plus.jsonl`
4. `shared_mp_fs_plus_generation` -> `raw_generations/shared_mp_fs_plus_generation.jsonl`

The shared MP-FS+ generation must be replayed deterministically as both
`d_g1_control` and `d_f_g1_vnext`.
