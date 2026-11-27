import json
import os
from scrapers.tiasang import process

def main():
    # Load configuration
    config_path = "scraping_config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    # Find Tia Sang source
    source = next((s for s in config["sources"] if s["id"] == "tiasang_com_vn"), None)
    
    if not source:
        print("Tia Sang source not found in config.")
        return
        
    global_settings = config.get("global_settings", {})
    
    # Run the scraper
    process(source, global_settings)

if __name__ == "__main__":
    main()
