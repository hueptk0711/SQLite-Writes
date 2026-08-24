# Stage 5 Reviewer README

This PATCH2 package hardens the confirmation comparator protocol around the
already accepted `D+F+G1` method freeze. It does not call a model, does not
select or edit a dataset, and does not report new evaluation results.

Review order:

1. `METHOD_FREEZE.md`
2. `CONFIRMATION_PROTOCOL_LOCK.json`
3. `CONFIRMATION_ARM_CONFIGS.json`
4. `CONFIRMATION_ENVIRONMENT_LOCK.json`
5. `EXECUTABLE_FREEZE_MANIFEST.json`
6. `../configs/stage5/resolved_direct_confirmation.json`
7. `../configs/stage5/resolved_j_fs_confirmation.json`
8. `../configs/stage5/resolved_original_mp_fs_plus.json`
9. `../configs/stage5/resolved_d_g1_control.json`
10. `../configs/stage5/resolved_mp_fs_plus_vnext_r1.json`
11. `../scripts/analysis/validate_stage5_method_freeze.py`
12. `../tests/test_stage5_method_freeze.py`
13. `VALIDATION_REPORT.md`

The central reviewer question is whether every confirmatory arm now has one
exact resolved executable config, SHA-256 hash, and generation/replay policy
before any new confirmation run. The accepted method freeze remains
`79f6a82144ec0407444ef37121f70eed2b20e01c`.

CPU validation command:

```bash
python scripts/analysis/validate_stage5_method_freeze.py
python -m pytest -q tests/test_stage5_method_freeze.py
```

Full fast-suite command:

```bash
python -m pytest -q -m "not integration"
```
