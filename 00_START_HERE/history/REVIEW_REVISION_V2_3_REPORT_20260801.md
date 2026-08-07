# Báo cáo xử lý nhận xét — reporting v2.3

## P0 — đã hoàn tất

| Nhận xét | Xử lý | Bằng chứng |
|---|---|---|
| Metric side effect bỏ sót target sai + off-target change | Sửa evaluator và reporting; giữ `side_effect_rate` làm alias tương thích của `any_off_target_change_rate` | `04_results/03_analysis_work/reporting_v2_3_20260801/off_target_metric_audit.json` |
| Hai ca D-FS-M/MP-FS-M bị báo zero | Xác nhận và công bố 1/300 cho mỗi phương pháp | `off_target_changes.csv` |
| Clean Linux reproduction lỗi basename Windows | Thêm `portable_filename()` và metadata basename | `tests/test_reporting_v2_3.py` |
| Thiếu regression tests | Thêm test evaluator, metrics, reporting và path Windows | 128/128 tests PASS |
| Claim quá mạnh ở mức thành phần | Chuyển thành kết luận system-level của cấu hình MP-FS+ đã đánh giá | `01_manuscript/drafts/IEEE_ACCESS_DRAFT.md` |
| Hình 3 có nhãn E1/E8 | Đổi toàn bộ sang nhãn xuất bản đầy đủ | `paper/figures/mp_fs_plus_error_taxonomy.pdf` |
| Metadata/license/footer | Đã tạo template và checklist; giữ blocker vì không được tự bịa thông tin tác giả/giấy phép | `AUTHOR_METADATA_TEMPLATE_VI.md`, `PUBLIC_RELEASE_BLOCKERS.md` |

## P1 — đã hoàn tất trong phạm vi artifact hiện có

| Nhận xét | Xử lý |
|---|---|
| Định vị bài | Title/abstract/conclusion định vị là controlled empirical comparison |
| Related Work còn mỏng | Mở rộng các nhóm text-to-SQL robustness, constrained generation, selective prediction và LLM tool use; bibliography có 25 entries |
| Thiếu efficiency | Thêm mean/median token và generation latency; không suy diễn end-to-end latency chưa đo |
| Đóng góp dài | Rút từ 7 xuống 4 đóng góp |
| Bảng hậu kiểm quá dày | Chuyển plan metrics, 14B robustness và backend swap sang supplement |
| Bootstrap diễn giải chưa rõ | Nêu rõ resampling unit là database; sample-level analysis chỉ là kiểm tra bổ sung |
| “External holdout” dễ gây hiểu nhầm | Dùng “reserved cross-database holdout” trong bản thảo |

## P2 — cập nhật 2026-08-02

Cross-family model đã được thực hiện sau đó bằng Yi-Coder-9B-Chat với protocol
freeze-before-inference, đủ 900 generation records và audit độc lập 95/95 PASS.
Kết quả được ghi nhãn post-hoc sensitivity vì dùng lại holdout đã consumed; xem
`REVIEW_CROSS_FAMILY_COMPLETION_20260802_VI.md`. Prompt formatting pairs,
runnable end-to-end component ablation và PostgreSQL chưa được tuyên bố là đã
hoàn tất; chúng vẫn cần protocol, implementation và holdout mới.

## Tính toàn vẹn

- Locked predictions modified: `false`.
- Primary metrics modified: `false`.
- Database execution repeated for v2.3: `false`.
- GPU required for v2.3: `false`.
- Unit tests: `128/128 PASS`.
- Off-target audit: `PASS`, counts `[1,0,0,1,0,0]` theo thứ tự sáu phương pháp.
- Clean one-step reproduction: `PASS`.
