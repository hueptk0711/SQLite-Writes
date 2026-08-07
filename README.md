# MP-FS+ code and results

This directory is the canonical technical workspace for the MP-FS+ study. It
contains code, frozen experiment artifacts, protocols, tests, result tables,
and reproducibility evidence. LaTeX and manuscript files are intentionally kept
in the separate `../PAPER_WRITING/` directory.

## 1. Study snapshot

- Evaluation set: 300 samples from 5 databases.
- Compared methods: D-FS-M, J-FS-M, S-FS-v2-M, MP-FS-M, MP-FS+, and Gold-MP.
- Primary target-state correct counts: 258, 258, 78, 34, 148, and 300.
- Primary predictions and headline results are frozen.
- The Qwen-14B, Yi-9B, safety replay, cascade, and downstream analyses are
  explicitly post-hoc robustness or exploratory evidence.

## 2. Canonical layout

- `src/`, `tests/`, `scripts/`, `configs/`: the only active source tree.
- `03_protocol_and_data/`: frozen protocol, holdout, and calibration evidence.
- `04_results/`: imported primary results and derived reporting outputs.
- `07_reproducibility/`: provenance, hashes, and server-run evidence.
- `08_tools/`: release and integrity validators.
- `archive/frozen_inference_source.zip`: read-only copy of the original
  inference-era source; it is provenance, not a second active code tree.
- `09_release_candidate/`: generated reviewer archives and validation reports.

## 3. Supported environment

Python 3.10 through 3.14 is supported. Deterministic reporting uses only the
Python standard library. Pytest is the only dependency required for tests.
SQLite is provided by Python. See `docs/ENVIRONMENT.md` for recorded versions.

## 4. Install from the repository root

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
```

## 5. Fast CPU tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m "not integration"
```

These tests exercise evaluator, parsing, planning, verification, and reporting
logic. They do not load model weights and do not require a GPU.
The current suite contains 140 fast tests; all must pass.

The default command is also supported:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

It runs 140 tests and reports the one opt-in integration test as skipped.

## 6. Clean-release integration test

Build the reviewer archive first, then run the slow integration test:

```powershell
.\.venv\Scripts\python.exe build_reporting_release.py
.\.venv\Scripts\python.exe -m pytest -q -m integration
```

The integration test extracts the archive into a fresh directory, removes an
inherited `PYTHONPATH`, runs the one-command reproduction, checks frozen
anchors, and cleans temporary files. Its subprocess timeout is 1,800 seconds.
Together with the 140 fast tests, the complete suite contains 141 tests.

## 7. One-command deterministic reproduction

```powershell
.\.venv\Scripts\python.exe reproduce_paper.py --artifact final_release
```

To keep generated files separate from the shipped evidence:

```powershell
.\.venv\Scripts\python.exe reproduce_paper.py `
  --artifact final_release `
  --workspace-root . `
  --config .\release_config.json `
  --output-root .\reproduced_outputs
```

Use `--stage corrective` or `--stage exploratory` for a partial rerun. The
program prints stage progress and writes `reproduction_timing.json` with Python,
platform, SQLite, and per-stage timing metadata.
Use `--keep-temp-on-failure` when debugging an exploratory-stage exception.

## 8. Evaluator audit

Strict full-state correctness and off-target detection now compare every
persistent user table in the SQLite database. Quoted identifiers,
schema-qualified names, trigger-generated expected changes, and unrelated-table
side effects have regression coverage. A locked-output replay audited all 1,800
method-sample pairs and found zero differences from the frozen target, strict,
or off-target labels. Evidence is under
`04_results/03_analysis_work/state_scope_audit_20260805/`.

## 9. CPU versus GPU boundary

No GPU is needed for tests, evaluator replay, reporting, figure construction,
or release validation. GPU execution is required only to regenerate model
predictions. That operation is outside this reviewer reproduction and must not
be run when merely rebuilding the paper tables and analyses.

## 10. Reproducibility guarantees

The release builder uses sorted paths, fixed ZIP timestamps, per-file SHA-256
hashes, and a release manifest. The clean-extraction validator rejects unsafe
archive paths and records the archive hash, stage timings, anchor checks, and
cleanup result. Absolute machine paths in historical evidence are provenance
only; active commands use paths relative to this workspace.

## 11. Known limitations

- Primary results cover the frozen 300-sample, 5-database protocol and should
  not be generalized beyond that scope without further experiments.
- Cross-family and larger-model results are post-hoc rather than preregistered.
- The downstream ablation is system-level and does not isolate every internal
  component independently.
- PostgreSQL validation is optional and is not required to reproduce the SQLite
  evaluation reported here.

## 12. Publication metadata and rights

Author names, repository URL/DOI, `CITATION.cff`, code license, and dataset
redistribution permissions remain owner decisions. No license or citation file
is fabricated in this release. Resolve the checklist in
`docs/ASSET_RIGHTS.md` before making the archive public.
