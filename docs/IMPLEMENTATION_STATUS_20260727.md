# MP-FS+ implementation status — 2026-07-27

## Completed

- Created `mp_fs_plus` as a separate working copy; canonical release v3 was
  not modified.
- Added deterministic source collection, selector, field, table, column, and
  constraint IDs.
- Added ID-only Mapping Plan and free-text plan JSON schemas.
- Added deterministic evidence span extraction and evidence-ID
  materialization.
- Added declared lossless normalization rules and per-value audit metadata.
- Added hard reference validation and fail-closed clarification behavior.
- Added isolated transactional preflight; LLM repair is absent from MP-FS+.
- Added matched 4+2 semantic demonstrations and final configs for D-FS-M,
  J-FS-M, S-FS-v2-M, MP-FS-M, and MP-FS+.
- Added coverage, accepted-output accuracy, abstention, risk,
  database-macro, original-only, state-changing, and conflict-sensitive
  metrics.
- Added exact McNemar, source-group clustered bootstrap, database-macro
  bootstrap, and family-wide Holm adjustment.
- Added calibration/final protocol templates, external-holdout metadata audit,
  final-protocol freeze script, and one-use final-run guards.
- Added paper/data release split templates.
- Audited the Qwen2.5-Coder-7B v3 GPU run beyond its original backend gates.
  The audit invalidated the original `pass`: both accept probes executed but
  produced an incorrect target state, and the reused editable environment
  imported `nldbwrite_v3` from the earlier v1 bundle.
- Fixed free-text assignment evidence so separators are excluded and complete
  multi-token values are offered before shorter overlapping candidates.
- Added a blocking runtime-source manifest and current-bundle import guard to
  GPU smoke, preflight, pilot, and DEV matrix scripts.
- Strengthened the smoke validator to require every accept probe to be
  accepted, executed, and target-state correct; every clarification probe must
  abstain with `NEEDS_CLARIFICATION`.
- Completed v4 smoke `mp_fs_plus_smoke15_v4_20260727T070320Z`. It confirmed
  2/2 target-state-correct accept probes, correct current-bundle runtime
  imports, 15/15 generation/parse, and zero truncation/output-limit hits. It
  failed only because both ambiguity probes abstained without the required
  `NEEDS_CLARIFICATION` reason code.
- Added deterministic request-language conflict-policy detection. A stated
  duplicate/conflict with a vague policy now abstains with
  `NEEDS_CLARIFICATION` regardless of the model's proposed policy.
- Completed v5 smoke `mp_fs_plus_smoke15_v5_20260727T072146Z`, status `pass`.
  All technical gates passed, including 2/2 target-state-correct accept
  probes, 2/2 clarification probes with `NEEDS_CLARIFICATION`, and verified
  current-bundle runtime imports.
- Added a 60-sample calibration authoring kit and blocking metadata audit for
  database allocation, consumed-data overlap, exact distribution, independent
  authorship, and two-person QA.

## Verification

- Unit tests: 94/94 passed.
- Pytest warnings from imported `test_profile` helpers: eliminated.
- `python -m compileall`: passed for source, tests, and scripts.
- Deterministic ID-only MP-FS+ mock smoke: 3/3 parse, validate, preflight,
  build, execute, target state, and strict state; exit code 0. This uses
  pre-supplied outputs, not a real model.
- External-holdout draft template audit: passed.
- Final-protocol freeze smoke: all six primary method configurations resolved
  and were authorized in a temporary frozen protocol.
- Canonical v3 release: all five entries in its `SHA256SUMS.txt` still match.
- Historical real-model technical smoke:
  `mp_fs_plus_smoke15_v3_20260727T062333Z`, original status `pass`, now
  invalidated. The hardened validator returns `fail` because 0/2 designated
  accept probes are target-state correct and only 1/2 clarification probes
  reports `NEEDS_CLARIFICATION`.
- Accepted real-model technical smoke:
  `mp_fs_plus_smoke15_v5_20260727T072146Z`, status `pass`. This authorizes
  independent calibration-data construction only and is not paper-result
  eligible.

## Deliberately not fabricated

- No MP-FS+ LLM accuracy is reported.
- No external holdout samples were auto-generated and mislabeled as
  independently authored.
- No second human QA review was simulated.
- No second-model or GPU run was claimed.
- No smoke metric was relabeled as calibration or paper accuracy.
- No code/data/database license or redistribution right was invented.
- No DOI or final release hash was created before the final artifact exists.

## Remaining external work

1. Independently author and review calibration data.
2. Pass the calibration metadata and Gold-MP gates.
3. Run the five-method calibration matrix and pass the go/no-go gates.
4. Independently author 300 final requests on 3-5 unseen databases.
5. Complete two-person dataset QA and database license/provenance review.
6. Freeze the final protocol, prompts, matched demonstrations, model,
   environment, data, gold plans, profiles, and databases.
7. Run the six primary methods once.
8. Run the stratified 150-sample second-model subset.
9. Produce the pre-registered comparison family, error transitions, tables,
   figures, and efficiency report.
10. Resolve licenses, persistent DOI, and IEEE Access submission materials.
