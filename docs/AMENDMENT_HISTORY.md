# Amendment history

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
