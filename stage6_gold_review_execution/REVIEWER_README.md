# Stage 6C Review Execution and Blind Adjudication Prep

This package ingests the completed R01/R02 review TSV submissions, verifies
immutable fields and decisions, computes agreement, and prepares a blind R03
packet when disagreements exist.

If R01/R02 agree to reject any item, the package also locks the final-rejection
resolution workflow. Agreed final rejections block confirmation until resolved by
the locked R04 technical classification and downstream correction/review or
registration-revision process.

The R04 packet is isolated to the agreed-rejected items only. R04 may see R01/R02
rejection reasons for those items but must be distinct from R01, R02, and R03 and
must not see model predictions.

It does not call a model, does not permit GPU preflight, and does not create a
final gold freeze while unresolved disagreement or final rejection remains.
