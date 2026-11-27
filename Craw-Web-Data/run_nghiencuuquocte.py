import sys
import json
from scrapers import nghiencuuquocte

# Fix console encoding for Windows
sys.stdout.reconfigure(encoding='utf-8')

if __name__ == "__main__":
    print("--- Starting Nghiễn cứu Quốc tế ---")
    
    # Load config manually
    with open("scraping_config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
        
    global_settings = config.get("global_settings", {})
    source_settings = None
    
    for src in config.get("sources", []):
        if src.get("id") == "nghiencuuquocte_org":
            source_settings = src
            break
            
    if not source_settings:
        print("Error: Configuration for nghiencuuquocte_org not found.")
        sys.exit(1)
        
    nghiencuuquocte.process(source_settings, global_settings)
    print("--- Finished Nghiễn cứu Quốc tế ---")
