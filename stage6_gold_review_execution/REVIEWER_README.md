# Stage 6C Review Execution and Blind Adjudication Prep

This package ingests the completed R01/R02 review TSV submissions, verifies
immutable fields and decisions, computes agreement, and prepares a blind R03
packet when disagreements exist.

It does not call a model, does not permit GPU preflight, and does not create a
final gold freeze while unresolved disagreement or final rejection remains.
