# Báo cáo dọn workspace — 2026-08-03

## Đã xóa

- Năm cây giải nén `release_validation_v2_4_final_*`.
- Thư mục `tmp/` chứa bản giải nén và ảnh render trung gian.
- ZIP workspace cũ ngày 2026-07-31.
- Release ZIP v2.2--v2.4 và checksum tương ứng.
- Gói Overleaf trước v2.6 và checksum tương ứng.
- `.venv`, `.pytest_cache`, `__pycache__`, `*.egg-info` và ba thư mục tạm bị khóa.
- Ảnh visual-QA và file phụ trợ LaTeX có thể tạo lại (`*.aux`, `*.log`, `*.blg`).

Dung lượng thu hồi xấp xỉ 1,11 GB. Các mục đã xóa trực tiếp, không chuyển vào
Recycle Bin.

## Đã giữ

- Toàn bộ raw archive, prediction, adjudication, audit và primary results.
- Code inference/GPU đóng băng và các phiên bản reporting cần cho traceability.
- Kết quả reporting v2.2--v2.4, robustness 14B và cross-family Yi-Coder-9B.
- Gói tái lập cuối, Overleaf v2.6, báo cáo clean extraction và log Linux.
- `99_archive_history_20260731` bên ngoài workspace và `server_results/` trong
  `CODE_AND_RESULTS`.

Checksum các artifact cuối đã được đối chiếu lại sau khi dọn và không thay đổi.
