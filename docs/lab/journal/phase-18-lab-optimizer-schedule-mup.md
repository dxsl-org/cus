---
phase: 18
title: "Lab nhỏ: Muon, lịch WSD + annealing, μP (cỡ S)"
status: in-progress
created: 2026-11-20
---

# Phase 18: Muon, WSD + annealing, μP ở cỡ S

> **Required — deviation-log:** Log every Decision / Deviation / Surprise in § Deviation Log the moment it occurs.

## Context Links
- plan.md · [phase 17](./phase-17-lab-hybrid-gdn-mtp.md) (combo-S, eval v2) · [phase 15](./phase-15-lab-dense-baseline.md) (bài học quét LR)

## Overview
Anh chọn ba hướng tối ưu quá trình train, thử ở cỡ S trước khi lên combo-M. QK-norm để dành cho model lớn hơn; dữ liệu và distillation teacher → student nghiên cứu sau.
1. **Muon**: optimizer cho các ma trận ẩn (Newton–Schulz trực giao hoá momentum); AdamW cho embedding, head, norm, router.
2. **Lịch WSD** (warmup–stable–decay, giảm dạng 1−√) **+ annealing**: trong pha giảm LR, đổi mix sang dữ liệu chất lượng cao.
3. **μP**: tham số hoá để LR tốt nhất ở chiều rộng nhỏ dùng được cho chiều rộng lớn, không phải quét lại ở cỡ M.

## Key Insights
- Muon với hệ số cập nhật √max(1, fan_out/fan_in) chính là μP theo chuẩn phổ: LR không phụ thuộc chiều rộng cho các ma trận ẩn. Phần còn lại của μP chỉ là nhân logits với d_base/d và chia LR AdamW của các ma trận ra-cố-định (router, wa/wb) cho m.
- head_dim = 64 ở cả S lẫn M, nên không cần đổi thang chia attention (1/√d_head) theo μP.
- `torch.optim.Muon` (2.11) chỉ nhận tham số 2D; expert MoE lưu dạng 3D (E, in, out), nên lab tự viết Muon chạy theo lô.
- Bài học phase 15: quét LR phải chạy ≥ 40% tổng token, nếu không thứ hạng bị đảo.

## Requirements
- Mặc định giữ nguyên hành vi cũ (AdamW, cosine, không μP): các run cũ resume được, chuỗi seed 1 so được với seed 0.
- Mọi thứ bật bằng config: `train.optimizer`, `train.schedule`, `train.decay_fraction`, `train.anneal_mix`, `model.mup_base_width`, `train.muon_lr`.

## Architecture
- `lab/optim_muon.py`: Muon theo lô (…, m, n), momentum Nesterov, weight decay tách rời, hệ số theo hướng in/out của từng tham số.
- `lab/train.py`: `make_optimizer` chia nhóm (Muon / AdamW theo μP); `lr_at` thêm WSD; sampler annealing cho các bước trong pha giảm.
- `lab/model.py` / `lab/config.py`: `mup_base_width` → nhân logits với d_base/d.

## Related Code Files
- Create: `lab/optim_muon.py`, `tests/test_lab_optim.py`, config trong `lab/configs/opt/`
- Modify: `lab/train.py`, `lab/config.py`, `lab/model.py`

## Implementation Steps
1. Code + test CPU (mặc định không đổi; Muon trực giao hoá đúng với 2D/3D; WSD; chia nhóm tham số đủ và không trùng).
2. E1 dense-S + WSD (AdamW, LR 3e-3) so với cosine 0,8245.
3. E2 dense-S + WSD + annealing.
4. E3 dense-S + Muon (+ WSD): quét LR Muon 3 mức, mỗi mức 0,5B token.
5. E4 μP: kiểm tra chuyển LR theo chiều rộng (d 256/512/768, độ sâu 8, 4 mức LR).
6. E5 combo-S với cấu hình thắng, so với combo-S seed 0/1; chấm `evalset-v2` (cloze).

## Todo List
- [ ] Code + test
- [ ] E1 WSD
- [ ] E2 annealing
- [ ] E3 Muon (quét LR)
- [ ] E4 μP chuyển LR theo chiều rộng
- [ ] E5 combo-S cấu hình thắng + eval v2

## Success Criteria
- Mỗi thay đổi có số đo bpb (và `vn_facts`) so với mốc cùng số token; chênh lệch được so với nhiễu seed (combo-S/A3 seed 0 vs 1).
- μP: LR tốt nhất giống nhau (sai khác ≤ một mức quét) ở 3 chiều rộng.

## Risk Assessment
- Muon có thể không ổn định với embedding/head hoặc MoE: chỉ áp cho ma trận ẩn; router giữ AdamW.
- Annealing đổi mix nên bpb/all held-out (trung bình theo domain) có thể không phản ánh chất lượng chung: đánh giá thêm bằng `evalset-v2`.
- Thời gian GPU local: khoảng 12–15 giờ cho E1–E5; xếp sau chuỗi seed 1.

## Security Considerations
Không có dữ liệu nhạy cảm mới; dùng shard đã decontam với v1 + v2.

## Next Steps
Cấu hình thắng → combo-M (phase sau), QK-norm ở cỡ M.

## Deviation Log
- **2026-11-20 Decision (dispatch MoE, làm trước phase 18 theo yêu cầu anh):** thay vòng lặp theo expert (`.tolist()` đồng bộ CPU, chạy eager) bằng `torch._grouped_mm`. Ngữ nghĩa giữ nguyên: cùng routing, dropless; số token mỗi expert đếm bằng `scatter_add`, có shape cố định nên compile được, không graph break. Đối chiếu với bản cũ trên GPU: output lệch tương đối 2·10⁻⁵, gradient khoảng 10⁻³ (bf16), kể cả khi 28/32 expert trống. Loss ở bước 15 giống hệt bản cũ (A3 8,3309; combo-S 11,136). Tốc độ trên 4070, `micro_batch` 8: A3 43,5k → 79k token/s (1,8×), combo-S 17,6k → 56k (3,2×). `micro_batch` 16 vẫn tràn VRAM (A3 16,6k token/s), nên giữ 8. Bản `loop` cũ vẫn giữ làm đường chạy CPU và chuẩn đối chiếu (`moe.dispatch: loop`).
- **2026-11-20 Surprise:** file báo cáo của `lab.decontam_scan` ban đầu ghi vào `TOKENS_DIR`, mà `data_loader` đọc mọi `*.json` ở đó như thống kê nguồn, nên mọi run train chết với `KeyError: 'train_tokens'`. Phát hiện khi benchmark, trước khi chạy chuỗi qua đêm. Đã chuyển báo cáo ra `LAB_DATA/decontam-scan.json`; volume Modal không bị dính file này.
- **2026-11-20 Result (E1–E3, dense-S 0,5B token, Modal H100, `micro_batch` 32; mốc cosine + AdamW 0,8245):** E1 WSD 0,8115 (−1,6%, tốt hơn đều ở mọi domain); E2 WSD + annealing 0,8031, nhưng đổi chỗ chứ không tốt đều: reference −5,0%, education −3,1%, literature −2,3%, law −1,3%, còn web +2,2%, forum +3,0% so với E1; bpb/all thấp hơn một phần nhờ cách lấy trung bình theo domain, nên cần `vn_facts` để phán xét. E3 Muon + WSD: LR 0,01 0,8032 (tốt hơn E1 ở mọi domain, −0,8 đến −1,2%), LR 0,02 0,8056, LR 0,04 0,8139; LR tối ưu ≤ 0,01, nên chạy thêm 0,005. Muon chỉ làm chậm khoảng 1% thông lượng (120k token/s trên 4070). Chi phí 5 run trên H100 khoảng $4,9, gồm cả upload lại 5,8 GB shard. Nhiễu theo seed chưa biết; mức chênh khoảng 1% đều ở mọi domain nhiều khả năng là thật.
- **2026-11-20 Result (E3 bổ sung):** Muon LR 0,005 cho 0,8091, nên LR tối ưu đã được chặn hai đầu quanh 0,01 (0,005/0,01/0,02/0,04 → 0,8091/0,8032/0,8056/0,8139). **Muon 0,01 + annealing: 0,7911**, tức −4,1% so với mốc cosine + AdamW, và hơn cả mức cộng dồn dự đoán (0,7948). Đánh đổi của annealing vẫn còn: web +2,2%, forum +3,0% so với Muon thuần. Lưới LR của E4 (0,005/0,01/0,02) vì vậy bao quanh đúng LR tối ưu.
- **2026-11-20 Surprise (ngân sách Modal):** lượt chạy thứ hai chỉ được timeout 12,6 phút. Launcher cộng cả số metered của Modal lẫn chi phí các run vừa xong trong 1 giờ gần nhất (đề phòng Modal cập nhật trễ), nên lượt thứ nhất bị tính hai lần và phần ngân sách còn lại chỉ $1,78. Hai run vẫn xong kịp, ở bước 1907. Metered sau đó báo $23,97, nhưng con số này còn trễ. Tạm dừng dùng Modal cho đến khi số metered ổn định; credit thật còn khoảng $4–5.
- **2026-11-20 Decision:** chuỗi `E:\llm-data\lab\logs\phase18-chain.ps1` chạy trên 4070 sau `seed1-chain.ps1`: chấm `evalset-v2` cho 6 run phase 18, rồi E4 (9 run μP).
- **2026-11-21 Result (nhiễu theo seed):** combo-S 0,7804 → seed 1 0,7846 (+0,5%); A3 0,7913 → seed 1 0,7975 (+0,8%). Hai run seed 1 chạy trên 4070 với dispatch `grouped`, còn bản gốc chạy trên Modal A100 hoặc dispatch `loop`, nên chưa tách được nhiễu do seed khỏi sai khác do môi trường. Quy ước: chênh lệch bpb dưới khoảng 0,8% chưa tính là thật. Muon so với AdamW cùng WSD (−1,0%) nằm sát ngưỡng; WSD so với cosine (−1,6%) và Muon + annealing (−4,1%) vượt ngưỡng.
- **2026-11-21 Result (`evalset-v2` cloze ở cỡ S):** `vn_facts` (n = 1077, CI95 ±0,027) dao động 0,278–0,314 giữa mọi biến thể; riêng seed của combo-S đã lệch 0,019 (0,295 → 0,314). `vi_mmlu` 0,257–0,268, sát mức đoán bừa 0,25. Annealing không làm tăng `vn_facts` đo được (WSD 0,293 so với WSD + annealing 0,294; Muon 0,288 so với Muon + annealing 0,281). **Ở cỡ S, eval kiến thức không tách được các biến thể optimizer/lịch; chọn theo bpb, còn eval kiến thức để dành cho cỡ M trở lên.**
- **2026-11-21 Deviation:** E4 d768 với `micro_batch` 16 tràn VRAM 12 GB (10–16k token/s, 3,5–5,5 giờ mỗi run). d768-muon0.005 đã xong ở `micro_batch` 16; hai run còn lại hạ xuống 8 (53k token/s; `batch_tokens` giữ nguyên, nên kết quả vẫn so sánh được). Run d768-muon0.01 dang dở ở bước 30 đã xoá để chạy lại từ đầu.
- **2026-11-21 Result (E4 μP, Muon + WSD, 0,2B token, độ sâu 8, head_dim 64; bpb ở LR 0,005/0,01/0,02/0,04):** d256 0,9603/0,9375/0,9326/0,9378; d512 0,8871/0,8692/0,8660/0,8757; d768 0,8537/0,8398/0,8405/0,8526. Khớp parabol theo log₂(LR) cho LR tối ưu 0,020 / 0,017 / 0,014, tức tối ưu trôi khoảng ∝ d^−0,31 thay vì đứng yên. μP chuyển LR **gần đúng chứ không tuyệt đối**. Đường cong rất phẳng quanh đáy: dùng chung 0,014 cho cả ba chiều rộng chỉ thiệt ≤0,2% bpb, dưới mức nhiễu. Ngoại suy cho d1024 khoảng 0,013, d2048 khoảng 0,010. Run dài hơn ưa LR thấp hơn (d512 ở 0,5B token: tối ưu khoảng 0,01). Khuyến nghị cho combo-M: muon_lr khoảng 0,01, kiểm bằng 2 run ngắn ở cỡ M (0,007 và 0,014).
- **2026-11-21 Decision (anh yêu cầu):** bpb chỉ đáng tin khi biết mức nhiễu. Đã thêm `lab.compare_bpb`: so cặp hai run trên cùng các đoạn held-out cố định (mặc định 256 đoạn mỗi domain), bootstrap theo từng đoạn và phân tầng theo domain, báo B/A − 1 (%), CI95 và p một phía. Loss từng đoạn được lưu cache trong `runs/<run>/bpb-windows-<n>.npz`. `DomainEval.per_window` là nguồn tính duy nhất, `run()` chỉ cộng lại. Test: `tests/test_lab_compare_bpb.py`. Lần thử đầu bắt được lỗi bootstrap cộng hai lần (CI rộng bằng 0), đã sửa. Thử trên CPU với 8 đoạn mỗi domain: WSD so với Muon 0,01 cho −0,96% [−1,12; −0,81]; Muon 0,01 so với 0,02 cho +0,34% [+0,17; +0,52]. **Sai số do chọn văn bản đo chỉ khoảng ±0,15% dù mới 8 đoạn; nguồn bất định chính là nhiễu do huấn luyện.**
- **2026-11-21 Decision:** E5 `combo-s-recipe` (combo-S + Muon 0,01 + WSD + annealing) chạy trên 4070. Sau đó `phase18-noise-chain.ps1` chạy công thức dense-S thắng với seed 0/1/2 trên 4070, rồi so từng cặp: giữa các seed, local so với Modal (ảnh hưởng của môi trường), các cặp quyết định cũ, combo seed và E5.
- **2026-11-21 Result (E5 `combo-s-recipe`, 4070, 0,5B token):** bpb **0,7473**, tức −4,2% so với combo-S (0,7804) và −4,8% so với combo-S seed 1 (0,7846), gấp khoảng 5 lần mức chênh giữa hai seed. So với mốc dense-S cosine + AdamW (0,8245): −9,4%, khớp với mức cộng dồn dự đoán (combo −5,3%, công thức −4,1%). Đánh đổi web/forum của annealing gần như biến mất ở combo: web −0,4%, forum +0,2% so với combo-S, trong khi reference −7,9%, education −6,7%, literature −5,6%, law −4,9%. `evalset-v2`: `vn_facts` 0,332, cao nhất trong các model S chưa thích nghi. So cặp: +3,6 điểm so với combo-S (p = 0,003), +1,8 điểm so với combo-S seed 1 (p = 0,08, chưa chắc). `vi_mmlu` và `vn_knowledge` không đổi; `language` +4,3 điểm so với seed 0, +0,6 so với seed 1. **Công thức phase 18 (Muon 0,01 + WSD + annealing) được chốt cho combo-M.**
- **2026-11-21 Result (nhiễu thật, `lab.compare_bpb` 256 đoạn mỗi domain):** công thức dense-S thắng chạy 3 seed trên 4070: bpb trong log 0,7903 / 0,7897 / 0,7900; trên 256 đoạn 0,8070 / 0,8067 / 0,8072. **σ theo seed ≈ 0,03% (2σ ≈ 0,06%), nhỏ hơn khoảng 20 lần so với ước đoán 0,5–0,8% trước đó.** Môi trường (Modal H100 `micro_batch` 32 so với 4070 `micro_batch` 16): −0,18% [−0,22; −0,14], lớn hơn nhiễu seed nhưng vẫn nhỏ. Các quyết định dense được xác nhận: Muon so với WSD AdamW −0,97% [−1,00; −0,94]; Muon 0,01 so với 0,02 +0,22% [+0,19; +0,24] (khoảng 7σ, LR 0,01 thắng thật). Ngược lại, combo-S seed 0 (Modal A100, dispatch `loop`) so với seed 1 (4070, `grouped`) chênh +0,57% [+0,52; +0,61], khoảng 19σ của dense. Hoặc MoE/GDN nhạy với seed hơn hẳn, hoặc môi trường/dispatch tác động lên combo nhiều hơn dense; cần tách hai nguyên nhân. E5 so với combo-S −4,22% [−4,33; −4,12], vẫn rõ ràng. Quy ước mới: so dense chỉ cần chênh lớn hơn 0,1%; so MoE/combo vẫn dùng ngưỡng khoảng 0,6% cho đến khi tách xong.
- **2026-11-21 Decision:** chạy `combo-s-local` (cấu hình combo-S, seed 0) trên 4070 với dispatch `grouped`, cùng môi trường với combo-S seed 1. Nếu nó khớp combo-S seed 0 trên Modal thì phần chênh là do seed; nếu khớp seed 1 thì là do môi trường.
- **2026-11-21 Result (tách nhiễu combo):** `combo-s-local` (seed 0, 4070, dispatch `grouped`) cho bpb 0,7804, **giống hệt** combo-S trên Modal (A100, `loop`): −0,03% [−0,07; +0,01]. So với combo-S seed 1: −0,59% [−0,64; −0,54]. **Môi trường và cách dispatch không tạo ra chênh lệch; 0,57% là nhiễu seed thật của combo.** Ước lượng σ của combo/MoE khoảng 0,4% (từ 1 cặp, còn thô; cặp A3 0,8% phù hợp), lớn hơn dense khoảng 13 lần; nguyên nhân có thể là routing chọn expert phụ thuộc đường đi ban đầu. Hệ quả: so một run với một run có MoE cần chênh khoảng 1,2% trở lên, hoặc chạy 2 seed mỗi bên. combo-S so với A3 (−1,4%) chỉ vừa qua ngưỡng; A2 so với A3 và H1–H4 là hoà; E5 (−4,2%) chắc chắn; mức tăng `vn_facts` của E5 nằm trong nhiễu. Thông lượng run này chỉ 28–41k token/s vì `dwm.exe` vẫn giữ 6,8 GB VRAM.
- **2026-11-22 Result (E5, ngữ cảnh dài và sức khoẻ MoE):** `eval_long_context combo-s-recipe` không thấy thoái lui so với H4. Needle ở độ sâu 90%: 95% / 90% / 85% tại 2k / 4k / 8k (H4: 100 / 90 / 65); ở độ sâu 10–50% vẫn hỏng ngoài 2k như H4. bpb theo vị trí ổn định đến 8k (0,794 ở 2–4k, 0,816 ở 4–8k). Trạng thái giải mã 16,8 MB ở 8k. MTP chấp nhận 69,7% (H4 69,0%). Tải expert cuối run `moe_load_max` 1,24, gần mức combo-S (1,15–1,21); không có expert chết.
- **2026-11-22 Surprise:** torch 2.12.1 (phát hành 6/2026) ghim `triton==3.7.1` trên Linux, đúng phiên bản fla 0.6.0 yêu cầu cho Hopper (#640). Nâng torch 2.11 → 2.12.1 sẽ mở được H100 cho GDN. Cần kiểm trước khi thuê RunPod.
- **2026-11-22 Decision (anh duyệt):** nâng torch 2.11.0+cu128 → 2.12.1+cu130 (index PyTorch không còn bản cu128 từ 2.12; driver máy hỗ trợ CUDA 13.1). `lab` ghim `torch>=2.12.1,<2.13`; image Modal dùng đúng phiên bản đó; đã bỏ chốt chặn GDN trên H100 trong `modal_train`. Kiểm tra:
  - 34 test lab + eval pass trên 4070.
  - combo-S 30 bước trên 4070 khớp loss với run cũ đến bước 23 (sai khác bf16), sau đó lệch nhẹ ở đúng giai đoạn router tự sắp xếp, nơi cả bản cũ cũng có đỉnh loss.
  - Modal H100 (Triton 3.7.1, kernel fla được chấp nhận): loss ở bước 10/20/30 là 12,43 / 10,01 / 9,72, so với A100 cũ 12,43 / 10,06 / 9,69. **375k token/s, gấp 2,8 lần A100** (133k). Theo giá Modal, chi phí mỗi token trên H100 rẻ hơn A100 khoảng 1,8 lần. Benchmark tốn khoảng $0,35.
  - Ở cỡ M nên thuê H100.
