# Recorded environment

Supported Python versions: 3.10-3.14. Reporting uses the Python standard
library; tests require pytest 8.x.

The final local validation record is written after the test run to
`09_release_candidate/LOCAL_VALIDATION_REPORT_20260805.json`. The reproduction
also writes `reproduction_timing.json`, recording the exact Python, platform,
SQLite, and stage timings used for that invocation.

GPU, CUDA, PyTorch, Transformers, and model weights are not required for the
reviewer reproduction. They are relevant only to prediction regeneration.
