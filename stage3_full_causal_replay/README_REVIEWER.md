# Stage 3 — Full A–G2 Causal Replay

## Scope

This package replays the single frozen MP-FS+ raw-generation set through nine cumulative deterministic variants. It does not call a model and does not regenerate predictions.

```text
V0 = Original MP-FS+
V1 = +A
V2 = +A+B
V3 = +A+B+C
V4 = +A+B+C+D
V5 = +A+B+C+D+E
V6 = +A+B+C+D+E+F
V7 = +A+B+C+D+E+F+G1
V8 = +A+B+C+D+E+F+G1+G2
```

The 300 samples are development diagnostic data because they informed A–G2. Results are causal replay evidence, not a fresh confirmatory accuracy claim.

## Frozen inputs

- G2 tag: `Stage2-G2-FINAL`
- G2 production commit: `b752867312727e9932dcf48af99c02b4b2af36cf`
- Replay code commit: `4fcbc5f6078667bf8f57e9878b80a9ec162a3a86`
- Dataset archive SHA-256: `525cdd7006ea32a8ab8d81f842332ac9b403dce2472cde608efb4e6962d456df`
- Original result archive SHA-256: `e456037422281d56e03dd7766baf1cc9efa78a95061234444c452f3c04810911`
- V0 comparison against frozen evaluation: zero mismatches across target correctness, strict correctness, and execution success.

## Key findings

| Step | Correct before | Correct after | Rescued | Regressed | Net |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 148 | 148 | 0 | 0 | 0 |
| B | 148 | 148 | 0 | 0 | 0 |
| C | 148 | 145 | 0 | 3 | -3 |
| D | 145 | 178 | 33 | 0 | +33 |
| E | 178 | 178 | 0 | 0 | 0 |
| F | 178 | 178 | 0 | 0 | 0 |
| G1 | 178 | 179 | 1 | 0 | +1 |
| G2 | 179 | 179 | 0 | 0 | 0 |

The three C regressions are `final_archeology_018`, `final_polar_018`, and `final_virtual_018`. They are retained exactly as observed.

G1 repairs `final_archeology_032` successfully and rescues its final target state. G2 attempts one bounded repair on `final_vaccine_002`; revalidation fails, atomic rollback is retained, and the sample remains incorrect. G2 therefore has no false acceptance or regression in this replay.

F records 55 exact-name repairs that pass local revalidation but remain globally target-state incorrect. The `false_repair` column uses the conservative operational definition “repair applied + local revalidation passed + final target state wrong.” The adjacent V5→V6 causal comparison has zero rescued and zero regressed samples, so these cases are not attributed as new regressions caused by F.

## Activation definition

`<component>_activated` means the component emitted a deterministic trace signal or caused an observable semantic fingerprint change relative to the preceding cumulative variant. Provenance-only metadata is removed from the fingerprint. The trace JSONL records both bases separately.

## Suggested review order

1. `results/rescue_regression_matrix.csv`
2. `results/variant_metrics.csv`
3. `results/causal_replay_sample_level.csv`
4. `results/repair_rule_summary.csv`
5. `results/failure_stage_transitions.csv`
6. `results/failure_taxonomy_V0_V8.csv`
7. `traces/A_to_G2_intervention_traces.jsonl`
8. `validation/` and `provenance/`

## Reproduction

This run is CPU-only. From the repository root:

```text
python scripts/analysis/run_stage3_causal_replay.py \
  --dataset-archive 03_protocol_and_data/final_holdout_release/mp_fs_plus_external_holdout_300_20260731.zip \
  --result-archive 04_results/00_incoming_from_server/mp_fs_plus_final300_protocol_v2_1_rev2_adjudicated_20260731T121531Z.tar.gz \
  --output-dir stage3_reproduced

python scripts/analysis/validate_stage3_causal_replay.py \
  --results-root stage3_reproduced
```

The output directory must be absent or empty. No GPU or SSH server is required.
