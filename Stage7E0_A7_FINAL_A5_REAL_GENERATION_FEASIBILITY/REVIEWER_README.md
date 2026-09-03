# Stage7E0-A7 Final A5 Real-Generation Feasibility

This package freezes the A7 one-call protocol before model execution. It uses
the final Stage7B-A5 candidate-domain rules inside the A6 one-call architecture.

Local validation:

```bash
python scripts/data/validate_stage7e0_a7_final_a5_real_generation_feasibility.py --stage-dir Stage7E0_A7_FINAL_A5_REAL_GENERATION_FEASIBILITY
python -m pytest -q tests/test_stage7e0_a7_final_a5_real_generation_feasibility.py
```

Server official run:

```bash
bash Stage7E0_A7_FINAL_A5_REAL_GENERATION_FEASIBILITY/SERVER_RUN_COMMANDS.sh
```

The Markdown command file is documentation only. Run `SERVER_RUN_COMMANDS.sh`.
