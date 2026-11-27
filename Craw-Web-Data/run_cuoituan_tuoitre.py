
import json
import os
import sys

# Add the current directory to sys.path to import local modules
sys.path.append(os.getcwd())

from scrapers import cuoituan_tuoitre

def main():
    # Load configuration
    config_path = 'scraping_config.json'
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    global_settings = config['global_settings']
    source = next((s for s in config['sources'] if s['id'] == 'cuoituan_tuoitre_vn'), None)
    
    if source and source.get('enabled'):
        print(f"--- Starting {source['name']} ---")
        cuoituan_tuoitre.process(source, global_settings)
    else:
        print("Source 'cuoituan_tuoitre_vn' not found or not enabled in config.")

if __name__ == "__main__":
    main()
