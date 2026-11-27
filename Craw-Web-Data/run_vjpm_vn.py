"""Run Tạp chí Y học Dự phòng scraper."""
import json
import sys
sys.stdout.reconfigure(encoding='utf-8')

from scrapers.ojs_crawler import process

with open("scraping_config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

global_settings = config.get("global_settings", {})
sources = config.get("sources", [])

source = next((s for s in sources if s["id"] == "vjpm_vn"), None)
if source and source.get("enabled"):
    print(f"\n--- Starting {source['name']} ---")
    process(source, global_settings)
else:
    print("Source 'vjpm_vn' not found or disabled.")
