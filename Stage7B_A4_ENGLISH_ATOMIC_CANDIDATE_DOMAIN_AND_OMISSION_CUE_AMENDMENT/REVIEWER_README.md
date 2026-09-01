# Stage7B-A4 Atomic Candidate Domain and Omission-Cue Amendment

This package audits a candidate-domain amendment after Stage7E0-A5 was closed
as a corrected valid feasibility failure at 2/12. It compares current
`lexical_ngram2` against an audit-only atomic-filtered domain on the frozen 728
non-pilot design-train samples.

Review order:

1. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/A5_CORRECTED_VALID_FAIL_FREEZE.json`
2. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/DOMAIN_AUDIT_PROTOCOL.json`
3. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/ATOMIC_CANDIDATE_DOMINANCE_RULE_SPEC.json`
4. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/OMISSION_CUE_SUPPRESSION_RULE_SPEC.json`
5. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/CURRENT_LEXICAL_NGRAM2_DOMAIN_AUDIT.json`
6. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/PATCH0_GENERIC_ATOMIC_DOMAIN_AUDIT.json`
7. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/SCHEMA_LABEL_AWARE_DOMAIN_AUDIT.json`
8. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/ATOMIC_FILTERED_DOMAIN_AUDIT.json`
9. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/DOMAIN_COMPARISON_AUDIT.json`
10. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/FALSE_SUPPRESSION_AUDIT.json`
11. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/OMISSION_CUE_DESIGN_TRAIN_AUDIT.json`
12. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/SYNTHETIC_OMISSION_CUE_SAFETY_AUDIT.json`
13. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/CANDIDATE_DOMAIN_AUDIT_ROWS.jsonl`
14. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/CANDIDATE_SUPPRESSION_EXAMPLES.jsonl`
15. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/SOURCE_INPUT_MANIFEST.json`
16. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/DERIVED_ARTIFACT_MANIFEST.json`
17. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/STAGE7B_A4_LOCK.json`
18. `Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT/VALIDATION_REPORT.md`
19. `scripts/data/build_stage7b_a4_atomic_candidate_domain_omission_cue.py`
20. `scripts/data/validate_stage7b_a4_atomic_candidate_domain_omission_cue.py`
21. `tests/test_stage7b_a4_atomic_candidate_domain_omission_cue.py`

Clean extraction commands:

```bash
python scripts/data/validate_stage7b_a4_atomic_candidate_domain_omission_cue.py \
  --stage-dir Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT
python -m pytest -q tests/test_stage7b_a4_atomic_candidate_domain_omission_cue.py
```

Full rebuild requires the local Gretel parquet source:

```bash
uv run --with pyarrow python scripts/data/build_stage7b_a4_atomic_candidate_domain_omission_cue.py \
  --raw-dir /path/to/gretel_synthetic_text_to_sql_740ab236
python scripts/data/validate_stage7b_a4_atomic_candidate_domain_omission_cue.py \
  --stage-dir Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT \
  --raw-dir /path/to/gretel_synthetic_text_to_sql_740ab236 \
  --rebuild
```

No GPU is required. No model is called. Gretel pilot/dev/test rows remain
closed.

Local artifact directory at build time:

```text
D:\paper kltn\text to sql\github_publish\SQLite-Writes\Stage7B_A4_ENGLISH_ATOMIC_CANDIDATE_DOMAIN_AND_OMISSION_CUE_AMENDMENT
```
