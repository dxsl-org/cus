# Lab kiến trúc cỡ M: kết quả phase 19

Báo cáo này tổng hợp các run của combo-M (kiến trúc và công thức đã chốt trong [ADR 0001](../decisions/0001-combo-m-architecture-and-training-recipe.md)) từ ngày 22 đến 24/11/2026: kiểm tra LR, run chính 2B token, kéo dài ngữ cảnh lên 8192, và ba cách thích nghi luật. Mốc so sánh là dense-M và L1 của dense-M trong [báo cáo lab cỡ S](lab-s-size-results-phase-15-18.md).

Diễn biến chi tiết nằm trong Deviation Log của [nhật ký phase 19](journal/phase-19-combo-m-runpod.md).

## Cách đo

Giống báo cáo cỡ S: `bpb/all` là trung bình đều trên 9 domain (64 đoạn held-out cố định mỗi domain); kiến thức đo bằng `evalset-v2` ở chế độ cloze; so cặp bằng `lab.compare_bpb` (256 đoạn mỗi domain) và `eval.compare` (bootstrap theo câu hỏi, 10.000 lần). `p_correct` là xác suất model đặt vào đáp án đúng; nó ít nhiễu hơn `acc`.

## Cấu hình combo-M

| | Giá trị |
|---|---|
| Kích thước | d768, 12 lớp `gggagggaggga` (3 GDN : 1 attention có cổng), 12 head (4 KV) |
| MoE | 32 expert × 416, top-4, 1 expert chung, cân bằng bằng bias |
| Tham số | 446,4M tổng, 124,3M kích hoạt; 835 MFLOP/token ở seq 2048 (dense-M: 831) |
| Công thức | Muon 0,01 + AdamW 3e-3, WSD 20% kèm annealing, μP base 512, MTP 0,3 |
| Dữ liệu | 2B token, seq 2048, batch 262k token (như dense-M) |

## Kiểm tra LR (0,3B token, RTX 4070)

| `muon_lr` | bpb/all cuối run | so với 0,01 |
|---|---|---|
| 0,005 | 0,7520 | +1,05% |
| **0,01** (μP dự đoán) | **0,7442** | — |
| 0,02 | 0,7463 | +0,28% |

- 0,005 kém ở cả 5 lần chấm, gấp khoảng 2,5 lần nhiễu seed của MoE. 0,02 dẫn trước tới bước 500 rồi về đích kém hơn, trong mức nhiễu.
- Không có sai lệch cỡ 2 lần: μP chuyển LR đúng cho MoE + GDN ở d768. Giữ 0,01 (run dài ưa LR thấp hơn run ngắn).

## Run chính: combo-M 2B token

| Domain | dense-M | combo-M | chênh |
|---|---|---|---|
| **all** | 0,7268 | **0,6501** | **−10,6%** |
| reference | 0,8939 | 0,7615 | −14,8% |
| education | 0,6303 | 0,5377 | −14,7% |
| health | 0,5930 | 0,5178 | −12,7% |
| law | 0,5641 | 0,4956 | −12,1% |
| literature | 0,9661 | 0,8529 | −11,7% |
| culture_history | 0,5969 | 0,5275 | −11,6% |
| news | 0,6160 | 0,5510 | −10,6% |
| web | 0,7777 | 0,7321 | −5,9% |
| forum | 0,9242 | 0,8935 | −3,3% |

- So cặp (256 đoạn mỗi domain): `bpb/all` −10,60%, CI95 [−10,76; −10,45]. Mọi domain đều tốt hơn với p < 0,001; nhỏ nhất là forum −3,09% [−3,62; −2,54]. Mức chênh gấp khoảng 9 lần ngưỡng nhiễu MoE một run (khoảng 1,2%).
- Cỡ S cho −9,4%; ở cỡ M lợi ích không co lại mà tăng nhẹ.
- Lợi ích nhỏ nhất ở web và forum, hai domain mà annealing không tăng trọng số.
- Huấn luyện ổn định: sau bước 500, `grad_norm` có trung vị 0,19 và lớn nhất 0,38.

## Kiến thức (`evalset-v2`, cloze)

| Model | `vn_facts` | `vn_facts:law` | `vn_knowledge` | `vi_mmlu` |
|---|---|---|---|---|
| dense-M | 0,334 | 0,374 | — | 0,261 |
| dense-M L1 | 0,359 | 0,478 | — | 0,261 |
| **combo-M** | **0,473** | 0,566 | 0,284 | 0,262 |
| combo-M ctx8k | 0,464 | 0,533 | 0,290 | 0,262 |
| **combo-M L1** | **0,473** | **0,610** | 0,285 | 0,260 |
| Qwen2.5-0.5B (base) | 0,297 | 0,297 | 0,289 | 0,277 |
| Qwen2.5-1.5B (base) | 0,417 | 0,445 | 0,323 | 0,305 |
| Qwen2.5-3B (base) | 0,524 | 0,538 | 0,352 | 0,330 |

- combo-M so với dense-M: `vn_facts` +13,8 điểm, CI95 [+11,1; +16,6], p < 0,001. combo-M L1 so với dense-M L1: +11,4 điểm, p < 0,001.
- Ở cỡ S, kiến thức không phân biệt được các công thức; ở cỡ M, combo hơn dense rõ rệt. Lợi ích này lớn hơn nhiều so với chênh bpb, phù hợp với việc sức nhớ dữ kiện đi theo tổng tham số (446M so với 101M).
- `vi_mmlu` vẫn ở mức đoán bừa với mọi model cỡ M.

**So với Qwen2.5 base** (so cặp, chấm 25/11 trên RTX 4070; Qwen train khoảng 18 nghìn tỷ token, gấp khoảng 9.000 lần combo-M):
- `vn_facts`: combo-M hơn Qwen2.5-0.5B (+17,5 điểm) và 1.5B (+5,6 điểm, CI95 [+2,4; +8,7]), kém 3B (−5,1 điểm, CI95 [−8,4; −1,9]).
- `vn_facts:law`: combo-M L1 0,610 so với Qwen2.5-3B 0,538: `p_correct` +8,9 điểm (p < 0,001), `acc` +7,1 điểm (p = 0,04). combo-M chưa thích nghi ngang 3B ở câu luật.
- `vn_knowledge` và `vi_mmlu`: Qwen hơn ở mọi cỡ từ 1.5B (combo-M kém 1.5B 3,9 điểm `vn_knowledge`, kém 3B 6,7 điểm `vi_mmlu`). Kiến thức tổng quát và kiểu câu MMLU đi theo lượng dữ liệu, nơi combo-M thiếu nhiều nhất.

## Kéo dài ngữ cảnh lên 8192 (0,2B token)

| | combo-M (train ở 2048) | combo-M ctx8k |
|---|---|---|
| bpb đoạn 2–4k | 0,6788 | 0,6743 |
| bpb đoạn 4–8k | 0,6900 (+1,7%) | **0,6719 (−0,4%)** |
| Needle ở 2048 (độ sâu 10 / 50 / 90%) | 40 / 100 / 100% | 100 / 100 / 100% |
| Needle ở 4096 | 10 / 25 / 90% | 10 / 75 / 100% |
| Needle ở 8192 | 15 / 0 / 70% | 0 / 0 / 100% |
| MTP chấp nhận | 73,8% | 74,8% |
| Trạng thái khi sinh ở 8192 | 25,8 MB | 25,8 MB |

- Tiêu chí ngữ cảnh 8192 (đoạn 4–8k không tệ hơn đoạn 2–4k quá 5%) đạt.
- **Điểm yếu:** ở 4–8k model chỉ tìm được needle gần cuối văn bản. Model có 3 lớp attention đầy đủ nên về nguyên tắc truy xuất được mọi vị trí; nhiều khả năng là do 0,2B token kéo dài quá ít và thiếu văn bản dài, không phải giới hạn của GDN (chưa kiểm). Mỗi ô chỉ có 20 lần thử.
- ctx8k làm `vn_facts` `p_correct` giảm 1,1 điểm (p = 0,012).

## Thích nghi luật (228M token luật, kèm replay)

| Cách chạy | bpb/law | bpb/all | `vn_facts` | `vn_facts:law` |
|---|---|---|---|---|
| combo-M (chưa thích nghi) | 0,4956 | 0,6501 | 0,473 | 0,566 |
| L1: từ checkpoint cuối, 24% replay theo mix pretrain | **0,4620** | 0,6621 | 0,473 | 0,610 |
| L1 replay40: như L1, 40% replay | 0,4634 | 0,6557 | 0,481 | 0,632 |
| **L1 replay40-reflit: 40% replay, nâng reference 8%, literature 6%** | 0,4635 | **0,6495** | **0,482** | **0,637** |
| L1 từ `ckpt-stable` (trước pha giảm LR) | 0,4686 | 0,6767 | 0,440 | — |
| L1, 24% replay theo mix anneal | 0,4625 | 0,6602 | 0,473 | 0,610 |

- **L1 combo so với L1 dense** (so cặp): `bpb/all` −8,84%, CI95 [−8,94; −8,74]; `bpb/law` −12,36% [−12,78; −11,97]; mọi domain tốt hơn với p < 0,001. Mức thích nghi luật ngang dense (−6,8% so với −7,2%), nhưng combo quên nhiều hơn: `bpb/all` +1,8% (dense: 0,0%), nặng nhất ở reference (+6,5%) và literature (+5,2%).
- **Bắt đầu từ `ckpt-stable`** (cách thích nghi kiểu WSD) kém ở mọi domain và mất 3,3 điểm `vn_facts` (CI95 [−5,5; −1,2]): nó không có phần lợi ích của pha anneal, mà L1 từ checkpoint cuối vẫn giữ lại được một phần (reference: 0,8113 so với 0,8527). **Loại.**
- **Replay theo mix anneal** giảm một nửa mức quên ở reference (+3,2%), literature (+2,8%), education, culture_history; đổi lại forum (+3,4%) và web (+2,6%) quên thêm vì bị cắt khỏi replay. `vn_facts` không đổi. Mức quên đi theo mix replay, không do LR.
- **Replay 40%** (giữ nguyên lượng token luật, tổng 0,38B token, thêm 27% compute): so cặp với L1, `bpb/law` ngang nhau (−0,03%, CI95 [−0,20; +0,14]), mọi domain khác tốt hơn 0,8–1,7%, `bpb/all` −1,05% (p < 0,001). Chênh cloze không có ý nghĩa. So với combo-M vẫn quên reference +6,0% và literature +4,2%: tăng replay chỉ giảm quên chậm.
- **Replay 40% nghiêng về reference và literature** (`reflit`, cùng số bước; web trong replay giảm từ 33% xuống 19%): so cặp với replay40, reference −3,77%, literature −2,32%, forum, education, culture_history −0,6 đến −0,9%; web +0,84%, law +0,10% (CI95 [+0,02; +0,17]); `bpb/all` −0,95% (p < 0,001). So với combo-M: `bpb/law` −6,97% mà `bpb/all` không đổi (−0,04%, CI95 [−0,14; +0,05]). Còn quên education +2,5%, health +2,2%, reference +2,0%, literature +1,8%, culture_history +0,9%; forum và web không quên. Cloze ngang replay40.
- Công thức tham chiếu: **từ checkpoint cuối, 60% domain + 40% replay, replay nghiêng về các domain dễ quên** (`combo-m-law-l1-replay40-reflit`): thích nghi luật mà không mất `bpb/all`. Mức quên còn lại nằm ở các domain nhỏ (education, health), nên bước tiếp theo nếu cần là nâng chúng trong replay.

## Chi phí và hạ tầng

| Việc | Nơi chạy | Thời gian | Chi phí |
|---|---|---|---|
| 3 run kiểm tra LR | RTX 4070 (`micro_batch` 2), 18,7k token/s | khoảng 13,5 giờ | $0 |
| combo-M 2B + ctx8k + `eval_long_context` | RunPod Secure H100 PCIe ($2,89/giờ) | khoảng 6,5 giờ pod | khoảng $20,7 |
| 5 run thích nghi luật + chấm điểm | RTX 4070 | khoảng 28 giờ | $0 |

- **RunPod Community hết H100** suốt khoảng 12 giờ dò (1.703 lần, cả SXM, NVL và PCIe). Secure H100 PCIe có máy sau khoảng 1 giờ. `runpod_train up` có `--cloud SECURE --gpu "H100 PCIe"`.
- **H100 PCIe chạy combo-M ở 112–119k token/s** (MFU 0,13), khoảng 62–65% tốc độ H100 SXM (181k trên Modal). Bước đầu mất khoảng 6 phút để Triton biên dịch kernel fla.
- **Upload shard qua ssh** ở khoảng 3,7 MB/s (9,55 GB) bị đứt một lần giữa chừng; `up` chạy lại chỉ gửi phần thiếu. Thời gian GPU chờ upload tốn khoảng $3, nên lần sau nên upload trước khi pod có GPU, hoặc dùng network volume của Secure.
- **RTX 4070 với combo-M:** `micro_batch` 8 và 4 tràn VRAM sang RAM (1,3k và 2,7k token/s); `micro_batch` 2 đạt 18–20k token/s. Batch token giữ nguyên nhờ tích luỹ gradient.
- **`train.init_ckpt`** chọn checkpoint của run nền (mặc định `ckpt.pt`, hoặc `ckpt-stable.pt`).
