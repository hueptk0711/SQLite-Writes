# Stage7E0-A5 English Column-Conditioned Real Generation Preflight PATCH3

This reviewer package prepares the UET server RTX 4090 primary GPU run for the
12 locked Stage7C-A5 primary cases. It does not open Gretel, development-dev, or
official test rows. A5 uses one model call only; Phase M is removed.

Clean extraction checks:

```bash
python scripts/data/validate_stage7c_a5_column_conditioned_phase_o_protocol.py --stage-dir Stage7C_A5_ENGLISH_COLUMN_CONDITIONED_PHASE_O_PROTOCOL_FREEZE
python scripts/data/validate_stage7e0_a5_english_preflight.py --stage-dir Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT
python -m pytest -q tests/test_stage7e0_a5_english_preflight.py
```

UET server commands are in `Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT/SERVER_RUN_COMMANDS.md`.

Package: `Stage7E0_A5_ENGLISH_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT_PATCH3_FINAL_REVIEWER_PACKAGE_20260901.zip`

Accepted Stage7C-A5 protocol commit: `1b68ef5ff1bfdc52de05da7ae6fd96857c783f63`
