# Stage6J Validation Report

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
5 passed
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

D+G1:
  N = 481
  target_state_correct = 0

D+F+G1:
  N = 481
  target_state_correct = 0
```

No significance tests were computed in Stage6J.
