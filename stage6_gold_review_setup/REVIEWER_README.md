# Stage 6C Independent Gold Review Setup

Status: packets created, pending human execution.

This package does not call a model and does not permit GPU preflight. It locks
the third-adjudicator rule, rejection policy, and R01/R02 packet templates for
the 500 registered CRUDSQL Stage6B gold items.

Each reviewer receives only their own archive:

```text
Stage6C_R01_review_packet_20260824.zip -> R01 only
Stage6C_R02_review_packet_20260824.zip -> R02 only
```

Reviewers fill only:

```text
decision in {approved, rejected}
notes
```

All immutable fields and content hashes must remain unchanged. Reviewer outputs
are sealed until both R01/R02 have submitted. Final rejected or unresolved items
block confirmation until resolved before model execution.
