# Hướng dẫn engine reskin XML

## 1. Phạm vi đã triển khai

Engine Python chạy local, hỗ trợ giai đoạn nền tảng và MVP `colors-icons` của [kế hoạch triển khai](reskin-implementation-plan.md).

| Khả năng | Trạng thái |
| --- | --- |
| Đọc bundle thư mục/ZIP, root folder bọc ngoài, JSON/screenshot/asset manifest | Có |
| Quét XML, view IDs, resource consumers và gợi ý runtime styling | Có; phân tích code là heuristic, không phải compiler Kotlin/Java |
| Đề xuất mapping theo tên chuẩn hóa | Có; không tự áp gợi ý |
| Mapping node → view → thuộc tính rõ ràng | Có |
| Dry-run, diff, scoped colors/icons, giữ formatting và comments XML | Có |
| Source fingerprints, staged/backup hashes, write-ahead journal và rollback | Có |
| Baseline build/capture, validation, checkpoint/resume, HTML report | Có |
| ADB install/start/wait-for-view/capture adapter | Có; cần thiết bị hoặc emulator thực |
| So ảnh không resize, crop tường minh, mask có lý do, vùng quan trọng | Có |
| Giới hạn vòng validation, dừng không cải thiện, lưu best candidate evidence | Có |
| Agent đọc diff và tạo candidate mới bằng skill | Có hướng dẫn portable; không tự đăng ký skill toàn cục |
| Tự tạo profile/mapping và sửa proposal qua AI | Có lệnh `auto`; xem [hướng dẫn riêng](automatic-reskin.md) |
| Thay font/spacing/structure, custom renderer, GUI controller | Chưa |

## 2. Yêu cầu

- Python 3.10+.
- Pillow cho so ảnh và chạy tests ảnh. `inspect`, `plan`, `apply` và `rollback` dùng Python standard library.
- App/source hiện có; build command phù hợp với module/variant của app.
- ADB và thiết bị/emulator nếu dùng adapter capture có sẵn.
- Font scale, density, locale, theme, data và trạng thái capture do profile/môi trường kiểm soát.

Engine không tự cài SDK, khởi chạy emulator, nâng dependencies hoặc sửa lỗi build của source cũ. Muốn bỏ bước điền cấu hình thủ công, dùng `auto --source ... --bundle ... --output ...`; các mục profile/skin dưới đây là hợp đồng nội bộ và chế độ nâng cao.

## 3. Project profile

Xem [project.template.json](../examples/project.template.json).

`source` tương đối với thư mục chứa profile, hoặc đường dẫn tuyệt đối. `module` tương đối với source; mỗi `resource_roots` tương đối với module. Với flavor/source set khác, khai báo rõ resource roots được phép patch và sửa build/capture command tương ứng.

Các trường:

| Trường | Ý nghĩa |
| --- | --- |
| `source` | Source Android đích |
| `module` | Module app, mặc định `app` |
| `resource_roots` | Roots chứa resources được phép sửa, mặc định `src/main/res` |
| `build.command` | Mảng argv của lệnh build |
| `build.timeout` | Timeout 1–900 giây, mặc định 180 |
| `screens.<key>.capture.steps` | Danh sách command chuẩn bị/điều hướng |
| `screens.<key>.capture.command` | Command trả **PNG binary trên stdout**, logs trên stderr |
| `screens.<key>.capture.timeout` | Timeout cho mỗi capture/preparation command |
| `screens.<key>.comparison` | Cấu hình so ảnh |
| `smoke` | Danh sách command kiểm chứng chức năng; command exit 0 mới tính đạt |
| `regression` | Commands kiểm tra các vùng/màn liên quan |
| `max_iterations` | Giới hạn validation trên một run, mặc định 6, tối đa 30 |

Command là mảng strings, không phải shell string. Engine thay `{source}`, `{run}`, `{output}`, `{engine}` trong từng argument, không qua shell interpolation. Đây là commands do người cấu hình profile kiểm soát; không đưa text từ Figma thành executable command.

Build command chạy với working directory là source. `--output` của runner không có nghĩa command tự viết file: capture command phải trả PNG stdout. Logs, exit code và lỗi được lưu trong run.

### ADB capture adapter

```powershell
python engine/device.py --serial emulator-5554 --apk path/to/app.apk --activity com.example.app/.HomeActivity --wait-id com.example.app:id/homeRoot > capture.png
```

Dùng adapter qua runner để ghi binary chính xác; một số shell/phiên bản PowerShell có thể làm hỏng redirect binary khi dùng lệnh ví dụ trực tiếp.

Adapter cài APK nếu có `--apk`, start Activity, chờ view ID/text trong UIAutomator hierarchy rồi lấy screenshot. `--wait-id` hoặc `--wait-text` là bắt buộc. Chọn view thể hiện màn đã sẵn sàng, không chỉ một container có mặt khi vẫn loading. Với luồng nhiều màn, dùng capture steps hoặc instrumentation script riêng.

Adapter không tự cố định clock, dữ liệu mạng, orientation, density hoặc permissions. Cần cấu hình trạng thái phù hợp trước khi đối chiếu Figma. Chưa có thiết bị đang kết nối thì không có bằng chứng runtime.

## 4. Skin và mapping

Xem [skin.template.json](../examples/skin.template.json).

Một skin có `name`, `mode: colors-icons` và `screens`. Mỗi screen có:

- `key`: tên duy nhất, dùng để chọn subset và liên kết profile.
- `figma`: tên JSON trong `screens/` của bundle; chấp nhận cả prefix `screens/`.
- `layout`: đường dẫn layout tương đối với source, **bao gồm module**, ví dụ `app/src/main/res/layout/activity_home.xml`.
- `reference`: tùy chọn đường dẫn screenshot trong bundle. Mặc định ghép `screenshots/<tên-json>.png`.
- `mappings`: node ID, view ID hiện có và `properties` cần sửa.
- `capture`/`comparison`: tùy chọn override cấu hình profile cho screen.

Không có mapping, node không tồn tại, view ID không duy nhất, node ẩn hoặc thuộc tính vượt scope đều khiến plan cần input. Mappings chỉ thể hiện các mục tiêu được chỉ định, không chứng minh toàn bộ node Figma đã đạt.

### Color mappings

```json
{
  "node": "1:2",
  "view": "tvTitle",
  "properties": {
    "android:textColor": "textColor",
    "android:background": {"value": "#FF146353"}
  }
}
```

- `fill`: lấy một visible SOLID paint từ node.
- `textColor`: lấy paint của TEXT node.
- `{"value":"#AARRGGBB"}`: màu Android được chỉ định tường minh. `#RRGGBB`, `#RGB`, `#ARGB` cũng được chuẩn hóa.
- Figma `hex` là CSS order, được đổi sang Android order; nếu có raw color thì ưu tiên raw channels/opacity.
- Mixed/layered/gradient/image fill không bị làm phẳng thành một màu đoán. Plan báo lỗi để xử lý theo một mapping/adapter phù hợp.

Color attributes hỗ trợ: `android:background`, `textColor`, `textColorHint`, `tint`, `backgroundTint`; `app:tint`, `backgroundTint`, `cardBackgroundColor`, `strokeColor`.

Không tự thay background drawable thành solid color vì có thể làm mất corner/stroke. Với trường hợp này, map background tint hoặc asset đã xác minh phù hợp thay vì làm phẳng shape.

### Icon/image mappings

```json
{
  "node": "1:3",
  "view": "btnSettings",
  "properties": {"app:srcCompat": {"asset": "settings.svg"}}
}
```

Asset phải có đúng một record thành công trong manifest. Hỗ trợ `android:src`, `app:srcCompat`, `android:background`. `src`/`srcCompat` yêu cầu kiểu ImageView/ImageButton được nhận diện; custom view cần review thực tế rồi ghi `view_type_reviewed: true`.

- PNG dùng density folder theo manifest scale 1/2/3/4; không mặc nhiên đặt ảnh 2× vào drawable thường.
- SVG chỉ hỗ trợ subset groups/paths và paint/stroke cơ bản. Không bỏ qua mask, clip, transform, filters, CSS hoặc primitives không hỗ trợ. Chuẩn bị PNG fallback đã kiểm tra và map filename đó nếu cần.
- Engine tạo tên scoped có content hash; asset trùng tên không được tự gộp theo phỏng đoán.

### Runtime overrides

Source index ghi các setters/presentation assignments và vị trí. Nếu cùng dòng có target view ID, plan yêu cầu review. Sau khi kiểm tra nguồn và xác nhận override không làm mất tác dụng của patch, có thể ghi `runtime_reviewed: true` trên mapping.

Đây là heuristic và có thể bỏ sót alias, multiline call hoặc helper gián tiếp. Luôn kiểm tra code của màn và capture runtime; không coi danh sách rỗng là chứng minh không có override.

## 5. Commands và lifecycle

Chạy từ repository root:

```powershell
python -m engine inspect --project profiles/app.json --bundle exports/design.zip --output runs/inspect.json
python -m engine plan --project profiles/app.json --skin skins/forest.json --bundle exports/design.zip --run-dir runs/plan-001 --screens home
python -m engine apply --run runs/plan-001
python -m engine resume --run runs/plan-001
python -m engine report --run runs/plan-001
python -m engine rollback --run runs/plan-001
```

- `inspect`: read-only với app; có thể ghi chỉ mục vào `--output`.
- `plan`: run dir mới/rỗng, không sửa app. Có lỗi mapping vẫn ghi report để agent xử lý.
- `apply`: áp các staged files khi plan hợp lệ; **không build/capture baseline**. Dùng khi việc áp patch chưa kiểm chứng là chủ đích.
- `run`: tạo plan mới, baseline build/capture, apply, build/capture/compare và smoke/regression.
- `resume`: tiếp tục validation ở cùng source snapshot đã apply; không tự gọi AI và không tự sửa mapping.
- `report`: sinh lại HTML từ artifacts.
- `rollback`: kiểm tra mọi file thuộc transaction trước khi restore, không restore một phần khi phát hiện user edit xung đột.

`run` thiếu build command sẽ dừng `needs-input` trước khi apply. Baseline build/capture lỗi cũng không apply. Sau apply, build/capture lỗi được báo trong checkpoint; source không tự rollback để người dùng/agent xem và xử lý.

Status quan trọng:

| State | Ý nghĩa |
| --- | --- |
| `planned` | Staged dry-run hợp lệ |
| `needs-input` | Thiếu mapping, dữ liệu hoặc cấu hình kiểm chứng |
| `patched` | Patch đã áp, chưa xác nhận runtime |
| `baseline-build-failed` / `baseline-capture-failed` | App cũ chưa qua bước nền; chưa áp patch |
| `build-failed` / `capture-failed` / `visual-failed` | Candidate đã áp chưa đạt gate tương ứng |
| `visual-passed-unverified` | Ảnh đạt nhưng chưa có smoke command |
| `completed` | Build/capture/visual và các smoke/regression commands được khai báo đạt |
| `iteration-stalled` / `iteration-limit` | Dừng retry candidate, cần chẩn đoán/replan |
| `rolled-back` | Đã khôi phục thay đổi của run |

`completed` chỉ xác nhận màn/state và checks đã khai báo, không phải toàn bộ app hoặc toàn Figma. Chất lượng smoke command thuộc trách nhiệm cấu hình profile.

Exit codes: `0` cho thao tác thành công, plan hợp lệ, patched/rolled-back hoặc các gate đạt; `2` cho trạng thái chưa đủ/không đạt; `1` cho lỗi cấu hình, filesystem, source conflict hoặc transaction. Đọc state thay vì chỉ dựa vào exit 0 của `apply`.

## 6. So ảnh

```powershell
python -m engine compare --reference reference.png --actual actual.png --config comparison.json --output runs/compare
```

```json
{
  "pixel_tolerance": 8,
  "max_mismatch_ratio": 0.01,
  "regions": [
    {"key": "settings-icon", "bounds": [300, 24, 24, 24], "max_mismatch_ratio": 0.001}
  ],
  "masks": [
    {"bounds": [0, 0, 100, 20], "reason": "system clock outside design content"}
  ],
  "max_mask_ratio": 0.1
}
```

Các số trên chỉ minh họa, cần hiệu chỉnh bằng dữ liệu đạt/chưa đạt của app. `max_mismatch_ratio` là bắt buộc, không tự gán một ngưỡng để báo PASS.

- Bounds là `[x,y,width,height]` pixel nguyên trên ảnh sau crop.
- `reference_crop`/`actual_crop` là lựa chọn tường minh cùng định dạng bounds; không có resize/căn ảnh tự động.
- Kích thước hai ảnh khác nhau tạo issue viewport-mismatch.
- Pixel mismatch khi độ lệch kênh lớn nhất vượt tolerance; từng vùng phải qua threshold riêng.
- Báo cả mean absolute error, diện tích mask và số pixel thực được đánh giá.
- Mask thiếu lý do, vượt giới hạn hoặc che toàn vùng quan trọng bị từ chối.
- Ảnh transparency được composite trên cùng background, mặc định trắng; có thể đặt `background`.

Report gồm reference/actual/overlay/diff và mask thực tế. Metric tổng không thay thế kiểm tra vùng nhỏ hoặc nguồn của sai lệch.

## 7. Vòng lặp cùng coding agent

Đọc [skill XML reskin](../skills/xml-reskin/SKILL.md) khi thực hiện job.

1. Agent inspect, lập mapping và chạy plan/run.
2. Đọc issues/report và kiểm tra source ở vùng nghi ngờ.
3. Nếu lỗi trong scope: sửa cấu hình/mapping/asset cần thiết, tạo **run mới** từ source hiện tại. Khi source đã thay đổi, không resume checkpoint cũ.
4. Giữ report/backup của candidate trước. Rollback child run trước parent khi cùng sửa một file.
5. Dừng theo ngân sách candidate của toàn job, mặc định 6/màn và 2 lần không cải thiện. Engine giới hạn retry trên một run; agent chịu trách nhiệm ngân sách xuyên nhiều run.
6. Nếu cần sửa layout/font ngoài capability hiện tại, báo rõ phạm vi chưa hỗ trợ; không đổi mode chỉ để lách validator.

Best candidate hiện lưu bằng chứng/fingerprint và score trong một run. Engine chưa tự lưu/khôi phục toàn bộ workspace cho nhiều nhánh candidate hoặc tự chọn giữa nhiều run; dùng worktree/snapshots và reports khi agent làm vòng lặp này.

## 8. Transaction, conflicts và rollback

- Trước apply, so snapshot source đã index và hash từng target; file mới phát sinh ở vị trí tạo resource cũng được kiểm tra.
- XML chỉ thay giá trị/thuộc tính được map, giữ comments/formatting, không serialize lại toàn layout.
- Resource mới mang tên scoped; file đã có nhưng bytes khác bị coi là collision/user modification.
- Write-ahead journal ghi file trước khi thay, cho phép rollback apply bị gián đoạn.
- Trước rollback, kiểm tra tất cả target phải còn đúng before/after hash. Có một conflict thì không restore file nào.
- Không xóa các thư mục rỗng hay thay đổi unrelated files khi rollback.
- Source/run locks dùng file trong temporary directory, có PID. Nếu tiến trình crash để lại lock, kiểm tra PID trước khi xóa lock được báo trong thông điệp lỗi.

Snapshot bao phủ source XML/Kotlin/Java trong module, binary resources của roots khai báo và các build files chính. Không phải snapshot Git toàn repository; source ngoài module và công cụ bên ngoài cần được profile/workspace quản lý thêm.

## 9. Kiểm chứng hiện tại và giới hạn pilot

Automated tests bao phủ nguồn fixture, directory/ZIP bundles, patch preserving XML, source conflict, rollback, idempotence, runtime-review gate, unsupported SVG, image-region comparison và command orchestration.

Đã chạy inspect read-only trên source V440 tham khảo: tìm được 23 layouts, 10 JSON Figma và 123 dòng có dấu hiệu runtime styling. Bộ `.figma` cũ thiếu screenshots/asset manifest theo bundle mới; không được dùng để tuyên bố reskin đã khớp Figma.

Tại lần kiểm tra môi trường triển khai đầu, `adb devices -l` không có thiết bị/emulator đang kết nối. Vì vậy chưa có build/render/smoke end-to-end trên app V440 thật và chưa áp patch vào source đó. Cần bundle export mới và emulator/thiết bị để chạy pilot thực.

Built-in skill validator cần PyYAML không có trong môi trường hiện tại; frontmatter và reference links của portable skill được kiểm tra bằng standard library. Đây không ảnh hưởng các commands engine.
