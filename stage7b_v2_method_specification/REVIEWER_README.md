# Stage7B V2 Method Specification

This package freezes the V2 method specification only. It contains no V2 implementation, no model call, no GPU call, and no experiment results.

Commands:
```bash
python scripts/data/validate_stage7b_v2_method_specification.py
python -m pytest -q tests/test_stage7b_v2_method_specification.py
```

Stage7B uses Stage7A PATCH1 artifacts as locked evidence and separates direct support from indicative support.
