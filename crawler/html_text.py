"""HTML → plain text helpers shared by adapters."""
import re
import unicodedata

import trafilatura
from lxml import html as lxml_html

_BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre",
               "table", "tr", "section", "article", "figure", "figcaption"}
_BLANK_LINES = re.compile(r"\n{3,}")


def fragment_to_text(fragment_html, drop_selectors=()):
    """
    Convert an HTML fragment (a post body, WordPress content) to text, keeping paragraph
    breaks. `drop_selectors`: CSS selectors removed first (e.g. quoted replies).
    """
    if not fragment_html or not fragment_html.strip():
        return ""
    root = lxml_html.fragment_fromstring(fragment_html, create_parent="div")
    for selector in drop_selectors:
        for node in root.cssselect(selector):
            node.drop_tree()
    for node in root.iter("script", "style", "noscript"):
        node.drop_tree()
    for br in root.iter("br"):
        br.tail = "\n" + (br.tail or "")
    for node in root.iter():
        if isinstance(node.tag, str) and node.tag in _BLOCK_TAGS:
            node.tail = "\n\n" + (node.tail or "")
    text = root.text_content()
    lines = [line.strip() for line in text.splitlines()]
    return unicodedata.normalize("NFC", _BLANK_LINES.sub("\n\n", "\n".join(lines))).strip()


def article_to_text(page_html, url):
    """Main content of a full article page via trafilatura, or "" when nothing is found."""
    text = trafilatura.extract(page_html, url=url, include_comments=False, include_tables=True,
                               favor_precision=True, deduplicate=False)
    return unicodedata.normalize("NFC", text).strip() if text else ""


def article_title(page_html):
    meta = trafilatura.extract_metadata(page_html)
    return unicodedata.normalize("NFC", meta.title).strip() if meta and meta.title else None
