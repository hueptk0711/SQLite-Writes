# Stage7C V2 Development/Data Protocol

This package freezes data provenance, CRUDSQL train/dev Create manifests,
adapter and leakage policies, slot-inventory construction policy, selection
rules, and evaluation environment before V2 implementation.

Commands:
```bash
python scripts/data/validate_stage7c_v2_development_data_protocol.py
python -m pytest -q tests/test_stage7c_v2_development_data_protocol.py
```

No model, GPU, V2 implementation, V2 generation, 481-test tuning, or
LiveSQLBench ground-truth access is performed in Stage7C.
