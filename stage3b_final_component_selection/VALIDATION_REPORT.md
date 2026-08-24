# Validation Report

## Integrity and invariants

- 300 unique frozen samples.
- 3,900 exact prompt builds (300 × 13 surfaces).
- 1,200 candidate evaluations (300 × 4 candidates).
- `FULL` matches frozen Stage 3 V8 sample-by-sample for target correctness, strict correctness, and first failure: zero mismatches.
- Prompt equivalence invariants passed for V0–V3 and V4–V8.
- All candidate prompts match V4 sample-by-sample.
- All input archive hashes match the frozen values.
- Zero off-target state changes for every candidate.

## Tests

- Dedicated Stage 3B: 5 passed.
- Compatibility A–G2 + Stage 3 + Stage 3B: 216 passed.
- Full fast suite: 366 passed, 1 deselected, 12 subtests passed.
- Output validator: PASS; 300 candidate rows, 300 prompt rows, four candidates, zero violations.

## Deterministic repeat

An independent second CPU replay was run against the same locked inputs. Thirteen core artifacts were compared by SHA-256 and were byte-identical: seven result CSVs, the intervention trace JSONL, invariant JSON, and four exact candidate configs.

See the `.txt` files in `validation/` for raw command output and hash comparison.

## Environment note

Initial sandboxed attempts stopped before evaluation because Windows denied access to Python/pytest temporary directories. The successful replay and test commands used the same code and frozen inputs outside the filesystem sandbox solely to obtain valid Windows temporary-directory ACLs. No GPU or model was used.
