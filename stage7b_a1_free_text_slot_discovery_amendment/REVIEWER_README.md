# Stage7B A1 Free-Text Slot Discovery Amendment

This reviewer package amends Stage7B before any V2 implementation or experiment.
It does not call a model or GPU. It uses Stage7C PATCH2 artifacts only as the
empirical trigger showing that deterministic regex slot discovery is not viable
under the frozen typed materialization contract.

Commands:
```bash
python scripts/data/build_stage7b_a1_free_text_slot_discovery_amendment.py --force
python scripts/data/validate_stage7b_a1_free_text_slot_discovery_amendment.py
python -m pytest -q tests/test_stage7b_a1_free_text_slot_discovery_amendment.py
```
