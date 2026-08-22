# Stage 2-G2 Patch 1 Checkpoint Report

## Result

G2 Patch 1 adds a tightly bounded temporal evidence-selection repair after the frozen Stage-E incompatibility diagnostic. It changes one `value_from` on a deep copy only when exactly one compatible candidate remains in the pre-enumerated, forward, same-sentence temporal set. It has no ranking or generative fallback.

## Frozen boundary

```text
base tag:    Stage2-G1-FINAL
base commit: b3d6e721b5d3c1ea9a5fd7e117692a807815dcb7
```

The resulting G2 commit and final validation counts are recorded in the reviewer package after commit creation.

## Diagnostic evidence

The regression fixture contains two temporal Stage-1 cases eligible under the exact G2 Patch 1 contract and one generic-text control that remains out of scope. Fixture use is diagnostic only. It does not establish full-sample rescue, and other latent failures can remain after the repaired slot.

## Safety coverage

Dedicated tests cover:

- exact Stage-E diagnostic gating;
- target/type validation;
- pre-enumerated candidates only;
- maximal temporal span filtering;
- forward and same-sentence constraints;
- zero/multiple candidate fail-closed behavior;
- exactly one diagnosed slot;
- deep-copy, one-slot mutation, and collision rejection;
- exactly one materializer retry;
- materializer and verifier atomic fail-closed paths;
- no G2→G1 recursive chain;
- disabled-mode G1 compatibility;
- mandatory reviewer provenance fields;
- unchanged V7/V8 prompts.

## Housekeeping included

Documentation now records the already-established final F diagnostic classification as `13/10/12`, freezes G1 at `Stage2-G1-FINAL`, describes G1 as a conservative boundary policy, and identifies comma as reserved compatibility behavior with no observed activation. No F or G1 implementation is changed.

## Environment disclosure

Project test metadata requires `pytest>=8,<9`. The current validation environment and actual pytest version are recorded in `VALIDATION_REPORT.md`; a version mismatch, if present, is disclosed rather than hidden.

## Not performed

No model execution, Stage 3 causal replay, dataset change, gold-label change, metric change, prompt change, protocol change, or new headline result is part of this patch.
