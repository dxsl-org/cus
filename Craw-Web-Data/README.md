# Data Migration Project: Markdown to SQLite

Dự án này tập trung vào việc thu thập, bóc tách và chuyển đổi dữ liệu từ các nguồn khác nhau (Wikipedia, Luật pháp, Diễn đàn, Tin tức) từ định dạng Markdown sang cơ sở dữ liệu SQLite để phục vụ cho việc tìm kiếm và huấn luyện AI.

## 📂 Cấu trúc thư mục

- `<source_id>`: Chứa các tệp Markdown (.md) thô được scrape từ web.
- `dataset\`: Chứa các tệp cơ sở dữ liệu SQLite (.db) sau khi chuyển đổi.
- `scrapers\`: Chứa logic thu thập dữ liệu và công cụ dùng chung.
- `*_migration.py`: Các script chuyên biệt để chuyển đổi dữ liệu cho từng nguồn.

## 🛠 Quy trình Migration

Quy trình chuyển đổi (Migration Pipeline) được chuẩn hóa qua các bước sau:

### 1. Phân tích dữ liệu (Analysis)
Mỗi nguồn dữ liệu có cấu trúc YAML Frontmatter khác nhau. Ví dụ:
- **Wikipedia**: `title`, `url`, `pageid`.
- **Luật pháp (VBPL)**: `so_ky_hieu`, `ngay_ban_hanh`, `loai_van_ban`.
- **Diễn đàn (VOZ)**: `thread_id`, `sub_forum`.

### 2. Trích xuất & Bóc tách (Extraction & Parsing)
Sử dụng thư viện `PyYAML` để đọc metadata từ frontmatter và tách phần nội dung chính (body content). Các script migration xử lý các trường hợp đặc biệt như:
- Chuyển đổi tags từ chuỗi thành danh sách.
- Chuẩn hóa định dạng ngày tháng.
- Xử lý lỗi mã hóa (encoding) hoặc ký tự đặc biệt.

### 3. Lưu trữ SQLite (Storage)
Tất cả dữ liệu được lưu vào bảng `articles` thông qua công cụ `storage_utils.py`.

**Schema bảng `articles`**:
- `id`: Khóa chính tự tăng.
- `url`: Địa chỉ duy nhất (Unique) để tránh trùng lặp.
- `title`: Tiêu đề bài viết.
- `author`: Tác giả/Người ký.
- `content`: Nội dung văn bản thuần.
- `category`: Chuyên mục/Loại văn bản.
- `metadata`: Toàn bộ metadata gốc được lưu dưới dạng JSON string.
- `word_count`: Số lượng từ trong bài viết.

### 4. Tối ưu hiệu suất (Performance Optimization)
- **Batch Insertion**: Dữ liệu được chèn vào DB theo từng lô (thường là 1000 bài/lần) trong một transaction duy nhất để tối ưu tốc độ ghi của SQLite.
- **WAL Mode**: Sử dụng `PRAGMA journal_mode = WAL` để tăng hiệu năng xử lý đồng thời.

---
*Ghi chú: Luôn kiểm tra file `storage_utils.py` trước khi thực hiện thay đổi cấu trúc bảng dữ liệu.*
