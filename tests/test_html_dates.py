from datetime import date

from pipeline.ingest.html_dates import extract_publish_date

LIMIT = date(2026, 3, 13)


def test_prefers_iso_meta_over_us_style_value():
    # plo.vn emits both; "3/1/2026" must not be read as 3 January.
    html = ('<head><meta itemprop="datePublished" content="3/1/2026 6:39:57 AM">'
            '<meta property="article:published_time" content="2026-03-01T06:39:00+07:00"></head>')
    assert extract_publish_date(html, LIMIT) == (date(2026, 3, 1), "meta:article:published_time")


def test_falls_back_to_json_ld_and_reports_missing():
    ld = '<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2010-02-28T17:51:00+07:00"}</script>'
    assert extract_publish_date(f'<meta property="article:published_time" content="">{ld}', LIMIT) == (date(2010, 2, 28), "ld:datePublished")
    assert extract_publish_date("<head><title>x</title></head>", LIMIT) == (None, "no_date_meta")


def test_rejects_date_after_original_crawl():
    html = '<meta property="article:published_time" content="2026-11-01T00:00:00">'
    assert extract_publish_date(html, LIMIT) == (None, "no_date_meta")
