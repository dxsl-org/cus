import sys
import json
from scrapers import dembuon

sys.stdout.reconfigure(encoding='utf-8')

if __name__ == "__main__":
    print("--- Starting DemBuon ---")
    with open("scraping_config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
    global_settings = config.get("global_settings", {})
    source_settings = next(s for s in config.get("sources", []) if s.get("id") == "dembuon_vn")
    dembuon.process(source_settings, global_settings)
    print("--- Finished DemBuon ---")
