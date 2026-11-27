from pipeline.ingest.wikimedia_dump import sister_wikitext_to_text, wiktionary_to_text


def test_sister_keeps_poem_lines_and_strips_noinclude():
    wt = """<noinclude><pagequality level="4" user="Test" /></noinclude>
<poem>
Trăm năm trong cõi người ta,
Chữ tài chữ mệnh khéo là ghét nhau.
</poem>"""
    out = sister_wikitext_to_text(wt)
    assert "Trăm năm trong cõi người ta," in out
    assert "Chữ tài chữ mệnh khéo là ghét nhau." in out
    assert "pagequality" not in out


def test_wiktionary_extracts_vietnamese_and_drops_translations():
    wt = """{{-vie-}}

{{-etym-}}
{{vi-etym-sino|云云}}.

{{-pron-}}
{{vie-pron}}

{{-adv-}}
'''vân vân'''

# Tỏ ý [[còn]] nhiều thứ [[tương tự]] như thế.
#: {{ux|vi|Nồi, bát, đĩa, '''vân vân'''.}}

{{-syn-}}
* {{l|vi|v.v.}}

{{-trans-}}
{{đầu}}
* {{eng}}: [[et cetera]]
* {{nld}}: [[enzovoort]]
{{cuối}}"""
    out = wiktionary_to_text("vân vân", wt)
    assert "Hán-Việt: 云云" in out
    assert "Phó từ:" in out
    assert "Tỏ ý còn nhiều thứ tương tự như thế." in out
    assert "Đồng nghĩa:" in out
    assert "enzovoort" not in out
    assert "{{" not in out


def test_wiktionary_skips_non_vietnamese_entries():
    wt = """{{-eng-}}
{{-noun-}}
# Quả táo."""
    assert wiktionary_to_text("apple", wt) == ""
