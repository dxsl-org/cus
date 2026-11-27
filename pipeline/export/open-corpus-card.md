---
license: other
license_name: per-document
license_link: https://huggingface.co/datasets/DXSL/cus-vi-open#license
language:
- vi
pretty_name: Cus Vietnamese open corpus
task_categories:
- text-generation
size_categories:
- 100K<n<1M
configs:
{configs}
---

# Cus Vietnamese open corpus

Openly licensed Vietnamese text collected and cleaned by the [Cus](https://github.com/dxsl-org/cus) data pipeline: legal normative documents and Vietnamese Wikimedia projects, extracted, filtered, deduplicated, PII-masked and decontaminated against Cus's eval sets.

Văn bản tiếng Việt có license mở do pipeline dữ liệu của [Cus](https://github.com/dxsl-org/cus) thu thập và làm sạch: văn bản quy phạm pháp luật và các dự án Wikimedia tiếng Việt. Đã bóc tách, lọc, khử trùng lặp, che thông tin cá nhân, và khử nhiễm với các bộ eval của Cus.

| Nguồn (config) | Tài liệu | MB văn bản | Bỏ vì trùng eval |
|---|---|---|---|
{table}

## License

Mỗi tài liệu giữ license của nguồn, ghi trong trường `license`:

- `vn-not-protected`: văn bản quy phạm pháp luật, văn bản hành chính, không thuộc đối tượng bảo hộ quyền tác giả theo Điều 15 Luật Sở hữu trí tuệ Việt Nam (`vbpl_vn`).
- `cc-by-sa-4.0`: Wikipedia, Wikisource, Wiktionary, Wikibooks, Wikiquote, Wikivoyage tiếng Việt. Dùng lại phải ghi nguồn (trường `url`) và chia sẻ theo cùng license.
- `agpl-3.0`: VNOI Wiki.

Không có tài liệu nào có license `copyrighted` hoặc chưa rõ license.

## Cách làm

- Trường: `doc_id`, `source_id`, `url`, `title`, `text`, `publish_date`, `license`.
- Nguồn đã qua phase 6 của pipeline (`vbpl_vn`, `vi_wikipedia_org`): lọc chất lượng, khử trùng lặp chính xác và MinHash, che thông tin cá nhân. Các nguồn Wikimedia nhỏ lấy từ bản canonical và được che thông tin cá nhân khi xuất.
- Khử nhiễm 13-gram với mọi bộ eval đóng băng ({evalsets}): tài liệu phủ từ 50% số 13-gram của một câu eval bị loại, kể cả câu holdout không công bố.
- Code: [`dxsl-org/cus@{commit}`](https://github.com/dxsl-org/cus/tree/{commit}), lệnh `python -m pipeline.export.open_corpus`.

## Không nằm trong bộ này

- Dataset mở đã có trên Hugging Face mà Cus dùng (lấy trực tiếp từ nguồn): [FineWeb-2](https://huggingface.co/datasets/HuggingFaceFW/fineweb-2) (`vie_Latn`), [CulturaX](https://huggingface.co/datasets/uonlp/CulturaX) (`vi`), [HPLT](https://huggingface.co/HPLT), [FinePDFs](https://huggingface.co/datasets/HuggingFaceFW/finepdfs).
- Bản án (`toaan_gov_vn`): có tên đương sự, nên không công bố dù không vướng quyền tác giả.
- Báo, diễn đàn, sách: có bản quyền, chỉ dùng nội bộ.
