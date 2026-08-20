# Apply and Validate — Stage 2 A–C Patch 4

Patch 4 is intended for:

```text
branch: stage2/mpfsplus-vnext
base commit: 97885c0c4383059f76392bf6291d1df34ede113c
```

It only hardens the shared free-text instruction/payload boundary.

## Validation commands

```powershell
python -m pytest -q tests/test_stage2_vnext_abc.py
```

Expected: **36 tests pass**.

```powershell
python -m pytest -q `
  tests/test_source_and_planner.py `
  tests/test_mp_fs_plus.py `
  tests/test_stage2_vnext_abc.py
```

Expected: 100%, no `FAILED`.

```powershell
python -m pytest -q -m "not integration"
```

Expected: 100%, no `FAILED`.

```powershell
python .\stage2_mpfsplus_vnext\cpu_smoke_patch3.py
```

Expected:

```json
{"status": "PASS"}
```

No GPU/model run is required.
