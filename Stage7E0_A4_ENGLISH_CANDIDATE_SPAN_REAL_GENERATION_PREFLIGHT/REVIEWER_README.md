# Stage7E0-A4 English Candidate-Span Real Generation Preflight PATCH0

This reviewer package prepares the GPU run for the 10 locked Stage7C-A4
candidate-span cases. It does not open Gretel, development, or official test
sets. Phase O emits only `operation` and `span_refs`; Phase M is unchanged.

Clean extraction checks:

```bash
python scripts/data/validate_stage7c_a4_candidate_span_phase_o_protocol.py --stage-dir Stage7C_A4_ENGLISH_CANDIDATE_SPAN_PHASE_O_PROTOCOL
python scripts/data/validate_stage7e0_a4_english_preflight.py --stage-dir Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT
python -m pytest -q tests/test_stage7e0_a4_english_preflight.py
```

Server commands are in:

```text
Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT/SERVER_RUN_COMMANDS.md
```

Package:

```text
Stage7E0_A4_ENGLISH_CANDIDATE_SPAN_REAL_GENERATION_PREFLIGHT_PATCH0_FINAL_REVIEWER_PACKAGE_20260831.zip
```

Accepted protocol commit:

```text
8512fbd42886934648c64aa867c710ba48faa827
```
