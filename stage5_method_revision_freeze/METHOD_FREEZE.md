# Stage 5 Method Revision Freeze

Status: PATCH2 ready for reviewer inspection; no model run is authorized by
this package.

## Frozen method

The revised method is frozen as:

```text
MP-FS+ vNext-R1 = D + F + G1
```

The executable config is:

```text
configs/stage5/resolved_mp_fs_plus_vnext_r1.json
```

`configs/stage5/mp_fs_plus_vnext_r1.json` remains as the human-readable
overlay. PATCH1 adds the resolved effective config so future confirmation runs
do not dynamically resolve `base_config` or `demonstration_bank`.

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

PATCH1 adds an executable freeze manifest:

```text
stage5_method_revision_freeze/EXECUTABLE_FREEZE_MANIFEST.json
```

It records SHA-256 hashes for the overlay config, resolved config, base configs,
matched demonstration bank, protocol lock, validator, and selected executable
method implementation files. The accepted executable tag is:

```text
stage5-vnext-r1-freeze-patch1
```

PATCH2 does not change the frozen method. It closes the remaining confirmation
protocol degrees of freedom by adding:

```text
stage5_method_revision_freeze/CONFIRMATION_ARM_CONFIGS.json
stage5_method_revision_freeze/CONFIRMATION_ENVIRONMENT_LOCK.json
```

The accepted D+F+G1 method freeze commit remains:

```text
79f6a82144ec0407444ef37121f70eed2b20e01c
```

The confirmation run must include both pre-specified hypotheses:

```text
H1: D+F+G1 vs Original MP-FS+
H2: D+F+G1 vs D+G1
```

For H2, `D+G1` and `D+F+G1` must be deterministic replays from the exact same
`shared_mp_fs_plus_generation` raw generation rows, so the incremental
contribution of `F` is not confounded with independent LLM sampling or prompt
drift. The exact comparator configs are frozen as:

```text
Direct:             configs/stage5/resolved_direct_confirmation.json
J-FS:               configs/stage5/resolved_j_fs_confirmation.json
Original MP-FS+:    configs/stage5/resolved_original_mp_fs_plus.json
D_G1 control:       configs/stage5/resolved_d_g1_control.json
D_F_G1 final:       configs/stage5/resolved_mp_fs_plus_vnext_r1.json
```

Input overflow blocks the confirmation run before GPU generation. Output
`max_new_tokens` hits are preserved, recorded as `hit_max_new_tokens`, kept in
the denominator, and scored by the deterministic pipeline without raising the
token budget after seeing confirmation outputs.

No GPU command is required for Stage 5. A future confirmation run, after
reviewer acceptance and dataset registration, should execute on the server
under `/home/uet/hue_ptk/`.
