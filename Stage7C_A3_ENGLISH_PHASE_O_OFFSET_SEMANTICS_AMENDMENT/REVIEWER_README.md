# Stage7C A3 English Phase O Offset-Semantics Amendment PATCH1

This package fixes PATCH0 wiring by amending the frozen V2-A1 A2 Phase O prompt
spec rather than the legacy planner prompt path. It keeps the same eight fresh
English questions and locks them as Stage7E0-A3-ready fixtures using TAB/COL/EV/SLOT refs.

Clean extraction commands:

```bash
python scripts/data/validate_stage7c_a3_english_offset_semantics.py --stage-dir Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT
python -m pytest -q tests/test_stage7c_a3_english_offset_semantics.py
```

No GPU is required. No model is called. The Gretel pilot pool is not opened.

Local artifact directory at build time:

```text
Stage7C_A3_ENGLISH_PHASE_O_OFFSET_SEMANTICS_AMENDMENT
```
