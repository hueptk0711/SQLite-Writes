# Start here

This is the technical workspace only; manuscript and LaTeX assets are in
`../PAPER_WRITING/`.

1. Read `README.md` for installation, tests, and reproduction commands.
2. Read `EXPERIMENT_FREEZE.md` before changing any frozen artifact.
3. Use the root `src/`, `tests/`, `scripts/`, and `configs/` directories as the
   only active source tree.
4. Treat `archive/frozen_inference_source.zip` as read-only provenance.
5. Use `04_results/02_paper_ready/` and the evidence indexed in
   `docs/REPRODUCIBILITY.md` when writing the paper.

Reporting and validation are CPU-only. Do not use a GPU unless model predictions
must be regenerated under a separately declared experiment.
