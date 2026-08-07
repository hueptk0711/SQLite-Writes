# Các bước tiếp theo

## Bạn cần làm ngay

1. Điền `01_manuscript/submission/ieee_access_latex_20260801/AUTHOR_METADATA_TEMPLATE_VI.md`.
2. Quyết định license code và quyền phân phối dataset theo
   `00_START_HERE/PUBLIC_RELEASE_BLOCKERS.md`.
3. Gửi lại cho tôi metadata đã chốt để tôi ghép vào `paper/main.tex`, supplement,
   `CITATION.cff` và checklist.

## Sau khi có metadata

1. Sinh bản nộp chính thức từ
   `MP_FS_PLUS_IEEE_ACCESS_OVERLEAF_REVISION_V2_5_20260802.zip` bằng pdfLaTeX.
2. Kiểm tra source/PDF khớp nhau, tác giả/bio/funding đúng, các liên kết và trích
   dẫn không lỗi.
3. Kiểm tra similarity và ngôn ngữ lần cuối.
4. Chỉ khi code/data license đã rõ mới phát hành kho công khai và điền URL vào bài.

## Không cần làm lại

- Không chạy lại primary GPU matrix.
- Không chạy lại Qwen-14B hoặc Yi-Coder-9B robustness; cả hai artifact đã import,
  audit và gắn nhãn hậu nghiệm.
- Không sửa phương pháp rồi tái dùng 300 mẫu consumed để gọi là blind test.
- Không cần CAL-A01 hoặc hai reviewer cho reporting v2.3; dữ liệu đã khóa.

## Nếu muốn tăng độ mạnh của bài

Runnable end-to-end component ablation, paired prompt-format study hoặc
PostgreSQL là nghiên cứu bổ sung và cần thiết kế protocol/holdout mới. Cross-family
Yi-Coder-9B đã hoàn tất dưới nhãn hậu nghiệm và không còn là mục mở.
