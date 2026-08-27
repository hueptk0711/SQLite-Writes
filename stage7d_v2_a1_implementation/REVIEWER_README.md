# Stage7D V2-A1 Implementation

This package implements the locked V2-A1 protocol as executable Python modules with synthetic fixtures and mocked LLM outputs only.

Run:

```bash
python scripts/data/build_stage7d_v2_a1_implementation.py --force
python scripts/data/validate_stage7d_v2_a1_implementation.py
python -m pytest -q tests/v2_a1/test_stage7d_v2_a1.py
```

Stage7D intentionally does not run Qwen, GPU generation, train/dev evaluation, ablations, the 481 confirmation set, or LiveSQLBench.
