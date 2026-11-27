import json
import os
from scrapers import x_discovery

def main():
    config_path = "scraping_config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    global_settings = config.get("global_settings", {})
    # Note: We might need to add X to scraping_config.json if not already there, 
    # but for now we can pass a mock source or ensure it's there.
    source = next((s for s in config["sources"] if s["id"] == "x_twitter"), {
        "id": "x_twitter",
        "name": "X (Twitter)",
        "enabled": True
    })
    
    print(f"Starting X Discovery process...")
    x_discovery.process(source, global_settings)

if __name__ == "__main__":
    main()
