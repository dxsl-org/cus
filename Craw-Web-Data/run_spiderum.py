import json, sys
sys.stdout.reconfigure(encoding='utf-8')
import os
from scrapers import spiderum_api

def main():
    # Load config
    with open('scraping_config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    global_settings = config['global_settings']
    spiderum_source = next(s for s in config['sources'] if s['id'] == 'spiderum_com')
    
    # Run scraper
    spiderum_api.process(spiderum_source, global_settings)

if __name__ == "__main__":
    main()
