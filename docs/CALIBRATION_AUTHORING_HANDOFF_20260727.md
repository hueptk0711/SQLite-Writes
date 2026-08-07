# Calibration authoring v2 handoff — 2026-07-27

## v2.2 feasibility correction

The v2.2 package removes the impossible `single_row + insert_ignore`
cross-product. Every insert-ignore case is now a batch with at least two
logical rows available: one pristine conflict witness and one non-conflicting
row that makes the request state-changing. The 60-sample marginal balances,
database allocation, workload-shape totals, source assets, and database hashes
remain unchanged.

## v2.1 portability hotfix

The v2.1 package changes one test so that its fixture is selected by the
frozen `operation_semantics` value rather than filesystem enumeration order.
This makes the test deterministic on both Windows and Linux. Runtime code,
the 60 authoring slots, source assets, frozen allocation, and database hashes
are unchanged from v2.

## Status and source

This v2 release supersedes the v1 authoring package. It prepares the human
authoring workflow only and is deliberately marked `draft_not_paper_eligible`.
Predictive model and GPU runs remain blocked.

The source is the official
`birdsql/livesqlbench-base-lite-sqlite` repository at revision:

```text
0664a2f28555faa0dd2947c8c23288df79bcc06b
```

The kit records checksums, SQLite `PRAGMA quick_check`, and structural inventory
for all 18 candidate databases. It includes only the two calibration databases:

```text
23153eba8d258ef4846257326c4481850d7632bc2efdbe2b2236efa00d316922  cybermarket
4bc022713669f1cab2736e453a012f53eea396e9b94457e744c858cc034d1fb3  museum
```

The frozen allocation is:

- calibration: `cybermarket`, `museum`;
- reserved final: `archeology`, `polar`, `robot`, `vaccine`, `virtual`.

No reserved-final database binary or published LiveSQLBench task file is
included.

## Frozen design

The 60 slots are balanced across database, operation, input mode, and
complexity. Batch size and multi-table structure are separated:

- 20 single-row/single-table;
- 10 small-batch/single-table;
- 10 small-batch/multi-table;
- 10 large-batch/single-table;
- 10 large-or-relational/multi-table.

The v2 controls add:

- an immutable per-sample allocation manifest;
- a revisioned authored-content SHA256;
- an append-only review ledger whose approvals bind to one revision and hash;
- semantic comparison of gold SQL, gold Write Plan, records, and tables;
- pristine conflict-witness, state-change, side-effect, and workload-shape
  checks;
- an atomic freeze command that refuses incomplete or existing targets;
- a deterministic CPU Gold-MP gate.

## Verify the draft

From the extracted release root:

```bash
export PYTHONPATH="$PWD/src"

python scripts/data/assemble_calibration_dataset.py \
  --samples-dir data/calibration/authoring_kit/samples \
  --ids data/calibration/authoring_kit/calibration_ids.txt \
  --output data/calibration/authoring_kit/dataset.json

python scripts/data/validate_calibration_authoring.py \
  --kit-dir data/calibration/authoring_kit \
  --data data/calibration/authoring_kit/dataset.json \
  --output artifacts/audit/calibration_authoring_draft.json \
  --allow-draft
```

Expected draft state:

```text
asset_status: valid
frozen_status: valid
samples: 60
reserved_final_database_files_included: false
published_task_files_included: false
authoring_status: draft_or_invalid
paper_result_eligible: false
gpu_run_authorized: false
```

The authoring and semantic issue counts are expected because the 60 content
slots are intentionally blank.

## Human workflow

Only assign IDs after confirming that one author and two reviewers are three
different people:

```bash
python scripts/data/assign_calibration_participants.py \
  --samples-dir data/calibration/authoring_kit/samples \
  --author-id CAL-A01 \
  --reviewer-id CAL-R01 \
  --reviewer-id CAL-R02
```

For each sample, the author writes the original request, gold records, gold
tables, gold Write Plan, and gold SQL. Reviewers then record independent
decisions against the current content hash:

```bash
python scripts/data/record_calibration_review.py \
  --sample data/calibration/authoring_kit/samples/SAMPLE_ID.json \
  --ledger data/calibration/authoring_kit/review_ledger.csv \
  --reviewer-id CAL-R01 \
  --decision approved
```

For an edit after rejection, start a new revision before changing content:

```bash
python scripts/data/start_calibration_revision.py \
  --sample data/calibration/authoring_kit/samples/SAMPLE_ID.json
```

Both reviewers must approve the same current revision and authored-content
hash. Strict validation, without `--allow-draft`, must report
`ready_for_freeze`.

## Freeze and Gold-MP

Create canonical artifacts only through the guarded freeze command:

```bash
python scripts/data/freeze_calibration_authoring.py \
  --kit-dir data/calibration/authoring_kit \
  --data data/calibration/authoring_kit/dataset.json \
  --output-dir data/calibration \
  --audit-output artifacts/audit/calibration_freeze_readiness.json
```

Then run the deterministic CPU-only Gold-MP audit:

```bash
python scripts/data/audit_calibration_gold_mp.py \
  --data data/calibration/dataset.json \
  --profile-dir data/calibration/authoring_kit/profiles \
  --db-root data/calibration/authoring_kit/databases \
  --output artifacts/audit/calibration_gold_mp.json
```

GPU authorization requires 60/60 parse, validation, build, execution,
target-state correctness, strict-state correctness, and zero unintended side
effects. Even a complete Gold-MP pass does not make predictive results
paper-eligible; the subsequent preregistered calibration protocol is still
required.
