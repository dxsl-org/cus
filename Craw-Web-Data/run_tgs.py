"""Run TGS.vn scraper standalone."""
import json, sys
sys.stdout.reconfigure(encoding='utf-8')

from scrapers.tgs import process

with open("scraping_config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

source = next((s for s in config["sources"] if s["id"] == "tgs_vn"), None)
if source:
    process(source, config["global_settings"])
else:
    print("Source not found in config.")
