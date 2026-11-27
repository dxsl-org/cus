import json, sys
sys.stdout.reconfigure(encoding='utf-8')
from scrapers.chinhsachonline import process

def main():
    with open('scraping_config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    source = next((s for s in config['sources'] if s['id'] == 'chinhsachonline_vn'), None)
    if not source:
        print("Source chinhsachonline_vn not found in config")
        return
        
    process(source, config.get('global_settings', {}))

if __name__ == "__main__":
    main()
