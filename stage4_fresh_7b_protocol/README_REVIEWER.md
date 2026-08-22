# Stage 4 Fresh 7B Protocol Reviewer Package

This package freezes the fresh 7B protocol only. It does not call a model and
does not contain raw model generations.

Patch 3 scope: frozen executable analysis only. Fresh samples, configs,
generation graph, model/inference lock, resume rules, and GPU preflight rules
remain unchanged from Patch 2.

Environment compatibility patch scope: corrected the expected GPU Python
major/minor from local protocol-build Python `3.14` to historically verified
GPU inference Python `3.12`. The dependency lock, Torch/CUDA package versions,
model snapshot, data, configs, prompts, generation graph, and analysis remain
unchanged.

Primary method: MP-FS+ vNext D_G1.
Primary comparison: Original MP-FS+ vs D_G1, both reprocessed from the
same immutable MP-FS+ raw generation after exact HF input-ID preflight.
Secondary ablations on the same shared MP-FS+ raw generation: D_ONLY, FULL,
NO_C.

This is a fresh-to-vNext, database-disjoint held-out evaluation subset drawn
from the archived frozen test pool; it is not described as a never-before-used
public dataset.
