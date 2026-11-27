"""Shared HTTP identity and TLS settings for every fetcher in the project."""
import ssl
import urllib.robotparser

import truststore

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DEFAULT_HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "vi,en;q=0.8"}
# OS trust store: several .gov.vn hosts send incomplete chains that certifi rejects
# but Windows completes via AIA fetching. Verification stays on.
TLS_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def parse_robots(status_code, text):
    """robots.txt parser; a missing or failing robots.txt allows everything (RFC 9309 §2.3.1.3)."""
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(text.splitlines() if status_code == 200 else [])
    return parser



def content_signals(robots_text):
    """
    Cloudflare-style `Content-Signal:` values that apply to every crawler (the `*` group),
    e.g. {"search": "yes", "ai-train": "no"}. voz.vn and lsvn.vn declare ai-train=no as an
    express rights reservation (EU DSM Directive Art. 4); we treat it as a hard refusal.
    """
    signals, in_star_group, last_was_agent = {}, False, False
    for raw in robots_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            in_star_group = (in_star_group and last_was_agent) or value == "*"
            last_was_agent = True
            continue
        last_was_agent = False
        if key == "content-signal" and in_star_group:
            for pair in value.split(","):
                name, _, setting = pair.partition("=")
                signals[name.strip().lower()] = setting.strip().lower()
    return signals