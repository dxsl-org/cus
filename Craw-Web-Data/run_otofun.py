import sys
import json
from scrapers import otofun

sys.stdout.reconfigure(encoding='utf-8')

if __name__ == "__main__":
    print("--- Starting Otofun.net ---")
    
    with open("scraping_config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
        
    global_settings = config.get("global_settings", {})
    source_settings = None
    
    for src in config.get("sources", []):
        if src.get("id") == "otofun_net":
            source_settings = src
            break
            
    if not source_settings:
        print("Error: Configuration for otofun_net not found.")
        sys.exit(1)
        
    otofun.process(source_settings, global_settings)
    print("--- Finished Otofun.net ---")
