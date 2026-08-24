# Stage 6C R03 Adjudication Ingest

This package ingests the completed blind R03 adjudication TSV, verifies immutable
fields and R03 decisions, and merges the result with R01/R02 agreement.

R03 approved 29 disagreement items and rejected 23. The 23 R03-rejected items
join the 17 R01/R02 agreed-rejected items in the same R04 technical
root-cause-resolution workflow. No gold files are corrected here.

This package does not call a model, does not permit GPU preflight, and does not
create a final gold freeze.
