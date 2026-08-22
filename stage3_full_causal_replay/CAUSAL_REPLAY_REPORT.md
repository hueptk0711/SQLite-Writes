# Stage 3 Causal Replay Report

## Aggregate outcomes

| Variant | Correct | Accuracy | Coverage | Accepted accuracy | False accept | Execution success |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| V0 | 148 | 49.33% | 54.67% | 90.24% | 16 | 164 |
| V1 | 148 | 49.33% | 54.67% | 90.24% | 16 | 164 |
| V2 | 148 | 49.33% | 55.00% | 89.70% | 17 | 165 |
| V3 | 145 | 48.33% | 54.00% | 89.51% | 17 | 162 |
| V4 | 178 | 59.33% | 63.00% | 94.18% | 11 | 189 |
| V5 | 178 | 59.33% | 63.33% | 93.68% | 12 | 190 |
| V6 | 178 | 59.33% | 63.33% | 93.68% | 12 | 190 |
| V7 | 179 | 59.67% | 63.33% | 94.21% | 11 | 190 |
| V8 | 179 | 59.67% | 63.33% | 94.21% | 11 | 190 |

All variants have zero observed off-target state changes. Target-state and strict-full-state correct counts are equal for every variant.

## Component-level causal result

- A: 51 activated; 0 rescue; 0 regression.
- B: 85 activated; 0 rescue; 0 regression. Coverage rises by one, but the additional accepted output is wrong, so false acceptance rises from 16 to 17.
- C: 82 activated; 0 rescue; 3 regressions; net -3.
- D: 43 activated; 33 rescues; 0 regressions; net +33.
- E: 14 activated; 0 rescue; 0 regression. Coverage rises by one and false acceptance rises by one.
- F: 34 activated samples; 0 rescue; 0 regression.
- G1: 1 activated; 1 rescue; 0 regression.
- G2: 1 activated; 0 rescue; 0 regression; revalidation fails closed.

## Repair accounting

F exact-name and singleton rules are separated in `repair_rule_summary.csv`. No singleton rule fires in this frozen replay. Exact-name repair produces 56 traces: 55 are applied and pass local revalidation, while none reaches a globally correct target state. This demonstrates that a bounded reference correction is not sufficient evidence of full semantic correctness.

G1 produces one attempt, one application, one successful revalidation, and one final-state rescue. G2 produces one attempt and application, zero successful revalidations, and zero final-state rescues. Atomic rollback prevents acceptance of the failed G2 repair.

## Interpretation boundary

The net V0→V8 increase is 31 correct samples, from 148 to 179. This number combines a -3 change at C, +33 at D, and +1 at G1; it must not be presented as every intervention contributing positively. Because these same 300 diagnostic samples informed method design, the replay is descriptive development evidence only. Generalization requires the later fresh 7B experiment.
