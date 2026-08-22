# Stage 4 Fresh 7B Protocol

Status: protocol freeze pending reviewer acceptance.

Fresh set: 300 samples selected deterministically from an archived 677-sample
test pool after excluding all overlap with the 300 diagnostic samples by sample
ID, source group, input-text hash, and canonical content hash.

Generation graph:

- Direct: Direct SQL prompt, generated independently.
- J-FS: JSON/J-FS prompt, generated independently.
- Shared MP-FS+: generated once only after GPU preflight proves that
  Original MP-FS+ and D_G1 final HF input IDs are identical for all 300 samples.

The shared MP-FS+ raw generation is processed as:

- Primary baseline: Original MP-FS+.
- Primary method: D_G1.
- Secondary: D_ONLY, FULL, NO_C.

If the GPU preflight reports Original-vs-D_G1 final input equality below
300/300, stop and return the preflight artifacts for review; do not change the
protocol automatically.

No component selection is allowed after seeing fresh 7B results. D_G1 remains
primary even if a secondary ablation scores higher on the fresh run.

Token-budget policy: frozen standard context, max_input_tokens=28672,
max_new_tokens=4096, truncation_policy=error. No 2K/4K/8K/full-context token
budget experiment is part of Stage 4.

Raw generation immutability: completed rows in raw_generations/direct.jsonl,
raw_generations/j_fs.jsonl, and raw_generations/mp_fs_plus_shared.jsonl are
never regenerated for semantic reasons. A raw arm is complete only if all 300
selected IDs occur exactly once, every row has `status == success`, and
`input_truncated == false`. `hit_max_new_tokens == true` remains immutable model
behavior when status is success. `oom`, `generation_error`, and
`input_truncation_error` stop the run and are not frozen as benchmark
predictions. Samples with no row because of an infrastructure crash may resume
with explicit `--resume` and the same locked config; attempt logs must be
preserved.

Resume contract: `result_root` may be reused only with explicit `--resume`.
Before resuming, the runner checks unchanged accepted commit, runner plan hash,
sample-ID hash, inference-config hash, model identity, and dependency-lock hash.

Environment contract: before first generation the runner compares installed
Python/package versions against `requirements-inference.lock.txt` for torch,
transformers, accelerate, bitsandbytes, tokenizers, and safetensors. Any mismatch
stops the run.

Stopping rule: if D_G1 shows systematic false acceptance, off-target state
changes, truncation, or missing predictions, preserve raw outputs and report the
failure. Do not patch/tune D/G1 on this fresh set and then reuse it as a test.
