"""
Heuristic quality filters for Vietnamese (Gopher / C4 / FineWeb rules, retuned).

A "word" is a whitespace token, i.e. one syllable: Vietnamese words are 1-3 syllables, so syllable
counts run ~1.4x English word counts and syllables are short (~3.5 letters). Thresholds live in
THRESHOLDS so a review round can move them without touching the rules. check() returns the FIRST
failing rule (rules are ordered cheap-to-expensive) plus the metrics it computed.
"""
import re
from collections import Counter

_LETTERS = re.compile(r"[^\W\d_]+")
# None of these occur in Unicode Vietnamese; many do in TCVN3 text decoded as cp1252/latin-1.
_TCVN3 = re.compile(r"[¸µ©¬®§¶¹»¼½¾ÆÇËÏÖ×ØÜÞßäåæçëïñö÷øüþÿ]")
_HAS_LETTER = re.compile(r"[^\W\d_]")
_PUNCT_END = (".", "!", "?", '"', "”", "…", ":", ";", ")", "»")
_BULLET = re.compile(r"^\s*([-*•●▪◦+]|\d+[.)])\s")
STOPWORDS = frozenset("và của là có được cho không những các một này với trong người đã để khi thì từ cũng "
                      "đến như về ra đó nhưng vì nên sẽ tại theo nhiều rất bị".split())

THRESHOLDS = {
    "garbled_single_letter": 0.14, "garbled_tcvn3_chars": 20,
    "min_syllables": 50, "max_syllables": 200_000,
    "min_mean_syllable_len": 2.5, "max_mean_syllable_len": 7.0,
    "min_alpha_token_ratio": 0.7, "min_stopwords": 2, "max_symbol_ratio": 0.1,
    "max_bullet_lines": 0.9, "max_ellipsis_lines": 0.3,
    "max_dup_line_chars": 0.3, "max_dup_para_chars": 0.3,
    # Gopher's n counts English words; one English word ~ 1.4 Vietnamese syllables, so n is scaled
    # (2,3,4 -> 3,4,6 and 5..10 -> 7..14). Duplicate-n-gram limits sit just above the 98th percentile
    # of Tuổi Trẻ articles (2026-11-15: 0.288/0.259/0.215/0.195/0.165/0.159): long Vietnamese proper
    # nouns ("Sở Giáo dục và Đào tạo TP.HCM") repeat in normal news, so Gopher's 0.15..0.10 cut 21%.
    "max_top_ngram_chars": {3: 0.20, 4: 0.18, 6: 0.16},
    "max_dup_ngram_chars": {7: 0.30, 8: 0.27, 10: 0.23, 11: 0.21, 13: 0.18, 14: 0.17},
    "min_line_punct_ratio": 0.12, "max_short_line_ratio": 0.67, "short_line_chars": 30,
}


# Statutes and rulings repeat formulae by design ("theo quy định tại khoản", party names, "Ủy ban nhân
# dân tỉnh"): vbpl's median 7-gram duplication is 0.26 vs 0.06 for news. Limits sit just above the
# 98th percentile of 1,475 vbpl documents (2026-11-15: dup7 0.59, dup14 0.42). Law journals that quote
# statutes verbatim use the same profile.
LEGAL_OVERRIDES = {"max_dup_ngram_chars": {7: 0.62, 8: 0.59, 10: 0.53, 11: 0.50, 13: 0.47, 14: 0.45}}
LEGAL_SOURCE_TYPES = frozenset({"legal", "court"})
LEGAL_SOURCES = frozenset({"lsvn_vn", "tapchitoaan_vn"})


def thresholds_for(source_id, source_type):
    """THRESHOLDS with the legal profile applied to statutes, rulings and law journals."""
    if source_type in LEGAL_SOURCE_TYPES or source_id in LEGAL_SOURCES:
        return {**THRESHOLDS, **LEGAL_OVERRIDES}
    return THRESHOLDS


def garbled(text, t=THRESHOLDS):
    """Broken PDF extraction ("N u ễ Huỳ T ị") or TCVN3 text ("KiÓm s¸t viªn")."""
    head = text[:4000]
    words = _LETTERS.findall(head)
    single = sum(len(w) == 1 for w in words) / max(1, len(words))
    return single >= t["garbled_single_letter"] or len(_TCVN3.findall(head)) > t["garbled_tcvn3_chars"]


def _dup_chars(units):
    """Fraction of characters in units (lines/paragraphs) that repeat an earlier unit."""
    seen, dup, total = set(), 0, 0
    for u in units:
        total += len(u)
        if u in seen:
            dup += len(u)
        seen.add(u)
    return dup / max(1, total)


def _top_ngram_chars(tokens, n):
    grams = Counter(" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1))
    if not grams:
        return 0.0
    gram, count = grams.most_common(1)[0]
    return count * len(gram) / max(1, sum(len(t) for t in tokens))


def _dup_ngram_chars(tokens, n):
    """Fraction of characters covered by n-grams that occur more than once (Gopher)."""
    positions, covered = {}, [False] * len(tokens)
    for i in range(len(tokens) - n + 1):
        key = tuple(tokens[i:i + n])
        if key in positions:
            for j in range(i, i + n):
                covered[j] = True
            for j in range(positions[key], positions[key] + n):
                covered[j] = True
        else:
            positions[key] = i
    total = sum(len(t) for t in tokens)
    return sum(len(t) for t, c in zip(tokens, covered) if c) / max(1, total)


def check(text, t=THRESHOLDS):
    """(None | reason, metrics) for normalized text."""
    if garbled(text, t):
        return "garbled", {}
    tokens = text.split()
    n = len(tokens)
    m = {"syllables": n}
    if n < t["min_syllables"]:
        return "too_short", m
    if n > t["max_syllables"]:
        return "too_long", m
    letters = [_LETTERS.findall(tok) for tok in tokens]
    m["mean_syllable_len"] = sum(len("".join(x)) for x in letters) / n
    if not t["min_mean_syllable_len"] <= m["mean_syllable_len"] <= t["max_mean_syllable_len"]:
        return "syllable_len", m
    m["alpha_ratio"] = sum(bool(_HAS_LETTER.search(tok)) for tok in tokens) / n
    if m["alpha_ratio"] < t["min_alpha_token_ratio"]:
        return "non_alpha", m
    lower = [tok.lower().strip(".,;:!?\"'()[]“”") for tok in tokens]
    if len(STOPWORDS.intersection(lower)) < t["min_stopwords"]:
        return "no_stopwords", m
    if (text.count("#") + text.count("...") + text.count("…")) / n > t["max_symbol_ratio"]:
        return "symbols", m
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if sum(bool(_BULLET.match(ln)) for ln in lines) / len(lines) > t["max_bullet_lines"]:
        return "bullets", m
    if sum(ln.rstrip().endswith(("...", "…")) for ln in lines) / len(lines) > t["max_ellipsis_lines"]:
        return "ellipsis", m
    if _dup_chars(lines) > t["max_dup_line_chars"]:
        return "dup_lines", m
    if _dup_chars([p.strip() for p in text.split("\n\n") if p.strip()]) > t["max_dup_para_chars"]:
        return "dup_paragraphs", m
    for k, limit in t["max_top_ngram_chars"].items():
        if _top_ngram_chars(lower, k) > limit:
            return f"top_{k}gram", m
    for k, limit in t["max_dup_ngram_chars"].items():
        if _dup_ngram_chars(lower, k) > limit:
            return f"dup_{k}gram", m
    m["line_punct_ratio"] = sum(ln.rstrip().endswith(_PUNCT_END) for ln in lines) / len(lines)
    if m["line_punct_ratio"] < t["min_line_punct_ratio"]:
        return "line_punct", m
    if sum(len(ln) <= t["short_line_chars"] for ln in lines) / len(lines) > t["max_short_line_ratio"]:
        return "short_lines", m
    return None, m
