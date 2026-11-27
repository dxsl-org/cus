"""
Paragraphs from non-PDF book files: EPUB / FB2 / HTML (MuPDF), Kindle MOBI / PRC / AZW / AZW3
(KindleUnpack, then MuPDF), DOCX (its XML), DOC / RTF / ODT (LibreOffice to UTF-8 text) and TXT.

read_paragraphs(path) -> list of raw paragraph strings, not yet NFC and possibly still in a legacy
TCVN3/VNI font encoding (the caller converts). Raises DrmError for encrypted Kindle books (they are
reported, never decrypted) and UnreadableError for files no reader can open.
"""
import contextlib
import io
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import mobi
import pymupdf

from pipeline.books.postprocess import SENTENCE_END, join_lines

KINDLE = {"mobi", "prc", "azw", "azw3"}
MUPDF = {"epub", "fb2", "html", "htm", "xhtml"}
LIBREOFFICE = {"doc", "rtf", "odt"}
SOFFICE = Path("C:/Program Files/LibreOffice/program/soffice.exe")
SOFFICE_TIMEOUT = 300
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_PARAGRAPH_PER_LINE = 120  # TXT whose median line is longer than this has one paragraph per line


class DrmError(Exception):
    pass


class UnreadableError(Exception):
    pass


def read_paragraphs(path):
    path = Path(path)
    ext = path.suffix.lower().lstrip(".")
    if ext in KINDLE:
        return _kindle(path)
    if ext in MUPDF:
        return _reflowed(path, ext)
    if ext == "docx":
        return _docx(path)
    if ext in LIBREOFFICE:
        return _libreoffice(path)
    if ext == "txt":
        return _txt(path.read_bytes())
    raise UnreadableError(f"no reader for .{ext}")


def _reflowed(path, filetype):
    """MuPDF lays the book out in pages; each text block is a paragraph. A block cut by a page break
    (no sentence end, next one starts lower-case) is rejoined."""
    try:
        doc = pymupdf.open(path, filetype=filetype)
    except Exception as e:
        raise UnreadableError(f"mupdf: {e}") from e
    out = []
    with doc:
        for page in doc:
            for block in page.get_text("blocks"):
                if block[6] != 0:  # image block
                    continue
                text = " ".join(block[4].replace("\xad\n", "").split())
                if not text:
                    continue
                if out and not out[-1].endswith(SENTENCE_END) and text[:1].islower():
                    out[-1] += " " + text
                else:
                    out.append(text)
    return out


def _kindle(path):
    try:
        with contextlib.redirect_stdout(io.StringIO()):  # KindleUnpack prints its progress
            tmp, unpacked = mobi.extract(str(path))
    except Exception as e:
        if "encrypted" in str(e).lower():
            raise DrmError("kindle DRM") from e
        raise UnreadableError(f"kindleunpack: {e}") from e
    try:
        unpacked = Path(unpacked)
        return _reflowed(unpacked, unpacked.suffix.lower().lstrip("."))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _docx(path):
    try:
        with zipfile.ZipFile(path) as z:
            root = ElementTree.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as e:
        raise UnreadableError(f"docx: {e}") from e
    out = []
    for p in root.iter(f"{_W}p"):
        parts = []
        for node in p.iter():
            if node.tag == f"{_W}t":
                parts.append(node.text or "")
            elif node.tag == f"{_W}tab":
                parts.append(" ")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")  # a manual line break: verse keeps its lines
        out.extend(line.strip() for line in "".join(parts).split("\n"))
    return out


def _libreoffice(path):
    with tempfile.TemporaryDirectory() as tmp:
        profile = Path(tmp, "profile").as_uri()  # own profile: works while the user has LibreOffice open
        try:
            subprocess.run([str(SOFFICE), f"-env:UserInstallation={profile}", "--headless", "--convert-to",
                            "txt:Text (encoded):UTF8", "--outdir", tmp, str(path)],
                           capture_output=True, timeout=SOFFICE_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise UnreadableError(f"libreoffice: {e}") from e
        out = Path(tmp, path.stem + ".txt")
        if not out.is_file():
            raise UnreadableError("libreoffice produced no text")
        return out.read_text(encoding="utf-8-sig").splitlines()


def decode(raw):
    """UTF-8 / UTF-16 (BOM) / Windows code pages. Legacy TCVN3/VNI files decode as cp1252 look-alikes,
    which the caller's font converter then maps."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _txt(raw):
    lines = decode(raw).splitlines()
    widths = sorted(len(l.strip()) for l in lines if l.strip())
    if not widths:
        return []
    if widths[len(widths) // 2] > _PARAGRAPH_PER_LINE:
        return [l.strip() for l in lines]
    # Hard-wrapped text: blank lines separate paragraphs; inside a group, rejoin wrapped lines.
    out = []
    for group in re.split(r"\n\s*\n", "\n".join(lines)):
        out.extend(join_lines(group.splitlines()))
    return out
