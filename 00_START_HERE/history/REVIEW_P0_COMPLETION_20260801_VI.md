# Báo cáo hoàn thành nhận xét P0 — v2.4

Ngày: 2026-08-01

## Kết luận

Toàn bộ công việc kỹ thuật và khoa học có thể hoàn thành mà không tạo mẫu mới
đã được xử lý trên 300 mẫu frozen. Không prediction nào được sinh lại, không số
primary nào thay đổi và không cần GPU.

## Nội dung đã hoàn thành

- Định vị lại bài thành controlled empirical study về representation,
  deterministic backend và transactional admission; viết lại title/abstract,
  bổ sung RQ1–RQ5 và giữ đúng bốn đóng góp.
- Thêm cascade hậu nghiệm J-then-D dưới common transactional preflight:
  282/300 đúng, 289/300 admitted, accepted accuracy 282/289, 7 false accepts,
  11 abstentions và 0 off-target event quan sát.
- Thêm downstream deterministic ablation V0–V3 trên 217 frozen MP-FS+ plans;
  V3 hash/metric anchor tái tạo đúng accuracy 0.4933 và coverage 0.5467.
- Thêm Wilson 95% CI, bound một phía cho 0/300, exact McNemar và Holm correction
  riêng cho paired 7B–14B.
- Audit exact/value-masked/near duplicate, calibration–final và demo–final;
  xuất cả dữ liệu per-sample và hash manifest.
- Sửa mô tả authoring/review: một designated author, hai reviewer độc lập,
  254 revision-1 và 46 revision-2; giải thích vì sao không báo Cohen's kappa.
- Bổ sung fairness appendix, efficiency quantiles, free-text cost analysis,
  backend swap và thuật ngữ `generation returned`.
- Chuẩn hóa 25 tài liệu tham khảo; bỏ citation trang sản phẩm AI và viết AI
  disclosure cụ thể trong Acknowledgment.
- Tạo một lệnh tái lập cả v2.3 và v2.4:
  `python reproduce_paper.py --artifact final_release`.

## Kiểm định cuối

- Canonical reproduction: PASS; archive/protocol SHA-256 đúng; 300 mẫu, 6 phương pháp.
- Fresh-extract reproduction từ release v2.4: PASS.
- Unit tests canonical và fresh-extract: 133/133 PASS.
- Main QA PDF: 13 trang; supplement: 2 trang; đã render và kiểm tra đủ 15 trang,
  không thấy clipping/overlap.
- Release nội bộ:
  `09_release_candidate/mp_fs_plus_reporting_v2_4_release_candidate_20260801.zip`.
- SHA-256 hiện hành được ghi trong file `.zip.sha256` nằm cạnh release; luôn dùng
  file này thay vì chép hash từ tài liệu khác.

## Tệp kết quả chính

- `04_results/02_paper_ready/`: primary đã khóa.
- `04_results/03_analysis_work/reporting_v2_4_20260801/`: cascade, uncertainty,
  ablation, paired scale tests, efficiency và redundancy audit.
- `02_code/reporting_amendment_v2_4_20260801/source_report_v2_4/`: mã và 133 tests.
- `01_manuscript/drafts/IEEE_ACCESS_DRAFT.md`: bản thảo nguồn v2.4.
- `01_manuscript/submission/ieee_access_latex_20260801/`: nguồn IEEE Access,
  supplementary material và gói Overleaf v2.4.

## Việc chỉ chủ bài có thể hoàn thành

Không được tự suy đoán: tên/thứ tự tác giả, affiliation, địa chỉ, email,
corresponding author, ORCID, biography/ảnh, funding, conflict of interest,
contributor acknowledgment, code/data license, public repository URL/DOI và
`CITATION.cff` hoàn chỉnh.

Model khác family và PostgreSQL là mở rộng tùy chọn, không phải điều kiện P0.
Nếu thực hiện, phải freeze protocol/holdout mới và ghi nhãn hậu nghiệm phù hợp.
