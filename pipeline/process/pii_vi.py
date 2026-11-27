"""
PII masking for Vietnamese text: emails, phone numbers, ID card and bank account numbers, and in
court rulings the given names the court left unmasked.

Values are replaced by natural-looking fakes (FineWeb style) rather than <tags>, so training text
keeps its shape. ID and bank numbers are only masked next to their keyword: bare 9-12 digit runs
are as often case, invoice or document numbers. Courts publish parties as "Nguyễn Văn T"; a full
name after a party role is reduced to that form.
"""
from collections import Counter

import regex

EMAIL = regex.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# 10-digit mobiles (0[35789]...) and 11-digit landlines (02...), 0- or +84-prefixed, digits optionally
# grouped by space/./-; never inside a longer digit run (order, invoice and document numbers).
PHONE = regex.compile(r"(?<![\d.,])(?:\+84[ .\-]?|0)[235789](?:[ .\-]?\d){8,9}(?!\d)(?![.,]\d)")
ID_NUMBER = regex.compile(r"(?i)\b(CCCD|CMND|CMTND|căn cước(?: công dân)?|chứng minh nhân dân|hộ chiếu|"
                          r"số định danh(?: cá nhân)?)([^\d\n]{0,40}?)(\d[\d .]{6,16}\d)")
BANK = regex.compile(r"(?i)\b(số tài khoản|STK|tài khoản số|TK số)([^\d\n]{0,30}?)(\d[\d .\-]{5,22}\d)")
COURT_ROLE = (r"bị cáo|bị hại|nguyên đơn|bị đơn|người làm chứng|người bị kết án|người phạm tội|đương sự|"
              r"người đại diện|người bảo vệ quyền lợi|người có quyền lợi(?:,)? nghĩa vụ liên quan|ông|bà|anh|chị|cháu")
# Role, then 2-4 capitalised syllables covering the WHOLE name (no capitalised syllable after), whose
# last one is a full word, i.e. not already reduced to an initial.
COURT_NAME = regex.compile(rf"(?i:\b(?:{COURT_ROLE}))\s*:?\s+((?:\p{{Lu}}\p{{Ll}}+\s+){{1,3}})(\p{{Lu}})(\p{{Ll}}+)\b"
                           r"(?!\s+\p{Lu})")


def _digits_to_x(text):
    return regex.sub(r"\d", "x", text)


def _phone(m):
    keep = 3 if m[0].startswith("+") else 1  # "+84" or "0"
    return m[0][:keep] + _digits_to_x(m[0][keep:])


def mask(text, source_type=None):
    """(masked text, Counter of masked items by kind)."""
    counts = Counter()

    def sub(pattern, kind, replace):
        nonlocal text

        def repl(m):
            counts[kind] += 1
            return replace(m)
        text = pattern.sub(repl, text)

    sub(EMAIL, "email", lambda m: "email@example.com")
    sub(ID_NUMBER, "id_number", lambda m: m[1] + m[2] + _digits_to_x(m[3]))
    sub(BANK, "bank_account", lambda m: m[1] + m[2] + _digits_to_x(m[3]))
    sub(PHONE, "phone", _phone)
    if source_type == "court":
        sub(COURT_NAME, "court_name", lambda m: m[0][:m.start(3) - m.start(0)])
    return text, counts
