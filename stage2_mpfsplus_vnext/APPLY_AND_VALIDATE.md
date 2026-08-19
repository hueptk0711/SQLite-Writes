# Apply and validate Stage 2 A–C Patch 2

Patch 2 must be applied **after** commit `082d76f` (`feat(stage2): add ablatable MP-FS+ vNext interventions A-C`) on branch `stage2/mpfsplus-vnext`.

```powershell
git branch --show-current
git rev-parse HEAD

git apply --check "C:\Users\KIM HUE\Downloads\stage2_mpfsplus_vnext_ABC_patch2_082d76f.patch"
git apply "C:\Users\KIM HUE\Downloads\stage2_mpfsplus_vnext_ABC_patch2_082d76f.patch"

python -m pytest -q tests/test_stage2_vnext_abc.py
python -m pytest -q `
  tests/test_source_and_planner.py `
  tests/test_mp_fs_plus.py `
  tests/test_stage2_vnext_abc.py
python -m pytest -q -m "not integration"
```

Expected: Patch-2 A–C tests pass (20 tests), compatibility subset reaches 100%, and the full fast suite reaches 100% with no `FAILED`.

Do not run D–G or 7B before reviewer acceptance of this patch.
