import json
import sys

# Force UTF-8 for Windows console
sys.stdout.reconfigure(encoding='utf-8')

from scrapers.wikipedia_vi import process

def main():
    with open("scraping_config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
        
    source = next((s for s in config["sources"] if s.get("id") == "vi_wikipedia_org"), None)
    
    if source:
        process(source, config["global_settings"])
    else:
        print("Source 'vi_wikipedia_org' not found in scraping_config.json")

if __name__ == "__main__":
    main()
