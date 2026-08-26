# Stage6J PATCH1 Validation Report

Validation date: 2026-08-26

## Validator

```text
status = PASS
violations = []
final_confirmation_n = 481
arm_count = 5
model_called = false
gpu_called = false
statistics_computed = false
```

Command:

```bash
PYTHONPATH=src python scripts/data/validate_stage6j_replay_evaluation.py
```

## Tests

```text
tests/test_stage6j_replay_evaluation.py
14 passed
```

Command:

```bash
PYTHONPATH=src python -m pytest tests/test_stage6j_replay_evaluation.py -q
```

## Replay Summary

```text
Direct:
  N = 481
  target_state_correct = 58

J-FS:
  N = 481
  target_state_correct = 50

Original MP-FS+:
  N = 481
  target_state_correct = 0
  failure_stage_counts:
    parse = 2
    verification = 436
    state_mismatch = 43

D+G1:
  N = 481
  target_state_correct = 0
  failure_stage_counts:
    parse = 2
    verification = 436
    state_mismatch = 43

D+F+G1:
  N = 481
  target_state_correct = 0
  failure_stage_counts:
    parse = 2
    verification = 436
    state_mismatch = 43
```

No significance tests were computed in Stage6J.

PATCH1 hardening validates mirrored Stage6I raw roots, outcome file hashes, source raw-row provenance, raw-output hashes, candidate program hashes, frozen Stage6E gold post-state hashes, target-state booleans, H2 shared raw-row identity, summary recomputation, and exact Stage6E denominator coverage.
