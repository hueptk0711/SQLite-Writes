# MP-FS+ Failure Analysis — Stage 1.1 causal/manual correction

## 1. Analysis protocol
Frozen prediction artifacts are read without rerunning model inference. State-mismatch cases are replayed on isolated SQLite copies to obtain gold/predicted database deltas. V0 is treated only as a system-level downstream bypass because it removes multiple components together; it is not interpreted as a single-component verifier intervention.

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

## 6. Final reviewed root-cause labels
For samples with completed manual review, this table uses `reviewer_root_cause`; all other incorrect samples retain the automatic diagnosis. The automatic-only summary is exported separately as `root_cause_summary_auto.csv`, while the final manual-overridden table is exported as `reviewed_root_cause_summary.csv`. V0 recoverability remains a system-level downstream-bypass observation rather than a component-isolated verifier causal claim.

| Root cause | N incorrect | % |
| --- | --- | --- |
| CONFLICT_SEMANTICS_PLANNING_ERROR | 20 | 13.16 |
| CONTROL_FIELD_POLICY_ERROR | 22 | 14.47 |
| DATE_NORMALIZATION_AND_EVIDENCE_SPAN_ERROR | 1 | 0.66 |
| DATE_NORMALIZATION_POLICY_ERROR | 9 | 5.92 |
| EVIDENCE_SPAN_BOUNDARY_ERROR | 1 | 0.66 |
| EVIDENCE_SPAN_SELECTION_ERROR | 2 | 1.32 |
| FREE_TEXT_COLUMN_MAPPING_ERROR | 1 | 0.66 |
| FREE_TEXT_EVIDENCE_AND_GROUP_PLANNING_ERROR | 1 | 0.66 |
| FREE_TEXT_TARGET_GROUP_OMISSION_ERROR | 1 | 0.66 |
| GROUNDING_ERROR | 35 | 23.03 |
| LLM_SEMANTIC_ERROR | 27 | 17.76 |
| MATERIALIZATION_ERROR | 11 | 7.24 |
| PREFLIGHT_ERROR | 7 | 4.61 |
| REPRESENTATION_LIMITATION | 3 | 1.97 |
| SOURCE_PARSER_CONTROL_ROW_SEGMENTATION_ERROR | 2 | 1.32 |
| SOURCE_PARSER_NULL_LITERAL_COERCION_ERROR | 2 | 1.32 |
| SOURCE_PARSER_ROW_SEGMENTATION_ERROR | 3 | 1.97 |
| UPDATE_COLUMN_OMISSION_PLANNING_ERROR | 4 | 2.63 |

## 7. Free-text vs semi-structured
| Input type | N | Correct | Accuracy | Coverage | Accepted accuracy |
| --- | --- | --- | --- | --- | --- |
| free_text | 60 | 8 | 13.33 | 21.67 | 61.54 |
| semi_structured | 240 | 140 | 58.33 | 62.92 | 92.72 |

## 8. Dependency-sensitive analysis
| Dependency-sensitive | N | Correct | Accuracy | Coverage | Accepted accuracy |
| --- | --- | --- | --- | --- | --- |
| no | 160 | 98 | 61.25 | 66.88 | 91.59 |
| yes | 140 | 50 | 35.71 | 40.71 | 87.72 |

## 9. Operation-type analysis
| Operation | N | Correct | Accuracy | Coverage | Accepted accuracy |
| --- | --- | --- | --- | --- | --- |
| insert_ignore | 100 | 46 | 46.00 | 51.00 | 90.20 |
| plain_insert | 100 | 53 | 53.00 | 60.00 | 88.33 |
| upsert_update | 100 | 49 | 49.00 | 53.00 | 92.45 |

## 10. Database-level analysis
| DB | N | MP-FS+ accuracy | Coverage | Main failure |
| --- | --- | --- | --- | --- |
| archeology | 60 | 50.00 | 53.33 | verification |
| polar | 60 | 61.67 | 66.67 | materialization |
| robot | 60 | 45.00 | 48.33 | materialization |
| vaccine | 60 | 41.67 | 46.67 | verification |
| virtual | 60 | 48.33 | 58.33 | reference_resolution |

## 11. Downstream bypass analysis (system-level, non-causal)
For reference-resolution, materialization, and verification first failures, the available V0 comparison removes hard verification, provenance, semantic gating, and preflight together. Therefore the table below reports bypass recoverability only; verifier precision/FNR are not computed.

| First failure stage | Rejects | Bypass-correct | Bypass-recoverable rate | Causal scope |
| --- | --- | --- | --- | --- |
| reference_resolution | 35 | 0 | 0.00 | system-level V0 bypass |
| materialization | 44 | 22 | 50.00 | system-level V0 bypass |
| verification | 44 | 0 | 0.00 | system-level V0 bypass |

## 12. Semantic-risk gate diagnostic
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

## 13. Executed-but-wrong state-diff audit
Executed successfully but target state wrong: 16. State-diff replay errors: 0.

| State-diff class | N |
| --- | --- |
| STATE_WRONG_CONFLICT_BEHAVIOR | 3 |
| STATE_EXTRA_ROW | 3 |
| STATE_WRONG_VALUE | 9 |
| STATE_MISSING_ROW | 5 |

Detailed gold delta, predicted delta, and final difference are written to `state_mismatch_audit.csv`.

## 14. Systematic manual-audit groups
| Audit group | N | Completed |
| --- | --- | --- |
| CONTROL_FIELD_OPERATION | 22 | 22 |
| DATE_NORMALIZATION | 11 | 11 |
| CONFLICT_AMBIGUITY | 20 | 20 |
| STATE_MISMATCH | 16 | 16 |

Manual review required: 69. Completed: 69. Pending: 0. `manual_review_notes` is never silently blank for required rows; pending rows are explicitly marked `PENDING_MANUAL_AUDIT`.

`manual_audit_evidence.jsonl` contains the source sample, schema DDL, raw/parsed/materialized plan, verification, compiled program, execution, evaluation, and state-diff evidence needed for the audit.

Use `manual_audit_decisions.template.csv` as the review worksheet. Save completed decisions as `04_results\mp_fs_plus_failure_analysis_v1\stage1_manual_audit_decisions.csv` and rerun the analysis.

## 15. MP-FS+ vs Direct/J paired analysis
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

No automatic `why_baseline_succeeded` claim is emitted. For baseline-correct/MP-FS+-wrong samples, the output records only the observed MP-FS+ failure stage/reason; causal explanation requires audit.

## 16. MP-FS+ partial/unique successes
Cases where MP-FS+ is correct while at least one baseline is wrong: 21. Conflict-sensitive among these: 21/21; dependency-sensitive: 9/21. The analysis does not attribute these wins to a specific MP-FS+ feature without component-level evidence.

## 17. Stage 1.1 completion status
COMPLETE — manual audits pending=0, state-diff replay errors=0.

See `candidate_fixes.md` for Stage-2 candidates. They are hypotheses/actions to test after Stage 1.1 audit closure, not established causal fixes.

Abstained samples: 136; system/stage bypass-correct among abstentions where an ablation is available: 22.
