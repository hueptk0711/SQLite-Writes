# Stage7C-A1 V2 Development Protocol

This package freezes the V2-A1 development protocol after Stage7B-A1. It reuses
the previously validated CRUDSQL source, train/dev Create manifests, gold INSERT
derivation, operation mapping, and contamination audits, while replacing the
superseded deterministic regex semantic-slot protocol with Phase O grounded
offset span selection. PATCH2 additionally locks the joint source-span oracle
ceiling under A1's one-source-span to one-SLOT contract.

Commands:
```bash
python scripts/data/build_stage7c_a1_v2_development_protocol.py --force
python scripts/data/validate_stage7c_a1_v2_development_protocol.py
python scripts/data/audit_stage7c_a1_leakage.py
python -m pytest -q tests/test_stage7c_a1_v2_development_protocol.py
```

No Qwen generation, GPU call, V2 implementation, experiment, 481-test tuning, or
LiveSQLBench ground-truth access is performed in this stage.
