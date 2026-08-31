# Stage7C-A4 English Candidate-Span Phase O Protocol

This package freezes the actual Phase O prompt/schema/output contract for the
Stage7B-A2 candidate-span reference architecture.

Review order:

1. `Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL/PHASE_O_SPAN_REF_OUTPUT_SPEC.json`
2. `Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL/PHASE_O_PROMPT_SPEC_A4_ENGLISH.json`
3. `Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL/PHASE_O_RUNTIME_SCHEMA_SPEC.json`
4. `Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL/CANDIDATE_SERIALIZATION_FREEZE.json`
5. `Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL/FULL_RENDERED_PROMPT_TOKEN_AUDIT.json`
6. `Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL/FRESH_ENGLISH_A4_SPAN_REF_FEASIBILITY_SET.jsonl`
7. `Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL/ORACLE_SPAN_REF_PATH_RESULTS.jsonl`
8. `Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL/ACCEPTANCE_POLICY_A4.json`
9. `Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL/CANDIDATE_MISS_FAILURE_POLICY.json`
10. `Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL/SOURCE_INPUT_MANIFEST.json`
11. `Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL/DERIVED_ARTIFACT_MANIFEST.json`
12. `Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL/STAGE7C_A4_LOCK.json`
13. `Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL/VALIDATION_REPORT.md`
14. `scripts/data/build_stage7c_a4_candidate_span_phase_o_protocol.py`
15. `scripts/data/validate_stage7c_a4_candidate_span_phase_o_protocol.py`
16. `tests/test_stage7c_a4_candidate_span_phase_o_protocol.py`

Clean extraction commands:

```bash
python scripts/data/validate_stage7c_a4_candidate_span_phase_o_protocol.py \
  --stage-dir Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL
python -m pytest -q tests/test_stage7c_a4_candidate_span_phase_o_protocol.py
```

No GPU is required. No model is called. The Gretel pilot pool remains closed.

Local artifact directory at build time:

```text
D:\paper kltn\text to sql\github_publish\SQLite-Writes\Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL
```
