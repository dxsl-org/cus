import json
import os
from scrapers import webtretho

def run():
    config_path = "scraping_config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    global_settings = config.get("global_settings", {})
    source = next(s for s in config["sources"] if s["id"] == "webtretho_com")
    
    webtretho.process(source, global_settings)

if __name__ == "__main__":
    run()
