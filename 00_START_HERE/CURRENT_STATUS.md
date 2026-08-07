# Trạng thái hiện hành — reviewer release 2026-08-05

## Đã hoàn thành

- Workspace kỹ thuật và workspace viết bài đã tách riêng.
- Code hoạt động đã được chuẩn hóa về thư mục gốc; không còn `02_code/` trong
  cây hiện hành.
- Source inference ban đầu được bảo toàn trong
  `archive/frozen_inference_source.zip` kèm SHA-256.
- Evaluator strict/off-target mặc định so sánh toàn bộ persistent user tables.
- Audit CPU trên 1.800 cặp method–sample PASS: 0 sai khác target, strict và
  off-target; các anchor primary không đổi.
- Có regression tests cho side effect trên bảng không được nhắc trong SQL,
  quoted/schema-qualified identifiers và thay đổi hợp lệ do trigger.
- Có lệnh tái lập CPU tại root, pytest markers, progress/timing, timeout và
  clean-extraction validator.
- Không prediction nào được sinh lại và không cần GPU.

## Kết quả primary đã khóa

| Method | Correct / 300 | Target-state accuracy |
|---|---:|---:|
| D-FS-M | 258 | 0.8600 |
| J-FS-M | 258 | 0.8600 |
| S-FS-v2-M | 78 | 0.2600 |
| MP-FS-M | 34 | 0.1133 |
| MP-FS+ | 148 | 0.4933 |
| Gold-MP | 300 | 1.0000 |

Cascade, downstream ablation, backend swap, Qwen-14B và Yi-9B phải tiếp tục
được ghi rõ là post-hoc/exploratory, không phải kết quả primary.

## Còn chờ chủ bài xác nhận

- License code và quyền tái phân phối dataset/artifact.
- Tác giả, funding, conflicts, acknowledgments.
- Public repository URL/DOI và `CITATION.cff`.

Chi tiết kiểm thử và hash của gói cuối nằm trong `09_release_candidate/` sau lần
đóng gói và kiểm định cuối.
