# Final external-holdout matrix

- Status: **PASS**
- Paper-result eligible: `true`
- Samples: `300`
- Methods: `6`
- Conservative adjudication: two degenerate output-limited `MP-FS+` generations remain in the 300-sample denominator and are scored as incorrect; no prediction was regenerated.
- Oracle gate correction: all 300 Gold-MP rows and perfect metrics were verified; a null metadata field had been misclassified as 300 missing predictions. No artifact was regenerated or rescored.

| Method | Target | Original request | State-changing | Conflict-sensitive | DB macro | Accepted accuracy | Coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| D-FS-M | 0.8600 | n/a | 0.8444 | 0.8500 | 0.8600 | 0.8629 | 0.9967 |
| J-FS-M | 0.8600 | n/a | 0.8444 | 0.8300 | 0.8600 | 0.9699 | 0.8867 |
| S-FS-v2-M | 0.2600 | n/a | 0.2889 | 0.0300 | 0.2600 | 0.2932 | 0.8867 |
| MP-FS-M | 0.1133 | n/a | 0.1111 | 0.0450 | 0.1133 | 0.5574 | 0.2033 |
| MP-FS+ | 0.4933 | n/a | 0.4704 | 0.4750 | 0.4933 | 0.9024 | 0.5467 |
| Gold-MP | 1.0000 | n/a | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

## Pre-registered paired comparisons

- MP-FS+ vs D-FS-M: difference=-0.3667; wins/losses=8/118; exact p=3.16332e-26; Holm p=1.26533e-25; clustered 95% CI=[-0.4267, -0.3067].
- MP-FS+ vs J-FS-M: difference=-0.3667; wins/losses=14/124; exact p=3.40125e-23; Holm p=6.80251e-23; clustered 95% CI=[-0.4333, -0.3033].
- MP-FS+ vs MP-FS-M: difference=0.3800; wins/losses=126/12; exact p=3.85533e-25; Holm p=1.1566e-24; clustered 95% CI=[0.3167, 0.4433].
- J-FS-M vs D-FS-M: difference=0.0000; wins/losses=24/24; exact p=1; Holm p=1; clustered 95% CI=[-0.0467, 0.0467].
