# Stage 4 Fresh 7B Protocol

Status: protocol freeze pending reviewer acceptance.

Fresh set: 300 samples selected deterministically from an archived 677-sample
test pool after excluding all overlap with the 300 diagnostic samples by sample
ID, source group, input-text hash, and canonical content hash.

Generation arms:

- Direct: Direct SQL prompt, generated independently.
- J-FS: JSON/J-FS prompt, generated independently.
- Original MP-FS+: historical MP-FS+ prompt, generated independently.
- vNext prompt: D-enabled MP-FS+ prompt, generated once.

The vNext raw generation is processed as:

- Primary: D_G1.
- Secondary: D_ONLY, FULL, NO_C.

No component selection is allowed after seeing fresh 7B results. D_G1 remains
primary even if a secondary ablation scores higher on the fresh run.

Token-budget policy: frozen standard context, max_input_tokens=28672,
max_new_tokens=4096, truncation_policy=error. No 2K/4K/8K/full-context token
budget experiment is part of Stage 4.

Stopping rule: if D_G1 shows systematic false acceptance, off-target state
changes, truncation, or missing predictions, preserve raw outputs and report the
failure. Do not patch/tune D/G1 on this fresh set and then reuse it as a test.
