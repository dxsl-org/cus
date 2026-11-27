import json
import os
from scrapers import wordpress_rest_api

def main():
    # Load settings
    with open('scraping_config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
        
    global_settings = config.get('global_settings', {})
    
    # Find the caphesach_wordpress_com source
    source = next((s for s in config['sources'] if s['id'] == 'caphesach_wordpress_com'), None)
    
    if not source:
        print("Source caphesach_wordpress_com not found in config!")
        return
        
    # Pre-clean progress for fresh start if requested (optional)
    # if os.path.exists("progress_cafesach_wordpress_com.json"):
    #     os.remove("progress_cafesach_wordpress_com.json")
        
    print(f"Starting re-scan for {source['name']} ({source['url']})...")
    wordpress_rest_api.process(source, global_settings)
    print("Re-scan completed.")

if __name__ == "__main__":
    main()
