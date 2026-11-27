import json, sys
sys.stdout.reconfigure(encoding='utf-8')
import os
from scrapers import plo_sitemap

def main():
    config_path = "scraping_config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    global_settings = config.get("global_settings", {})
    source = next((s for s in config["sources"] if s["id"] == "plo_vn"), None)
    
    if source:
        print(f"Starting re-scan for: {source['name']}")
        plo_sitemap.process(source, global_settings)
    else:
        print("Source plo_vn not found in configuration.")

if __name__ == "__main__":
    main()
