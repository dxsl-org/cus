# Lab kiến trúc cỡ S: kết quả phase 15–18

Báo cáo này tổng hợp các thí nghiệm kiến trúc và công thức huấn luyện ở cỡ S (khoảng 40M tham số kích hoạt), từ ngày 19 đến 22/11/2026. Kết quả được dùng để chọn cấu hình cho model cỡ M. Quyết định cuối cùng ghi trong [ADR 0001](../decisions/0001-combo-m-architecture-and-training-recipe.md).

Diễn biến chi tiết, kể cả các sự cố và sai lệch so với kế hoạch, nằm trong Deviation Log của từng phase trong [nhật ký thí nghiệm](journal/).

## Cách đo

- **bpb (bits per byte)** đo trên phần held-out của từng domain, với tokenizer `lab-vi-32k`. Số càng nhỏ càng tốt, và không phụ thuộc tokenizer. `bpb/all` là trung bình đều trên 9 domain, mỗi domain 64 đoạn cố định (`DomainEval`).
- **Điều kiện so sánh:** mọi run cỡ S dùng cùng hỗn hợp dữ liệu, 0,5B token, batch 262k token và seed 0 (nên cùng thứ tự dữ liệu), trừ khi ghi khác.
- **Kiến thức:** đo bằng `evalset-v2` ở chế độ cloze (chấm theo nội dung đáp án). Các bộ chính là `vn_facts` (1.077 câu) và `vi_mmlu` (14.042 câu).
- **So cặp:** dùng `lab.compare_bpb` cho bpb (256 đoạn mỗi domain, bootstrap theo đoạn) và `eval.compare` cho eval (bootstrap theo câu hỏi).
- Dữ liệu huấn luyện là 4,73B token, đã khử nhiễm với mọi bộ eval đã đóng băng (v1 và v2).

## Mức nhiễu (đo ở phase 18)

| Nguồn nhiễu | Mức | Cách đo |
|---|---|---|
| Văn bản dùng để đo | khoảng ±0,04% (CI95 của `bpb/all`, 256 đoạn mỗi domain) | bootstrap theo đoạn |
| Seed, model dense | σ ≈ 0,03% | 3 seed của công thức dense thắng: 0,7903 / 0,7897 / 0,7900 |
| Seed, model có MoE | σ ≈ 0,4% (ước từ 1–2 cặp) | combo-S seed 0 so với seed 1: +0,57%; A3: +0,8% |
| Môi trường (GPU, `micro_batch`, cách dispatch MoE) | ≤ 0,18% | dense: Modal so với 4070 −0,18%; combo: Modal A100 dispatch `loop` so với 4070 dispatch `grouped` −0,03% |

**Ngưỡng tin cậy khi so hai run đơn lẻ:**
- model dense: chênh trên khoảng 0,1%;
- model có MoE: chênh trên khoảng 1,2%, hoặc chạy 2 seed mỗi bên.

Model có MoE nhạy với seed hơn dense khoảng 13 lần. Giả thuyết (chưa kiểm chứng) là cách router chia việc cho expert hình thành sớm và phụ thuộc vào seed.

## Phase 15: mốc dense

| Run | Cấu hình | Tham số | Token | LR | bpb/all |
|---|---|---|---|---|---|
| dense-S (`dense-s-full-lr3.0e-3`) | d512, 8 lớp, 8 head (4 KV) | 40,4M | 0,5B | 3e-3 | **0,8245** (mốc) |
| dense-M | d768, 12 lớp, 12 head (4 KV) | 100,7M | 2B | 2e-3 | 0,7268 |

Quét LR quá ngắn (0,1B token) chọn sai LR: thứ hạng đảo ngược khi chạy đủ 0,5B. Từ đó, quét LR phải dài ít nhất khoảng 40% tổng số token.

## Phase 16: MoE (40,4M tham số kích hoạt, so với dense-S)

| Run | Thiết kế | Tổng tham số | bpb/all | so với mốc |
|---|---|---|---|---|
| A1 | 8 expert, top-2, aux loss | 92,3M | 0,8153 | −1,1% |
| A2 | 32 expert nhỏ, top-4, 1 expert chung, aux loss | 136,7M | 0,7923 | −3,9% |
| A3 | như A2, cân bằng bằng bias | 136,7M | 0,7913 | −4,0% |

- Yếu tố chính là dùng nhiều expert nhỏ kèm một expert chung (từ A1 sang A2: −2,8%).
- Cân bằng bằng bias và bằng aux loss cho kết quả ngang nhau (chênh nằm trong nhiễu MoE). Chọn bias vì không cần thêm loss phụ.
- Expert chia việc chủ yếu theo loại token, chỉ hơi chuyên hoá theo domain.

## Phase 17: lai Gated DeltaNet + MTP, rồi gộp lại

### Bộ trộn token (FFN dense, so với dense-S)

| Run | Thiết kế | bpb/all | so với mốc |
|---|---|---|---|
| H1 | GDN 3:1 (3 lớp GDN, 1 lớp attention) | 0,8094 | −1,8% (ít hơn 15% FLOPs) |
| H2 | chỉ GDN | 0,8190 | −0,7% |
| H3 | H1 + cổng attention | 0,8082 | −2,0% |
| H4 | H3 + MTP (dự đoán thêm token t+2) | 0,8076 | −2,0% |

H1, H3, H4 chênh nhau 0,1–0,2%, coi như hoà. Cổng attention và MTP được giữ lại vì rẻ. Riêng MTP còn cho phép giải mã tự suy đoán (speculative decoding): 69% bản nháp t+2 được chấp nhận.

**Ngữ cảnh dài** (train ở 2048, đo tới 8192):
- dense-S hỏng sau 2k (bpb 1,60 ở đoạn 4–8k), còn hybrid ổn định (H4: 0,91).
- Trạng thái lưu khi sinh văn bản ở 8k: dense 64 MB, hybrid 16,8 MB.

### combo-S = GDN 3:1 + cổng attention + MoE kiểu A3 + MTP

- bpb/all **0,7804**: −5,3% so với dense-S, −1,4% so với A3. Lợi ích của hai hướng cộng dồn được khoảng 80%.
- 144,2M tham số tổng, 47,9M kích hoạt.

### Thích nghi theo domain (dense-M, luật, 0,3B token)

| Run | bpb/law | bpb/all |
|---|---|---|
| dense-M (chưa thích nghi) | 0,5641 | 0,7268 |
| L1: luật + 24% dữ liệu gốc (replay) | 0,5236 | 0,7265 |
| L2: chỉ luật | 0,5191 | 0,7491 |
| L3: model chỉ học luật, train từ đầu | 0,6243 | 1,0726 |

Một model chung rồi thích nghi có replay tốt hơn hẳn model riêng cho từng domain. Vì vậy chọn hướng một model chung kèm thích nghi.

## Phase 18: optimizer, lịch LR, μP

### dense-S, 0,5B token

| Run | bpb/all | so với mốc |
|---|---|---|
| Mốc: cosine + AdamW | 0,8245 | — |
| E1: WSD (20% cuối giảm theo 1−√) | 0,8115 | −1,6% |
| E2: WSD + annealing (20% cuối chuyển sang hỗn hợp dữ liệu chất lượng cao) | 0,8031 | −2,6% |
| E3: Muon + WSD, LR 0,005 / 0,01 / 0,02 / 0,04 | 0,8091 / **0,8032** / 0,8056 / 0,8139 | LR 0,01 thắng |
| Muon 0,01 + WSD + annealing | **0,7911** | −4,1% |

- **Muon so với AdamW** (cùng WSD, so cặp): −0,97% [−1,00; −0,94]. **LR 0,01 so với 0,02:** +0,22% [+0,19; +0,24]. Cả hai đều vượt xa nhiễu dense.
- **Annealing** giảm bpb ở các domain chất lượng cao (reference −5%, education −3%) nhưng làm tăng ở web và forum (+2–3%). Ở combo, mức tăng này gần như biến mất.

### μP: chuyển LR theo độ rộng (Muon + WSD, 0,2B token, độ sâu 8)

| Độ rộng | LR 0,005 | 0,01 | 0,02 | 0,04 | LR tối ưu (khớp parabol) |
|---|---|---|---|---|---|
| 256 | 0,9603 | 0,9375 | 0,9326 | 0,9378 | 0,020 |
| 512 | 0,8871 | 0,8692 | 0,8660 | 0,8757 | 0,017 |
| 768 | 0,8537 | 0,8398 | 0,8405 | 0,8526 | 0,014 |

- LR tối ưu trôi chậm khi model rộng ra (khoảng ∝ d^−0,31), nên μP chỉ chuyển LR gần đúng.
- Đáy đường cong rất phẳng: dùng chung một LR cho cả ba độ rộng chỉ thiệt ≤ 0,2%.
- Run dài hơn ưa LR thấp hơn.

### E5: combo-S + công thức mới (Muon 0,01 + WSD + annealing)

| Run | bpb/all | so với dense-S |
|---|---|---|
| combo-S seed 0 / seed 1 | 0,7804 / 0,7846 | −5,3% / −4,8% |
| **combo-S + công thức mới** | **0,7473** | **−9,4%** |

- Lợi ích kiến trúc (−5,3%) và lợi ích công thức (−4,1%) cộng dồn trọn vẹn.
- So cặp với combo-S: −4,22% [−4,33; −4,12], cao hơn khoảng 3,5 lần ngưỡng nhiễu MoE.
- Ngữ cảnh dài không kém H4: needle ở độ sâu 90% đúng 85% ở 8k; MTP chấp nhận 69,7%.
- Tải expert cân bằng (expert bận nhất 1,24 lần trung bình), không có expert chết.

## Kiến thức (`evalset-v2`, cloze, acc)

| Model | `vn_facts` | `vi_mmlu` |
|---|---|---|
| dense-S | 0,278 | 0,261 |
| H4 | 0,293 | 0,258 |
| combo-S seed 0 / seed 1 | 0,295 / 0,314 | 0,259 / 0,260 |
| A3 seed 0 / seed 1 | 0,303 / 0,299 | 0,262 / 0,257 |
| **combo-S + công thức mới** | **0,332** | 0,260 |
| dense-M (100M) | 0,334 | 0,261 |
| Qwen2.5-0.5B (base) | 0,297 | 0,277 |
| L1 (dense-M thích nghi luật) | 0,359 | 0,261 |

- Ở cỡ S, eval kiến thức không phân biệt được các biến thể optimizer và lịch LR: chính combo-S đổi seed đã lệch 1,9 điểm `vn_facts`. `vi_mmlu` ở mức đoán bừa (0,25).
- Vì vậy ở cỡ S chọn theo bpb; eval kiến thức chỉ có ý nghĩa từ cỡ M trở lên.
- Sức nhớ dữ kiện đi theo tổng số tham số: dense-M và các model MoE hơn dense-S.

## Hạ tầng rút ra

- **MoE dispatch** bằng `torch._grouped_mm`: A3 nhanh hơn 1,8×, combo-S nhanh hơn 3,2× trên 4070, và không còn graph break.
- **4070 (12 GB) trên Windows:** khi tràn VRAM, driver lặng lẽ chuyển sang RAM thay vì báo lỗi OOM; dấu hiệu duy nhất là token/s tụt.
  - MoE, hybrid và d768 dùng `micro_batch` 8.
  - Khi `dwm.exe` rò VRAM (6,8 GB sau hai ngày chạy liên tục), run tụt còn một nửa tốc độ.
- **Gated DeltaNet trên H100** cần Triton ≥ 3.7.1 (fla #640), tức torch ≥ 2.12.1. Lab đã nâng lên torch 2.12.1 + CUDA 13.0 ngày 22/11.
  - combo-S trên Modal H100 đạt 375k token/s, gấp 2,8 lần A100 (133k). Theo giá Modal, chi phí mỗi token rẻ hơn khoảng 1,8 lần.
  - Loss 30 bước đầu khớp với run A100.
