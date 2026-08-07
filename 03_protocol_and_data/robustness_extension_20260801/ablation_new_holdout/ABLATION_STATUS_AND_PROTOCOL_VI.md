# Trạng thái ablation và protocol hợp lệ

## Kết luận kiểm tra code

File `configs/ablations/mp_fs_plus_components.json` chỉ liệt kê MP-0 đến MP-4 và
được đánh dấu `development_only=true`. Không có code path đọc trường
`components` để bật/tắt từng thành phần. Vì vậy các tên MP-0--MP-4 hiện chưa là
biến thể thực thi và chưa thể tạo bảng ablation khoa học.

## Công việc bắt buộc trước khi chạy

1. Viết một implementation/config độc lập cho từng biến thể, không chỉ đổi nhãn.
2. Mọi biến thể phải dùng cùng model, demonstrations, output cap, compiler cuối
   và transactional preflight; chỉ thành phần đang ablate được thay đổi.
3. Thêm unit test chứng minh mỗi switch thực sự thay đổi đúng một thành phần.
4. Chạy DEV/calibration; sửa lỗi chỉ ở đây.
5. Freeze code, prompt, model, dữ liệu, split, evaluator và bảng so sánh.
6. Giao một holdout mới cho người biên soạn và hai reviewer độc lập. Không dùng
   lại `archeology`, `polar`, `robot`, `vaccine`, `virtual`.
7. Chạy một lần sau freeze. Mẫu lỗi/truncation vẫn ở mẫu số; không regenerate.

## Ma trận tối thiểu

| Biến thể | Evidence grounding | Lossless guard | Hard verifier | Common preflight |
|---|---:|---:|---:|---:|
| MP-Base | không | không | không | có |
| MP-Evidence | có | không | không | có |
| MP-Norm | có | có | không | có |
| MP-Verify | có | có | có | có |
| MP-FS+ | có | có | có | có |

`Common preflight` phải giữ nguyên để không trộn lợi ích representation với
execution boundary. Với direct SQL, hard Write Plan verifier không áp dụng;
chỉ transactional preflight có thể chuẩn hóa trực tiếp.

## Metric bắt buộc

- Target-state và strict full-state accuracy.
- Execution success.
- Common-preflight coverage và false-accept rate.
- Method-specific admission coverage/accuracy, ghi rõ boundary.
- Constraint violation, side effect và abstention.
- Error taxonomy theo parse, grounding, planning, verification và preflight.
- Kết quả theo input format và single/multi-table.

Không được điền bảng ablation trong bài cho đến khi các gate trên hoàn tất.
