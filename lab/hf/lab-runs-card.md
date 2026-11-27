---
license: apache-2.0
language:
- vi
pretty_name: Cus lab runs
tags:
- training-logs
- pretraining
- mixture-of-experts
- gated-deltanet
- vietnamese
configs:
- config_name: summary
  data_files: summary.csv
---

# Cus lab runs

Training logs, configs and evaluation scores of every experiment in the [Cus](https://github.com/dxsl-org/cus) architecture lab: {n_runs} runs, from 40M-parameter dense baselines to the 446M-parameter Cus-M (Gated DeltaNet + attention, MoE, MTP, Muon, WSD). Published so that nobody has to rerun an experiment just to learn its result. No model weights are included.

Nhật ký train, config và điểm đánh giá của mọi thí nghiệm trong lab kiến trúc của [Cus](https://github.com/dxsl-org/cus), LLM thuần Việt train từ số 0: {n_runs} run, từ dense 40M tham số tới Cus-M 446M tham số. Công bố để người sau không phải chạy lại chỉ để biết kết quả. Không kèm trọng số.

- Code đã tạo các run: [`dxsl-org/cus@{commit}`](https://github.com/dxsl-org/cus/tree/{commit}).
- Diễn giải kết quả: [báo cáo lab cỡ S](https://github.com/dxsl-org/cus/blob/main/docs/lab/lab-s-size-results-phase-15-18.md), [báo cáo lab cỡ M](https://github.com/dxsl-org/cus/blob/main/docs/lab/lab-m-size-results-phase-19.md), [nhật ký thí nghiệm](https://github.com/dxsl-org/cus/tree/main/docs/lab/journal) (mọi quyết định, sự cố, kết quả âm, chi phí).

## Nội dung

```text
summary.csv                       mỗi run một dòng: bước cuối, bpb held-out cuối (tổng và theo 9 domain)
runs/<run>/config.yaml            config đầy đủ (model, mix dữ liệu, optimizer, lịch LR)
runs/<run>/log.jsonl              mỗi bước: loss, lr, grad_norm, tok_s, mfu, moe_load_max; mỗi lần eval: bpb theo domain, vài đoạn model tự sinh (samples)
runs/<run>/bpb-windows-256.npz    bpb của 256 đoạn held-out cố định mỗi domain, dùng để so cặp hai run (lab.compare_bpb)
runs/<run>/long_context.{json,md} needle, bpb theo vị trí tới 8192 token, bộ nhớ trạng thái khi sinh
runs/<run>/experts.{json,md}      MoE: mức chuyên môn hoá của expert theo domain
eval/<evalset>/<model>/<mode>.json    điểm theo nhóm, ngôn ngữ, domain, độ khó, kèm CI95 Wilson
eval/<evalset>/<model>/<mode>.jsonl   từng câu theo id: đáp án chọn, đúng/sai, p_correct, log-prob từng phương án
tokenizer/lab-vi-32k.json         tokenizer BPE 32k tiếng Việt dùng cho mọi run (thư viện tokenizers)
```

- **bpb** (bits per byte) đo trên tập held-out cố định của 9 domain: web, news, law, literature, reference, forum, health, culture_history, education. Càng thấp càng tốt, so được giữa các tokenizer.
- **Eval** chỉ gồm id và điểm, không có đề: một phần bộ eval là holdout nội bộ. Chế độ `generate` bỏ phần văn bản model sinh. So cặp hai model trên cùng id bằng `python -m eval.compare`.
- Run tên `lab_*` trong `eval/` là checkpoint lab (`lab:<run>`); tên `Qwen__*` là model Hugging Face, chấm cùng harness để làm mốc.

## Giới hạn

- Dữ liệu train gồm cả nguồn có bản quyền (báo, diễn đàn, sách) dùng nội bộ, nên trọng số không được công bố; danh sách nguồn và license ở [`manifests/sources.yaml`](https://github.com/dxsl-org/cus/blob/main/manifests/sources.yaml).
- Trường `samples` trong log là văn bản model tự sinh ở mỗi lần eval; với model nhỏ, có thể trùng một phần với văn bản đã train.
- Nhiễu giữa hai seed của cùng công thức MoE ở cỡ S là khoảng 1,2% bpb; xem báo cáo lab trước khi đọc chênh lệch nhỏ.
