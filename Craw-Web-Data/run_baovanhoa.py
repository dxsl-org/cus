import json, sys
sys.stdout.reconfigure(encoding='utf-8')
from scrapers.baovanhoa import process

def main():
    with open('scraping_config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # Find Báo Văn Hóa config
    source = next((s for s in config['sources'] if s['id'] == 'baovanhoa_vn'), None)
    
    if not source:
        print("Source baovanhoa_vn not found in scraping_config.json")
        return
        
    global_settings = config.get('global_settings', {})
    
    print(f"Starting Báo Văn Hóa re-scan...")
    process(source, global_settings)

if __name__ == "__main__":
    main()
