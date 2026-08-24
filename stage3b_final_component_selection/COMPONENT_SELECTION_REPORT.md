# Stage 3B Component-Selection Report

## Interpretation boundary

These 300 diagnostic samples informed A–G2 and are development data. The replay holds raw generations fixed. It estimates deterministic post-generation behavior conditional on those generations; it does not estimate the full end-to-end causal effect of changing prompt surface D.

## Prompt audit

Exactly one adjacent boundary changes prompts:

| Boundary | Changed | Same | Changed input type |
| --- | ---: | ---: | --- |
| V0→V1 | 0 | 300 | — |
| V1→V2 | 0 | 300 | — |
| V2→V3 | 0 | 300 | — |
| V3→V4 | 39 | 261 | semi-structured only |
| V4→V5 | 0 | 300 | — |
| V5→V6 | 0 | 300 | — |
| V6→V7 | 0 | 300 | — |
| V7→V8 | 0 | 300 | — |

The exact changed sample IDs and all per-sample SHA-256 values are in `results/prompt_equivalence_matrix.csv`.

## Candidate comparison

Relative to frozen V0 (148/300 correct):

- `FULL`: 34 rescues, 3 regressions, net +31.
- `NO_C`: 34 rescues, 0 regressions, net +34; its 13 false accepts are the highest of the four candidates.
- `D_ONLY`: 34 rescues, 0 regressions, net +34; 9 false accepts.
- `D_G1`: 35 rescues, 0 regressions, net +35; 8 false accepts.

The three `FULL` regressions are unchanged from Stage 3: `final_archeology_018`, `final_polar_018`, and `final_virtual_018`. No alternative candidate regresses a V0-correct sample.

## Input-type result

| Candidate | Free-text correct | Semi-structured correct |
| --- | ---: | ---: |
| `FULL` | 6/60 | 173/240 |
| `NO_C` | 9/60 | 173/240 |
| `D_ONLY` | 8/60 | 174/240 |
| `D_G1` | 9/60 | 174/240 |

Detailed input-type, operation-type, and database subgroups are in `results/candidate_subgroup_metrics.csv`. The exact false-accept set for every candidate is in `results/candidate_false_accept_ids.csv`.

## Selection evidence

Using the pre-declared priority—avoid regressions, minimize false accepts, then maximize target-state accuracy, accepted-output accuracy, coverage, and parsimony—`D_G1` dominates `FULL` on this development replay. It also improves on `D_ONLY` by the one bounded G1 rescue without adding coverage or a regression.

This is evidence for reviewer selection, not a confirmatory claim and not an automatic freeze decision.
