import json, sys
sys.stdout.reconfigure(encoding='utf-8')
import os
from scrapers import nhandan

def main():
    config_file = 'scraping_config.json'
    with open(config_file, 'r', encoding='utf-8') as f:
        config = json.load(f)
        
    global_settings = config.get('global_settings', {})
    
    # Find Nhan Dan source
    source = next((s for s in config['sources'] if s['id'] == 'nhandan_vn'), None)
    
    if not source:
        # Fallback/Default if not in config yet
        source = {
            "id": "nhandan_vn",
            "name": "Báo Nhân Dân",
            "url": "https://nhandan.vn"
        }
        
    # Ensure dataset directory exists
    if not os.path.exists('dataset'):
        os.makedirs('dataset')
        
    nhandan.process(source, global_settings)

if __name__ == "__main__":
    main()
