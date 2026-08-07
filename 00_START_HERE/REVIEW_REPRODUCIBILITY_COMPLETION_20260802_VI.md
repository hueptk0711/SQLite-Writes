# Hoàn tất nhận xét reproducibility và submission hygiene — v2.6

## P0 kỹ thuật đã xử lý

1. `common_safety_replay.py` không còn dùng `Path(...).name` cho provenance path
   Windows. Nó ưu tiên `archive_filename`, dùng `portable_filename()` cho report
   cũ và kiểm SHA-256 trước replay.
2. Đã thêm regression tests cho provenance path Windows và `archive_filename`.
3. Đã thêm integration test/validator giải nén archive vào cây mới, bỏ inherited
   `PYTHONPATH`, không tạo symlink và chạy one-command reproduction.
4. README sạch dùng `pip install -e ".[test]"` và `python -m pytest -q` trên
   Linux/macOS và Windows.
5. Full validation: 136/136 tests, `compileall`, reproduction, primary unchanged
   và cross-family audit 95/95 đều PASS. Không chạy inference hoặc GPU.

Snapshot v2.5 trước khi tách workspace có SHA-256
`50032078dc1acc9132b229430bd51b18d71d746860c14e94e3e8766f73dc3925`.
Ngày 2026-08-03, snapshot đó được xác nhận trên một host Linux độc lập:
clean extraction PASS, 136/136 tests và 9 subtests PASS trong 13,80 giây; không
GPU, không model inference và primary results không đổi. Bằng chứng được giữ tại
`07_reproducibility/legacy_mixed_release_validation_20260803/`.

Sau khi tách LaTeX/manuscript sang `PAPER_WRITING`, artifact kỹ thuật hiện hành là
`09_release_candidate/mp_fs_plus_code_and_results_v1_20260803.zip`, SHA-256
`419b716e641171e8de1b60d0626ee76fd82f50433e73c2ad26e99142596543c7`.
Gói có 0 entry viết bài và clean-extraction reproduction đã PASS.

## Manuscript và packaging đã xử lý

- LiveSQLBench dùng tiêu đề chính thức và năm 2025; BIRD-INTERACT dùng record
  ICLR 2026; bibliography thống nhất 25 entries và bổ sung DOI/URL/page metadata.
- AI disclosure ghi thời gian, phạm vi, các việc AI không làm và trách nhiệm tác giả.
- Table 7 main chỉ giữ key ablation; bảng diagnostics đầy đủ được chuyển sang S7.
- Main QA 14 trang và supplement 3 trang; toàn bộ 17 trang đã render và kiểm tra,
  không thấy clipping, overlap hoặc bảng/hình vượt trang.
- Đã chuẩn bị CRediT intake, `CITATION.cff.template` và checklist quyền phát hành.

## Không thể tự quyết thay chủ bài

- Tên/thứ tự tác giả, affiliation, email, ORCID, corresponding author.
- Funding, conflict, biography, ảnh và CRediT role mapping.
- Code license, data/database/model-output redistribution rights.
- Repository URL, tag, Zenodo DOI và `CITATION.cff` cuối.
- Official pdfLaTeX build sau khi metadata hoàn chỉnh.

Các mục trên là thông tin/quyền pháp lý chưa được cung cấp, không phải lỗi code
hoặc thiếu GPU. Không được tự tạo placeholder giả thành metadata thật.
