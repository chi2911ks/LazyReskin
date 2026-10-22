# LazyReskin — Figma Export

Plugin trích xuất giao diện Figma (sử dụng Figma Plugin API) tối ưu cho workflow Figma → Android XML. Hoạt động nội bộ trong Figma Desktop, không phụ thuộc REST API token, không bị giới hạn HTTP 429.

## Cài đặt

1. Mở Figma Desktop → **Plugins → Development → Import plugin from manifest…**
2. Chọn file `manifest.json` trong thư mục `plugin/figma-local-export-plugin/` (hoặc giải nén từ `dist/figma-local-export-plugin.zip`).
3. Chạy plugin **LazyReskin - Figma Export**.

## Các chế độ nguồn

1. **Selected screens**: Xuất các frame/màn hình đang được chọn trên canvas.
2. **Whole page**: Xuất toàn bộ các frame/màn hình trên trang hiện tại.

> **Mẹo:** Bạn có thể click đúp (double-click) vào card nguồn để bắt đầu xuất ngay.

## Cơ chế lấy Assets theo thư viện (Library-Driven)

Nhằm loại bỏ hoàn toàn việc trích xuất thừa thãi các hình khối, khung nền, layout layers, plugin sử dụng cơ chế **đối soát thư viện**:

1. **Quét toàn bộ thư viện Asset trong file:**
   - Quét tất cả các Component và Component Set trên toàn bộ các trang (tab Assets trong Figma).
   - Quét các trang chuyên dụng: `Assets`, `Libraries`, `Components`, `Icons`, `Design System`.
   - Lập danh mục thư viện theo tên chuẩn hóa, ID và Component Key.
2. **Đối soát Layer trên màn hình:**
   - Khi xuất màn hình, plugin rà soát từng layer trong frame.
   - Nếu layer là một **Instance** của một Component trong thư viện, hoặc có **tên trùng khớp với tên Asset trong thư viện**, plugin sẽ trích xuất asset đó từ thư viện về.
   - Các layer chỉ là khung layout, rectangle nền, divider... không có trong thư viện sẽ được giữ nguyên cấu trúc View/Layout trong JSON mà **không bị bóc tách thành file ảnh rác**.
3. **Khử trùng lặp (Deduplication):**
   - Nếu 77 màn hình đều sử dụng cùng một icon/logo trong thư viện, asset đó chỉ được xuất **đúng 1 file duy nhất** (ví dụ: `assets/search.svg`).

## Tùy chọn xuất

- **Asset filter**:
  - `Libraries only (Mặc định - Khuyên dùng)`: Chỉ lấy những assets thực sự có trong thư viện Components/Assets của file và được sử dụng trên màn hình.
  - `All artwork`: Chế độ mở rộng bóc tách mọi hình khối, vector và image fills trên trang.
- **Asset format**:
  - `Auto`: Vector/Icons xuất SVG, ảnh/fill xuất PNG.
  - `PNG` / `SVG`: Ép toàn bộ asset sang định dạng mong muốn.
- **PNG scale**: 1×–4× (mặc định 2×).
- **Screenshots**:
  - `All screens (1 per frame)` *(Mặc định)*: Chụp ảnh PNG tham chiếu cho **toàn bộ 100% màn hình** (không bị giới hạn số lượng).
  - `First screen only`: Chỉ chụp 1 màn hình đầu tiên.
  - `Limit count…`: Giới hạn số lượng ảnh chụp tối đa tùy chọn.
  - `None`: Bỏ qua ảnh chụp để xuất siêu nhanh.
- **Include screen assets**: Bật/tắt việc đóng gói assets kèm layout JSON (mặc định luôn bật).

## Cấu trúc Bundle (<title>.zip)

Bundle tải về sẽ tự động lấy tên theo tiêu đề tài liệu Figma (ví dụ: `INV166_LiveTV_7_Trinity_Copy.zip`), trong đó khoảng trắng và các ký tự đặc biệt được chuẩn hóa an toàn:

- `screens/*.json`: Layout JSON chi tiết cho từng màn hình (tọa độ, kích thước, hierarchy, fills, typography).
- `screenshots/*.png`: Ảnh chụp màn hình chuẩn của từng frame (tên file tương ứng 1:1 với file JSON).
- `assets/*`: Các file vector SVG và ảnh PNG của các assets thư viện được dùng trên màn hình.
- `assets.manifest.json`: Bảng kê chi tiết từng asset (node ID, filename, scale, loại định dạng, trạng thái).
- `meta.json`: Thống kê tổng số màn hình, số screenshot, số asset, lỗi nếu có.

## Kiểm chứng tự động

Chạy từ thư mục gốc repo với Node.js:

```sh
node --test plugin/figma-local-export-plugin/tests/export.test.cjs
```

23 kiểm tra tự động đã đạt:
- Chọn nguồn (Selected screens / Whole page).
- Tự động đặt tên file bundle ZIP theo tiêu đề file Figma (chuẩn hóa khoảng trắng và ký tự đặc biệt).
- Hỗ trợ Figma `dynamic-page` (`getMainComponentAsync` không đồng bộ, an toàn bộ nhớ).
- Trích xuất và đối soát asset theo thư viện components (Library-driven matching).
- Khử trùng lặp asset trên nhiều màn hình (Deduplication).
- Xuất screenshot đầy đủ cho toàn bộ màn hình (không bị giới hạn 10 ảnh).
- Kiểm tra validation giới hạn asset, khóa busy, khôi phục khi lỗi/retry.
- Cấu trúc ZIP/manifest, layout JSON + assets đi kèm.
- Xử lý trùng tên asset và format/scale SVG/PNG.
