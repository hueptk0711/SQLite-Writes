# Apply and validate Stage 2 A–C

## 1. Start from the frozen Stage-1 baseline

Use the actual tag name in your repository. Example:

```powershell
git status
git switch -c stage2/mpfsplus-vnext stage1.1-final
```

If the Stage-2 branch already exists, switch to it instead.

## 2. Apply the patch

```powershell
git apply --check "C:\Users\KIM HUE\Downloads\stage2_mpfsplus_vnext_ABC.patch"
git apply "C:\Users\KIM HUE\Downloads\stage2_mpfsplus_vnext_ABC.patch"
```

`git apply --check` must complete without an error before applying.

## 3. Run checkpoint tests

```powershell
python -m pytest -q tests/test_stage2_vnext_abc.py
```

Expected: 13 tests pass.

Then run the fast repository suite:

```powershell
python -m pytest -q -m "not integration"
```

Expected: 100%, no FAILED tests.

## 4. Do not run 7B yet

This is only checkpoint A–C. Do not run a new LLM experiment, causal replay, parser-v2, reference repair, or targeted repair before review of this checkpoint.

## 5. Stage only Stage-2 A–C files

```powershell
git add `
  configs/stage2 `
  src/nldbwrite_v3/vnext `
  src/nldbwrite_v3/planner/materialize.py `
  src/nldbwrite_v3/planner/references.py `
  src/nldbwrite_v3/planner/evidence.py `
  src/nldbwrite_v3/verifier/verify.py `
  src/nldbwrite_v3/pipeline.py `
  src/nldbwrite_v3/experiments/run_method.py `
  src/nldbwrite_v3/ir/models.py `
  src/nldbwrite_v3/compiler/compiler.py `
  tests/test_stage2_vnext_abc.py `
  stage2_mpfsplus_vnext
```

Review what will be committed:

```powershell
git diff --cached --name-status
git diff --cached --stat
```

## 6. Commit and push

```powershell
git commit -m "feat(stage2): add ablatable MP-FS+ vNext interventions A-C"
git push -u origin stage2/mpfsplus-vnext
```

## 7. Reviewer checkpoint

Send the branch or the checkpoint ZIP for review before implementing D–G.
