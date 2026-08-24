# Stage 3B — Prompt-Surface Audit and Final Component Selection

## Scope

This package is a CPU-only, fixed-generation development analysis. It calls no model, creates no generation, and does not modify the frozen A–G2 implementations, dataset, labels, evaluator, metrics, or Stage 3 results.

Four hypothesis-driven candidates were specified before replay:

| Candidate | Enabled components |
| --- | --- |
| `FULL` | A+B+C+D+E+F+G1+G2 |
| `NO_C` | A+B+D+E+F+G1+G2 |
| `D_ONLY` | D |
| `D_G1` | D+G1 |

All candidates reuse the same frozen original MP-FS+ raw generations. Therefore candidate results are conditional deterministic replay results, not fresh end-to-end model effects.

## Suggested review order

1. `results/prompt_surface_summary.csv`
2. `results/prompt_equivalence_matrix.csv`
3. `results/candidate_metrics.csv`
4. `results/candidate_rescue_regression.csv`
5. `results/candidate_sample_level.csv`
6. `results/candidate_subgroup_metrics.csv`
7. `results/candidate_false_accept_ids.csv`
8. `configs/`, `validation/`, and `provenance/`

## Main observed results

| Candidate | Correct | Accuracy | Coverage | Accepted accuracy | False accept | Rescue | Regression |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `FULL` | 179 | 59.67% | 63.33% | 94.21% | 11 | 34 | 3 |
| `NO_C` | 182 | 60.67% | 65.00% | 93.33% | 13 | 34 | 0 |
| `D_ONLY` | 182 | 60.67% | 63.67% | 95.29% | 9 | 34 | 0 |
| `D_G1` | 183 | 61.00% | 63.67% | 95.81% | 8 | 35 | 0 |

`D_G1` is the strongest development candidate under the reviewer's stated ordering, but this package does not freeze that choice. Final selection remains a reviewer decision.

## Prompt-surface audit

- V0–V3 are prompt-identical for all 300 samples.
- V3→V4 changes 39/300 prompts, all 39 semi-structured and zero free-text.
- V4–V8 are prompt-identical for all 300 samples.
- All four Stage 3B candidate prompt surfaces equal V4 sample-by-sample.

The Stage 3 D result must therefore be described as a fixed-generation conditional deterministic replay effect. A fresh end-to-end experiment must generate the vNext arm using the D-enabled prompt surface.

## Frozen identities

- Stage 3 base/result commit: `671a0958ae8271c17ca68d9100c7b34c4aa7dbe0`
- Stage 3B replay code commit: `11c036b` (full SHA in `provenance/run_lock.json`)
- Frozen G2 tag/commit: `Stage2-G2-FINAL` / `b752867312727e9932dcf48af99c02b4b2af36cf`
- Dataset SHA-256: `525cdd7006ea32a8ab8d81f842332ac9b403dce2472cde608efb4e6962d456df`
- Result archive SHA-256: `e456037422281d56e03dd7766baf1cc9efa78a95061234444c452f3c04810911`
- Raw generations SHA-256: `bf6e5365efd532c9e181ec77272c695dd22b49b9071dc83faa962b6528f24925`

See `RUN_STAGE3B_CPU.md` for exact reproduction commands. No GPU or SSH server is required for this stage.
