# Apply and Validate — Stage 2 A–C Patch 3

Patch 3 is intended for branch `stage2/mpfsplus-vnext` at Patch-2 commit `b8c8812`.

After applying the patch, run:

```powershell
python -m pytest -q tests/test_stage2_vnext_abc.py
python -m pytest -q tests/test_source_and_planner.py tests/test_mp_fs_plus.py tests/test_stage2_vnext_abc.py
python -m pytest -q -m "not integration"
python .\stage2_mpfsplus_vnext\cpu_smoke_patch3.py
```

Expected: 30 A–C tests pass, compatibility/full fast suites have no failures, and CPU smoke prints `"status": "PASS"`.

Do not run D–G or 7B before reviewer acceptance of Patch 3.
