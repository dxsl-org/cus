"""Run vsqi_gov_vn scraper standalone."""
import json, sys
sys.stdout.reconfigure(encoding='utf-8')

from scrapers.vsqi_tcvn import process

with open("scraping_config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

source = next((s for s in config["sources"] if s["id"] == "vsqi_gov_vn"), None)
if source:
    process(source, config["global_settings"])
else:
    print("Source not found in config.")
