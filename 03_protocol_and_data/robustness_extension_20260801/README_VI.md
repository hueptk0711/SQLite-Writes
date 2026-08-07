# Gói thí nghiệm bổ sung sau phản biện

Gói này không thay đổi kết quả chính 300 mẫu và không sửa MP-FS+ dựa trên
holdout đã xem. Nó chuẩn bị hai nhánh hợp lệ:

1. `second_model/`: chạy hậu nghiệm ba phương pháp D-FS-M, J-FS-M và MP-FS+
   trên Qwen2.5-Coder-14B-Instruct đã có trong cache server. Đây là robustness
   analysis, không phải blind primary result.
2. `ablation_new_holdout/`: đặc tả cho ablation có kiểm soát trên dữ liệu mới.
   Cấu hình MP-0 đến MP-4 hiện có chỉ là đặc tả khái niệm, chưa phải biến thể
   thực thi; vì vậy tuyệt đối không được điền bảng kết quả giả hoặc chạy lại
   chúng trên holdout 300 rồi gọi là blind test.

## Yêu cầu GPU

Nhánh second-model cần GPU. Mô hình mặc định:

```text
$HOME/hue_ptk/hf_cache/hub/models--Qwen--Qwen2.5-Coder-14B-Instruct/snapshots/aedcc2d42b622764e023cf882b6652e646b95671
```

Các lệnh đầy đủ nằm trong `second_model/RUN_ON_SERVER_VI.md`.

## Điều kiện kết luận

- Có thể hoàn thiện báo cáo v2.2 và viết bài mà không chạy GPU.
- Muốn đáp ứng nhận xét “ít nhất một model khác/lớn hơn”, phải chạy nhánh
  second-model một lần sau khi protocol được freeze.
- Muốn tuyên bố đóng góp thành phần MP-0--MP-4, phải triển khai biến thể,
  kiểm thử, freeze, rồi đánh giá trên một holdout mới độc lập.
