# Stage6K Frozen Statistical Analysis

This package closes the V1 confirmatory statistical analysis from frozen Stage6J replay outcomes.

Scope:
- No model calls.
- No GPU calls.
- No new generations.
- No gold, denominator, metric, or hypothesis changes.
- Confirmatory family contains only H1 and H2.

Commands:
```bash
python scripts/data/build_stage6k_frozen_statistics.py --force
python scripts/data/validate_stage6k_frozen_statistics.py
pytest tests/test_stage6k_frozen_statistics.py
```

Primary metric: `target_state_correct`

Confirmatory hypotheses:
- H1: D+F+G1 vs Original MP-FS+
- H2: D+F+G1 vs D+G1

Bootstrap protocol:
- cluster key: `source_group`
- seed: `240824`
- replicates: `10000`
- CI level: `0.95`
