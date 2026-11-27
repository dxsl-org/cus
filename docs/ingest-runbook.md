# Ingest runbook

Tất cả lệnh chạy từ thư mục gốc của repo. Dữ liệu nằm trong `data/`; muốn đặt ở ổ khác thì set biến `LLM_DATA_ROOT`.

| Việc | Lệnh | Đầu ra |
|---|---|---|
| Kiểm tra registry nguồn | `uv run python -m pipeline.schema.sources_manifest` | báo lỗi nếu nguồn cũ chưa được khai báo |
| SQLite cũ → canonical | `uv run python -m pipeline.ingest.export_legacy_sqlite [source_id ...]` | `data/canonical/<source>/part-*.parquet`, `data/reports/export/<source>.json` |
| Cào lại ngày đăng | `uv run python -m pipeline.ingest.recrawl_publish_dates <source_id ...> [--rps 1] [--limit N]` | `data/patches/publish_dates/<source>/` |
| Wikipedia | `uv run python -m pipeline.ingest.wikipedia_dump download` rồi `... export` | `data/raw/vi_wikipedia_org/`, `data/canonical/vi_wikipedia_org/` |
| Dataset mở: xem dung lượng / tải / kiểm tra | `uv run python -m pipeline.acquire.hf_snapshot plan\|download\|verify [name...] [--wave 1]` | `<DATA_ROOT>/raw/open_datasets/<name>`, eval → `<DATA_ROOT>/eval/<name>`, khóa phiên bản ở `manifests/datasets/<name>.lock.json` |
| Dataset mở → canonical | `uv run python -m pipeline.ingest.open_dataset_export <name> [--limit-files N]` | `<DATA_ROOT>/canonical/<name>` |
| Báo cáo corpus | `uv run python -m pipeline.reports.corpus_report` | `data/reports/corpus_report.md` |
| LLM Data Studio: soạn / duyệt / xuất bộ câu hỏi, xem corpus, chấm tay các CSV `dung`/`diem` | `studio\llm-studio.cmd` hoặc `uv run python -m studio.server [--port 8767] [--lan]` | `<DATA_ROOT>/taskset/taskset.db`, bản xuất ở `<DATA_ROOT>/taskset/exports/` |
| Sách: kiểm kê → trích PDF có chữ (kể cả TCVN3/VNI) → trích EPUB/MOBI/PRC/AZW3/FB2/DOCX/DOC/RTF/ODT/TXT → mẫu đọc kiểm tra → phân loại thể loại, thứ tự OCR | `uv run python -m pipeline.books.inventory D:/Book`, rồi `... pipeline.books.extract_pdf`, `... pipeline.books.extract_ebook` (DOC/RTF/ODT cần LibreOffice), `... pipeline.books.review_sample`, `uv run --group eval python -m pipeline.books.classify_genre`, `... pipeline.books.genre_report` | `<DATA_ROOT>/books/books.sqlite` (trạng thái từng file, bảng `genres`), `<DATA_ROOT>/canonical/owner_books/{book,ebook}/`, `<DATA_ROOT>/reports/books/` (`genres.md`, `ocr-priority.csv`) |
| Test | `uv run pytest` | |

## Quy tắc

- Export **ghi đè nguyên tử**: shard mới được ghi vào thư mục tạm rồi mới đổi tên. Chạy lại bất cứ lúc nào cũng an toàn.
- Cào lại ngày đăng **có thể tiếp tục khi dừng giữa chừng**: tài liệu đã có patch (kể cả lần thất bại) sẽ được bỏ qua, trừ khi dùng `--retry-failed`. Sau khi cào lại xong một nguồn, **chạy export lại nguồn đó** để gộp ngày vào canonical.
- Chạy cào lại dài ngày tách khỏi terminal:
  ```powershell
  Start-Process -WindowStyle Hidden uv -ArgumentList 'run','python','-u','-m','pipeline.ingest.recrawl_publish_dates','plo_vn' `
    -WorkingDirectory D:\LLM -RedirectStandardOutput data\reports\recrawl\run.log -RedirectStandardError data\reports\recrawl\run.err
  ```
- Trường metadata bị chứng minh là ngày cào thì khai báo trong `UNRELIABLE_META_KEYS` (`pipeline/ingest/date_normalizer.py`). Cột `docs/day ⚠` của báo cáo corpus dùng để phát hiện các trường hợp này.
- Tài liệu có `usage_scope=blocked` (ví dụ bản án vỡ dấu: `meta.text_issue`) vẫn nằm trong canonical để tiện sửa sau, nhưng không bao giờ được đưa vào train.

## Vị trí dữ liệu (2026-11-13)

- Dataset mở (đợt 1, khoảng 500 GB) nằm ở **`E:\llm-data`**. Các lệnh thao tác trên chúng phải chạy với `LLM_DATA_ROOT=E:/llm-data`.
- Dữ liệu cũ (canonical của 57 nguồn, patch ngày đăng, dump Wikipedia) vẫn ở `D:\LLM\data` cho tới khi các job nền xong. Sau đó chuyển `D:\LLM\data\*` sang `E:\llm-data\` và đặt cố định `setx LLM_DATA_ROOT E:\llm-data`.
- CulturaX là dataset gated: cần chấp nhận điều khoản trên Hugging Face, đặt `HF_TOKEN`, rồi chạy `... hf_snapshot download culturax_vi`.

## Crawler mới (`crawler/`)

Mỗi nguồn có một spec ở `manifests/crawl/<source_id>.yaml`, gồm các trường `adapter`, `client` (httpx/impersonate), `rps` và `params`. Danh sách adapter: `sitemap`, `listing` (trang lưu trữ theo tháng/chuyên mục), `wordpress` (REST, cho site tự host), `xenforo`, `oai_pmh`, `wayback`, `commoncrawl`.

| Việc | Lệnh |
|---|---|
| Cào (dừng lúc nào cũng được, chạy lại thì cào tiếp) | `uv run python -m crawler.run crawl <source_id> [--max-pages N] [--kinds sitemap,listing,forum]` |
| Tiến độ | `uv run python -m crawler.run status <source_id>` |
| Bóc tách offline từ WARC → canonical | `uv run python -m crawler.extract <source_id>` |

- Trạng thái frontier lưu ở `<DATA_ROOT>/crawl_state/<source_id>.sqlite`; HTML gốc ở `<DATA_ROOT>/raw/<source_id>/<YYYY-MM>/*.warc.gz`.
- Tôn trọng robots.txt. URL bị chặn được đánh dấu `skipped`, không thử lại.
- Với `--kinds`, chỉ cào các trang liệt kê, dùng để đếm số URL trước khi cào thật.
- Canonical chia theo nguồn gốc dữ liệu: `canonical/<source_id>/{legacy,crawl,dump,hf,book,ebook}/`. Export lại một nguồn gốc không xóa dữ liệu của nguồn gốc khác.

## Lab kiến trúc (`lab/`, phase 15–18)

Model nhỏ tự viết, train trên GPU của máy. Mọi lệnh chạy với `LLM_DATA_ROOT=E:/llm-data` và `uv run --group lab`. Kết quả nằm ở `E:\llm-data\lab\`.

| Việc | Lệnh |
|---|---|
| Tokenizer lab 32k (tạm, không phải tokenizer phase 10) | `python -m lab.tokenizer_train train`, rồi `... fertility` để so với Qwen2.5 |
| Token hoá thành shard theo nguồn | `python -m lab.data_prep` (chạy lại thì bỏ qua nguồn đã xong) |
| Train (chạy lại cùng lệnh thì train tiếp từ checkpoint) | `python -u -m lab.train lab/configs/<run>.yaml` |
| bpb theo domain + sinh mẫu từ checkpoint | `python -m lab.evaluate <run_name> --samples` |
| So hai run bằng bpb, có khoảng tin cậy (cùng các đoạn held-out, bootstrap theo đoạn) | `python -m lab.compare_bpb <run_a> <run_b> [--windows 256]`; loss từng đoạn được cache ở `runs/<run>/bpb-windows-<n>.npz`. Nhiễu theo seed: dense σ ≈ 0,03%, MoE khoảng 0,4% (`docs/lab/lab-s-size-results-phase-15-18.md`) |
| Chọn LR tốt nhất từ các run quét và ghi vào config | `python -m lab.pick_lr <config.yaml> <run> <run> ...` |
| MoE: expert có chuyên môn hoá theo domain không | `python -m lab.analyze_experts <run_name>` → `runs/<run>/experts.{json,md}` |
| Context dài: needle, bpb theo vị trí, bộ nhớ, tỷ lệ chấp nhận MTP | `python -m lab.eval_long_context <run_name> [--lengths 2048 4096 8192]` → `runs/<run>/long_context.{json,md}` |
| Tự thử model: gõ đoạn mở đầu, các model viết tiếp | `python -m lab.generate_text [run ...] [--prompt TEXT] [--device cuda]` (mặc định lấy run tốt nhất mỗi cỡ theo bpb: combo-S công thức mới `combo-s-recipe` và combo-M `combo-m`; chạy trên CPU; run nào chưa có checkpoint thì bỏ qua) |
| Công bố lên Hugging Face (tổ chức `DXSL`) | `python -m lab.publish_runs` → `DXSL/cus-lab-runs` (config, log, bpb, điểm eval từng câu theo id; không có trọng số) · `python -m eval.publish_public` → `DXSL/cus-eval-public` (câu eval không thuộc holdout, nguồn có license mở) · `python -m pipeline.export.open_corpus` (cần `--group process`) → `DXSL/cus-vi-open` (văn bản license mở, đã khử nhiễm eval). Mỗi lệnh có `--dry-run`; token: `cmdkey /generic:huggingface /user:DXSL /pass:<token>` |
| Train trên GPU Modal (H100), trong ngân sách credit | `python -m lab.modal_train bench <config> [--micro-batch N]` · `train <config> [<config> ...] [--hours H]` · `fetch <run>` · `spent` (cần `--group cloud`; đăng nhập một lần bằng `uv run --group cloud modal token new`) |
| Train trên RunPod (H100: SXM, nếu hết thì NVL, rồi PCIe; Community mặc định), trong số dư tài khoản | `python -m lab.runpod_train up [--hours H] [--cloud SECURE] [--gpu "H100 PCIe"]` (mã thoát 3: chưa có H100 nào trống, thử lại sau; Community từng hết H100 suốt 12 giờ, Secure có máy sớm hơn) · `bench <config> [--micro-batch N]` · `train <config> [<config> ...] [--micro-batch N]` · `status` · `fetch <run> [--no-stable]` · `down` (cần `--group lab`; API key lưu một lần bằng `cmdkey /generic:runpod /user:runpod /pass:<key>`) |
| Chấm model lab trên bộ eval (kiến thức, trắc nghiệm) | `uv run --group eval --group lab python -m eval.harness --model lab:<run> --evalset evalset-v2 --mode cloze`; so hai model: `python -m eval.compare evalset-v2 cloze lab:<A> lab:<B>` |
| Shard lab có rò rỉ câu eval không | `python -m lab.decontam_scan` → `LAB_DATA/decontam-scan.json`; token hoá lại nguồn bị nhiễm: `python -m lab.data_prep --redo <source> …` |

- Train tiếp từ trọng số của một run khác (thích nghi domain, kéo dài context): đặt `init_from: <run>` trong `train:`; kiến trúc phải trùng, chỉ `max_seq_len` được khác. Ví dụ ở `lab/configs/law/`.
- Mix train khai báo trong config của từng run, theo `source_id` hoặc domain (`lab/configs/domains.yaml`). Nguồn mới phải được gán domain trước khi token hoá.
- MoE bật bằng khoá `moe:` trong `model:` của config (`lab/layers/moe.py`, `DEFAULTS`); ví dụ ở `lab/configs/moe/`. Gated DeltaNet bật bằng `gdn: {attention_every: N}` (`lab/layers/gdn.py`; trên GPU dùng kernel `fla`, trên CPU dùng bản chunkwise tự viết), cổng attention bằng `attn_gate: true`, MTP bằng `mtp: {weight: 0.3}`; ví dụ ở `lab/configs/hybrid/`.
- Chuỗi chạy nền: `E:\llm-data\lab\logs\phase15-chain.ps1` (quét LR → dense-S), `phase15-dense-m.ps1`, `phase16-chain.ps1` (chờ dense-M xong → MoE-S A1–A3 → phân tích expert), `phase17-chain.ps1` (chờ phase 16 xong → test kernel fla trên GPU → hybrid-S H1–H4). Sau khi máy khởi động lại thì chạy lại script, các bước sẽ tự tiếp tục. Log của PowerShell (`*.log` trong `logs/`) là UTF-16; tiến độ đọc ở `runs/<run>/log.jsonl`.
- Modal: dữ liệu nằm trên volume `llm-lab` (tự upload phần còn thiếu trước mỗi lần chạy, kể cả checkpoint của `init_from`). Nhiều config trong một lệnh `train` chạy song song, mỗi config một GPU. Ngân sách: số metered của Modal + tiền giữ chỗ (theo timeout) của các run đang chạy, phải nằm trong `BUDGET_USD` − $1,5 dự phòng; sổ ghi ở `E:\llm-data\lab\modal\ledger.jsonl`. Chốt thứ hai là spend limit $0 trong Modal → Usage & Billing. Config có GDN chạy được trên H100 từ khi lab dùng torch 2.12.1 (kèm Triton 3.7.1, mức tối thiểu fla đòi trên Hopper, fla #640); combo-S đạt 375k token/s trên H100, so với 133k trên A100. Run trên cloud đóng băng config kèm `ckpt_minutes: 10`, nên phải resume trên Modal, không resume được ở máy local.
- RunPod: một pod dùng cho mọi run. Community không gắn được network volume, nên shard (9 GB) chỉ upload một lần lên volume disk `/workspace` của pod. `train` xếp các config vào hàng đợi trên pod rồi trả về ngay. Các config chạy lần lượt, và hàng đợi dừng ở run lỗi đầu tiên để `init_from` không lấy phải checkpoint dở dang; chạy lại đúng lệnh `train` thì các run resume. `fetch` tải được run đã xong trong lúc pod train run kế tiếp; `down` không xoá pod khi còn run chưa tải về (trừ khi có `--force`). Ngân sách: watchdog trên pod tự stop pod ở mốc `/workspace/deadline`. Mỗi lệnh đặt lại mốc này bằng now + (số dư − $1) / giá pod, có trần là `--hours` của `up`. Khi hàng đợi kết thúc (xong, lỗi hay bị dừng), mốc rút còn 45 phút. `up` khởi động lại pod đã bị stop. SSH dùng `~/.ssh/id_ed25519` (tự đăng ký lên tài khoản).
