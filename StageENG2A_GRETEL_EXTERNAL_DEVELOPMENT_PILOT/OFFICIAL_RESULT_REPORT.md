# Stage ENG2A Official Server Result Report

stage=StageENG2A_GRETEL_EXTERNAL_DEVELOPMENT_PILOT
patch=PATCH3
backend=hf
status=PASS
pilot_n=100
model_calls_total=300
model_calls_per_sample_per_method=1
retry_count=0
runtime_profile=uet_rtx4090_cuda124_visible0
model_revision=c03e6d358207e414f1eca0bb1891e29f1db0e242
server_result_archive=stageeng2a_gretel_external_development_pilot_uet_rtx4090_results_20260903.tar.gz
server_result_archive_sha256=22492d6cdb1638761b0873cbd768b8be19f0dcb6c8c3148f5cac663bc5f7c728

| Method | Target State | Exec. Success | Admission | Accepted Correct | Off-target | Calls | Tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| M0_DIRECT_SQL | 96/100 | 100/100 | 100/100 | 96/100 | 6 | 100 | 38464 |
| M1_J_FS | 87/100 | 91/100 | 91/100 | 87/91 | 4 | 100 | 41427 |
| M2_FROZEN_A7 | 50/100 | 68/100 | 68/100 | 50/68 | 18 | 100 | 104628 |

Notes:
- These are official HF model outputs from the one-off UET RTX 4090 server run.
- The bundled mock dry-run remains a wiring check only and is not used as a scientific result.
- No retry or automatic repair was used.
