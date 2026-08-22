# Stage 4 Protocol Validation Report

Status: PASS
Fresh samples: 300
Prompt rows: 2100
Model calls: 0
GPU required for this protocol package: no
Exact tokenizer count: deferred to mandatory GPU preflight before generation

Validation commands:

- Protocol validator: PASS, 300 samples, 2100 prompt rows, 4 generation arms.
- Dedicated Stage4 tests: 5 passed.
- Compatibility A-G2 + Stage3 + Stage3B + Stage4: 221 passed.
- Full fast suite: 371 passed, 1 skipped, 12 subtests passed.
- Deterministic repeat: PASS, 16 core artifacts byte-identical.

Note: the compatibility and full fast pytest runs were executed outside the
Windows sandbox because pytest temp-directory creation was blocked by local ACLs
inside the sandbox. No GPU/model call was made.
