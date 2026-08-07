# Post-hoc second-model robustness audit

- Status: **PASS**
- Analysis class: post-hoc cross-model robustness; not a primary result.
- Model: Qwen2.5-Coder-14B-Instruct, 4-bit, deterministic decoding.
- Rows: 300 per method; identical sample IDs and order.
- Mechanical failures: 0 truncations, 0 output-limit hits, 0 missing generations.
- Archive SHA-256: `554efdc84ef7e15d00fb0ebe7fc21b7f27a72457f534622f39c4d70966821d37`
- Frozen second-model protocol SHA-256: `449ddf102dc99ffe74add9048e4dc6254ca4ac622bf379d282855a7818876f40`

| Method | 7B target | 14B target | Delta | 7B coverage | 14B coverage | Delta | 7B accepted acc. | 14B accepted acc. | Delta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| D-FS-M | 0.8600 | 0.9233 | +0.0633 | 0.9967 | 0.9967 | +0.0000 | 0.8629 | 0.9264 | +0.0635 |
| J-FS-M | 0.8600 | 0.8800 | +0.0200 | 0.8867 | 0.9033 | +0.0167 | 0.9699 | 0.9742 | +0.0042 |
| MP-FS+ | 0.4933 | 0.5300 | +0.0367 | 0.5467 | 0.5900 | +0.0433 | 0.9024 | 0.8983 | -0.0041 |

The larger same-family model improves target-state accuracy for all three methods, while the ordering D-FS-M > J-FS-M > MP-FS+ remains unchanged. Because the already-consumed holdout was reused, these values are labeled post-hoc robustness evidence and do not replace or modify the frozen 7B primary results.
