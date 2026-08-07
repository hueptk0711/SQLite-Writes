# Reporting amendment v2.3 summary

- Status: **PASS**
- Predictions modified: `false`
- Database executions repeated: `false`
- Primary results changed: `false`
- Side-effect definition corrected: `side_effect_rate` now means any off-target state modification.
- GPU required: `false`
- `coverage` is retained for backward compatibility and is now named method-specific admission coverage.

## Stage funnel

| Method | Generation | Parse | Validation | Build | Execution | Correct given execution | Target | Admission boundary | Admitted | Correct given admitted |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| D-FS-M | 1.0000 | 0.9967 | n/a | 0.9967 | 0.8900 | 0.9663 | 0.8600 | successful_build | 0.9967 | 0.8629 |
| J-FS-M | 1.0000 | 1.0000 | 0.8867 | 0.8867 | 0.8800 | 0.9773 | 0.8600 | successful_build | 0.8867 | 0.9699 |
| S-FS-v2-M | 1.0000 | 1.0000 | n/a | 0.8867 | 0.3567 | 0.7290 | 0.2600 | successful_build | 0.8867 | 0.2932 |
| MP-FS-M | 1.0000 | 0.8900 | 0.2033 | 0.2033 | 0.1300 | 0.8718 | 0.1133 | successful_build | 0.2033 | 0.5574 |
| MP-FS+ | 1.0000 | 0.9800 | 0.5700 | 0.5700 | 0.5467 | 0.9024 | 0.4933 | transactional_preflight | 0.5467 | 0.9024 |
| Gold-MP | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | oracle_build | 1.0000 | 1.0000 |

## Off-target state modifications

| Method | Count | Rate | Target also wrong |
|---|---:|---:|---:|
| D-FS-M | 1 | 0.0033 | 1 |
| J-FS-M | 0 | 0.0000 | 0 |
| S-FS-v2-M | 0 | 0.0000 | 0 |
| MP-FS-M | 1 | 0.0033 | 1 |
| MP-FS+ | 0 | 0.0000 | 0 |
| Gold-MP | 0 | 0.0000 | 0 |

The compatibility field `side_effect_rate` is identical to `any_off_target_change_rate`. The narrower `target_correct_with_side_effect_rate` is reported separately.

## Recorded efficiency

| Method | Mean/median input tokens | Mean output tokens | Mean generation latency (s) | Mean preflight latency (s) | Output-limit hit |
|---|---:|---:|---:|---:|---:|
| D-FS-M | 8630.3 / 8372.0 | 334.9 | 4.95 | n/a | 0.0000 |
| J-FS-M | 8632.2 / 8374.5 | 429.1 | 6.02 | n/a | 0.0000 |
| S-FS-v2-M | 8632.2 / 8374.5 | 429.1 | 6.04 | n/a | 0.0000 |
| MP-FS-M | 9608.6 / 9327.0 | 462.9 | 6.62 | n/a | 0.0000 |
| MP-FS+ | 5511.5 / 3222.5 | 409.6 | 5.58 | 0.0003 | 0.0067 |
| Gold-MP | n/a / n/a | n/a | n/a | n/a | 0.0000 |

Generation latency is reported separately. Deterministic parser/compiler/database end-to-end latency was not instrumented and is not inferred.

## Corrected plan-level reporting

| Method | Plan coverage | Conditional target-column F1 | End-to-end target-column F1 | Conditional table accuracy | End-to-end table accuracy |
|---|---:|---:|---:|---:|---:|
| D-FS-M | n/a | n/a | n/a | n/a | n/a |
| J-FS-M | 0.8867 | 0.9914 | 0.8790 | 1.0000 | 0.8867 |
| S-FS-v2-M | 1.0000 | 0.9726 | 0.9726 | 0.9933 | 0.9933 |
| MP-FS-M | 0.3633 | 0.8831 | 0.3209 | 0.9633 | 0.3500 |
| MP-FS+ | 0.7233 | 0.9125 | 0.6600 | 0.9171 | 0.6633 |
| Gold-MP | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

## Leave-one-database-out sensitivity

- MP-FS+ vs D-FS-M: leave-one-database-out difference range [-0.4000, -0.3500].
- MP-FS+ vs J-FS-M: leave-one-database-out difference range [-0.4208, -0.3250].
- MP-FS+ vs MP-FS-M: leave-one-database-out difference range [0.3750, 0.3875].
- J-FS-M vs D-FS-M: leave-one-database-out difference range [-0.0250, 0.0208].

## Error taxonomy

- Rows remaining in `other`: `0`.
- MP-FS+ ID, clarification, normalization, duplicate-target, and conflict-mask failures are now explicitly categorized.

## Risk–coverage limitation

The locked artifacts contain a binary, method-specific admission decision but no continuous confidence score or pre-registered threshold family. A risk–coverage curve or AURC cannot be reconstructed without inventing a post-hoc ranking. This amendment therefore reports the observed operating point only. A future protocol must freeze a confidence score and threshold grid before evaluation.
