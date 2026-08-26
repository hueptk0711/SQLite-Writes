# Stage6J PATCH1 Deterministic Replay Evaluation

This stage freezes CPU-only deterministic replay/evaluation for the accepted Stage6I confirmation generations.

No model or GPU is called in this stage. Stage6J consumes the 1924 raw generations from Stage6I and evaluates five deterministic arms over the fixed Stage6E denominator of 481 samples:

- Direct from `direct.jsonl`
- J-FS from `j_fs.jsonl`
- Original MP-FS+ from `original_mp_fs_plus.jsonl`
- D+G1 from `shared_mp_fs_plus_generation.jsonl`
- D+F+G1 from the same `shared_mp_fs_plus_generation.jsonl`

PATCH1 hardens row-level validation and fixes MP-FS+ failure attribution. The 436 previously labeled construction failures in each MP-FS+ arm are now attributed to verification with `pipeline_stage = evidence_materialization`.

Stage6J intentionally does not compute McNemar tests, Holm correction, bootstrap intervals, or any significance statistics. Those belong to Stage6K after reviewer acceptance of the deterministic replay outcomes.

Validation command:

```bash
PYTHONPATH=src python scripts/data/validate_stage6j_replay_evaluation.py --stage6j-dir stage6_replay_evaluation
```
