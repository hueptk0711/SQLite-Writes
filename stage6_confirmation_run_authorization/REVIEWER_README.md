# Stage6G Confirmation Run Authorization Lock

This CPU-only stage authorizes the frozen Stage6 final confirmation set for
confirmatory generation after Stage6F reviewer acceptance. It does not create
predictions and does not call the model or GPU.

PATCH1 hardens the execution boundary:

- copies the exact Stage5 `generation_lock` object, including `temperature: null`;
- locks Stage5 protocol, arm-config, environment, resolved-config, and method-source hashes;
- requires an exact reviewer-accepted Stage6G PATCH1 Git HEAD and clean worktree at execution;
- requires zero pre-existing raw generation files before initial execution;
- locks runtime prompt/input-ID identity checks against the Stage6F prompt audit;
- locks infrastructure-only resume semantics.

The confirmatory run must use exactly four LLM generation streams:

1. `direct` -> `raw_generations/direct.jsonl`
2. `j_fs` -> `raw_generations/j_fs.jsonl`
3. `original_mp_fs_plus` -> `raw_generations/original_mp_fs_plus.jsonl`
4. `shared_mp_fs_plus_generation` -> `raw_generations/shared_mp_fs_plus_generation.jsonl`

The shared MP-FS+ generation must be replayed deterministically as both
`d_g1_control` and `d_f_g1_vnext`.

Before generation on the GPU server, run:

```bash
python scripts/data/validate_stage6g_confirmation_authorization.py   --authorization-dir stage6_confirmation_run_authorization   --repo-root .   --expected-git-head <REVIEWER_ACCEPTED_STAGE6G_PATCH1_COMMIT>   --require-git-clean
```
