import json
import os
from scrapers import thuvienphapluat

def main():
    config_path = "scraping_config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    global_settings = config.get("global_settings", {})
    source = next((s for s in config["sources"] if s["id"] == "thuvienphapluat_vn"), {
        "id": "thuvienphapluat_vn",
        "name": "Thư viện Pháp luật",
        "enabled": True
    })
    
    print(f"Starting Thư viện Pháp luật scan...")
    thuvienphapluat.process(source, global_settings)

if __name__ == "__main__":
    main()
