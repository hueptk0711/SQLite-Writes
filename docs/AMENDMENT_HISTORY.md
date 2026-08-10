# Amendment history

## 2026-07-31 primary output-capacity amendment

- An incomplete pre-analysis run showed that the original 4,096-token
  generation limit was insufficient for at least one primary configuration.
- The output limit was increased uniformly from 4,096 to 8,192 tokens for
  all five predictive configurations.
- All five predictive configurations were rerun from a clean state under
  the revised 8,192-token limit.
- No prediction from the incomplete 4,096-token run was retained in the
  primary evaluation.

## 2026-07-31 conservative output-limit adjudication

- After the clean 8,192-token rerun, two MP-FS+ generations,
  `final_polar_048` and `final_vaccine_047`, reached the revised maximum
  output length.
- Both outputs exhibited schema-invalid repetitive sequential-reference
  expansion and failed to produce valid Mapping Plans.
- Both requests remained in the complete 300-request evaluation denominator
  and were conservatively scored as incorrect.
- The two predictions were not regenerated, manually completed, repaired,
  or removed. The prompt, parser, evaluator correctness definition, and
  8,192-token limit were not changed.
- No other primary configuration was changed by this adjudication.
- This decision was made after generation and is therefore recorded as a
  post-run reporting amendment. The primary experiment should not be
  described as strictly preregistered in its final form.
- The frozen machine-readable record is
  `07_reproducibility/server_final_run/final_output_limit_adjudication_v2_1.json`.

## 2026-08-05 reviewer release v2

- Promoted reporting code to one canonical source tree at the archive root.
- Archived the original inference-era source as a hash-verified read-only ZIP.
- Changed strict full-state and off-target evaluation to compare all persistent
  user tables by default.
- Added regression tests for unrelated-table side effects, quoted and
  schema-qualified identifiers, and trigger-generated expected changes.
- Replayed all 1,800 frozen method-sample pairs; target, strict, and off-target
  labels had zero mismatches against the prior results.
- Added pytest markers, clean-root installation, progress and timing records,
  subprocess timeout, deterministic release metadata, and a current README.

No primary prediction was regenerated or modified by these amendments.
