import sys
import json
from scrapers import vjol

sys.stdout.reconfigure(encoding='utf-8')

if __name__ == "__main__":
    print("--- Starting VJOL.info.vn ---")
    
    with open("scraping_config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
        
    global_settings = config.get("global_settings", {})
    source_settings = None
    
    for src in config.get("sources", []):
        if src.get("id") == "vjol_info_vn":
            source_settings = src
            break
            
    if not source_settings:
        print("Error: Configuration for vjol_info_vn not found.")
        sys.exit(1)
        
    vjol.process(source_settings, global_settings)
    print("--- Finished VJOL.info.vn ---")
