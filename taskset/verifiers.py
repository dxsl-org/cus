"""
Answer verifiers for TaskItems: exact, numeric, regex, choice, unit_test.

check(verifier, candidate, content) -> (passed, detail). `verifier` is the item's
`{type, spec?, tolerance?, normalize?}`; when `spec` is absent the expected value comes
from the item (`reference_answer`, or `answer_key` for choice).

unit_test runs untrusted (model-written) code: separate process, isolated interpreter
(-I), temp working dir, wall-clock timeout, sockets disabled. The socket block is a
guard against accidents, not a sandbox; batch RL verification must move to a container.
"""
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

TYPES = ("exact", "numeric", "regex", "choice", "unit_test")
NORMALIZERS = ("nfc", "lower", "strip_punct", "fold_accents")
CHOICE_LABELS = "ABCDEFGHIJ"
DEFAULT_TIMEOUT_S = 10

_NUMBER = re.compile(r"[-−]?\d[\d.,]*")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_CHOICE_LEAD = re.compile(r"^\(?([A-J])(?:[).:\s]|$)")
_CHOICE_PHRASE = re.compile(r"(?:đáp án|answer|chọn)\s*(?:đúng)?\s*(?:là|is)?\s*[:\-]?\s*\(?([A-J])\b", re.IGNORECASE)
_NO_NETWORK = ("import socket as _s\n"
               "def _blocked(*a, **k):\n    raise OSError('network disabled in verifier')\n"
               "_s.socket = _blocked; _s.create_connection = _blocked\n")


def validate_spec(verifier):
    """Errors in a verifier declaration (empty list when valid)."""
    if not isinstance(verifier, dict) or verifier.get("type") not in TYPES:
        return [f"verifier.type must be one of {TYPES}"]
    errors = [f"unknown normalizer {n!r}" for n in verifier.get("normalize", []) if n not in NORMALIZERS]
    kind, spec = verifier["type"], verifier.get("spec")
    if kind == "regex":
        try:
            re.compile(spec or "")
        except re.error as exc:
            errors.append(f"bad regex: {exc}")
        if not spec:
            errors.append("regex verifier needs spec")
    if kind == "unit_test":
        if not isinstance(spec, dict) or spec.get("language") != "python" or not spec.get("test"):
            errors.append("unit_test spec needs {language: python, test: <code>, setup?: <code>}")
    if kind == "numeric" and not set(verifier.get("tolerance") or {}) <= {"abs", "rel"}:
        errors.append("numeric tolerance keys are abs, rel")
    return errors


def normalize(text, steps=()):
    text = " ".join(str(text).split())
    for step in steps:
        if step == "nfc":
            text = unicodedata.normalize("NFC", text)
        elif step == "lower":
            text = text.lower()
        elif step == "strip_punct":
            text = " ".join(_PUNCT.sub(" ", text).split())
        elif step == "fold_accents":
            decomposed = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
            text = "".join(c for c in decomposed if not unicodedata.combining(c))
    return text


def parse_number(text):
    """
    Last number in `text`, reading both Vietnamese (1.000,5) and English (1,000.5) styles.
    With one separator kind: repeated, or one group of exactly 3 digits after a non-zero
    integer part, means thousands ("1.000" = "1,000" = 1000); otherwise it is the decimal mark.
    """
    matches = _NUMBER.findall(str(text))
    if not matches:
        return None
    raw = matches[-1].rstrip(".,").replace("−", "-")
    sign, digits = (-1, raw[1:]) if raw.startswith("-") else (1, raw)
    if "." in digits and "," in digits:
        decimal = "." if digits.rfind(".") > digits.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        digits = digits.replace(thousands, "").replace(decimal, ".")
    elif "." in digits or "," in digits:
        sep = "." if "." in digits else ","
        head, *groups = digits.split(sep)
        is_thousands = len(groups) > 1 or (len(groups[0]) == 3 and head.lstrip("0") != "" and len(head) <= 3)
        digits = digits.replace(sep, "") if is_thousands else digits.replace(sep, ".")
    try:
        return sign * float(digits)
    except ValueError:
        return None


def parse_choice(text):
    text = str(text).strip()
    if m := _CHOICE_LEAD.match(text) or _CHOICE_PHRASE.search(text):
        return m[1].upper()
    return None


def _expected(verifier, content, key="reference_answer"):
    value = verifier.get("spec", content.get(key))
    return value if isinstance(value, list) else [value]


def _run_unit_test(spec, code):
    timeout = spec.get("timeout", DEFAULT_TIMEOUT_S)
    with tempfile.TemporaryDirectory(prefix="verifier-") as tmp:
        program = Path(tmp) / "check.py"
        # setup (e.g. the task's own imports) runs before the candidate, test after it.
        program.write_text(f"{_NO_NETWORK}\n{spec.get('setup', '')}\n{code}\n\n{spec['test']}\n", encoding="utf-8")
        try:
            done = subprocess.run([sys.executable, "-I", str(program)], cwd=tmp, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=timeout,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return False, f"timeout after {timeout}s"
    if done.returncode == 0:
        return True, "tests passed"
    lines = (done.stderr or done.stdout).strip().splitlines()
    return False, lines[-1][:300] if lines else f"exit {done.returncode}"


def check(verifier, candidate, content):
    kind, steps = verifier["type"], verifier.get("normalize", ["nfc"])
    if kind == "exact":
        got = normalize(candidate, steps)
        ok = any(got == normalize(e, steps) for e in _expected(verifier, content) if e is not None)
        return ok, "match" if ok else f"no match for {got[:80]!r}"
    if kind == "numeric":
        got = parse_number(candidate)
        if got is None:
            return False, "no number in answer"
        tol = verifier.get("tolerance") or {}
        for e in _expected(verifier, content):
            want = parse_number(e)
            if want is not None and abs(got - want) <= max(tol.get("abs", 0.0), tol.get("rel", 1e-9) * abs(want)):
                return True, f"{got} == {want}"
        return False, f"{got} not within tolerance"
    if kind == "regex":
        ok = re.fullmatch(verifier["spec"], normalize(candidate, steps)) is not None
        return ok, "match" if ok else "no match"
    if kind == "choice":
        got, want = parse_choice(candidate), _expected(verifier, content, "answer_key")
        return got in want, f"chose {got}, key {want}"
    return _run_unit_test(verifier["spec"], str(candidate))
