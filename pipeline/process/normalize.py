"""
Text normalization shared by processing (phase 6) and exports: Unicode repair, NFC, markdown
residue from the crawlers, and per-source boilerplate.

ftfy repairs mojibake (UTF-8 read as cp1252 and similar) but is told to keep curly quotes and
to normalize to NFC; it cannot repair TCVN3/VNI legacy encodings, which filters_vi rejects.
"""
import re
import unicodedata

import ftfy

_FTFY = ftfy.TextFixerConfig(uncurl_quotes=False, normalization="NFC", explain=False)
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BLANK_LINES = re.compile(r"\n\s*\n+")
_SPACES = re.compile(r"[ \t\u00a0\u200b]+")
# Leading/trailing junk that one source attaches to every page (regex, applied once, anchored).
BOILERPLATE = {
    "vbpl_vn": [re.compile(r"^\s*Are You suprised \?\s*", re.IGNORECASE)],
}
# Whole lines to drop wherever they occur: in-body "related article" links and similar widgets.
BOILERPLATE_LINES = {
    "tuoitre_vn": [re.compile(r"ĐỌC NGAY\s*$"), re.compile(r"^\*?\s*#{2,6}\s")],
}


def nfc(text):
    return unicodedata.normalize("NFC", text or "")


def clean_markdown(text):
    """Drop images, keep link text, collapse whitespace runs left by the crawler."""
    text = _MD_LINK.sub(r"\1", _MD_IMAGE.sub("", nfc(text))).replace("**", "")
    return _SPACES.sub(" ", _BLANK_LINES.sub("\n\n", text)).strip()


def normalize(text, source_id=None):
    """Repaired, NFC, markdown-cleaned text with the source's boilerplate removed."""
    text = clean_markdown(ftfy.fix_text(text or "", _FTFY))
    for pattern in BOILERPLATE.get(source_id, ()):
        text = pattern.sub("", text, count=1)
    drop = BOILERPLATE_LINES.get(source_id, ())
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(ln for ln in lines if not any(p.search(ln) for p in drop)).strip()
