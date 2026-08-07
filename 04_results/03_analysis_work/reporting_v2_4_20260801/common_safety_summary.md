# Post-hoc common transactional-preflight replay

Locked predictions are replayed without model inference. Primary results are unchanged.

| Method | Preflight coverage | Target after gate | Correct given gate | False accepts | False-accept rate |
|---|---:|---:|---:|---:|---:|
| D-FS-M | 0.8900 | 0.8600 | 0.9663 | 9 | 0.0337 |
| J-FS-M | 0.8800 | 0.8600 | 0.9773 | 6 | 0.0227 |
| MP-FS+ | 0.5467 | 0.4933 | 0.9024 | 16 | 0.0976 |

The common gate removes execution failures but cannot detect programs that execute successfully into the wrong target state.
