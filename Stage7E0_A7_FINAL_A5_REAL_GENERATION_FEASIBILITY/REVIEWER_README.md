# Stage7E0-A7 Final A5 Real-Generation Feasibility

This package freezes the A7 one-call protocol before model execution. It uses
the final Stage7B-A5 candidate-domain rules inside the A6 one-call architecture.

Freeze-only validation:

```bash
python scripts/data/validate_stage7e0_a7_final_a5_real_generation_feasibility.py --stage-dir Stage7E0_A7_FINAL_A5_REAL_GENERATION_FEASIBILITY --skip-bundled-official-result
python -m pytest -q tests/test_stage7e0_a7_final_a5_real_generation_feasibility.py
```

Bundled official-result validation:

```bash
python scripts/data/validate_stage7e0_a7_final_a5_real_generation_feasibility.py --stage-dir Stage7E0_A7_FINAL_A5_REAL_GENERATION_FEASIBILITY
```

Server official run:

```bash
bash Stage7E0_A7_FINAL_A5_REAL_GENERATION_FEASIBILITY/SERVER_RUN_COMMANDS.sh
```

The Markdown command file is documentation only. Run `SERVER_RUN_COMMANDS.sh`.

Official UET RTX4090 result:

```text
target_state_accuracy=7/12
status=FAIL
model_calls_total=12
model_calls_per_sample=1
phase_m_invocations=0
retry_count=0
```

The official result archive and extracted raw outputs are under
`Stage7E0_A7_FINAL_A5_REAL_GENERATION_FEASIBILITY/official_results/`. The frozen gate remains 12/12, so a 7/12 run
is an official FAIL result, not a package/test failure to be repaired by rerun.
Running the bundled official-result validator without
`--skip-bundled-official-result` is expected to return FAIL with
`official_target_state_accuracy=7/12`.

