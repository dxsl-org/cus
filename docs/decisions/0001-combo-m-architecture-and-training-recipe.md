# 0001. Kiến trúc và công thức huấn luyện cho model cỡ M: combo (GDN 3:1 + MoE + MTP) với Muon, WSD + annealing, μP

- **Trạng thái:** Chấp nhận
- **Ngày:** 2026-11-22
- **Bằng chứng:** [Lab cỡ S, phase 15–18](../lab/lab-s-size-results-phase-15-18.md); kiểm chứng ở cỡ M: [Lab cỡ M, phase 19](../lab/lab-m-size-results-phase-19.md) (24/11)

## Bối cảnh

Dự án huấn luyện một LLM ưu tiên tiếng Việt. Trước khi chi tiền thuê GPU cho các model lớn hơn, cần chốt hai việc ở quy mô nhỏ, nơi một run chỉ tốn vài giờ trên RTX 4070 hoặc vài USD trên cloud:
- kiến trúc model;
- công thức huấn luyện (optimizer, lịch LR, cách chuyển LR khi đổi kích thước).

Các ràng buộc:
- **Ngân sách GPU nhỏ:** máy local 12 GB VRAM, credit Modal miễn phí, RunPod trả tiền cho cỡ M.
- **Dữ liệu:** 4,73B token tiếng Việt thuộc 9 domain, trong đó có luật, y tế, văn học.
- **Cách dùng model về sau:** cần ngữ cảnh dài, chạy suy luận rẻ, và thích nghi theo domain (luật trước tiên).

## Các yếu tố quyết định

- bpb trên held-out, ở cùng số token và cùng tham số kích hoạt. Chênh lệch phải vượt mức nhiễu đã đo: σ ≈ 0,03% với dense, khoảng 0,4% với MoE.
- Chi phí suy luận: FLOPs kích hoạt mỗi token, và kích thước trạng thái phải giữ khi sinh văn bản dài.
- Khả năng chuyển kết quả sang cỡ lớn hơn mà không phải dò lại toàn bộ siêu tham số.
- Độ phức tạp vận hành: kernel phải chạy được trên GPU thuê, độ ổn định khi train.

## Các phương án đã cân nhắc

### Kiến trúc

1. **Dense kiểu Llama** (mốc phase 15). Đơn giản và ổn định nhất, nhiễu theo seed thấp nhất. Nhưng bpb kém nhất: cỡ S đạt 0,8245. Ngữ cảnh dài hỏng sau độ dài đã train (bpb 1,60 ở đoạn 4–8k). Trạng thái khi sinh lớn (64 MB ở 8k). **Loại** vì kém về chất lượng, về ngữ cảnh dài lẫn chi phí suy luận.
2. **Chỉ MoE** (A3: 32 expert nhỏ, top-4, 1 expert chung, cân bằng bằng bias). Lợi nhất trong các thay đổi đơn lẻ: −4,0%, không tốn thêm FLOPs kích hoạt. Nhưng vẫn mang điểm yếu ngữ cảnh dài của attention đầy đủ. **Loại khi dùng một mình.**
3. **Chỉ lai GDN** (H4: 3 lớp Gated DeltaNet cho mỗi lớp attention, có cổng, có MTP). −2,0%, ít FLOPs hơn, ngữ cảnh dài ổn định, trạng thái 16,8 MB ở 8k. Nhưng thua MoE về bpb, và FFN dense không tăng được sức chứa kiến thức. **Loại khi dùng một mình.**
4. **Model chuyên biệt cho từng domain**, train từ đầu (L3, chỉ luật). Kém xa model chung rồi thích nghi: bpb/law 0,6243 so với 0,5236. **Loại.**
5. **Combo = GDN 3:1 + cổng attention + MoE kiểu A3 + MTP.** −5,3% với công thức cũ, cộng dồn được khoảng 80% lợi ích của hai hướng. Giữ ưu điểm ngữ cảnh dài và trạng thái nhỏ của hybrid. **Chọn.**

### Công thức huấn luyện

1. **Cosine + AdamW** (mốc). Đã quen thuộc, nhưng kém nhất: thua WSD 1,6%, thua Muon thêm khoảng 1%. **Loại.**
2. **WSD + AdamW.** −1,6%. Cho phép dừng hoặc kéo dài run mà không phải định trước tổng số bước. **Giữ WSD, thay optimizer.**
3. **Muon (ma trận ẩn và expert) + AdamW (embedding, gain, router) + WSD.** Tốt hơn AdamW ở mọi domain: −0,97%, CI [−1,00; −0,94]. Chỉ chậm khoảng 1%. **Chọn.**
4. **Thêm annealing** (20% cuối chuyển sang hỗn hợp dữ liệu chất lượng cao). Ở dense có đánh đổi: web và forum kém đi 2–3%. Ở combo, đánh đổi đó gần như biến mất, và tổng bpb giảm thêm khoảng 1,5%. **Chọn.**
5. **Dò lại LR ở mỗi kích thước, không dùng μP.** Tốn nhiều run ở cỡ M, mỗi run trả tiền thật. **Loại.** Thay bằng μP cộng 2 run kiểm tra ngắn.

## Quyết định

Model cỡ M dùng kiến trúc combo và công thức phase 18:

- **Bộ trộn token:** cứ 3 lớp Gated DeltaNet (fla 0.6.0) thì có 1 lớp attention có cổng, head dim 64.
- **FFN:** MoE kiểu A3: nhiều expert nhỏ, top-4, 1 expert chung, cân bằng tải bằng bias (không dùng aux loss), dispatch bằng `torch._grouped_mm`.
- **MTP:** dự đoán thêm token t+2, trọng số loss 0,3.
- **Optimizer:**
  - Muon cho ma trận ẩn, ma trận GDN và các tensor expert, `muon_lr` khoảng 0,01, scale phổ √max(1, fan_out/fan_in).
  - AdamW 3e-3 cho embedding, conv, các tham số gain và vô hướng.
  - Router và `wa`/`wb` của GDN đi nhóm readout (LR chia cho hệ số độ rộng m).
- **Lịch LR:** WSD, 20% cuối giảm theo 1−√, kèm hỗn hợp dữ liệu annealing trong đoạn giảm.
- **μP:** `mup_base_width` 512 (logit nhân base/d, khởi tạo co theo độ rộng).
- **Kiểm tra trước khi chạy dài:** 3 run thô ở cỡ M, mỗi run 0,3B token, với `muon_lr` 0,005 / 0,01 / 0,02. Mục đích chỉ là bắt sai lệch LR lớn (từ 2 lần trở lên), vì nhiễu seed của MoE (khoảng 0,4%) che các khác biệt nhỏ hơn. Run ngắn thường ưa LR cao hơn, nên khi chọn phải lệch về phía thấp. Ở cỡ S, LR tối ưu trôi khoảng ∝ d^−0,31. Trước đây dự định 2 run 0,8B với 0,007 / 0,014; đổi ngày 22/11 vì rẻ hơn mà vẫn đủ cho mục đích này.
- **Môi trường:** torch 2.12.1 + CUDA 13.0, kèm Triton 3.7.1, để kernel GDN chạy đúng trên H100 (fla #640).

Ở cỡ S, cấu hình này đạt bpb 0,7473, tức −9,4% so với dense cùng tham số kích hoạt.

### Kiểm chứng ở cỡ M (2026-11-24)

- **LR:** 3 run kiểm tra cho 0,005 / 0,01 / 0,02 → bpb 0,7520 / **0,7442** / 0,7463. μP chuyển đúng LR cho MoE + GDN ở d768; giữ `muon_lr` 0,01.
- **Run chính 2B token:** `bpb/all` 0,6501, tức −10,60% so với dense-M (so cặp, CI95 [−10,76; −10,45]); mọi domain đều tốt hơn. `vn_facts` 0,473 so với 0,334 (+13,8 điểm, p < 0,001).
- **Kéo dài ngữ cảnh lên 8192** (0,2B token): đoạn 4–8k không tệ hơn đoạn 2–4k (−0,4%). Needle ở 4–8k chỉ tìm được khi nằm gần cuối văn bản.
- **Thích nghi theo domain:** từ checkpoint cuối, 60% domain + 40% replay, replay nghiêng về các domain dễ quên (`combo-m-law-l1-replay40-reflit`: reference 8%, literature 6%): `bpb/law` −6,97% so với combo-M mà `bpb/all` không đổi (−0,04%, CI95 [−0,14; +0,05]). Bắt đầu từ `ckpt-stable` bị loại (kém ở mọi domain, −3,3 điểm `vn_facts`). Mức quên đi theo mix replay, không theo LR.

## Hệ quả

**Tích cực:**
- Chất lượng tốt nhất trên mọi domain ở cỡ S, mà FLOPs kích hoạt không hơn dense.
- Ngữ cảnh dài ổn định quá độ dài đã train. Trạng thái khi sinh nhỏ hơn khoảng 4 lần so với dense.
- MTP cho sẵn bản nháp để giải mã tự suy đoán (chấp nhận khoảng 70%).
- Đi theo hướng một model chung rồi thích nghi theo domain (có replay), thay vì nhiều model chuyên biệt.

**Tiêu cực và rủi ro:**
- **Nhiễu theo seed cao** (σ khoảng 0,4% so với 0,03% ở dense). Ở cỡ M, chỉ so được các thay đổi có hiệu ứng lớn trên khoảng 1,2%; so tinh hơn cần 2 seed mỗi bên, tức gấp đôi chi phí.
- **Phụ thuộc vào fla và Triton:** fla khoá đúng phiên bản 0.6.0. Trên Hopper cần Triton ≥ 3.7.1, nên torch bị ghim ở nhánh 2.12.
- **μP mới được kiểm trên dense** ở cỡ S. Đã kiểm ở cỡ M (24/11): LR μP dự đoán thắng cả hai mức bên cạnh.
- **MoE tốn bộ nhớ hơn dense** (tổng tham số gấp khoảng 3 lần số kích hoạt). Trên GPU 12 GB, cỡ S dùng `micro_batch` 8, cỡ M chỉ dùng được 2.
- **Ở cỡ S, eval kiến thức không phân biệt được các công thức.** Ở cỡ M, combo hơn dense +13,8 điểm `vn_facts` (p < 0,001).
- **Thích nghi theo domain làm combo quên nhiều hơn dense** (`bpb/all` +1,8% so với 0,0%). Mức quên đi theo thành phần replay; chưa có công thức replay giữ đều mọi domain.
- **Truy xuất xa sau khi kéo dài ngữ cảnh còn yếu** (needle ở đầu và giữa văn bản 4–8k gần như mất). Chưa rõ cần thêm token ngữ cảnh dài hay đổi tỷ lệ attention.

## Tham chiếu

- Nhật ký thí nghiệm phase 15–19 (kế hoạch và Deviation Log): [docs/lab/journal/](../lab/journal/).
- Code: `lab/model.py`, `lab/layers/gdn.py`, `lab/layers/moe.py`, `lab/layers/mtp.py`, `lab/optim_muon.py`, `lab/train.py`, `lab/compare_bpb.py`.
- Config tham chiếu: cỡ S `lab/configs/combo/combo-s-recipe.yaml`; cỡ M `lab/configs/combo/combo-m.yaml`, `combo-m-ctx8k.yaml`; thích nghi luật `lab/configs/law/combo-m-law-l1-replay40-reflit.yaml`.
