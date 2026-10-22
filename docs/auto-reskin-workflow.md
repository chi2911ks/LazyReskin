# Workflow auto reskin: Figma → Android XML

> Đây là tài liệu khảo sát ban đầu. Kế hoạch triển khai hiện hành nằm tại [reskin-implementation-plan.md](reskin-implementation-plan.md). Các nhận xét về plugin bên dưới phản ánh source tại thời điểm khảo sát.

Ngày khảo sát: 2026-10-21. Trạng thái: khảo sát mã nguồn và thiết kế workflow; chưa triển khai pipeline mới.

Nguồn tham khảo:
`C:\Users\Admin\Downloads\Telegram Desktop\V440_Dog_Translator_2-release-v1.0.0\v440_dog_translator_2`

Phạm vi: plugin Figma, hợp đồng dữ liệu export, parser, assets, sinh/cập nhật XML và kiểm chứng giao diện. Ứng dụng Dog Translator cung cấp mẫu đầu vào và dự án Android để thử nghiệm.

## 1. Pipeline đang có

```text
Figma selection/page
  → plugin/code.js: phân nhóm màn hình, flatten nodes, export assets
  → plugin/ui.html: đóng project_bundle.zip
  → screens/*.json + assets/* + screenshots/* + meta.json
  → parse_figma.py: inventory TSV, cây node, tài nguyên gợi ý
  → detect_ui_patterns.js: đề xuất list/button/gradient/badge
  → agent đọc skill và viết XML/resources
  → copy_assets.py: copy raster, chuyển SVG sang VectorDrawable
  → các script kiểm tra + Gradle build
```

Đường dẫn plugin: `plugin/figma-local-export-plugin/`.
Đường dẫn skill, scripts và templates: `.agent/skills/json_to_code/`.

`generate_boilerplate.py` sinh khung Kotlin; pipeline được khảo sát chưa có bộ sinh XML tự động đầy đủ. Quy trình hiện tại phụ thuộc vào agent đọc hướng dẫn rồi viết giao diện.

## 2. Kết quả khảo sát

| Thành phần | Bằng chứng trong mã nguồn | Hệ quả đối với auto reskin |
| --- | --- | --- |
| Export layout | `extractNode()` chủ động bỏ Auto Layout và sizing constraints | Chỉ giữ tọa độ sẽ mất thông tin co giãn, padding và flow |
| Component | `componentProperties` rỗng, `variantProperties` null, `mainComponentId` undefined | Khó nhận diện component dùng lại và trạng thái |
| Asset | `ic_` xuất SVG; `img_` xuất PNG; dừng duyệt con tại node asset | Cần hợp đồng rõ về node được raster/vector hóa cả nhánh |
| Asset trùng tên | `runExport()` loại trùng theo filename, giữ bản xuất đầu tiên | Hai instance cùng tên nhưng khác màu/nội dung có thể bị gộp sai |
| Export lỗi | `exportNodeAsset()` đổi visibility rồi khôi phục sau `await`, không có `finally` | Nếu export thất bại, bước khôi phục có thể không chạy |
| Báo cáo lỗi | `meta.failedCount` khởi tạo bằng 0; lỗi asset gửi riêng nhưng không cập nhật tổng kết | UI có thể báo Done trong khi bundle thiếu file |
| Bundle | ZIP ghi từng screen, meta, assets và screenshots; không ghi toàn bộ `assetExports` manifest | Mất bảng liên kết node → asset có ở payload tổng |
| Screenshot | Chỉ export root đầu tiên | Chưa đủ ảnh chuẩn để đối chiếu nhiều màn hình |
| Hierarchy | `childIds` ghi tất cả children, trong khi flatten bỏ node ẩn/status bar và con của asset | Cần phân biệt nhánh cố ý gộp với tham chiếu bị mất |
| Kiểm tra layout | `check_layout_against_figma.py` ghép tên bằng substring; chỉ duyệt XML → Figma | Không chứng minh mọi node Figma đã được render |
| Kiểm tra spacing | Chỉ kiểm tra một số quan hệ ConstraintLayout có thể ghép theo tên | PASS không đồng nghĩa toàn bộ vị trí chính xác |
| Chuyển SVG | Converter tự viết bỏ `defs`/`clipPath`; nhánh đã đọc chưa xử lý transforms | Cần khai báo tập SVG hỗ trợ và báo lỗi/fallback rõ ràng |
| Tài liệu | README plugin còn nói PNG-only/UIKit; mã thực tế có SVG và bundle Android | Cần đồng bộ tài liệu với implementation |

### Kiểm chứng đã chạy

- Chạy `parse_figma.py --inventory` trên `.figma/screens/home_1.json`: exit 0, **68 nodes**.
- Mẫu này có **0 node chứa `layoutMode`**, **8 tham chiếu child không có trong flattened**. Chưa phân loại từng tham chiếu; không kết luận tất cả là dữ liệu lỗi.
- Đưa XML chỉ có một `FrameLayout` rỗng vào `verify_layout_grounding()` với cùng mẫu: hàm trả **True** và báo thành công. Đây là bằng chứng kiểm tra hiện tại không phát hiện thiếu nội dung.
- Chưa chạy plugin trong Figma, chưa render Android hoặc build app. Những phát hiện khác trong bảng là kết quả đọc mã nguồn.

## 3. Workflow đề xuất

```text
Export bundle có phiên bản
  → kiểm tra schema, asset và tính toàn vẹn
  → chuẩn hóa thành mô hình thiết kế chung (Design IR)
  → đọc XML/resources và các chỗ sử dụng binding trong app đích
  → map màn hình, trạng thái, component, node và view ID
  → sinh patch trong staging
  → kiểm tra cấu trúc/resources + build + render/so ảnh
  → xuất diff, ảnh đối chiếu và danh sách phần chưa xử lý
  → áp dụng patch đã đạt điều kiện
```

### A. Plugin và bundle

- Thêm `schemaVersion`, phiên bản exporter và định danh nguồn thiết kế.
- Giữ tọa độ cùng Auto Layout, padding, gap, sizing, constraints và transform khi có; không suy diễn dữ liệu thiếu thành 0.
- Xuất metadata component/variant thực tế, text runs khi typography mixed và dữ liệu liên kết token nếu có.
- Ghi `assets.manifest.json`: node ID, file, định dạng, kích thước logic, scale, trạng thái export và hash nội dung.
- Phân biệt asset bằng định danh nguồn và nội dung; chỉ gộp khi đã xác nhận tương đương.
- Mỗi screen/state có screenshot và liên kết tới root ID. Ghi nhận thiếu screenshot như một trạng thái chưa kiểm chứng.
- Xuất asset tuần tự; khôi phục visibility bằng `try/finally`; tổng kết success/failed/skipped chính xác.

### B. Design IR và mapping

- Chuẩn hóa hierarchy, geometry, paint, typography, asset và layout intent vào một mô hình dùng chung cho generator và validator.
- Giữ provenance: mỗi giá trị được export trực tiếp, suy ra, hay thiếu; mỗi phép suy ra có lý do.
- Mỗi node có trạng thái xử lý: map vào view, gộp vào drawable, nằm trong template item, hoặc bỏ qua có lý do.
- Lưu `mapping.json`: nguồn thiết kế + node ID → layout file + view ID + resource + screen state. Cho phép một-nhiều/nhiều-một có khai báo.
- Node ID giúp truy vết trong cùng nguồn; thiết kế được duplicate/recreate cần cơ chế remap bằng component key, vai trò và cấu trúc, kèm mức tin cậy.
- Phân biệt screen và state bằng mapping rõ ràng; tên frame chỉ cung cấp gợi ý.

### C. Sinh patch reskin

- Đọc layout, style, resource và Kotlin/Java sử dụng chúng trước khi lập kế hoạch cập nhật.
- Ưu tiên tái sử dụng view ID/binding và hành vi hiện có. Nếu thiết kế đòi thay kiểu view hoặc cấu trúc ảnh hưởng binding, báo ảnh hưởng và tạo patch tương ứng có phạm vi rõ ràng.
- Dùng compiler cho các phép chuyển đổi xác định được; agent xử lý quyết định ngữ nghĩa như list, group, state và mapping chưa chắc chắn.
- Sinh tên resource ổn định; tài nguyên riêng màn hình và token dùng chung có quy tắc sở hữu rõ ràng.
- Kiểm tra mọi nơi dùng tài nguyên chung trước khi đổi. Lưu hash đầu vào và file đích để phát hiện chỉnh sửa giữa lúc lập patch và áp dụng.
- Chạy lại cùng đầu vào/cấu hình phải cho cùng output; khi app đã ở trạng thái đích thì không tạo diff mới.

### D. Điều kiện xác nhận

1. Bundle đúng schema; mọi asset bắt buộc tồn tại; lỗi export được hiển thị.
2. Mọi node trong phạm vi đều được giải thích bởi mapping; không chỉ kiểm tra tên view có giống tên Figma.
3. Resource references, view types và binding tương thích; build biến thể được chọn thành công.
4. Render từng screen/state ở viewport chuẩn, kiểm soát font, density, system bars, nội dung mẫu và animation trước khi so với screenshot Figma.
5. Báo cả độ phủ kiểm tra lẫn sai khác hình ảnh; ngưỡng ảnh cần hiệu chỉnh bằng bộ mẫu được chấp nhận.
6. Kiểm tra thêm viewport rộng/hẹp và thao tác gắn với các view bị thay đổi. Không có runtime validation thì ghi rõ chưa kiểm chứng hành vi.

## 4. Thứ tự phát triển

| Mốc | Đầu ra | Điều kiện hoàn thành |
| --- | --- | --- |
| 1. Bundle đáng tin cậy | Schema, manifest, sửa xử lý lỗi/trùng asset/khôi phục visibility | Ca lỗi có báo cáo đúng; các asset khác nội dung không bị mất |
| 2. Parser thống nhất | Adapter dữ liệu cũ, Design IR, diagnostics, coverage | Fixture thực và fixture lỗi cho kết quả xác định; phân loại mọi node |
| 3. Reskin một màn | Mapping có thể chỉnh, generator cho tập view hỗ trợ, staging patch | Giữ được binding cần thiết; build và đối chiếu ảnh; chạy lại không có diff |
| 4. Nhiều màn/trạng thái | Shared resources, state mapping, incremental runs | Đổi một màn không gây thay đổi ngoài phạm vi mà không được phát hiện |
| 5. Điều phối tự động | CLI thống nhất, checkpoint, báo cáo và retry có giới hạn | Dừng tại lỗi cụ thể, tiếp tục từ checkpoint, không báo PASS cho phần chưa kiểm tra |

MVP đề xuất: một màn hình, một trạng thái, một bundle local và một app Android XML đích. Ưu tiên làm chắc hợp đồng plugin → parser → mapping trước khi tự động hóa toàn bộ ứng dụng.
