# Reproducibility map

The supported entry point is `reproduce_paper.py` at the workspace root. It
rebuilds corrective v2.3 reporting and exploratory v2.4 analyses from immutable
artifacts without running model inference.

Evidence map:

- Primary imported archive and extraction report:
  `04_results/00_incoming_from_server/` and
  `07_reproducibility/server_final_run/IMPORT_REPORT.json`.
- Paper-ready tables: `04_results/02_paper_ready/`.
- Corrective analysis: `04_results/03_analysis_work/reporting_v2_3_20260801/`.
- Exploratory analysis: `04_results/03_analysis_work/reporting_v2_4_20260801/`.
- All-user-table evaluator audit:
  `04_results/03_analysis_work/state_scope_audit_20260805/`.
- Original inference code: `archive/frozen_inference_source.zip` plus its
  adjacent SHA-256 file.
- Clean archive validator: `08_tools/validate_clean_release.py`.

The reporting pipeline is deterministic and CPU-only. Historical absolute paths
are retained solely as provenance; active execution resolves the canonical
workspace passed through `--workspace-root`.
