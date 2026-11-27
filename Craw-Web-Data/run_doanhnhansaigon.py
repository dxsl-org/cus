import json
import os
import sys

# Add the current directory to sys.path to import local modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from scrapers.doanhnhansaigon_sitemap import process

def main():
    # Load configuration
    config_path = 'scraping_config.json'
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    global_settings = config['global_settings']
    
    # Find the doanhnhansaigon_vn source
    source = next((s for s in config['sources'] if s['id'] == 'doanhnhansaigon_vn'), None)
    
    if source and source.get('enabled'):
        print(f"--- Starting {source['name']} ---")
        process(source, global_settings)
        print(f"--- Finished {source['name']} ---")
    else:
        print("Source 'doanhnhansaigon_vn' not found or not enabled in config.")

if __name__ == "__main__":
    main()
