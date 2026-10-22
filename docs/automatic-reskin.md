# Reskin bằng AI: source + ZIP Figma

## Cách chạy

```powershell
cd D:\LazyReskin
python -m engine auto --source "D:\Apps\MyApp" --bundle "D:\Designs\figma.zip" --output runs/my-app-auto
```

Không cần tự tạo profile, chọn node/view hoặc viết mapping JSON. Đó là đầu ra của AI. Input bắt buộc là source hiện có, bundle Figma và thư mục output mới.

Điều kiện một lần: Python/Pillow, Codex CLI đã đăng nhập, Android build environment phù hợp source, một emulator/thiết bị ADB đã authorized. Nếu có nhiều thiết bị, chọn `--serial <id>`. Engine không tự đăng nhập hoặc cài SDK.

Lập mapping và dry-run không cần thiết bị:

```powershell
python -m engine auto --source "D:\Apps\MyApp" --bundle "D:\Designs\figma.zip" --output runs/my-app-plan --dry-run
```

`--dry-run` vẫn gọi AI để đọc source/bundle. Đây không phải tên khác của heuristic matching.

## Luồng tự động

1. Kiểm tra input; với execution, kiểm tra thiết bị ADB trước khi gọi model.
2. Copy source thành workspace riêng; bỏ Git/cache/build outputs thông thường, giữ source và thay đổi chưa commit hiện tại. Không follow symlink source.
3. Đọc manifest/module/resource roots, tạo source index và context bundle local.
4. Xử lý từng JSON màn hình. Agent đọc code, Figma và screenshot tham chiếu để đề xuất layout, assemble task, Activity, ready view và mappings.
5. Agent trả JSON theo contract. Không trả shell script hoặc tự sửa source.
6. Engine xác minh module/layout/IDs/assets/attributes và tạo profile/skin/staged patch.
7. Baseline build/capture → apply patch → build/capture/compare → Activity ready-state smoke.
8. Với lỗi mapping/visual, chuyển issues và ảnh actual/diff cho agent tạo proposal mới. Candidate chưa đạt được rollback trước candidate tiếp theo.
9. Giữ bản đạt, hoặc phục hồi candidate có score tốt nhất nếu hết budget. Dừng sau hai candidate không cải thiện.
10. Kiểm tra lại các màn đã đạt trên source cuối cùng để phát hiện hồi quy do màn làm sau. Hai state Figma cần giá trị khác nhau trên cùng thuộc tính của cùng view sẽ được báo cần selector/state mapping, không ghi đè qua lại rồi báo cả hai đạt.
11. Xuất report toàn job và report từng candidate. Source gốc không được merge tự động.

## Đầu ra

```text
runs/my-app-auto/
├── workspace/                 # Source làm việc và kết quả reskin
├── context/                   # Bundle/source index để AI đọc
├── proposals/<screen>/<n>/
│   ├── request.json
│   ├── response.json
│   ├── project.json           # Engine tạo; không phải người dùng điền
│   ├── skin.json
│   └── agent.*.log
├── candidates/<screen>/<n>/   # Diff, backup, capture, visual issues
├── auto-checkpoint.json
└── report.html
```

Chỉ dùng workspace làm kết quả khi report cho biết trạng thái phù hợp. Candidate tốt nhất vẫn có thể chưa đạt; `partial` không có nghĩa app đã giống Figma toàn bộ.

## AI provider

Mặc định gọi `codex exec` với `--sandbox read-only`, `--output-schema`, `--output-last-message` và ảnh tham chiếu/feedback. CLI sử dụng cấu hình/đăng nhập sẵn có; không đổi model hoặc xin API key mới trong engine.

Lệnh non-interactive và JSON-schema output dựa trên [tài liệu Codex chính thức](https://developers.openai.com/codex/noninteractive). Luồng proposal → validation → feedback tham khảo [iterative repair loops](https://developers.openai.com/cookbook/examples/codex/build_iterative_repair_loops_with_codex).

Có thể dùng AI khác qua `--agent-command path/to/adapter.json`. File này là mảng argv của adapter tin cậy, ví dụ `["python", "D:/tools/my_agent_adapter.py"]`. Engine thêm `--request <json>` và `--output <json>`; adapter đọc request, gọi provider và ghi response đúng `schema.json`. Không phải CLI nào cũng hỗ trợ trực tiếp giao thức này. Chưa có adapter AGY tích hợp sẵn.

Không dùng các cờ bypass sandbox/approvals. Trường hợp CLI chưa đăng nhập, provider lỗi hoặc output sai contract sẽ dừng job thay vì gọi lại mọi màn liên tục.

## Giới hạn và chính sách

- Mặc định tối đa 6 proposal/màn; `--max-iterations` cho phép 1–12. `--agent-timeout` mặc định 600 giây/lần gọi.
- Chọn subset bằng `--screens home_default_1_1.json settings_default_2_1.json`; không chọn thì xử lý mọi JSON trong bundle.
- Policy ảnh mặc định: pixel tolerance 8, mismatch ratio 0.01. Có thể đổi qua CLI `--pixel-tolerance`, `--max-mismatch-ratio`; agent không được tự nới ngưỡng hoặc mask ảnh để báo đạt.
- Đây là policy ban đầu, cần hiệu chỉnh theo renderer/viewport. Điểm ảnh đạt không bảo đảm mọi chi tiết giống tuyệt đối. Engine so trên kích thước tham chiếu thực, không tự resize để ép khớp.
- Scope chỉ màu/icon. Gradients, typography, bố cục, runtime override cần sửa code hoặc SVG ngoài subset sẽ được báo; không tự viết lại app.
- Hiện agent chọn các màn mở được bằng declared Activity + observable ready view. Luồng Fragment nhiều bước hoặc trạng thái cần tương tác phức tạp chưa có navigation planner tự động.
- APK được tìm từ Gradle output-metadata theo variant, không bắt người dùng đoán tên file. Split-only APK không có universal output sẽ báo chưa hỗ trợ.
- Smoke mặc định chỉ xác nhận Activity mở được và view/text sẵn sàng. Không thay cho kiểm thử toàn bộ chức năng hoặc navigation của app.
- Snapshot/build/capture chạy local. Input source và ảnh cần thiết được proposal agent đọc để xử lý job.
- `auto-checkpoint.json` là báo cáo tiến độ hiện tại; chưa có resume toàn bộ auto job sau khi process crash. Các candidate riêng vẫn có checkpoint và rollback của engine. Khi cần chạy lại, dùng output mới và source/workspace đã chọn có chủ đích.

## Trạng thái kiểm chứng

Tests dùng provider subprocess giả lập để kiểm tra tự tạo mapping/config, sửa theo feedback, vòng lặp, best candidate, protocol lỗi, command restriction và bảo toàn source gốc. Tests không gọi dịch vụ AI thật hoặc giả làm đã render Android.

Chưa có source + bundle Figma mới + thiết bị trong phiên này để kiểm chứng đầu-cuối với Codex và Android thật. Report thực phải ghi đúng phần đạt/chưa đạt; không suy ra kết quả app từ việc tests của engine đã qua.
