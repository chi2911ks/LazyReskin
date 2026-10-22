# LazyReskin

Workflow local để reskin màu/icon của app Android XML có source sẵn bằng bundle Figma.

- `plugin/figma-local-export-plugin/`: exporter Figma hiện có.
- `engine/`: CLI đọc source/bundle, tạo patch, build/capture, so ảnh và rollback.
- `skills/xml-reskin/SKILL.md`: hướng dẫn coding agent sử dụng engine và sửa qua nhiều candidate.
- [Kế hoạch](docs/reskin-implementation-plan.md) và [hướng dẫn engine](docs/reskin-engine.md).

## Bắt đầu

Python 3.10+. Chỉ phần so ảnh/tests ảnh cần Pillow:

```powershell
python -m pip install -r requirements.txt
python -m engine --help
```

### AI tự động: chỉ cần source và ZIP Figma

```powershell
python -m engine auto --source "D:\Apps\MyApp" --bundle "D:\Designs\figma.zip" --output runs/my-app-auto
```

AI tự đọc source, chọn module/layout, tạo profile và mapping, engine build/capture/so ảnh rồi gửi lỗi cho AI sửa tiếp. Source được copy vào `runs/my-app-auto/workspace`; bản gốc không được áp patch. Kết quả và các màn chưa đạt nằm trong `runs/my-app-auto/report.html`.

Cần Codex CLI đã đăng nhập và một thiết bị/emulator ADB đang kết nối. Không cần tự điền profile/skin JSON. Muốn chỉ lập mapping và dry-run trước:

```powershell
python -m engine auto --source "D:\Apps\MyApp" --bundle "D:\Designs\figma.zip" --output runs/my-app-auto-plan --dry-run
```

Xem [hướng dẫn auto](docs/automatic-reskin.md) về phạm vi, chính sách so ảnh, giới hạn và adapter AI khác.

### Chế độ cấu hình thủ công

Copy [project template](examples/project.template.json) và [skin template](examples/skin.template.json), sửa source/module/build variant, screen/node/view/asset mappings theo dự án thật. Đây là templates, không phải cấu hình app chạy được ngay.

```powershell
python -m engine inspect --project profiles/my-app.json --bundle exports/design.zip --output runs/inspect.json
python -m engine plan --project profiles/my-app.json --skin skins/forest.json --bundle exports/design.zip --run-dir runs/forest-plan
```

`plan` chỉ viết staged files, diff và report vào run directory; không sửa app. Đọc `runs/forest-plan/report.html`, `mapping.json`, `change-plan.json` và `patch.diff`.

```powershell
python -m engine run --project profiles/my-app.json --skin skins/forest.json --bundle exports/design.zip --run-dir runs/forest-run
python -m engine resume --run runs/forest-run
python -m engine rollback --run runs/forest-run
```

`run` build/capture baseline trước khi áp patch, rồi build/capture/compare và chạy các checks đã cấu hình. Nó có thể sửa **source được profile chỉ định**; dùng working copy/worktree phù hợp. Lệnh `apply` riêng cho phép áp patch chưa kiểm chứng runtime khi đó là chủ đích.

## Trạng thái thực tế

Bản đầu hỗ trợ `colors-icons`, mappings do AI tạo hoặc cấu hình tường minh, scoped resources, SVG subset/PNG, source conflicts, checkpoint, report HTML, ADB capture adapter và đối chiếu theo vùng. `appearance`, cấu trúc `layout` và GUI controller chưa được triển khai.

`auto` gọi agent để tạo/sửa proposal theo vòng lặp; các lệnh thủ công `plan/run/resume` không gọi AI. `resume` thử lại validation trên cùng source. Không có thiết bị/capture/smoke thì không được báo job đạt toàn bộ.

## Kiểm tra

```powershell
python -m unittest discover -s tests -v
```

Các tests dùng source/bundle fixture và subprocess fixture; không thay cho build/render app Android thật. Không có thay đổi được áp vào source V440 chỉ để chạy tests.
