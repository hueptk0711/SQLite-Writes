# MP-FS+ review checklist

| Review item | Status | Evidence |
|---|---|---|
| Preserve canonical v3 release | Complete | Work occurs only in `mp_fs_plus`; `paper_v3_release_20260726` is unchanged |
| Treat 677 as consumed diagnostic evidence | Complete | README and final protocol explicitly prohibit untouched-holdout claims |
| Source collection/selector IDs | Complete | `source_parser/parser.py`, `planner/references.py` |
| Table/column/constraint IDs | Complete | `schema/profile.py`, `schema/prompt.py` |
| Candidate column IDs plus `NONE` | Complete | `ranked_column_candidates`, `collection_grounding` |
| Free-text evidence IDs | Complete | `planner/evidence.py` |
| Lossless normalization allow-list and audit | Complete | `compiler/normalization.py` |
| Transactional preflight | Complete | `compiler/executor.py::preflight_program` |
| LLM repair excluded from final method | Complete | `configs/final/mp_fs_plus.json`; final matrix omits repair |
| Matched 4+2 semantic bank | Complete | `configs/demonstrations/matched_semantic_bank.json` |
| Matched final method configs | Complete | `configs/final/` |
| Selective reliability metrics | Complete | coverage, accepted accuracy, abstention, risk in `experiments/metrics.py` |
| Database-macro metric and bootstrap | Complete | `experiments/metrics.py`, `analysis/statistics.py` |
| Exact McNemar and family-wide Holm correction | Complete | `analysis/statistics.py` |
| External-holdout metadata gate | Complete in code | `data/external_holdout.py`, audit script and templates |
| Calibration protocol | Template ready; data pending | `configs/experiments/calibration_protocol.template.json` |
| Final 300-request holdout | Pending independent authorship | Must use 3-5 unseen databases and two QA reviewers |
| Second model subset | Pending GPU/model selection | 150 stratified final samples after primary freeze |
| Code license | Pending owner decision | Do not invent a license without rights confirmation |
| Dataset/database licenses and provenance | Pending data selection | Required before redistribution |
| Persistent DOI | Pending final artifact | Create only after data/code freeze |
| Unit tests | Complete | 83/83 pass; no PytestReturnNotNone warnings |
| Final empirical claims | Pending external runs | No MP-FS+ result has been fabricated |

The implementation and protocol scaffolding are ready for data construction
and calibration. The paper is not ready for IEEE Access submission until the
external holdout, full matched matrix, second-model subset, statistical family,
licenses/provenance, and final artifact freeze are complete.
