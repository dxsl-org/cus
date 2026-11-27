"""
Hand-label sheets made by the pipeline CLIs, filled in the Studio instead of Excel. The CSV stays
the contract: `review_sample --score` reads `dung` (1 right / 0 wrong) and `classifier check` reads
`diem` (0-5), so the Studio rewrites the same file with the same columns and encoding.
"""
import csv
import os
from pathlib import Path

from pipeline.paths import DATA_ROOT, PROCESSED_DIR

MODES = {"dung": ("0", "1"), "diem": ("0", "1", "2", "3", "4", "5")}
TEXT_COLUMNS = ("excerpt", "text")


def known():
    paths = sorted((PROCESSED_DIR / "review").glob("*.csv")) + sorted((DATA_ROOT / "quality").glob("hand-labels*.csv"))
    out = []
    for p in paths:
        try:
            out.append(summary(p))
        except (ValueError, OSError):
            continue
    return out


def _read(path):
    path = Path(path)
    if path.suffix.lower() != ".csv" or not path.is_file():
        raise FileNotFoundError(f"không thấy file CSV {path}")
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows, columns = list(reader), reader.fieldnames or []
    mode = next((m for m in MODES if m in columns), None)
    text = next((c for c in TEXT_COLUMNS if c in columns), None)
    if mode is None or text is None:
        raise ValueError(f"{path.name}: cần cột dung hoặc diem và cột excerpt hoặc text")
    return path, rows, columns, mode, text


def summary(path):
    path, rows, _, mode, _ = _read(path)
    done = sum(r[mode].strip() in MODES[mode] for r in rows)
    return {"path": str(path), "name": path.name, "mode": mode, "rows": len(rows), "done": done}


def load(path):
    path, rows, _, mode, text = _read(path)
    return {**summary(path), "values": list(MODES[mode]), "text_column": text,
            "items": [{"row": i, **r} for i, r in enumerate(rows)]}


def save(path, row, value, note=None):
    """Set one row's mark (and note); the whole file is rewritten via a temp file and an atomic replace."""
    path, rows, columns, mode, _ = _read(path)
    value = "" if value is None else str(value).strip()
    if value and value not in MODES[mode]:
        raise ValueError(f"{mode} phải là một trong {', '.join(MODES[mode])}")
    rows[int(row)][mode] = value
    if note is not None and "ghi_chu" in columns:
        rows[int(row)]["ghi_chu"] = " ".join(str(note).split())
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, columns)
        writer.writeheader()
        writer.writerows(rows)
    try:
        os.replace(tmp, path)
    except PermissionError:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"không ghi được {path.name}: file đang mở trong Excel?") from None
    return summary(path)
