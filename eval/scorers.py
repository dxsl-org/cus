"""
Scoring for eval harness outputs: model text -> correct?, and per-group accuracy with
Wilson 95% intervals. Grading reuses the TaskItem verifiers so a frozen eval item is
judged exactly as it was checked at review time.
"""
import math
import re
from collections import defaultdict

from taskset import verifiers

_CODE_BLOCK = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.DOTALL)
# Shown after an MCQ in generate mode; non-listed languages get English.
MCQ_INSTRUCTION = {"vi": "Chỉ trả lời bằng một chữ cái ({labels}).",
                   "en": "Answer with a single letter ({labels})."}
# Cue before the answer letter in loglik mode, in the item's language.
ANSWER_CUE = {"vi": "Đáp án", "en": "Answer", "zh": "答案", "ja": "答え", "ko": "정답", "fr": "Réponse",
              "de": "Antwort", "es": "Respuesta", "ru": "Ответ"}
# Numeric items in generate mode: models reason step by step, so ask for a marked final line.
NUMERIC_INSTRUCTION = {"vi": "Trình bày lời giải ngắn gọn. Dòng cuối cùng ghi đúng dạng: Đáp số: <một số>",
                       "en": "Solve briefly. End with a final line exactly of the form: Answer: <one number>"}
_FINAL_ANSWER = re.compile(r"(?:đáp số|đáp án|answer)\s*[:：]\s*(.+)", re.IGNORECASE)


def wilson(correct, n, z=1.96):
    """Wilson score interval for a binomial proportion; (0, 0) when n == 0."""
    if n == 0:
        return 0.0, 0.0
    p = correct / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def labels(item):
    return "ABCDEFGHIJ"[:len(item["choices"])]


def is_numeric(item):
    return item["kind"] != "eval_code" and (item.get("verifier") or {}).get("type") == "numeric"


def generate_prompt(item):
    """User message for generate mode."""
    text = item["messages"][-1]["content"]
    if item["kind"] == "eval_mcq":
        template = MCQ_INSTRUCTION.get(item["lang"], MCQ_INSTRUCTION["en"])
        text += "\n\n" + template.format(labels=", ".join(labels(item)))
    elif is_numeric(item):
        text += "\n\n" + NUMERIC_INSTRUCTION.get(item["lang"], NUMERIC_INSTRUCTION["en"])
    return text


def final_answer(text):
    """Text after the last 'Đáp số:/Answer:' marker; else the whole output (parse_number takes its last number)."""
    found = _FINAL_ANSWER.findall(text)
    return found[-1].strip() if found else text


def loglik_context(item):
    """Plain-text context for loglik mode (no chat template: works for base checkpoints)."""
    return f"{item['messages'][-1]['content']}\n{ANSWER_CUE.get(item['lang'], ANSWER_CUE['en'])}:"


CLOZE_BLANK = "___"
_OPTION_LINE = re.compile(r"^[A-J][.)]\s")


def question_stem(item):
    """The MCQ prompt without its trailing 'A. …' option lines."""
    lines = item["messages"][-1]["content"].split("\n")
    while lines and _OPTION_LINE.match(lines[-1]):
        lines.pop()
    return "\n".join(lines).rstrip()


def cloze_pairs(item):
    """
    (context, continuation) per choice for cloze mode: the choice text itself is scored, so base models that
    cannot map an answer to a letter are still measured. Items with meta.cloze (a sentence with one ___) score
    the rest of that sentence from the blank on; others score " <choice>" after the stem and the answer cue.
    A space before the blank moves into the continuation, where BPE tokenizers expect it.
    """
    sentence = (item.get("meta") or {}).get("cloze")
    if sentence and sentence.count(CLOZE_BLANK) == 1:
        prefix, suffix = sentence.split(CLOZE_BLANK)
        space = " " if prefix.endswith(" ") else ""
        return [(prefix.rstrip(" "), f"{space}{choice}{suffix}") for choice in item["choices"]]
    context = f"{question_stem(item)}\n{ANSWER_CUE.get(item['lang'], ANSWER_CUE['en'])}:"
    return [(context, f" {choice}") for choice in item["choices"]]


def cloze_result(item, scores, n_bytes):
    """
    One cloze outcome. scores: summed log-probabilities of each continuation; n_bytes: their UTF-8 lengths.
    `correct` compares per-byte scores (acc_norm: removes the length bias between short and long choices);
    `correct_raw` compares the sums. p_correct is the probability mass the model puts on the gold choice among
    the choices (softmax of the sums): a continuous measure that moves before accuracy flips, so it separates
    close models with fewer items.
    """
    norm = [s / b for s, b in zip(scores, n_bytes)]
    gold = labels(item).index(item["answer_key"])
    top = max(scores)
    weights = [math.exp(s - top) for s in scores]
    best = max(range(len(norm)), key=norm.__getitem__)
    best_raw = max(range(len(scores)), key=scores.__getitem__)
    return {"pred": labels(item)[best], "correct": best == gold, "correct_raw": best_raw == gold,
            "p_correct": round(weights[gold] / sum(weights), 5), "scores": [round(s, 4) for s in scores]}


def extract_code(text):
    blocks = _CODE_BLOCK.findall(text)
    return max(blocks, key=len) if blocks else text


def short_answer(text):
    line = next((x for x in text.strip().splitlines() if x.strip()), "")
    return re.sub(r"^(?:đáp án|answer|trả lời)\s*[:：]\s*", "", line.strip(), flags=re.IGNORECASE).rstrip(" .。")


def _contains_reference(item, answer):
    """
    Chat models answer in a sentence ("... là bức tường thành Thiers."). Count it when a reference
    appears as a whole phrase in a short first line: at most 2x the reference's words + 10, so a
    long dump that happens to include the phrase does not pass.
    """
    steps = item["verifier"].get("normalize", ["nfc"])
    got = verifiers.normalize(answer, steps)
    refs = item["reference_answer"] if isinstance(item["reference_answer"], list) else [item["reference_answer"]]
    for ref in refs:
        want = verifiers.normalize(ref, steps)
        if want and re.search(rf"(?<!\w){re.escape(want)}(?!\w)", got) \
                and len(got.split()) <= 2 * len(want.split()) + 10:
            return True
    return False


def score_generation(item, output):
    """(correct, detail) for one generated answer."""
    kind = item["kind"]
    if kind == "eval_mcq":
        return verifiers.check({"type": "choice"}, output, item)
    if kind == "eval_code":
        return verifiers.check(item["verifier"], extract_code(output), item)
    if is_numeric(item):
        return verifiers.check(item["verifier"], final_answer(output), item)
    answer = short_answer(output)
    passed, detail = verifiers.check(item["verifier"], answer, item)
    if not passed and item["verifier"]["type"] == "exact" and _contains_reference(item, answer):
        return True, "reference phrase in short answer"
    return passed, detail


def summarize(results):
    """
    results: dicts with group, lang, correct (optionally domain, difficulty, correct_raw, p_correct).
    Returns {key: {n, correct, acc, ci95[, acc_raw, p_correct]}} for every group, plus "group:<value>" keys
    for each of lang / domain / difficulty ("d3") along which a group's items differ.
    """
    sums = defaultdict(lambda: defaultdict(float))
    values = defaultdict(lambda: defaultdict(set))
    dims = (("lang", ""), ("domain", ""), ("difficulty", "d"))
    for r in results:
        for dim, _ in dims:
            values[r["group"]][dim].add(r.get(dim))
    for r in results:
        keys = [r["group"]] + [f"{r['group']}:{prefix}{r[dim]}" for dim, prefix in dims
                               if r.get(dim) is not None and len(values[r["group"]][dim]) > 1]
        for key in keys:
            s = sums[key]
            s["n"] += 1
            s["correct"] += bool(r["correct"])
            if "p_correct" in r:
                s["correct_raw"] += bool(r["correct_raw"])
                s["p_correct"] += r["p_correct"]
    out = {}
    for key in sorted(sums):
        s = sums[key]
        n, correct = int(s["n"]), int(s["correct"])
        lo, hi = wilson(correct, n)
        out[key] = {"n": n, "correct": correct, "acc": round(correct / n, 4), "ci95": [round(lo, 4), round(hi, 4)]}
        if "p_correct" in s:
            out[key] |= {"acc_raw": round(s["correct_raw"] / n, 4), "p_correct": round(s["p_correct"] / n, 4)}
    return out
