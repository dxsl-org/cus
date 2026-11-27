from datetime import date

from pipeline.ingest.github_stem_export import _parse_pub_date, parse_markdown_doc


def test_parse_markdown_doc_strips_frontmatter_and_liquid():
    raw = """---
title: "Bài 1: Hồi quy tuyến tính"
date: 2016-12-28
tags: [Linear Algebra, Regression]
---
<!-- comment -->
{% include toc %}
{{ site.baseurl }}
Nội dung giải thích thuật toán với công thức $y = wx + b$.
"""
    meta, title, text = parse_markdown_doc(raw, "_posts/2016-12-28-lr.markdown")
    assert title == "Bài 1: Hồi quy tuyến tính"
    assert "include toc" not in text
    assert "site.baseurl" not in text
    assert "$y = wx + b$" in text
    assert _parse_pub_date(meta, date(2026, 11, 19)) == date(2016, 12, 28)


def test_parse_markdown_doc_falls_back_to_h1_and_filename_date():
    raw = "# Quy hoạch động\n\nGiới thiệu chi tiết quy hoạch động."
    meta, title, text = parse_markdown_doc(raw, "_posts/2019-05-10-dp.md")
    assert title == "Quy hoạch động"
    assert _parse_pub_date(meta, date(2026, 11, 19)) == date(2019, 5, 10)
