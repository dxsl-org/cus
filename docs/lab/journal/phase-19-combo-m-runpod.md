---
phase: 19
title: "combo-M trên RunPod: train chính, kéo dài ngữ cảnh, thích nghi luật"
status: done
---

# Phase 19: combo-M trên RunPod

## Context Links
- [ADR 0001](../../decisions/0001-combo-m-architecture-and-training-recipe.md), [báo cáo lab cỡ S](../lab-s-size-results-phase-15-18.md)
- [Phase 17](./phase-17-lab-hybrid-gdn-mtp.md) (bước 4: chạy M, kéo dài context, thích nghi luật), [Phase 18](./phase-18-lab-optimizer-schedule-mup.md)
- Config: `lab/configs/combo/combo-m.yaml`

## Overview
Chạy kiến trúc combo với công thức phase 18 ở cỡ M, cùng FLOPs với dense-M, trên 2B token như dense-M. Sau đó kéo dài ngữ cảnh lên 8192 và thích nghi luật có replay. Chạy trên GPU thuê của RunPod, có chặn ngân sách.

## Key Insights
- **Cấu hình:** d768, 12 lớp (gggagggaggga), 12 head (4 KV), 32 expert × 416 (top-4 + 1 expert chung), MTP. Tổng 446,4M tham số, 124,3M kích hoạt, 835 MFLOP/token ở seq 2048; dense-M là 831.
- **Benchmark Modal H100** (`micro_batch` 32, 22/11): 181k token/s, MFU 0,15. 2B token mất khoảng 3,1 giờ GPU. MFU thấp do MoE dispatch và kernel GDN (combo-S: 0,125), không phải do batch.
- **Nhiễu seed của MoE khoảng 0,4%:** một run kiểm tra LR chỉ phát hiện được sai lệch lớn (cỡ ≥ 2× LR), không tinh chỉnh được.
- **WSD:** `ckpt-stable.pt` (lưu ở bước giảm LR đầu tiên) cho phép kéo dài lên 4,7B token sau này mà không phải train lại từ đầu.

## Requirements
- Run chính: 2B token, seq 2048, `batch_tokens` 262144, Muon 0,01, WSD 20% kèm annealing, μP base 512.
- Kéo dài ngữ cảnh: 0,2B token ở seq 8192, `init_from` combo-m.
- Thích nghi luật (L1): 0,3B token, 76% luật + 24% replay, `init_from` combo-m.
- Chấm local: bpb, `evalset-v2` cloze, `eval_long_context`; so với dense-M (0,7268) và L1 của dense-M.
- Ngân sách cứng: launcher tự dừng khi đạt mức trần anh đặt.

## Architecture
RunPod pod (H100 SXM 80GB) → image torch 2.12.1 + cu130 → volume chứa shard (5,8 GB) → `lab.train` → checkpoint mỗi 15 phút → tải về `E:\llm-data\lab\runs\`. Launcher theo khuôn `lab/modal_train.py`: sổ ghi chi phí, timeout theo ngân sách còn lại, tự xoá pod khi xong.

## Related Code Files
- Create: `lab/runpod_train.py` (launcher), `lab/configs/combo/combo-m-ctx8k.yaml`, `lab/configs/law/combo-m-law-l1.yaml`, các config kiểm tra LR (nếu chạy).
- Modify: `lab/train.py` (đã thêm `ckpt-stable.pt`), `docs/ingest-runbook.md`.

## Implementation Steps
1. Anh tạo tài khoản RunPod, nạp credit và tạo API key.
2. Viết `lab/runpod_train.py`, chạy thử 30 bước (khoảng $0,2).
3. (Tuỳ chọn) kiểm tra LR.
4. Run chính, rồi kéo dài ngữ cảnh và thích nghi luật.
5. Chấm local và cập nhật báo cáo lab cùng ADR.

## Todo List
- [x] Launcher RunPod (bench 30 bước bị bỏ qua: chạy thẳng run chính trên Secure H100 PCIe)
- [x] Kiểm tra LR (local 4070): giữ `muon_lr` 0,01
- [x] combo-M 2B
- [x] Kéo dài ngữ cảnh 8192
- [x] Thích nghi luật L1 (local 4070)
- [x] Chấm bpb, `evalset-v2` cloze, `eval_long_context`
- [x] Viết báo cáo lab cỡ M (`docs/lab/lab-m-size-results-phase-19.md`) và cập nhật ADR 0001

## Success Criteria
- bpb/all của combo-M thấp hơn dense-M (0,7268) rõ rệt. Theo kết quả cỡ S, kỳ vọng khoảng −8 đến −10%.
- `vn_facts` hơn dense-M (0,334), với p < 0,05 khi so cặp.
- Ngữ cảnh 8192 ổn định: bpb ở đoạn 4–8k không tệ hơn đoạn 2–4k quá 5%.
- Chi phí thực tế nằm trong dự toán cộng 30% dự phòng.

## Risk Assessment
- **LR chuyển sai cho MoE và GDN ở d768:** giảm thiểu bằng run kiểm tra hoặc bằng theo dõi đường loss sớm.
- **Pod Community bị gián đoạn:** checkpoint mỗi 15 phút, launcher resume được.
- **Lỗi kernel fla trên H100 ở run dài:** benchmark 30 bước đã khớp loss với A100; theo dõi các đỉnh grad_norm.
- **MFU thấp:** đây là chi phí đã biết, đã tính vào dự toán.

## Security Considerations
- API key RunPod đặt trong biến môi trường hoặc keyring, không commit.
- Dữ liệu train là dữ liệu nội bộ; nên dùng volume riêng và xoá pod sau khi chạy xong.

## Next Steps
- Nếu đạt tiêu chí: lên phase 13 (thang 0,1B → 1,5B) với tokenizer chính thức của phase 10.

## Deviation Log
- **2026-11-22 Decision (anh chọn):** kiểm tra LR thô 3 × 0,3B token (`muon_lr` 0,005 / 0,01 / 0,02) thay cho 2 × 0,8B (ADR 0001 đã sửa theo); dùng RunPod Community H100 SXM ($2,69/giờ); chạy đủ run chính 2B, kéo dài ngữ cảnh 8192 (0,2B) và thích nghi luật L1 (0,3B). Trần ngân sách $25; dự toán $16,8, cộng 30% dự phòng là khoảng $21,8.
- **2026-11-22 Decision:** `lab.train` lưu `ckpt-stable.pt` ở bước giảm LR đầu tiên của lịch WSD (đã kiểm bằng run 10 bước: lưu đúng ở bước 8 khi giảm từ bước 8), để sau này kéo dài pha ổn định của combo-M lên 4,7B token mà không phải train lại. Kéo dài ngữ cảnh và thích nghi luật dùng optimizer mới, đỉnh LR bằng 0,3 lần run chính (Muon 0,003, AdamW 9e-4).
- **2026-11-22 Launcher:** `lab/runpod_train.py` + `lab/runpod_api.py` (REST API v2; RunPod đã bỏ REST v1). Image `runpod/base:2.1.0-ubuntu2404` + venv uv trên `/workspace` (torch 2.12.1 cu130, host `minCudaVersion` 13.0). Shard thực tế 9 GB (không phải 5,8). Một pod cho cả hàng đợi; chặn ngân sách bằng watchdog trên pod (stop theo số dư − $1, tối đa `--hours`; rút còn 45 phút khi hàng đợi kết thúc), thay cho sổ ghi kiểu Modal vì số dư RunPod đọc được trực tiếp. Đã kiểm transport tar/ssh, hàng đợi và mốc dừng bằng ssh giả lập; chưa chạy trên pod thật.
- **2026-11-22 Surprise + Decision (anh chọn):** Community H100 SXM hết hàng nhiều giờ (cả NVL và PCIe cũng hết). `up` giờ thử lần lượt H100 SXM ($2,69) → NVL ($2,59) → PCIe ($1,99), gọi thẳng lệnh tạo pod thay vì tin catalog (catalog chậm hơn thực tế), và thoát với mã 3 khi cả ba bị từ chối vì hết chỗ. Job dò chạy `up` mỗi 20 giây, tối đa 6 giờ. MFU tính theo đỉnh bf16 của đúng loại GPU. Hàng đợi tự tạo pod khi có máy (deploy-when-available) của RunPod chỉ có ở Secure, nên không dùng được cho Community.
- **2026-11-22 Decision (anh chọn):** 3 run kiểm tra LR chạy local trên 4070 (tuần tự 0,01 → 0,005 → 0,02), không chạy trên RunPod. `micro_batch` 2: 8 và 4 tràn sang bộ nhớ chia sẻ (1,3k và 2,7k token/s), 2 đạt khoảng 16,5k token/s, tức khoảng 5 giờ mỗi run. Chỉ dò RunPod sau khi cả 3 run xong và đã chọn `muon_lr` cho run chính, để không đốt tiền run chính 2B với LR chưa kiểm. Có máy thì chạy hàng đợi combo-M 2B (`micro_batch` 32) → ctx8k (`micro_batch` 8) → luật L1 (32), tải về từng run.
- **2026-11-23 Kết quả kiểm tra LR (0,3B token, 4070):** `bpb/all` cuối run: 0,005 → 0,7520, **0,01 → 0,7442**, 0,02 → 0,7463. 0,005 kém hơn 1,05% (gấp khoảng 2,5 lần nhiễu seed) và kém ở mọi lần chấm. 0,02 dẫn trước tới bước 500 nhưng về đích kém hơn 0,28% (trong mức nhiễu). Không sai lệch gấp 2 lần; giữ `muon_lr` 0,01 theo μP (run dài còn ưa LR thấp hơn). Không có đỉnh `grad_norm` sau warmup. Tốc độ khoảng 18,7k token/s, mỗi run khoảng 4h20–4h40. Dò RunPod lại từ 10:24 tới 22:30.
- **2026-11-23 Surprise + Decision (anh chọn):** dò Community 10:24–22:30 (1.703 lần), không lần nào có H100. Chuyển sang Secure H100 PCIe ($2,89/giờ; ước khoảng 5 giờ, khoảng $15 cho cả hàng đợi). `up` thêm `--cloud` (mặc định COMMUNITY) và `--gpu` (thử đúng một loại GPU).
- **2026-11-24 Surprise + Decision (anh chọn):** upload 9,55 GB qua ssh bị đứt ở 5,57 GB ("Connection reset"); `up` lần sau chỉ gửi phần thiếu, mất khoảng $3 tiền GPU chờ. Pod Secure H100 PCIe chạy combo-M ở 112k token/s (MFU 0,124), tức khoảng 62% tốc độ SXM; bước đầu mất khoảng 6 phút để Triton biên dịch. Run 2B khoảng 5 giờ, nên ngân sách còn lại chỉ đủ cho ctx8k. Luật L1 chuyển sang chạy local trên 4070 sau khi tải `combo-m` về.
- **2026-11-24 Kết quả combo-M 2B (bước 7629, xong 05:43):** `bpb/all` 0,6501, so với dense-M 0,7268 là −10,6% (kỳ vọng −8 đến −10%). Theo domain: reference −14,8%, education −14,7%, health −12,7%, law −12,1%, literature −11,7%, culture_history −11,6%, news −10,6%, web −5,9%, forum −3,3%. Đã tải `ckpt.pt` + `ckpt-stable.pt` về local. ctx8k chạy trên pod ở 110k token/s (dự kiến khoảng 06:25 xong); luật L1 chạy local từ 05:57 ở 18k token/s, `accum` 64.
- **2026-11-24 Kết quả ctx8k + `eval_long_context` (chạy trên pod, 06:36):** ctx8k xong lúc 06:23, `grad_norm` lớn nhất 0,84. Tiêu chí ngữ cảnh 8192 đạt: bpb đoạn 4–8k là 0,6719, đoạn 2–4k là 0,6743 (−0,4%); combo-M gốc (train ở 2048) là 0,6900 so với 0,6788 (+1,7%). Needle ở 2048 đạt 100% ở cả 3 độ sâu (gốc: 40/100/100). Ở 4096 là 10/75/100 và ở 8192 là 0/0/100: chỉ nhớ được needle ở gần cuối; mỗi ô chỉ 20 lần thử nên nhiễu cao. MTP chấp nhận 74,8%. Trạng thái giải mã 25,8 MB ở 8192. Pod đã xoá sau khi tải cả hai run; số dư còn $4,33, tức tổng chi phí khoảng $20,7 (trong trần $25).
- **2026-11-24 Kết quả luật L1 (4070, 05:57–10:49, `grad_norm` lớn nhất 0,56):** `bpb/law` 0,4956 → **0,4620** (−6,8%; dense-M L1: −7,2%, xuống 0,5236). `bpb/all` 0,6501 → 0,6621 (+1,8%), trong khi dense-M L1 giữ nguyên (0,7268 → 0,7265). Quên nhiều nhất ở reference (+6,5%) và literature (+5,2%). So với dense-M L1: law −11,8%, all −8,9%. combo quên nhiều hơn dense dù cùng 24% replay. Có thể do đỉnh LR Muon 0,003 còn cao với MoE + GDN; chưa kiểm.
- **2026-11-24 Kết quả `evalset-v2` cloze (so cặp, 10.000 lần bootstrap):** `vn_facts` combo-M **0,473** so với dense-M 0,334, chênh +13,8 điểm, CI95 [+11,1, +16,6], p < 0,001. Riêng câu luật: 0,566 so với 0,374. Sau thích nghi luật: combo L1 0,473 so với dense L1 0,359, chênh +11,4 điểm, p < 0,001. So với combo-M, L1 tăng `vn_facts:law` lên 0,610 (`p_correct` +5,6 điểm, p < 0,001), còn `vn_facts` chung giữ nguyên. ctx8k làm `p_correct` của `vn_facts` giảm 1,1 điểm (p = 0,012), `acc` giảm không ý nghĩa. `vi_mmlu` vẫn ở mức đoán bừa (0,26) với mọi model.
- **2026-11-24 Decision (anh chọn):** chạy `combo-m-law-l1-stable`: giống hệt L1, chỉ khác là khởi đầu từ `ckpt-stable.pt` của combo-M (bước 6103, trước khi giảm LR) thay cho `ckpt.pt` đã anneal. Mục đích: tách phần quên do mất lợi ích anneal khỏi phần do kéo LR lên lại, và thử cách thích nghi domain kiểu WSD. Thêm `train.init_ckpt` (mặc định `ckpt.pt`); `lab.train`, `runpod_train` và `modal_train` dùng nó để nạp và tải lên đúng checkpoint. Điểm xuất phát kém hơn nhiều: combo-M ở bước 6000 có `bpb/all` 0,7305, `bpb/law` 0,5726 (pha giảm LR + anneal kéo xuống 0,6501 / 0,4956). Bắt đầu 12:44, 19,6k token/s.
- **2026-11-24 Kết quả L1-stable (12:44–17:13, `grad_norm` lớn nhất 0,46):** kém L1 ở mọi domain: `bpb/law` 0,4686 (L1: 0,4620, +1,4%), `bpb/all` 0,6767 (L1: 0,6621, +2,2%); reference +5,1%, education +3,5%, literature +3,3%, forum và web ngang nhau. Bắt đầu từ checkpoint đã anneal tốt hơn: L1 giữ lại được một phần lợi ích anneal mà L1-stable không bao giờ có. Cách thích nghi kiểu WSD (từ `ckpt-stable`) bị loại; giữ L1 từ checkpoint cuối. Phần quên của L1 (reference, literature) đi theo mix: replay lấy theo mix pretrain, mà mix này chỉ có 0,5% mỗi domain đó. Hướng sửa khả dĩ (chưa thử): replay theo mix anneal.
- **2026-11-24 Cloze L1-stable so với L1 (so cặp):** `vn_facts` 0,440 so với 0,473: −3,3 điểm, CI95 [−5,5, −1,2]; `p_correct` −4,0 điểm, p < 0,001. `vn_facts:law` ngang nhau (0,610). Xác nhận loại cách thích nghi từ `ckpt-stable`: kiến thức chung mất phần mà pha anneal đã đem lại.
- **2026-11-24 Decision (anh chọn):** chạy `combo-m-law-l1-anneal-replay`: giống hệt L1, chỉ đổi 24% replay sang lấy theo mix anneal của combo-M (bỏ phần law, chia lại cho đủ 0,24): reference 6,67%, literature 5,33%, news 4%, fineweb2 2,67%, culturax, education, health, culture_history mỗi loại khoảng 1,33%. Forum không có trong mix anneal nên không được replay. Chạy trên 4070, sau đó chấm cloze và so cặp với L1 và combo-M.
- **2026-11-24 Kết quả L1 anneal-replay (17:34–21:54, `grad_norm` lớn nhất 0,25):** `bpb/law` 0,4625 (L1: 0,4620, ngang nhau). `bpb/all` 0,6602 (L1: 0,6621, −0,3%). Mức quên so với combo-M giảm khoảng một nửa: reference +3,2% (L1: +6,5%), literature +2,8% (+5,2%), education +2,0% (+3,8%), culture_history +1,2% (+2,4%). Bù lại forum +3,4% (L1: −0,7%) và web +2,6% (+0,7%), vì replay mới bỏ forum và giảm fineweb2 từ 15% xuống 2,67%. Quên đi theo mix replay, không phải do LR.
- **2026-11-24 Cloze L1 anneal-replay (so cặp):** so với L1, `vn_facts` `acc` ngang nhau (0,473 so với 0,473); `p_correct` −0,9 điểm, CI95 [−1,9, +0,1], sát ngưỡng ý nghĩa (một phía khoảng 0,04 theo chiều kém hơn). `vn_facts:law` ngang nhau (0,610). So với combo-M, `vn_facts:law` `p_correct` +5,9 điểm (p < 0,001), như L1. `vi_mmlu` `p_correct` +0,4 điểm (p = 0,003) nhưng `acc` vẫn ở mức đoán bừa. Kết luận: đổi mix replay chủ yếu dời chỗ quên giữa các domain, không làm kiến thức đo được tốt lên. Giữ L1 gốc làm công thức tham chiếu; nếu cần giữ đều mọi domain thì thử replay trộn một nửa mix pretrain, một nửa mix anneal (chưa chạy).
- **2026-11-25 Decision:** chạy `combo-m-law-l1-replay40` trên 4070 từ 22:00: giống L1 (từ checkpoint cuối, replay theo mix pretrain), chỉ nâng replay từ 24% lên 40% và giữ nguyên 228M token luật, nên tổng tăng lên 0,38B token (1449 bước, so với 1144).
- **2026-11-26 Kết quả L1 replay40 (22:00–04:07):** log cuối `bpb/law` 0,4634 (L1: 0,4620), `bpb/all` 0,6557 (L1: 0,6621; combo-M: 0,6501). So cặp với L1 (256 đoạn mỗi domain): `bpb/law` −0,03%, CI95 [−0,20; +0,14], ngang nhau; mọi domain khác tốt hơn 0,8–1,7% (p < 0,001); `bpb/all` −1,05% [−1,08; −1,01]. So với combo-M vẫn quên: reference +6,0%, literature +4,2%, education +3,1%, health +2,1%, culture_history +1,5%; `bpb/all` +0,92%. Mức quên chỉ giảm khoảng 1 điểm % mỗi domain so với L1. Cloze so với L1: `vn_facts` 0,481 so với 0,473, `vn_facts:law` 0,632 so với 0,610, `vi_mmlu` ngang; không chênh nào có ý nghĩa. Kết luận: replay40 hơn hoặc bằng L1 ở mọi mặt, đổi lấy thêm 27% compute; chọn replay40 làm công thức thích nghi tham chiếu. Tăng replay lên chỉ giảm quên chậm, nên muốn giữ reference và literature thì phải đưa chúng vào phần replay (chưa chạy).
- **2026-11-26 Decision (anh chọn):** chạy `combo-m-law-l1-replay40-reflit` trên 4070 từ 21:28: giống replay40 (60% luật, 1449 bước), chỉ chia lại 40% replay: reference 8%, literature 6% (replay40: mỗi loại 0,83%; mức anneal-replay từng giảm nửa quên), fineweb2 14%, culturax 5%, news 4%, forum 1,5%, health, culture_history, education mỗi loại 0,5%. Rủi ro: phần web giảm từ 33% xuống 19% nên web có thể quên thêm. So cặp với replay40 và combo-M.
- **2026-11-27 Kết quả L1 replay40-reflit (21:28–03:49):** log cuối `bpb/law` 0,4635, `bpb/all` 0,6495 (combo-M: 0,6501). So cặp với replay40: reference −3,77%, literature −2,32%, forum −0,85%, education −0,58%, culture_history −0,63%; web +0,84%, law +0,10% (CI95 [+0,02; +0,17]); `bpb/all` −0,95% (p < 0,001). So với combo-M: law −6,97%, `bpb/all` −0,04% (CI95 [−0,14; +0,05], không quên tổng thể); còn quên education +2,5%, health +2,2%, reference +2,0%, literature +1,8%, culture_history +0,9%; forum −3,5%, web −0,1%. Cloze ngang replay40 (`vn_facts` 0,482, `vn_facts:law` 0,637, không chênh nào có ý nghĩa). Rủi ro web chỉ xảy ra nhẹ (+0,84% so với replay40, vẫn không quên so với combo-M). Kết luận: chọn reflit làm công thức thích nghi tham chiếu; ADR 0001 và báo cáo lab đã cập nhật.
