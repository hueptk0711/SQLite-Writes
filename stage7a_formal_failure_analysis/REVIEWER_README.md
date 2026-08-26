# Stage7A Formal Failure Analysis

This package analyzes frozen V1 failures only. It does not implement V2.

PATCH1 separates direct verifier causal evidence from observed parse/state mismatch evidence.

Commands:
```bash
python scripts/data/build_stage7a_formal_failure_analysis.py --force
python scripts/data/validate_stage7a_formal_failure_analysis.py
python -m pytest -q tests/test_stage7a_formal_failure_analysis.py
```

Representative arm: `d_f_g1_vnext`

Hash policy: `text_sha256_canonical_lf`.

Frozen scope:
- no model calls
- no GPU calls
- no prompt or heuristic changes
- no sample, gold, or replay changes
