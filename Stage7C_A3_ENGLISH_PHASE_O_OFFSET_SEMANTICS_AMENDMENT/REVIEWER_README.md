# Stage7C A3 English Phase O Offset-Semantics Amendment

This package freezes a narrow Phase O prompt wording amendment and 8 fresh
English synthetic smoke cases. It does not include or use Gretel pilot rows.

Review order:

1. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/PHASE_O_PROMPT_AMENDMENT.md`
2. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/PHASE_O_PROMPT_AUDIT.json`
3. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/FRESH_ENGLISH_SYNTHETIC_CASES.jsonl`
4. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/PHASE_O_EXPECTED_SPANS.jsonl`
5. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/PHASE_M_EXPECTED_MAPPINGS.jsonl`
6. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/TYPED_TARGET_STATES.jsonl`
7. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/SYNTHETIC_SQLITE_DB_MANIFEST.jsonl`
8. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/STAGE7C_SMOKE_LOCK.json`
9. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/DERIVED_ARTIFACT_MANIFEST.json`
10. `src/nldbwrite_v3/planner/prompt.py`
11. `scripts/data/build_stage7c_a3_english_offset_semantics.py`
12. `scripts/data/validate_stage7c_a3_english_offset_semantics.py`
13. `tests/test_stage7c_a3_english_offset_semantics.py`
14. `scripts/analysis/validate_stage5_method_freeze.py`
15. `Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT/VALIDATION_REPORT.md`

Rerun:

```bash
python scripts/data/build_stage7c_a3_english_offset_semantics.py \
  --out-dir Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT \
  --package Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT_PATCH0_FINAL_REVIEWER_PACKAGE_20260830.zip
python scripts/data/validate_stage7c_a3_english_offset_semantics.py \
  --stage-dir Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT
python -m pytest -q tests/test_stage7c_a3_english_offset_semantics.py
```

No GPU is required. No model is called.

Local artifact directory at build time:

```text
Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT
```
