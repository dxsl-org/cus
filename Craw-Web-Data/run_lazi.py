import sys
import json
from scrapers import lazi

sys.stdout.reconfigure(encoding='utf-8')

if __name__ == "__main__":
    print("--- Starting Lazi.vn ---")
    
    with open("scraping_config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
        
    global_settings = config.get("global_settings", {})
    source_settings = None
    
    for src in config.get("sources", []):
        if src.get("id") == "lazi_vn":
            source_settings = src
            break
            
    if not source_settings:
        print("Error: Configuration for lazi_vn not found.")
        sys.exit(1)
        
    lazi.process(source_settings, global_settings)
    print("--- Finished Lazi.vn ---")
