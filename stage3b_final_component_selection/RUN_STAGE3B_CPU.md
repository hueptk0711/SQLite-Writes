# Reproduce Stage 3B on CPU

From the repository root:

```powershell
python scripts/analysis/run_stage3b_component_selection.py `
  --dataset-archive 03_protocol_and_data/final_holdout_release/mp_fs_plus_external_holdout_300_20260731.zip `
  --result-archive 04_results/00_incoming_from_server/mp_fs_plus_final300_protocol_v2_1_rev2_adjudicated_20260731T121531Z.tar.gz `
  --stage3-root stage3_full_causal_replay `
  --output-dir stage3b_reproduced

python scripts/analysis/validate_stage3b_component_selection.py `
  --results-root stage3b_reproduced
```

The output directory must be absent or empty. The runner verifies frozen archive hashes before extracting or evaluating any sample. It does not load or call a model and does not require GPU/SSH.
