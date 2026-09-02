# Stage7E0-A6 English Atomic-Domain Column-Conditioned Real Generation Preflight PATCH0

This reviewer package prepares the UET server RTX 4090 primary GPU run for the
12 locked Stage7C-A6 primary cases. It does not open Gretel, development-dev, or
official test rows. A6 uses one model call only; Phase M is removed.

Clean extraction checks:

```bash
python scripts/data/validate_stage7c_a6_atomic_domain_column_conditioned_protocol_freeze.py --stage-dir Stage7C_A6_ENGLISH_ATOMIC_DOMAIN_COLUMN_CONDITIONED_PROTOCOL_FREEZE
python scripts/data/validate_stage7e0_a6_english_preflight.py --stage-dir Stage7E0_A6_ENGLISH_ATOMIC_DOMAIN_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT
python -m pytest -q tests/test_stage7e0_a6_english_preflight.py
```

UET server commands are in `Stage7E0_A6_ENGLISH_ATOMIC_DOMAIN_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT/SERVER_RUN_COMMANDS.sh`. The companion
Markdown file `Stage7E0_A6_ENGLISH_ATOMIC_DOMAIN_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT/SERVER_RUN_COMMANDS.md` is documentation and also
delegates to the shell script if run with `bash`.

Package: `Stage7E0_A6_ENGLISH_ATOMIC_DOMAIN_COLUMN_CONDITIONED_REAL_GENERATION_PREFLIGHT_PATCH0_FINAL_REVIEWER_PACKAGE_20260902.zip`

Accepted Stage7C-A6 protocol commit: `e1f4b4b73fdaeb6a2235c1d96e4928ce8736bc49`
