# Post-hoc cross-family robustness audit

- Status: **PASS**
- Analysis class: post-hoc external-model robustness; not a primary result.
- Model: Yi-Coder-9B-Chat, 4-bit, deterministic decoding.
- Rows: 300 per method; identical sample IDs and order.
- The consumed holdout was reused; no prediction was regenerated.
- Output-limit hits were retained as incorrect under the frozen conservative policy.
- Corrected off-target counts: {'D-FS-M': 0, 'J-FS-M': 0, 'MP-FS+': 0}.
- Archive SHA-256: `5e087344cea56d7401e7af57898aaaf9304bd7c03aef2352d61e197f007c441e`
- Frozen cross-family protocol SHA-256: `a5e6fdbd7dcbb6621092ea94dbc57bc45a08fa2d41d16fc62ba43802b250c256`

| Method | 7B target | Yi-9B target | Delta | 7B wrong/Yi correct | 7B correct/Yi wrong | Exact McNemar p | Holm p | Coverage | Admitted accuracy | Output-limit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| D-FS-M | 0.8600 | 0.5833 | -0.2767 | 12 | 95 | 3.485e-17 | 6.971e-17 | 1.0000 | 0.5833 | 5 |
| J-FS-M | 0.8600 | 0.1900 | -0.6700 | 3 | 204 | 1.438e-56 | 4.313e-56 | 0.2067 | 0.9194 | 3 |
| MP-FS+ | 0.4933 | 0.3700 | -0.1233 | 2 | 39 | 7.84e-10 | 7.84e-10 | 0.3833 | 0.9652 | 3 |

Yi-Coder preserves the ordering D-FS-M > MP-FS+ > J-FS-M on target-state accuracy, but all three interfaces score below their Qwen2.5-Coder-7B primary counterparts. MP-FS+ remains the most reliable admitted Yi-Coder interface (111/115 correct) while admitting 115/300 samples. These results are sensitivity evidence only because the consumed holdout was reused.
