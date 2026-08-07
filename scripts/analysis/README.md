# Analysis scripts

- `analyze_source_formats.py`: mode, format, row-count, and collection-loss
  analysis.
- `review_added_samples.py`: machine review of snapshot-only samples.
- `select_dev_pilot.py`: deterministic stratified development split.
- `compare_runs.py`: paired McNemar, clustered bootstrap, and Holm-adjusted
  comparison.

All analysis scripts consume immutable data/run artifacts and do not modify
predictions.
