import json, sys
sys.stdout.reconfigure(encoding='utf-8')
import os
from scrapers import cafef

def main():
    # Load settings
    with open('scraping_config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
        
    global_settings = config.get('global_settings', {})
    
    # Find the cafef_vn source
    source = next((s for s in config['sources'] if s['id'] == 'cafef_vn'), None)
    
    if not source:
        print("Source cafef_vn not found in config!")
        return
        
    # Pre-clean progress for fresh start if requested
    # progress_file = f"progress_{source['id']}.json"
    # if os.path.exists(progress_file):
    #     os.remove(progress_file)
        
    print(f"Starting CafeF re-scan ({source['url']})...")
    cafef.process(source, global_settings)
    print("CafeF scan completed.")

if __name__ == "__main__":
    main()
