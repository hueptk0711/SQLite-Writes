# Bắt đầu từ đây — CODE_AND_RESULTS

Đây là workspace kỹ thuật hiện hành. Phần bản thảo và LaTeX nằm riêng tại
`../../PAPER_WRITING/`.

## Đọc theo thứ tự

1. `../README.md`: cài đặt, kiểm thử và lệnh tái lập.
2. `../EXPERIMENT_FREEZE.md`: ranh giới kết quả đã khóa.
3. `CURRENT_STATUS.md`: trạng thái kiểm định mới nhất.
4. `../docs/REPRODUCIBILITY.md`: bản đồ code–artifact–kết quả.
5. `../docs/ASSET_RIGHTS.md`: việc cần xác nhận trước khi phát hành công khai.

## Cây hiện hành

- `../src/`, `../tests/`, `../scripts/`, `../configs/`: cây code hoạt động duy nhất.
- `../archive/frozen_inference_source.zip`: source inference cũ đã đóng băng, chỉ
  dùng làm bằng chứng nguồn gốc.
- `../03_protocol_and_data/`: protocol và dữ liệu đã khóa.
- `../04_results/02_paper_ready/`: kết quả dùng cho bài báo.
- `../04_results/03_analysis_work/`: phân tích corrective/post-hoc và audit.
- `../07_reproducibility/`: checksum, provenance và biên bản server.
- `../09_release_candidate/`: gói reviewer và báo cáo kiểm định.

Mọi tác vụ reporting/test/release đều chạy CPU. Chỉ cần GPU nếu chủ động sinh
lại prediction, việc đó nằm ngoài phạm vi tái lập reviewer này.
