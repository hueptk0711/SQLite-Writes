# Stage 5 PATCH2 Validation Report

Status: PASS

Validation date: 2026-08-24

## Scope

Stage 5 PATCH2 is a CPU-only confirmation-protocol hardening package. No model
inference, GPU execution, dataset selection, gold-label edit, metric edit, or
prior result rewrite was performed.

## Commands

```text
python scripts\analysis\validate_stage5_method_freeze.py
```

Result: PASS

```text
git status --porcelain
python scripts\analysis\validate_stage5_method_freeze.py --require-accepted-tag
```

Result: PASS from clean detached worktree at tag
`stage5-vnext-r1-freeze-patch2`.

```text
python -m pytest -q tests\test_stage5_method_freeze.py
```

Result: PASS, 13 tests passed.

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
stage = Stage5_METHOD_REVISION_FREEZE_PATCH2
method_name = MP-FS+ vNext-R1
component_set = D,F,G1
model_called = false
gpu_called = false
confirmation_run_allowed_now = false
violations = []
```

## PATCH2 hardening coverage

- Direct, J-FS, Original MP-FS+, D_G1, and D_F_G1 have exact resolved config
  paths and SHA-256 hashes.
- D_G1 and D_F_G1 are collapsed into one `shared_mp_fs_plus_generation` arm with
  two deterministic replay configs.
- The accepted method freeze commit is anchored to
  `79f6a82144ec0407444ef37121f70eed2b20e01c`.
- The Stage4 validated inference/environment locks are anchored for future GPU
  preflight.
- Validator rejects deliberate mutations of comparator config path/hash,
  accepted commit, environment lock, and shared-generation identity.
