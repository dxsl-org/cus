"""
Label documents with a 0-5 knowledge/information value score using a local instruct model
(phase 6 step 7; decision 2026-11-15: Qwen2.5-7B-Instruct, 4-bit, on the local GPU).

    uv run --group eval python -m pipeline.quality.label_llm [--limit N] [--batch-size 8]

Reads DATA_ROOT/quality/label_sample.jsonl and appends {doc_id, score, reason, model} to
DATA_ROOT/quality/labels-<model>.jsonl; doc_ids already there are skipped, so the job can be
stopped and resumed. Greedy decoding; a reply without a parseable "Điểm: N" is stored with
score null and counted, never guessed.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

from pipeline.paths import DATA_ROOT

MODEL = "Qwen/Qwen2.5-7B-Instruct"
DOC_CHARS = 2000
MAX_NEW_TOKENS = 96
# Qwen sometimes drifts into Chinese mid-reply on Vietnamese input; the system turn pins the language.
SYSTEM = "Bạn là chuyên gia đánh giá văn bản tiếng Việt. Luôn trả lời hoàn toàn bằng tiếng Việt."
RUBRIC = """Bạn là người đánh giá dữ liệu huấn luyện cho mô hình ngôn ngữ tiếng Việt. Hãy chấm đoạn văn bản dưới đây theo GIÁ TRỊ KIẾN THỨC VÀ THÔNG TIN, thang 0–5, cộng dồn từng tiêu chí:

- +1 nếu văn bản có thông tin thật, dù ngắn hoặc lẫn quảng cáo, điều hướng, nội dung thừa.
- +1 nếu văn bản trình bày được một chủ đề cụ thể (sự kiện, khái niệm, hướng dẫn, quy định, ý kiến có lập luận), tương đối mạch lạc.
- +1 nếu thông tin cụ thể, kiểm chứng được (số liệu, nguồn, mốc thời gian, tên riêng) và viết bằng tiếng Việt chuẩn, dễ hiểu.
- +1 nếu văn bản giải thích hoặc phân tích (vì sao, như thế nào), giúp người đọc hiểu sâu hơn, không chỉ liệt kê tin.
- +1 nếu văn bản có giá trị học tập lâu dài như bài giảng, sách, bài viết chuyên môn, văn bản pháp luật đầy đủ, viết cẩn thận.

Cho 0 điểm nếu là spam, quảng cáo thuần, văn bản vô nghĩa, lặp lại, chỉ có danh sách liên kết hoặc nội dung độc hại.
Tối đa 1 điểm nếu là tử vi, phong thủy, bói toán, tin giật gân hoặc câu view, bài PR giới thiệu sản phẩm/dịch vụ, hoặc nội dung chủ yếu kêu gọi mua hàng, đăng ký, xem chương trình.
Tối đa 2 điểm nếu văn bản chỉ là tin vắn liệt kê sự kiện, không có thông tin gì ngoài việc sự kiện đã diễn ra.
Chỉ chấm nội dung, không chấm vì chủ đề bạn thích hay không.

Văn bản:
\"\"\"
{text}
\"\"\"

Trả lời đúng 2 dòng:
Lý do: <tối đa 30 từ>
Điểm: <số nguyên 0-5>"""
_SCORE = re.compile(r"Điểm\s*:\s*([0-5])\b")


def parse_score(reply):
    m = _SCORE.findall(reply)
    return int(m[-1]) if m else None


def _load(model_name):
    tok = AutoTokenizer.from_pretrained(model_name, padding_side="left")
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(model_name, quantization_config=quant, device_map="cuda").eval()
    return tok, model


@torch.no_grad()
def generate(tok, model, user_messages, max_new_tokens=MAX_NEW_TOKENS):
    """Greedy replies, one per user message, under the Vietnamese-pinning system turn."""
    prompts = [tok.apply_chat_template([{"role": "system", "content": SYSTEM}, {"role": "user", "content": m}],
                                       tokenize=False, add_generation_prompt=True) for m in user_messages]
    enc = tok(prompts, return_tensors="pt", padding=True).to("cuda")
    out = model.generate(**enc, do_sample=False, max_new_tokens=max_new_tokens, pad_token_id=tok.pad_token_id,
                         temperature=None, top_p=None, top_k=None)
    return tok.batch_decode(out[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)


def label_batch(tok, model, docs):
    return generate(tok, model, [RUBRIC.format(text=d["text"][:DOC_CHARS]) for d in docs])


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sample", help="input JSONL (default DATA_ROOT/quality/label_sample.jsonl)")
    parser.add_argument("--out", help="output JSONL (default DATA_ROOT/quality/labels-<model>.jsonl)")
    args = parser.parse_args()
    sample = Path(args.sample) if args.sample else DATA_ROOT / "quality" / "label_sample.jsonl"
    out = Path(args.out) if args.out else DATA_ROOT / "quality" / f"labels-{args.model.split('/')[-1]}.jsonl"
    done = set()
    if out.exists():
        done = {json.loads(x)["doc_id"] for x in out.read_text(encoding="utf-8").splitlines() if x.strip()}
    docs = [d for d in map(json.loads, sample.read_text(encoding="utf-8").splitlines()) if d["doc_id"] not in done]
    docs = docs[: args.limit or None]
    docs.sort(key=lambda d: len(d["text"]))  # similar lengths per batch: less padding
    tok, model = _load(args.model)
    unparsed = 0
    with open(out, "a", encoding="utf-8", newline="\n") as fh:
        for i in range(0, len(docs), args.batch_size):
            batch = docs[i:i + args.batch_size]
            for d, reply in zip(batch, label_batch(tok, model, batch)):
                score = parse_score(reply)
                unparsed += score is None
                fh.write(json.dumps({"doc_id": d["doc_id"], "source_id": d["source_id"], "score": score,
                                     "reason": reply.strip()[:400], "model": args.model}, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"{i + len(batch)}/{len(docs)} unparsed={unparsed}", flush=True)


if __name__ == "__main__":
    main()
