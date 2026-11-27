"""
Which encoding a PDF's extracted Vietnamese text is in. Old Vietnamese PDFs often embed TCVN3 (ABC,
.VnTime) or VNI fonts without a Unicode map, so extraction yields Latin-1 look-alikes instead of
Vietnamese letters: "ViÖt Nam" (TCVN3), "Vieät Nam" (VNI). Those need conversion before use.

classify(text) -> "unicode" | "tcvn3" | "vni" | "latin" | "cjk" | "garbled" | "none". Heuristic by
character statistics; a label is only given when its signature clearly dominates.
"""
import re
import unicodedata

# Letters only real Unicode Vietnamese produces: ă đ ơ ư and the U+1EA0 block (ạ ả ấ … ỹ). Neither legacy
# font yields them (both reuse Latin-1 â ê ô ù õ …), so their share separates Unicode from TCVN3/VNI.
_VI = set("ăđơưĂĐƠƯ") | {chr(c) for c in range(0x1EA0, 0x1EFA)}
# TCVN3 glyph slots almost absent from real Latin-1 text: ¸ á, µ à, ¶ ả, · ã, ¹ ạ, ¨ ă, ® đ.
_TCVN3 = set("¸µ¶·¹¨®")
# VNI: a base vowel followed by a Latin-1 accent glyph (Vieät, hoïc, ngöôøi), and ñ / Ñ for đ / Đ.
_VNI = re.compile(r"[aeiouyAEIOUY][øùûõïöôâêëæ]|ñ|Ñ")
_WORD = re.compile(r"[^\W\d_]+")


def classify(text):
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 50:
        return "none"
    n = len(letters)
    head = letters[:5000]
    if sum("CJK" in unicodedata.name(c, "") for c in head) / len(head) > 0.3:
        return "cjk"
    vi = sum(c in _VI for c in letters) / n
    tcvn3 = sum(c in _TCVN3 for c in text) / n  # ¸ · ¨ are not letters: count over the whole text
    vni = len(_VNI.findall(text)) / max(1, len(_WORD.findall(text)))
    if vi > 0.03 and tcvn3 < 0.01:
        return "unicode"
    if tcvn3 > 0.01:
        return "tcvn3"
    if vni > 0.15:
        return "vni"
    if sum(c.isascii() for c in letters) / n > 0.85:  # English, French, … (no Vietnamese-only letters)
        return "latin"
    return "garbled"
