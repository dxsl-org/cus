"""
TCVN3 (ABC, .VnTime) and VNI-Windows text -> Unicode, for PDFs whose fonts carry no Unicode map.

TCVN3 puts each precomposed Vietnamese letter in one Latin-1 slot ("ViÖt" = Việt). Upper-case toned
letters lived in a separate font (.VnTimeH) that reused the lower-case slots, so they come out lower
case; that loss cannot be undone from the text alone. VNI writes a base letter plus one or two accent
glyphs ("Vieät" = Việt, "Ñ" = Đ); conversion is greedy longest match.
"""
import re
import unicodedata

from pipeline.books.encoding import classify

_TCVN3 = {
    "µ": "à", "¸": "á", "¶": "ả", "·": "ã", "¹": "ạ",
    "¨": "ă", "»": "ằ", "¾": "ắ", "¼": "ẳ", "½": "ẵ", "Æ": "ặ",
    "©": "â", "Ç": "ầ", "Ê": "ấ", "È": "ẩ", "É": "ẫ", "Ë": "ậ",
    "®": "đ", "Ì": "è", "Ð": "é", "Î": "ẻ", "Ï": "ẽ", "Ñ": "ẹ",
    "ª": "ê", "Ò": "ề", "Õ": "ế", "Ó": "ể", "Ô": "ễ", "Ö": "ệ",
    "×": "ì", "Ý": "í", "Ø": "ỉ", "Ü": "ĩ", "Þ": "ị",
    "ß": "ò", "ã": "ó", "á": "ỏ", "â": "õ", "ä": "ọ",
    "«": "ô", "å": "ồ", "è": "ố", "æ": "ổ", "ç": "ỗ", "é": "ộ",
    "¬": "ơ", "ê": "ờ", "í": "ớ", "ë": "ở", "ì": "ỡ", "î": "ợ",
    "ï": "ù", "ó": "ú", "ñ": "ủ", "ò": "ũ", "ô": "ụ",
    "\u00ad": "ư", "õ": "ừ", "ø": "ứ", "ö": "ử", "÷": "ữ", "ù": "ự",
    "ú": "ỳ", "ý": "ý", "û": "ỷ", "ü": "ỹ", "þ": "ỵ",
    "¡": "Ă", "¢": "Â", "£": "Ê", "¤": "Ô", "¥": "Ơ", "¦": "Ư", "§": "Đ",
    "μ": "à",  # PDF extraction may turn µ (micro sign) into Greek mu
}
_TCVN3_TABLE = str.maketrans(_TCVN3)
# Some TCVN3 PDFs draw the ư slot (soft hyphen) as a minus sign: "®−êng". After the slot mapping
# ("đ−ờng"), a minus between two letters is ư; between digits it is still a minus.
_TCVN3_MINUS = re.compile(r"(?<=[^\W\d_])−(?=[^\W\d_])")

# VNI tone glyphs -> combining mark; hat glyphs (â ê ô) and breve glyphs (ă), each with an optional tone.
_TONES = {"ø": "\u0300", "ù": "\u0301", "û": "\u0309", "õ": "\u0303", "ï": "\u0323"}
_HAT_TONES = {"â": "", "à": "\u0300", "á": "\u0301", "å": "\u0309", "ã": "\u0303", "ä": "\u0323"}
_BREVE_TONES = {"ê": "", "è": "\u0300", "é": "\u0301", "ú": "\u0309", "ü": "\u0303", "ë": "\u0323"}


def _vni_table():
    nfc = lambda s: unicodedata.normalize("NFC", s)  # noqa: E731
    table = {}
    for case in (str.lower, str.upper):
        for base in "aeiouy":
            for glyph, mark in _TONES.items():
                table[case(base) + case(glyph)] = case(nfc(base + mark))
        for base in "aeo":
            for glyph, mark in _HAT_TONES.items():
                table[case(base) + case(glyph)] = case(nfc(base + "\u0302" + mark))
        for glyph, mark in _BREVE_TONES.items():
            table[case("a") + case(glyph)] = case(nfc("a\u0306" + mark))
        for glyph, mark in {"": "", **_TONES}.items():  # ơ = ô, ư = ö, each with an optional tone glyph
            table[case("ô") + case(glyph)] = case(nfc("o\u031b" + mark))
            table[case("ö") + case(glyph)] = case(nfc("u\u031b" + mark))
        # Single-glyph letters: ì í ỉ ĩ ị (i has no base+glyph form), ỵ, đ.
        for glyph, letter in {"ì": "ì", "í": "í", "æ": "ỉ", "ó": "ĩ", "ò": "ị", "î": "ỵ", "ñ": "đ"}.items():
            table[case(glyph)] = case(letter)
    by_first = {}
    for seq, uni in sorted(table.items(), key=lambda kv: -len(kv[0])):  # longest sequences first
        by_first.setdefault(seq[0], []).append((seq, uni))
    return by_first


_VNI_FIRST = _vni_table()


CAPS_LINE_SHARE = 0.8
_LETTERS = re.compile(r"[^\W\d_]+")


def tcvn3_to_unicode(text):
    return "\n".join(map(_caps_font_line, _TCVN3_MINUS.sub("ư", text.translate(_TCVN3_TABLE)).split("\n")))


def _caps_font_line(line):
    """
    TCVN3 capital fonts (.VnTimeH, .VnArialH) put accented capitals in the lower-case slots, so a heading
    converts to "PHầN THứ NHấT". A line whose multi-letter words are (almost) all upper-case in their
    ASCII letters was set in such a font: upper-case it whole.
    """
    words = [w for w in _LETTERS.findall(line) if sum(c.isascii() for c in w) >= 2]
    caps = sum(all(c.isupper() for c in w if c.isascii()) for w in words)
    return line.upper() if words and caps >= CAPS_LINE_SHARE * len(words) else line


def vni_to_unicode(text):
    out, i = [], 0
    while i < len(text):
        for seq, uni in _VNI_FIRST.get(text[i], ()):
            if text.startswith(seq, i):
                out.append(uni)
                i += len(seq)
                break
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def to_unicode(page_text):
    """(text, encoding it was in): a page is converted only when it classifies as TCVN3 or VNI."""
    encoding = classify(page_text)
    if encoding == "tcvn3":
        return tcvn3_to_unicode(page_text), encoding
    if encoding == "vni":
        return vni_to_unicode(page_text), encoding
    return page_text, encoding
