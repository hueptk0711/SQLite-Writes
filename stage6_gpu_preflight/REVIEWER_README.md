# Stage6F GPU Environment Preflight PATCH0

This package prepares the GPU environment preflight for the accepted Stage6E
final confirmation set. It does not create confirmation predictions and does
not authorize the 7B confirmatory run.

Current local status:

- Frozen Stage5/Stage6E artifact audit: PASS.
- Final confirmation N: 481.
- Confirmation predictions created: false.
- Confirmation run allowed now: false.
- GPU/model/tokenizer preflight: not run on this local workspace.

The GPU portion must be executed from a clean server checkout with the locked
Qwen2.5-Coder-7B-Instruct environment. Use `RUN_STAGE6F_ON_SERVER.md` for the
server commands. A successful server run must validate with:

```bash
python scripts/data/validate_stage6f_gpu_preflight.py \
  --preflight-dir <server-output-dir>/stage6_gpu_preflight \
  --require-gpu-pass
```

Important guardrails:

- Do not run confirmatory inference in Stage6F.
- Do not create raw generation files for the 481 confirmation samples.
- Do not change Stage5 configs or Stage6E final gold artifacts.
- H2 must use one shared raw MP-FS+ generation for D_G1 and D_F_G1.
- `confirmation_run_allowed_now` must remain `false` until reviewer acceptance
  of the completed GPU preflight and a separate run-authorization lock.

