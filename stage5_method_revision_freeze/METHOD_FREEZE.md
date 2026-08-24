# Stage 5 Method Revision Freeze

Status: ready for reviewer inspection; no model run is authorized by this
package.

## Frozen method

The revised method is frozen as:

```text
MP-FS+ vNext-R1 = D + F + G1
```

The executable config is:

```text
configs/stage5/mp_fs_plus_vnext_r1.json
```

Component interpretation:

- `D`: structured source parser, selected from the original development
  evidence.
- `F`: constrained exact-name reference repair, promoted after post-hoc Stage 4
  failure analysis.
- `G1`: diagnostic evidence-boundary repair, selected from the original
  development evidence.

The config intentionally leaves A, B, C, E, and G2 disabled. This is a method
freeze only; it does not change datasets, gold labels, metrics, previous
outputs, or evaluator behavior.

## Evidence boundary

Stage 4 initially evaluated the pre-specified `D+G1` configuration. After the
Stage 4 failure analysis was used to promote `F`, Stage 4 became diagnostic
evidence for `D+F+G1`; it must not be described as untouched confirmatory
evidence for the revised method.

Permitted wording:

```text
Within the frozen Stage-4 outputs, adding F to the D+G1 configuration was
sufficient to reproduce all five FULL-vs-D_G1 state-level rescues without
observed state-level regressions.
```

Forbidden wording:

```text
D+F+G1 achieved 34.67% on an untouched held-out Stage-4 test.
```

## Confirmation gate

The next confirmatory evaluation is blocked until a new untouched dataset is
registered, hashed, overlap-audited, and accepted for review. The lock in
`CONFIRMATION_PROTOCOL_LOCK.json` freezes the method, model, token budget,
prompt surface, generation parameters, metrics, statistical tests, and dataset
registration requirements before any GPU run.

No GPU command is required for Stage 5. A future confirmation run, after
reviewer acceptance and dataset registration, should execute on the server
under `/home/uet/hue_ptk/`.
