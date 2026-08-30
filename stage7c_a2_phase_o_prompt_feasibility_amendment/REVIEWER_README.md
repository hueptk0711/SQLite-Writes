# Stage7C-A2 Phase O Prompt Feasibility Amendment

This package opens a narrow prompt-protocol amendment after Stage7E0 PATCH9
showed that the constrained-generation backend is label-independent,
incremental, non-enumerative, and scalable, while the frozen zero-shot Phase O
prompt still fails atomic semantic span selection.

Scope:
- Revise Phase O prompt wording to define atomic, smallest, verbatim value spans.
- Keep Phase M, schemas, model, backend, materializer, compiler, datasets, gold labels, metrics, and protocol architecture unchanged.
- Lock four fresh synthetic smoke cases before any model/GPU run.
- Do not run Qwen, GPU generation, train/dev generation, 481 confirmation, or LiveSQLBench.

Commands:
```bash
python scripts/data/build_stage7c_a2_phase_o_prompt_amendment.py --force
python scripts/data/validate_stage7c_a2_phase_o_prompt_amendment.py
python -m pytest -q tests/test_stage7c_a2_phase_o_prompt_amendment.py
```

After reviewer approval, the next separate stage should run a Stage7E0-A2 real
generation preflight with the PATCH9 backend and the locked A2 Phase O prompt.
