# Stage6J Deterministic Replay Evaluation

This package freezes CPU-only deterministic replay/evaluation for the accepted Stage6I confirmation generations.

No model or GPU is called in this stage. Stage6J consumes the 1924 raw generations from Stage6I and evaluates five deterministic arms over the fixed Stage6E denominator of 481 samples:

- Direct from `direct.jsonl`
- J-FS from `j_fs.jsonl`
- Original MP-FS+ from `original_mp_fs_plus.jsonl`
- D+G1 from `shared_mp_fs_plus_generation.jsonl`
- D+F+G1 from the same `shared_mp_fs_plus_generation.jsonl`

The D+G1 and D+F+G1 outcomes record the same `shared_raw_generation_row_sha256` for each sample, preserving the H2 shared-generation design.

Stage6J intentionally does not compute McNemar tests, Holm correction, bootstrap intervals, or any significance statistics. Those belong to Stage6K after reviewer acceptance of the deterministic replay outcomes.

Primary artifacts:

- `STAGE6J_REPLAY_EVALUATION_LOCK.json`
- `REPLAY_ARM_MANIFEST.json`
- `REPLAY_EVALUATION_SUMMARY.json`
- `DENOMINATOR_AUDIT.json`
- `H2_SHARED_REPLAY_PROVENANCE_AUDIT.json`
- `replay_outcomes/*.jsonl`
- `stage6i_generation_inputs/`

Validation command:

```bash
PYTHONPATH=src python scripts/data/validate_stage6j_replay_evaluation.py --stage6j-dir stage6_replay_evaluation
```
