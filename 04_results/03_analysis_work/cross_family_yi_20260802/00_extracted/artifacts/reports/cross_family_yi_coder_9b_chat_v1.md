# Post-hoc cross-family robustness — Yi-Coder-9B-Chat

This is not a blind primary result. The consumed holdout is reused, and no prompt, method, or threshold was tuned after protocol freeze.

| Method | Target | Execution | Coverage | Admitted accuracy | Off-target rate | Generation failures | Input trunc. | Output-limit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| D-FS-M | 0.5833 | 0.6167 | 1.0000 | 0.5833 | 0.0000 | 0 | 0 | 5 |
| J-FS-M | 0.1900 | 0.2033 | 0.2067 | 0.9194 | 0.0000 | 0 | 0 | 3 |
| MP-FS+ | 0.3700 | 0.3833 | 0.3833 | 0.9652 | 0.0000 | 0 | 0 | 3 |

Interpretation must remain post-hoc external-model robustness. A different family does not convert this reused holdout into an independent confirmation set.
