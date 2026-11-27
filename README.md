# Cus

**Cus** là mô hình ngôn ngữ ưu tiên tiếng Việt, train từ đầu. Repo này gồm toàn bộ nền tảng: thu thập và làm sạch dữ liệu, bộ đánh giá, và phòng thí nghiệm kiến trúc (lab) nơi các phiên bản Cus được thiết kế và train.

**Trạng thái (11/2026):** đã chốt kiến trúc và công thức huấn luyện ở cỡ S (khoảng 40M tham số kích hoạt) và kiểm chứng ở cỡ M (Cus-M: 446M tham số, 124M kích hoạt, 2B token): Cus-M hơn dense cùng FLOPs 10,6% bpb và 13,8 điểm kiến thức dữ kiện Việt Nam.

## Kiến trúc Cus

Cus là model lai: kết hợp Gated DeltaNet và attention, dùng Mixture-of-Experts, có dự đoán đa token. Lý do chọn và các phương án đã loại ghi trong [ADR 0001](docs/decisions/0001-combo-m-architecture-and-training-recipe.md).

| Thành phần | Lựa chọn |
|---|---|
| Bộ trộn token | Cứ 3 lớp Gated DeltaNet ([fla](https://github.com/fla-org/flash-linear-attention)) có 1 lớp attention có cổng; GQA, RoPE, head dim 64 |
| FFN | MoE: 32 expert nhỏ, mỗi token chọn 4 expert, cộng 1 expert chung; cân bằng tải bằng bias, không dùng loss phụ |
| Đầu ra | Dự đoán thêm token t+2 (MTP, trọng số 0,3), dùng được làm bản nháp cho giải mã tự suy đoán |
| Optimizer | Muon cho ma trận ẩn và expert; AdamW cho embedding, các tham số gain và router |
| Lịch LR | WSD: 20% cuối giảm LR, đồng thời chuyển sang hỗn hợp dữ liệu chất lượng cao (annealing) |
| Mở rộng kích thước | μP (độ rộng gốc 512), để LR tìm được ở cỡ nhỏ dùng được cho cỡ lớn |
| Tokenizer | BPE 32k tự train cho tiếng Việt (`lab-vi-32k`), dùng tạm cho lab; tốn ít hơn khoảng 14% token so với tokenizer của Qwen2.5 |

### Kết quả ở cỡ S (0,5B token, cùng dữ liệu)

bpb (bits per byte) đo trên held-out, càng thấp càng tốt:

| Model | bpb/all | so với dense |
|---|---|---|
| Dense kiểu Llama (cosine + AdamW) | 0,8245 | — |
| MoE (32 expert nhỏ) | 0,7913 | −4,0% |
| Lai GDN + MTP | 0,8076 | −2,0% |
| Kiến trúc Cus, công thức cũ | 0,7804 | −5,3% |
| **Kiến trúc Cus + Muon + WSD + annealing** | **0,7473** | **−9,4%** |

So với dense, Cus còn giữ ổn định khi đọc văn bản dài gấp 4 lần độ dài lúc train (8192 token), và chỉ cần khoảng 1/4 bộ nhớ trạng thái khi sinh văn bản. Chi tiết: [báo cáo lab cỡ S](docs/lab/lab-s-size-results-phase-15-18.md). Báo cáo này có cả mức nhiễu đã đo, nên biết được chênh lệch nào là thật.

### Kết quả Cus-M (2B token, cùng dữ liệu và FLOPs với dense-M)

| | dense-M | Cus-M |
|---|---|---|
| Kích thước | d768, 12 lớp, 101M tham số | d768, 12 lớp; 32 expert × 416; 446M tổng, 124M kích hoạt |
| bpb/all | 0,7268 | **0,6501 (−10,6%)** |
| `vn_facts` (cloze) | 0,334 | **0,473 (+13,8 điểm)** |
| Sau thích nghi luật: bpb/law, bpb/all, `vn_facts` | 0,5236, 0,7265, 0,359 | **0,4635, 0,6495, 0,482** |
| Config | | [`lab/configs/combo/combo-m.yaml`](lab/configs/combo/combo-m.yaml) |

Cus-M còn có bản kéo dài ngữ cảnh lên 8192 token: bpb đoạn 4–8k không tệ hơn đoạn 2–4k. Thích nghi luật dùng 40% replay nghiêng về các domain dễ quên, nên bpb/all không giảm so với trước khi thích nghi. Chi tiết, kể cả các điểm yếu (truy xuất xa sau 4k, các domain còn quên), và so sánh với Qwen2.5 base: [báo cáo lab cỡ M](docs/lab/lab-m-size-results-phase-19.md).

## Cấu trúc repo

| Thư mục | Nội dung |
|---|---|
| `lab/` | Model (`model.py`, `layers/`), train (`train.py`), optimizer Muon, đánh giá bpb và ngữ cảnh dài, so cặp bpb, chạy trên GPU Modal (`modal_train.py`), config của mọi thí nghiệm (`configs/`) |
| `eval/` | Harness chấm model trên bộ eval (`loglik`, `cloze`, `generate`); so cặp hai model bằng bootstrap |
| `pipeline/` | Schema dữ liệu, thu thập dataset mở, ingest, xử lý (lọc, khử trùng lặp, khử nhiễm eval, che PII), sách, báo cáo corpus |
| `crawler/` | Crawler theo spec (`sitemap`, `listing`, `wordpress`, `xenforo`, `oai_pmh`, `wayback`, `commoncrawl`), lưu WARC |
| `studio/`, `taskset/` | LLM Data Studio: soạn, duyệt và xuất bộ câu hỏi (eval, SFT); xem corpus |
| `manifests/` | Registry nguồn, spec crawl, khoá phiên bản dataset, định nghĩa bộ eval |
| `Craw-Web-Data/` | Crawler và migration cũ (Markdown → SQLite), giữ để export lại dữ liệu cũ |
| `docs/` | Hợp đồng dữ liệu, runbook, báo cáo lab, ADR, nhật ký thí nghiệm (`docs/lab/journal/`) |
| `tests/` | Test (`uv run pytest`) |

Dữ liệu, checkpoint và kết quả eval **không** nằm trong git. Chúng ở thư mục do biến `LLM_DATA_ROOT` chỉ tới.

## Cài đặt

Cần Python 3.12, [uv](https://docs.astral.sh/uv/), và GPU NVIDIA có driver hỗ trợ CUDA 13. Chạy được native trên Windows, nhờ `triton-windows`.

```powershell
uv sync --group lab --group eval --group dev     # thêm --group cloud để chạy trên Modal, --group process cho pipeline xử lý
$env:LLM_DATA_ROOT = 'E:\llm-data'                # thư mục chứa dữ liệu, checkpoint, kết quả eval
uv run pytest
```

Các nhóm phụ thuộc tách riêng: pipeline dữ liệu không cần torch; `lab` ghim torch 2.12.1 vì kernel Gated DeltaNet cần Triton ≥ 3.7.1 trên H100.

## Dùng lab

```powershell
# Train (chạy lại cùng lệnh thì train tiếp từ checkpoint)
uv run --group lab python -u -m lab.train lab/configs/combo/combo-s-recipe.yaml

# bpb theo domain, so cặp hai run có khoảng tin cậy, ngữ cảnh dài
uv run --group lab python -m lab.evaluate combo-s-recipe --samples
uv run --group lab python -m lab.compare_bpb combo-s combo-s-recipe
uv run --group lab python -m lab.eval_long_context combo-s-recipe

# Chấm kiến thức trên evalset-v2 rồi so hai model
uv run --group eval --group lab python -m eval.harness --model lab:combo-s-recipe --evalset evalset-v2 --mode cloze
uv run --group eval python -m eval.compare evalset-v2 cloze lab:combo-s lab:combo-s-recipe

# Gõ đoạn mở đầu, các model viết tiếp
uv run --group lab python -m lab.generate_text

# Train trên GPU Modal, có chặn ngân sách
uv run --group lab --group cloud python -m lab.modal_train train lab/configs/combo/combo-m.yaml --gpu H100
```

Toàn bộ lệnh, kể cả pipeline dữ liệu và crawler: [docs/ingest-runbook.md](docs/ingest-runbook.md).

## Dữ liệu và đánh giá

- **Corpus train của lab:** 4,73B token tiếng Việt, 9 domain (web, tin tức, luật, văn học, tham khảo, forum, y tế, văn hoá – lịch sử, giáo dục). Corpus đã khử trùng lặp, và đã khử nhiễm với mọi bộ eval đã đóng băng. Schema: [docs/data-contract.md](docs/data-contract.md).
- **`evalset-v2`:** 18.984 câu, gồm `vn_facts` (1.077 câu dữ kiện Việt Nam, đã kiểm chứng), Global-MMLU tiếng Việt (14.042 câu), cùng các nhóm đa ngôn ngữ, kiến thức Việt Nam, đọc hiểu, code, ngôn ngữ, luật và toán. Chấm theo cloze (so log-likelihood của nội dung đáp án), nên dùng được cả với model nền nhỏ.
- **Công bố trên Hugging Face:**
  - [DXSL/cus-vi-open](https://huggingface.co/datasets/DXSL/cus-vi-open): khoảng 397 nghìn tài liệu (2,25 GB văn bản) có license mở do pipeline này làm ra: văn bản quy phạm pháp luật từ vbpl.vn, Wikipedia và các dự án Wikimedia tiếng Việt khác, VNOI Wiki. Đã che thông tin cá nhân và khử nhiễm eval. Phần báo, diễn đàn, sách có bản quyền chỉ dùng nội bộ.
  - [DXSL/cus-eval-public](https://huggingface.co/datasets/DXSL/cus-eval-public): các câu eval không thuộc holdout, từ nguồn có license mở (Global-MMLU, HumanEval, MBPP), đúng định dạng harness chấm. Các câu tự soạn (`vn_facts`, luật, toán, ngôn ngữ) là holdout, không công bố.

## Nhật ký thí nghiệm

Mỗi phase lab (15–19) có một nhật ký đầy đủ trong [docs/lab/journal/](docs/lab/journal/): mục tiêu, cách làm, tiêu chí thành công, và Deviation Log ghi theo ngày mọi quyết định, sự cố, kết quả âm và chi phí. Mục đích: người sau biết công thức nào đã thử, thắng hay thua bao nhiêu, tốn bao nhiêu, để khỏi phải chạy lại.

Số đo thô của cả 55 run nằm trên Hugging Face: [DXSL/cus-lab-runs](https://huggingface.co/datasets/DXSL/cus-lab-runs). Gồm config, log train từng bước, bpb từng đoạn held-out để so cặp, kết quả ngữ cảnh dài, điểm eval từng câu theo id (không có đề), và tokenizer `lab-vi-32k`. Không có trọng số. Đồng bộ lại bằng `python -m lab.publish_runs`.

## License

Mã nguồn và tài liệu: [Apache License 2.0](LICENSE). Dữ liệu từ bên thứ ba giữ license gốc của từng nguồn, ghi trong `manifests/sources.yaml`.
