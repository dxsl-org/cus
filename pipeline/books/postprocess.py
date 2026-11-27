"""
Pages of extracted book text -> one clean text. Removes running headers/footers and page numbers,
undoes hyphenation, and rejoins lines a PDF broke mid-sentence, so paragraphs read as written.
"""
import re
import unicodedata
from collections import Counter

from pipeline.books.syllables import tokens, vocab

EDGE_LINES = 2  # header/footer candidates: first and last lines of a page
REPEAT_SHARE = 0.3  # a candidate repeated on >= 30% of pages (and >= 3) is a running header/footer
FULL_LINE = 0.8  # a line at least this share of the median line length ran to the right margin
_PAGE_NUMBER = re.compile(r"^\s*[-–—(\[]*\s*(\d{1,4}|[ivxlcdm]{1,6})\s*[-–—)\]]*\s*$", re.IGNORECASE)
SENTENCE_END = (".", "!", "?", ":", ";", "…", "”", '"', "»", ")")
_HYPHEN_END = re.compile(r"[^\W\d_]-$")


def _key(line):
    """Header identity ignoring page numbers and spacing ("Chương 2 · 17" == "Chương 2 · 18")."""
    return re.sub(r"\d+", "#", " ".join(line.lower().split()))


def _running_lines(pages):
    counts = Counter()
    for lines in pages:
        edge = [l for l in lines if l.strip()]
        counts.update({_key(l) for l in edge[:EDGE_LINES] + edge[-EDGE_LINES:]})
    floor = max(3, REPEAT_SHARE * len(pages))
    return {k for k, n in counts.items() if n >= floor}


def join_lines(lines):
    """
    Lines -> paragraphs. A break is a wrap (joined with a space) when the line before has no sentence
    end and either the next line starts lower-case / with a digit, or the line before ran (nearly) the
    full measure: Vietnamese capitalises every syllable of a name, so "Việt\\nMinh" must join too.
    Short unterminated lines (headings, verse) stay on their own.
    """
    lines = [" ".join(raw.split()) for raw in lines]
    lines = [l for l in lines if l]
    widths = sorted(len(l) for l in lines)
    full = FULL_LINE * widths[len(widths) // 2] if widths else 0
    out, prev_len = [], 0
    for line in lines:
        tail = out[-1] if out else ""
        if tail and _HYPHEN_END.search(tail):
            out[-1] = tail + line if _whole_syllables(tail, line) else tail[:-1] + line
        elif tail and not tail.endswith(SENTENCE_END) and (
                line[:1].islower() or line[:1].isdigit() or prev_len >= full):
            out[-1] = tail + " " + line
        else:
            out.append(line)
        prev_len = len(line)
    return out


def _whole_syllables(tail, line):
    """Vietnamese never splits a syllable at a line end, so "Âu-|Phi" between two known syllables is a real
    hyphen and stays; "infor-|mation" is a hyphenated word and joins without it."""
    before, after = tokens(tail[-40:]), tokens(line[:40])
    known = vocab()
    return bool(before and after) and before[-1] in known and after[0] in known


def clean(pages):
    """`pages`: raw page texts in order. Returns the book text (paragraphs separated by newlines), NFC."""
    split = [unicodedata.normalize("NFC", p).splitlines() for p in pages]
    running = _running_lines(split)
    body = []
    for lines in split:
        edge = {i for i, l in enumerate(lines) if l.strip()}
        edge = set(sorted(edge)[:EDGE_LINES] + sorted(edge)[-EDGE_LINES:])
        body += [l for i, l in enumerate(lines)
                 if not (_PAGE_NUMBER.match(l) or (i in edge and _key(l) in running))]
    return "\n".join(join_lines(body))
