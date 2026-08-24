# Stage 5 Reviewer README

This package freezes the revised method only. It does not call a model, does
not select or edit a dataset, and does not report new evaluation results.

Review order:

1. `METHOD_FREEZE.md`
2. `CONFIRMATION_PROTOCOL_LOCK.json`
3. `../configs/stage5/mp_fs_plus_vnext_r1.json`
4. `../scripts/analysis/validate_stage5_method_freeze.py`
5. `../tests/test_stage5_method_freeze.py`
6. `VALIDATION_REPORT.md`

The central reviewer question is whether `MP-FS+ vNext-R1 = D+F+G1` is frozen
cleanly before any new confirmation run. Stage 4 is explicitly marked as
diagnostic-used evidence for this revised method, not confirmatory evidence.

CPU validation command:

```bash
python scripts/analysis/validate_stage5_method_freeze.py
python -m pytest -q tests/test_stage5_method_freeze.py
```

Full fast-suite command:

```bash
python -m pytest -q -m "not integration"
```
