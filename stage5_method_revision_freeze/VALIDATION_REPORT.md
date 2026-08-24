# Stage 5 PATCH1 Validation Report

Status: PASS

Validation date: 2026-08-24

## Scope

Stage 5 PATCH1 is a CPU-only method-freeze hardening package. No model
inference, GPU execution, dataset selection, gold-label edit, metric edit, or
prior result rewrite was performed.

## Commands

```text
python scripts\analysis\validate_stage5_method_freeze.py
```

Result: PASS

```text
python -m pytest -q tests\test_stage5_method_freeze.py
```

Result: PASS, 7 tests passed.

```text
$env:PYTHONPATH = 'tests\support\windows_py314_pytest_tempdir'
python -m pytest -q -m "not integration" --basetemp pytest_tmp_stage5_freeze
```

Result: PASS.

## Environment note

The first full-suite attempt without the repository's Windows Python 3.14
tempdir shim failed during pytest temp-directory setup with:

```text
PermissionError: [WinError 5] Access is denied
```

No Stage 5 assertion failed in that run. The full suite passed after enabling
the existing local shim at `tests/support/windows_py314_pytest_tempdir`.

## Validator summary

```text
stage = Stage5_METHOD_REVISION_FREEZE_PATCH1
method_name = MP-FS+ vNext-R1
component_set = D,F,G1
model_called = false
gpu_called = false
confirmation_run_allowed_now = false
violations = []
```

## PATCH1 hardening coverage

- Executable freeze manifest present and hash-checked.
- Resolved config present and required for future confirmation runs.
- Overlay, resolved config, base configs, demonstration bank, protocol lock,
  validator, and selected implementation files are SHA-256 anchored.
- Validator rejects deliberate mutations of D/F/G1 method parameters.
- Validator rejects deliberate mutations of model lock, token/context lock,
  prompt builder, statistics lock, and method-edit gate.
- Confirmation arms are locked for Original, D_G1, and D_F_G1, with Direct and
  J-FS included and FULL excluded.
- H1 (`D_F_G1` vs Original) and H2 (`D_F_G1` vs `D_G1`) are pre-specified.
- Output max-token hits remain in the denominator and are scored as system
  behavior under the frozen token budget.
