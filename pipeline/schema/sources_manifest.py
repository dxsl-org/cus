"""
Registry of every data source (manifests/sources.yaml) and its consistency checks.

The YAML is the source of truth for source-level policy (type, license, usage
scope, loss risk). `legacy` links an entry to the old crawler config ids and
SQLite files so nothing in Craw-Web-Data is left unaccounted for.

CLI: python -m pipeline.schema.sources_manifest  -> validates and prints a summary.
"""
import collections
import json
import pathlib
import sys

import yaml

from pipeline.schema.document_record import SOURCE_TYPES, USAGE_SCOPES

ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "manifests" / "sources.yaml"
LEGACY_CONFIG = ROOT / "Craw-Web-Data" / "scraping_config.json"
LEGACY_DATASET_DIR = ROOT / "Craw-Web-Data" / "dataset"

ACQUISITIONS = frozenset({"crawl", "open_dataset", "books"})
LOSS_RISKS = frozenset({"gone", "high", "medium", "low"})
STATUSES = frozenset({"active", "partial", "empty", "planned", "retired"})
REQUIRED = ("id", "name", "source_type", "acquisition", "loss_risk", "license", "usage_scope", "status")


def load(path=MANIFEST_PATH):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["sources"]


def validate(entries, legacy_config_ids=(), legacy_db_files=()):
    """
    Returns a list of violations. Checks required fields, enum values, unique ids,
    and that every legacy config id / DB file is claimed by exactly one entry.
    """
    errors = []
    ids = collections.Counter(e.get("id") for e in entries)
    errors += [f"duplicate id {i!r}" for i, n in ids.items() if n > 1]
    enums = {"source_type": SOURCE_TYPES, "acquisition": ACQUISITIONS, "loss_risk": LOSS_RISKS,
             "usage_scope": USAGE_SCOPES, "status": STATUSES}
    claimed_cfg, claimed_db = collections.Counter(), collections.Counter()
    for e in entries:
        sid = e.get("id", "<no id>")
        errors += [f"{sid}: missing {f}" for f in REQUIRED if not e.get(f)]
        errors += [f"{sid}: {field}={e[field]!r} not in {sorted(allowed)}"
                   for field, allowed in enums.items() if e.get(field) and e[field] not in allowed]
        legacy = e.get("legacy") or {}
        claimed_cfg.update(legacy.get("config_ids", []))
        claimed_db.update(legacy.get("dbs", []))
    for cfg in legacy_config_ids:
        if claimed_cfg[cfg] != 1:
            errors.append(f"legacy config id {cfg!r} claimed {claimed_cfg[cfg]} times")
    for db in legacy_db_files:
        if claimed_db[db] != 1:
            errors.append(f"legacy db {db!r} claimed {claimed_db[db]} times")
    return errors


def legacy_inventory():
    """Config ids and DB file names currently present in Craw-Web-Data."""
    with open(LEGACY_CONFIG, encoding="utf-8") as f:
        config_ids = sorted({s["id"] for s in json.load(f)["sources"]})
    db_files = sorted(p.name for p in LEGACY_DATASET_DIR.glob("*.db"))
    return config_ids, db_files


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    entries = load()
    config_ids, db_files = legacy_inventory()
    errors = validate(entries, config_ids, db_files)
    by = lambda field: dict(collections.Counter(e[field] for e in entries).most_common())
    print(f"{len(entries)} sources; legacy: {len(config_ids)} config ids, {len(db_files)} DB files")
    for field in ("source_type", "loss_risk", "status", "license"):
        print(f"  {field}: {by(field)}")
    for err in errors:
        print("ERROR", err)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
