# Data contract

Mọi dữ liệu pretrain/mid-training đều là `DocumentRecord`. Bộ câu hỏi (SFT, preference, RL, eval) dùng `TaskItem`, định nghĩa riêng ở phase 8.

- Code: `pipeline/schema/document_record.py` (schema Arrow + `validate`)
- Registry nguồn: `manifests/sources.yaml` (kiểm tra bằng `uv run python -m pipeline.schema.sources_manifest`)
- Test: `uv run pytest`

## DocumentRecord

| Trường | Kiểu | Bắt buộc | Quy tắc |
|---|---|---|---|
| `doc_id` | string | ✓ | `make_doc_id(source_id, locator)`: sha1 của source + URL chuẩn hóa (hoặc đường dẫn / sha256 với sách), lấy 20 ký tự hex. Không đổi khi cào lại. |
| `source_id` | string | ✓ | phải có trong `manifests/sources.yaml` |
| `source_type` | string | ✓ | `news, blog, forum, qa, legal, court, academic, education, reference, corporate, social, book, code, open_dataset` |
| `url` | string | | `canonical_url()`: scheme/host viết thường, bỏ fragment, bỏ `utm_*`, `fbclid`, `gclid`, `zarsrc` |
| `title`, `text` | string | `text` ✓ | Unicode **NFC** |
| `lang`, `lang_score` | string, float32 | | ISO 639-3 + script (`vie_Latn`); điền ở pipeline |
| `publish_date` | date32 | | **không bao giờ** lấy từ thời điểm cào; phải ≤ ngày của `crawl_time` |
| `publish_date_source` | string | ✓ | `url`, `meta:<key>`, `title`, `content`, `interp`, `recrawl`, `dump`, `none`. Giá trị `none` ⇔ `publish_date` rỗng |
| `crawl_time` | timestamp(ms) | ✓ | thời điểm lấy dữ liệu; là cận trên của `publish_date` |
| `era` | string | ✓ | `pre_llm` nếu `publish_date` < **2022-12-01**, `post_llm` nếu từ ngày đó trở đi. Khi không có `publish_date`: `pre_llm` nếu `crawl_time` < 2022-12-01 (trang đã tồn tại trước ChatGPT), ngược lại `unknown` |
| `author`, `category`, `tags` | string, string, list | | |
| `raw_ref` | string | | `warc://<file>#<offset>` hoặc đường dẫn file gốc |
| `text_sha256` | string | ✓ | sha256 của `text` (UTF-8), dùng khử trùng lặp chính xác |
| `license` | string | ✓ | `copyrighted`, `cc-by-sa-4.0`, `vn-not-protected`, `unknown`… (theo nguồn) |
| `usage_scope` | string | ✓ | `train_ok`, `research_only`, `blocked` |
| `extractor` | string | ✓ | `<tên>@<version>`; dùng để chạy lại có chọn lọc khi bộ bóc tách thay đổi |
| `meta` | JSON string | | metadata gốc; cấm các key `cookie`, `authorization`, `token`, `api_key`, `password` |

## TaskItem (bộ câu hỏi: SFT, preference, RL kiểm chứng, eval)

Mã ở `taskset/`. Pretrain không dùng schema này.

- **Lưu trữ**: `taskset.db` (SQLite). Nội dung item nằm trong `item_versions`, chỉ ghi thêm, không bao giờ sửa. Muốn sửa thì tạo phiên bản mới. `reviews`, `verifier_runs`, `edits` cũng chỉ ghi thêm. Các ràng buộc nằm ở trigger trong `taskset/schema.sql`, nên sửa DB trực tiếp bằng SQL cũng không vượt qua được.
- **kind**: `sft`, `multi_turn`, `preference`, `reasoning`, `rl_verifiable`, `eval_mcq`, `eval_open`, `eval_code`. Các trường nội dung bắt buộc theo từng kind được kiểm tra trong `taskset/content.py`. Taxonomy (`domain`, `skills`, `difficulty`) lấy từ `taskset/taxonomy.yaml`, và mỗi phiên bản ghi lại `taxonomy_version` của nó.
- **Trạng thái**: `draft → pending → auto | approved | corrected | dropped`. Chỉ được chuyển theo bảng `status_transitions`.
  - `approved`/`corrected` cần một review của **phiên bản hiện tại** với đúng verdict đó.
  - `auto` cần một lần chạy verifier đạt trên đáp án tham chiếu (`reference`).
  - `rl_verifiable` còn cần thêm ít nhất một câu trả lời sai mẫu (`negative`) bị verifier đánh trượt.
  - Sửa nội dung một item đã chấp nhận sẽ đưa nó về `pending`.
  - Item `rl_verifiable` không sửa qua verdict `corrected` được, vì verifier phải chạy lại trên phiên bản mới. Thay vào đó: sửa nội dung, chạy verifier, rồi duyệt `approved`.
- **split** cố định từ lúc tạo item. Với kind huấn luyện: 90/5/5 train/dev/test, theo hash có salt `llm-taskset/split/v1` của các lượt user. Với kind `eval_*`: luôn `test` hoặc `holdout`. Nguồn không phải `train_ok` không vào được train/dev.
- **Export** (`python -m taskset.export`):
  - train/dev nhận `auto`, `approved`, `corrected`; test/holdout chỉ nhận `approved`, `corrected`.
  - Item có cờ (`pii`, `unsafe`, `contaminated`, `duplicate`) không bao giờ được export.
  - `decontam.jsonl` chứa mọi đề và đáp án của test/holdout, dùng cho bộ lọc 13-gram ở phase 6.
  - Tên thư mục chứa hash nội dung, nên export lại một kho không đổi sẽ dùng lại đúng thư mục cũ.
- **Verifier** (`taskset/verifiers.py`):
  - `exact` và `regex` hỗ trợ chuẩn hoá `nfc`, `lower`, `strip_punct`, `fold_accents`.
  - `numeric` đọc được cả `1.000,5` lẫn `1,000.5`.
  - `choice` chỉ nhận nhãn in hoa A–J.
  - `unit_test` chạy Python trong tiến trình riêng (`-I`, thư mục tạm, có timeout, chặn socket). `spec.setup` (ví dụ dòng import của đề) chạy trước code ứng viên. Đây chỉ là lớp bảo vệ khỏi sơ suất, không phải sandbox thật; khi chạy RL hàng loạt phải chuyển sang container.
- **Nhập và duyệt** (cho tới khi có Studio):
  - `taskset.import_eval`: nhập benchmark công khai vào split `test`, trạng thái `pending`.
  - `taskset.import_drafts`: nhập câu soạn mới vào split `holdout`. Có `--check` để kiểm tra file nháp mà không ghi DB.
  - `taskset.bulk_review`: xuất mẫu kiểm tra, sau đó duyệt hàng loạt benchmark công khai, ghi lại sha256 của file mẫu.
  - `taskset.review_sheet`: duyệt từng câu qua file CSV (`verdict` = `approved`/`dropped`/`needs_work`).

## Bộ eval đóng băng

- Khai báo nhóm câu và hạn mức ở `manifests/evalsets/<name>.yaml`. Lệnh `python -m eval.build_evalset <name>` chỉ lấy câu đã duyệt, ghi ra `DATA_ROOT/eval/<name>/{items.jsonl, decontam.jsonl, MANIFEST.json}`, và từ chối build lại nếu thư mục đã tồn tại. Muốn sửa thì tạo phiên bản mới. Mỗi nhóm chọn câu theo `sources`, và lọc thêm được theo `domains`, `langs`, `meta` (ví dụ `meta: {set: vn_facts}`). Một câu chỉ thuộc nhóm đầu tiên chọn nó.
- `evalset-v2` = các nhóm của v1, cộng thêm `vn_facts` (câu dữ kiện tự soạn, độ khó 1–5, mỗi câu có `meta.cloze`: một câu khẳng định với một chỗ trống `___`) và `vi_mmlu` (toàn bộ 14.042 câu Global-MMLU tiếng Việt).
- Harness: `uv run --group eval python -m eval.harness --model <hf id> --evalset <name> --mode loglik|cloze|generate`. Checkpoint lab: `--model lab:<run>` (thêm `--group lab`).
  - Chế độ `loglik` (chỉ câu trắc nghiệm) so xác suất của " A", " B", … sau một gợi ý trả lời. Model phải đã học định dạng trắc nghiệm; model nền nhỏ chỉ ngang đoán bừa.
  - Chế độ `cloze` (chỉ câu trắc nghiệm) chấm xác suất của chính nội dung từng phương án: điền vào câu `meta.cloze` nếu có, nếu không thì đặt sau đề và gợi ý trả lời. Có ba chỉ số: `acc` (chọn theo log-prob chia cho số byte), `acc_raw` (theo tổng log-prob) và `p_correct` (xác suất model đặt vào đáp án đúng trong số các phương án; là số liên tục nên tách được hai model gần nhau). Dùng được từ model 40M trở lên.
  - Chế độ `generate` (chỉ model Hugging Face) dùng chat template, giải mã greedy, chấm bằng verifier của item. Với câu trả lời mở, câu trả lời cũng được tính đúng nếu đáp án xuất hiện nguyên cụm trong một dòng đầu ngắn.
  - Kết quả theo nhóm, và trong mỗi nhóm theo ngôn ngữ, domain, độ khó (`d1`–`d5`) nếu các câu khác nhau ở chiều đó. Kèm khoảng tin cậy Wilson 95%, ghi ở `DATA_ROOT/eval/runs/`.
  - Cùng model, cùng chế độ, cùng batch size thì output giống hệt từng byte.
- So hai model: `python -m eval.compare <evalset> <mode> <model A> <model B>`. Đây là bootstrap ghép cặp theo từng câu, cho hiệu B − A, khoảng tin cậy 95% và p(B ≤ A), theo từng nhóm, domain và độ khó. Hai model chấm trên cùng câu nên độ khó riêng của từng câu triệt tiêu, nhờ đó thấy được chênh lệch 1–2 điểm mà hai khoảng Wilson riêng rẽ không tách được.
- Chống rò rỉ: mọi bộ eval đã build (thư mục có `decontam.jsonl`) đều tự vào bộ lọc 13-gram (`pipeline.process.decontam.EvalIndex()`, không cần sửa code khi có bộ mới). Bộ lọc này chạy ở stage s4 cho nguồn crawl, ở `lab.data_prep` cho mọi nguồn mà lab token hoá (gồm FineWeb-2, CulturaX và sách, những nguồn đọc thẳng từ canonical), và khi export. Shard lab đã có thì quét bằng `python -m lab.decontam_scan`, rồi token hoá lại nguồn bị nhiễm bằng `python -m lab.data_prep --redo <source> …`. Phần `meta.cloze` không vào danh sách lọc: câu dữ kiện ngắn là kiến thức model cần học, lọc nó đi sẽ xoá luôn văn bản gốc.

## Lưu trữ

```text
<repo>/                         (git: code + manifests + docs)
  manifests/sources.yaml         registry nguồn
  manifests/datasets/*.yaml      dataset mở đã tải (revision, sha256)
  manifests/mixes/*.yaml         công thức trộn cho từng lần train
  data/                          (ngoài git, có thể chuyển sang ổ khác)
    raw/<source_id>/<YYYY-MM>/*.warc.gz
    raw/books/<sha256>.<ext>
    canonical/<source_id>/<origin>/*.parquet    DocumentRecord, zstd, 256–512 MB/shard; origin ∈ legacy|crawl|dump|hf
                                 (cùng một bài có thể xuất hiện ở nhiều origin: phase 6 khử trùng lặp theo doc_id/text)
    processed/<stage>/<source_id>/...
    eval/                        bộ đánh giá, không bao giờ vào train
    taskset/taskset.db           TaskItem (SQLite, nguồn gốc); exports/<yyyymmdd>-<hash12>/ bất biến
```

## Chính sách (chốt 2026-11-13)

- Dữ liệu chỉ dùng huấn luyện nội bộ, không phát hành. `license` được giữ ở cấp tài liệu để có thể lọc nếu sau này phát hành model.
- `vn-not-protected`: văn bản quy phạm pháp luật, văn bản hành chính và văn bản thuộc lĩnh vực tư pháp, không thuộc đối tượng bảo hộ quyền tác giả (Luật SHTT, Điều 15).
- **Tín hiệu không cho train AI (chốt 2026-11-14):**
  - Site có `Content-Signal: ai-train=no` trong robots.txt (voz.vn, lsvn.vn): crawler **từ chối cào mới** (`ai_train_refused`). Dữ liệu đã cào trước đó vẫn được dùng.
  - Site chỉ chặn một số bot AI theo tên (tinhte, vjol): vẫn cào.
  - Cả hai trường hợp đều ghi `ai_objection` ở nguồn trong `manifests/sources.yaml`, để lọc ra nếu sau này phát hành model.
