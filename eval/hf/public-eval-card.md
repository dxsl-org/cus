---
license: other
license_name: per-item
license_link: https://huggingface.co/datasets/DXSL/cus-eval-public#license
language:
- vi
- en
- de
- es
- fr
- ja
- ko
- ru
- zh
pretty_name: Cus public eval items
configs:
- config_name: evalset-v2
  data_files: data/evalset-v2.jsonl
  default: true
- config_name: evalset-v1
  data_files: data/evalset-v1.jsonl
---

# Cus public eval items

The public part of the frozen eval sets of [Cus](https://github.com/dxsl-org/cus), exactly as its harness scores them, so the per-item scores in [DXSL/cus-lab-runs](https://huggingface.co/datasets/DXSL/cus-lab-runs) can be reproduced by id.

Phần công khai của các bộ eval đóng băng của [Cus](https://github.com/dxsl-org/cus), đúng như harness chấm, để tái lập được điểm từng câu trong [DXSL/cus-lab-runs](https://huggingface.co/datasets/DXSL/cus-lab-runs) theo `id`.

| Bộ eval | Công khai | Giữ lại |
|---|---|---|
{totals}

**Giữ lại** gồm:
- Toàn bộ câu tự soạn (`vn_facts`, luật, toán, ngôn ngữ): thuộc holdout, là thước đo nội bộ, không bao giờ công bố để không model nào học trước được.
- Câu từ các bản mirror chưa rõ license (ViQuAD 2.0, VMLU).

## License

Mỗi câu giữ license của bộ gốc, ghi trong trường `license`:

| Nguồn | License | Số câu |
|---|---|---|
{sources}

## Trường dữ liệu

- `id`: `<nguồn>:<khoá>`, trùng với `id` trong file điểm của DXSL/cus-lab-runs.
- `group`, `domain`, `lang`, `difficulty` (1–5): nhóm báo cáo của harness.
- `messages`: đề như model nhận; `choices`, `answer_key`: phương án và đáp án đúng (câu trắc nghiệm).
- `meta.cloze`: câu điền chỗ trống cho chế độ `cloze` (nếu có); `verifier`: cách chấm (chế độ `generate`).
- `split`: luôn là `test` (holdout không được công bố).

Chấm bằng code của Cus (bản công khai là tập con nên chạy như trial, không qua MANIFEST): `python -m eval.harness --model <model> --items data/evalset-v2.jsonl --mode cloze`. Điểm từng câu so được với file điểm cùng `id` trong DXSL/cus-lab-runs. Code: [`dxsl-org/cus@{commit}`](https://github.com/dxsl-org/cus/tree/{commit}).
