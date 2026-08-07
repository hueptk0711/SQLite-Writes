# Kết quả second-model Qwen2.5-Coder-14B

Trạng thái: **PASS**. Đây là phân tích độ nhạy hậu nghiệm trên cùng họ model,
không phải kết quả primary và không phải một blind holdout mới.

## Cấu trúc

- `00_extracted/`: bản giải nén làm việc từ archive server; không được sửa hoặc
  dùng để thay đổi prediction.
- `01_audit/SECOND_MODEL_IMPORT_REPORT.json`: biên bản kiểm toán máy đọc,
  72/72 điều kiện PASS.
- `01_audit/second_model_cross_model_table.csv`: bảng đối chiếu primary 7B với
  post-hoc 14B.
- `01_audit/second_model_robustness_summary.md`: bản tóm tắt dùng khi viết bài.

Archive bất biến và checksum được lưu tại:

```text
04_results/00_incoming_from_server/second_model_qwen14b/
```

Không cần chạy GPU lại. Muốn kiểm toán lại local, chạy:

```powershell
python .\08_tools\audit_second_model_results.py `
  --archive .\04_results\00_incoming_from_server\second_model_qwen14b\mp_fs_plus_second_model_qwen25_coder_14b_v1_20260801T064530Z.tar.gz `
  --checksum .\04_results\00_incoming_from_server\second_model_qwen14b\mp_fs_plus_second_model_qwen25_coder_14b_v1_20260801T064530Z.tar.gz.sha256 `
  --extracted-root .\04_results\03_analysis_work\second_model_qwen14b_20260801\00_extracted `
  --primary-table .\04_results\02_paper_ready\tables\final_main_table.csv `
  --output-dir .\04_results\03_analysis_work\second_model_qwen14b_20260801\01_audit
```

Khi ghi số liệu, luôn nêu rõ `post-hoc`, `same-family model-size robustness`,
và `consumed holdout reused`. Không gọi đây là cross-family validation hoặc
independent replication.
