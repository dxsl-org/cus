import sys
import json
from scrapers import didau

sys.stdout.reconfigure(encoding='utf-8')

if __name__ == "__main__":
    print("--- Starting Didau ---")
    with open("scraping_config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
    global_settings = config.get("global_settings", {})
    source_settings = next(s for s in config.get("sources", []) if s.get("id") == "didau_com")
    didau.process(source_settings, global_settings)
    print("--- Finished Didau ---")
