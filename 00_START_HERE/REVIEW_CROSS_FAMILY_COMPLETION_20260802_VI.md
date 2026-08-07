# Hoàn tất nhận xét cross-family — manuscript v2.5

Ngày: 2026-08-02

## Kết luận

Nhận xét yêu cầu kiểm tra model family khác đã được xử lý bằng
`01-ai/Yi-Coder-9B-Chat`. Đây là **post-hoc external-model robustness** trên 300
mẫu đã consumed, không phải primary result hoặc blind test mới.

## Tính toàn vẹn

- Protocol được freeze trước prediction Yi-Coder đầu tiên.
- Snapshot revision:
  `356a1f8d4e4a606d0b879e54191ca809918576b8`.
- Protocol SHA-256:
  `a5e6fdbd7dcbb6621092ea94dbc57bc45a08fa2d41d16fc62ba43802b250c256`.
- Result archive SHA-256:
  `5e087344cea56d7401e7af57898aaaf9304bd7c03aef2352d61e197f007c441e`.
- Audit độc lập: 95/95 kiểm tra PASS; 300 hàng cho mỗi phương pháp, cùng ID và
  thứ tự; không missing prediction, input truncation hoặc generation failure.
- Có 11 output-limit hits (D=5, J=3, MP-FS+=3). Tất cả được giữ trong mẫu số và
  tính sai; không prediction nào được sinh lại hoặc sửa.
- Metric off-target đã được tính lại theo định nghĩa P0 từ strict mismatches và
  Gold-MP target tables: 0/300 cho cả ba phương pháp.

## Kết quả

| Method | Target | Coverage | Accepted accuracy | Qwen-7B delta |
|---|---:|---:|---:|---:|
| D-FS-M | 0.5833 | 1.0000 | 0.5833 | -0.2767 |
| J-FS-M | 0.1900 | 0.2067 | 0.9194 | -0.6700 |
| MP-FS+ | 0.3700 | 0.3833 | 0.9652 | -0.1233 |

Paired win/loss Qwen-7B--Yi-9B là 12/95, 3/204 và 2/39; Holm-adjusted exact
McNemar p lần lượt là `6.97e-17`, `4.31e-56`, `7.84e-10`. Thứ hạng đổi thành
D-FS-M > MP-FS+ > J-FS-M.

## Giới hạn diễn giải

Không quy thay đổi này riêng cho “family”: mô hình đồng thời khác parameter
count, pretraining/instruction tuning, tokenizer và chat template. Holdout đã
consumed nên kết quả chỉ hỗ trợ kết luận model--interface sensitivity và không
thay đổi bất kỳ primary score nào.

## Tệp chuẩn

- Incoming archive: `04_results/00_incoming_from_server/cross_family_yi_20260802/`.
- Extracted artifact: `04_results/03_analysis_work/cross_family_yi_20260802/00_extracted/`.
- Audit: `04_results/03_analysis_work/cross_family_yi_20260802/01_audit/`.
- Audit tool: `08_tools/audit_cross_family_results.py`.
- Manuscript source: `../PAPER_WRITING/01_manuscript/drafts/IEEE_ACCESS_DRAFT.md`.
