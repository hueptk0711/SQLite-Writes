# Báo cáo tách code/results và paper writing — 2026-08-03

## Cấu trúc

- `CODE_AND_RESULTS/`: code, protocol/data, results, reproducibility, tools,
  release artifacts và server downloads.
- `PAPER_WRITING/`: manuscript, LaTeX/Overleaf, references, previous paper assets
  và QA PDFs.

Cây kỹ thuật được quét đệ quy và có 0 file `.tex`, `.bib`, `.pdf`, đồng thời
không có thư mục manuscript/Overleaf. Cây viết bài không chứa `02_code`,
`04_results` hoặc các thư mục kỹ thuật tương đương.

## Xác minh sau khi tách

- One-command deterministic reproduction: PASS.
- 300 samples, 6 methods: PASS.
- Primary results unchanged: PASS.
- GPU required: false; model inference rerun: false.
- Cascade accuracy: 0.94; downstream variants: 4.
- Gói `mp_fs_plus_code_and_results_v1_20260803.zip`: 631 ZIP entries, 0 entry
  LaTeX/manuscript/BibTeX/PDF; clean extraction PASS.
- SHA-256:
  `419b716e641171e8de1b60d0626ee76fd82f50433e73c2ad26e99142596543c7`.

Gói hỗn hợp trước khi tách được lưu trong kho lịch sử, không còn là artifact
hiện hành. Việc tách không thay đổi prediction hoặc metric.
