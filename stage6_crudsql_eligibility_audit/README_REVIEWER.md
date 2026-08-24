# Stage 6A CRUDSQL Eligibility Audit

Status: PASS for eligibility audit; not registered as the final confirmation
dataset in this package.

This package is CPU-only. It does not call Qwen, does not run GPU inference,
does not translate or paraphrase Chinese questions, and does not create new
data. It audits whether the public CRUDSQL official test split is eligible for
Stage 6B confirmation-dataset registration.

## Source Pin

```text
Repository: https://github.com/bizard-lab/CRUDSQL.git
Commit:     63bfce67d8391185453a812751e115a499201363
License:    GPL-3.0
```

The source clone used for the audit was outside the project repository:

```text
D:\paper kltn\text to sql\external_sources\CRUDSQL_63bfce67
```

The package records upstream file hashes in:

```text
artifacts/crudsql_source_file_hashes.json
```

## Audit Result

Official split counts:

```text
train: 7040 total; 1760 Create / 1760 Delete / 1760 Update / 1760 Read
dev:    960 total;  240 Create /  240 Delete /  240 Update /  240 Read
test:  2000 total;  500 Create /  500 Delete /  500 Update /  500 Read
```

Stage 6A only considers official test `type=0` examples. All 500 test `type=0`
examples compile into deterministic SQLite INSERT operations and execute on an
in-memory copy of `test.db` with one-row state increments.

Recommendation after reviewer acceptance:

```text
Use all 500 eligible official test type=0 examples.
Do not randomly sample down to 300.
Do not use train/dev.
Do not translate or paraphrase questions.
```

Claim boundary:

```text
external generalization to a public Chinese single-table SQLite insert benchmark
```

## Review Order

1. `CANDIDATE_SOURCE_REGISTRY.json`
2. `STAGE6A_DECISION.json`
3. `artifacts/crudsql_eligibility_audit.json`
4. `artifacts/crudsql_overlap_audit.json`
5. `artifacts/stage6_sample_size_sensitivity.json`
6. `artifacts/crudsql_official_test_type0_ids.txt`
7. `scripts/data/audit_crudsql_stage6a.py`
8. `tests/test_stage6_crudsql_eligibility.py`
9. `VALIDATION_REPORT.md`

## Rerun

```bash
git clone https://github.com/bizard-lab/CRUDSQL.git /path/to/CRUDSQL
cd /path/to/CRUDSQL
git checkout 63bfce67d8391185453a812751e115a499201363

cd /path/to/SQLite-Writes
python scripts/data/audit_crudsql_stage6a.py \
  --crudsql-root /path/to/CRUDSQL \
  --out-dir stage6_crudsql_eligibility_audit
PYTHONPATH=tests/support/windows_py314_pytest_tempdir \
python -m pytest -q tests/test_stage6_crudsql_eligibility.py \
  --basetemp pytest_tmp_stage6a_fresh
```

On Windows PowerShell, use:

```powershell
$env:PYTHONPATH = "tests\support\windows_py314_pytest_tempdir"
python -m pytest -q tests\test_stage6_crudsql_eligibility.py --basetemp pytest_tmp_stage6a_fresh
```
