# MP-FS+ Failure Analysis

## 1. Analysis protocol
Frozen prediction artifacts were read without rerunning model inference. Downstream oracle-bypass uses the existing isolated replay ablation outputs; no production database is modified.

## 2. Dataset/run identity
Samples: 300. Result archive: `mp_fs_plus_final300_protocol_v2_1_rev2_adjudicated_20260731T121531Z.tar.gz`. Dataset archive: `mp_fs_plus_external_holdout_300_20260731.zip`.

## 3. Overall result
MP-FS+ target-state accuracy: 148/300 = 49.33%. Coverage/admission: 164/300 = 54.67%. Incorrect: 152.

## 4. Stage-wise survival
{
  "total": 300,
  "generation_pass": 300,
  "parse_pass": 294,
  "reference_pass": 259,
  "materialization_pass": 215,
  "verification_pass": 171,
  "compilation_pass": 171,
  "semantic_gate_pass": 170,
  "preflight_pass": 164,
  "executed": 164,
  "target_correct": 148
}

## 5. First-failure distribution
| First failure stage | N | % of 300 | % of incorrect |
| --- | --- | --- | --- |
| parse | 6 | 2.00 | 3.95 |
| reference_resolution | 35 | 11.67 | 23.03 |
| materialization | 44 | 14.67 | 28.95 |
| verification | 44 | 14.67 | 28.95 |
| semantic_gate | 1 | 0.33 | 0.66 |
| preflight | 6 | 2.00 | 3.95 |
| state_mismatch | 16 | 5.33 | 10.53 |

## 6. Root-cause distribution
| Root cause | N incorrect | % |
| --- | --- | --- |
| GROUNDING_ERROR | 35 | 23.03 |
| LLM_SEMANTIC_ERROR | 43 | 28.29 |
| MATERIALIZATION_ERROR | 22 | 14.47 |
| PREFLIGHT_ERROR | 7 | 4.61 |
| REPRESENTATION_LIMITATION | 23 | 15.13 |
| VERIFIER_OVER_REJECTION | 22 | 14.47 |

## 7. Free-text vs semi-structured
| Input type | N | Correct | Accuracy | Coverage | Accepted accuracy |
| --- | --- | --- | --- | --- | --- |
| free_text | 60 | 8 | 13.33 | 21.67 | 61.54 |
| semi_structured | 240 | 140 | 58.33 | 62.92 | 92.72 |

## 8. Database-level analysis
| DB | N | MP-FS+ accuracy | Coverage | Main failure |
| --- | --- | --- | --- | --- |
| archeology | 60 | 50.00 | 53.33 | verification |
| polar | 60 | 61.67 | 66.67 | materialization |
| robot | 60 | 45.00 | 48.33 | materialization |
| vaccine | 60 | 41.67 | 46.67 | verification |
| virtual | 60 | 48.33 | 58.33 | reference_resolution |

## 9. Verification false rejection analysis
Verifier-boundary rejects: 123. Oracle-correct if bypassed: 22. False rejection rate: 17.89%.

| metric | value |
| --- | --- |
| A_pass_correct | 148 |
| B_false_accept_pass_wrong | 23 |
| C_false_reject_reject_correct | 22 |
| D_reject_wrong | 101 |
| precision | 86.55 |
| recall_of_bad_candidates | 81.45 |
| false_accept_rate | 13.45 |
| false_reject_rate | 17.89 |

## 10. Semantic gate analysis
Semantic-gate first failures: 1.

| metric | value |
| --- | --- |
| A_pass_correct | 148 |
| B_false_accept_pass_wrong | 22 |
| C_false_reject_reject_correct | 0 |
| D_reject_wrong | 1 |
| precision | 87.06 |
| recall_of_bad_candidates | 4.35 |
| false_accept_rate | 12.94 |
| false_reject_rate | 0.00 |

## 11. Executed-but-wrong cases
Executed successfully but target state wrong: 16. These are all marked `manual_review_required=1`.

## 12. MP-FS+ vs Direct/J paired analysis
| paired_category | N |
| --- | --- |
| ALL_CORRECT | 127 |
| ALL_WRONG | 17 |
| DIRECT_CORRECT_MP_WRONG | 11 |
| DIRECT_J_CORRECT_MP_WRONG | 107 |
| J_CORRECT_MP_WRONG | 17 |
| MP_CORRECT_DIRECT_WRONG | 7 |
| MP_CORRECT_J_WRONG | 13 |
| ONLY_MP_CORRECT | 1 |

## 13. Unique MP-FS+ successes
Unique or partial unique MP-FS+ successes: 21.

## 14. Candidate issues for method revision
See `candidate_fixes.md` for issue-level notes. Acceptance questions: planning/parse=6, grounding=35, materialization=44, verifier_boundary=123, verifier_oracle_correct=22, semantic_or_preflight=7, executed_wrong=16, abstained=136, abstained_oracle_correct=22.
