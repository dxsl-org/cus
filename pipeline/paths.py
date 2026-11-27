"""
Filesystem layout (docs/data-contract.md). Data lives outside git under DATA_ROOT,
overridable with LLM_DATA_ROOT so bulk data can sit on another drive.
"""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_ROOT = pathlib.Path(os.environ.get("LLM_DATA_ROOT", ROOT / "data"))
LEGACY_DATASET_DIR = ROOT / "Craw-Web-Data" / "dataset"

CANONICAL_DIR = DATA_ROOT / "canonical"
PATCHES_DIR = DATA_ROOT / "patches"
REPORTS_DIR = DATA_ROOT / "reports"
RAW_DIR = DATA_ROOT / "raw"
PROCESSED_DIR = DATA_ROOT / "processed"
# Readers look for a source in each root, first hit wins: legacy/crawl exports were written under the
# repo's data/ before wave-1 open data moved to LLM_DATA_ROOT (phase 4).
CANONICAL_ROOTS = tuple(dict.fromkeys([CANONICAL_DIR, ROOT / "data" / "canonical"]))


def canonical_source_dir(source_id):
    """Directory holding a source's canonical exports, or None."""
    return next((r / source_id for r in CANONICAL_ROOTS if (r / source_id).is_dir()), None)
