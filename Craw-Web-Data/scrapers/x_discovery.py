import os
import json
import time
import requests
from scrapers.storage_utils import get_storage

def process(source, global_settings):
    """
    Main entry point for X (Twitter) data discovery.
    """
    storage = get_storage(source['id'])
    
    datasets = [
        {
            "name": "huggingface_minhtoan",
            "url": "https://huggingface.co/datasets/minhtoan/vietnamese-comment-sentiment/resolve/main/vietnamese-comment-sentiment.csv?download=true",
            "type": "csv",
            "mapping": {"Content": "content", "Sentiment": "category", "Title": "title", "URL": "url"}
        },
        {
            "name": "anotherpolarbear_train",
            "url": "https://huggingface.co/datasets/anotherpolarbear/vietnamese-sentiment-analysis/resolve/main/train.csv?download=true",
            "type": "csv",
            "mapping": {"comment": "content", "label": "category"}
        },
        {
            "name": "anotherpolarbear_test",
            "url": "https://huggingface.co/datasets/anotherpolarbear/vietnamese-sentiment-analysis/resolve/main/test.csv?download=true",
            "type": "csv",
            "mapping": {"comment": "content", "label": "category"}
        }
    ]
    
    for ds in datasets:
        print(f"Processing dataset: {ds['name']}")
        local_path = download_dataset(ds['url'], ds['name'], global_settings)
        if local_path:
            import_csv_dataset(local_path, storage, ds['mapping'], ds['name'])

def download_dataset(url, name, global_settings):
    try:
        os.makedirs("tmp/datasets", exist_ok=True)
        local_path = f"tmp/datasets/{name}.csv"
        
        print(f"  Downloading {url}...")
        # Use allow_redirects=True (default) and check history
        r = requests.get(url, stream=True, timeout=120, allow_redirects=True)
        if r.status_code == 200:
            with open(local_path, 'wb') as f:
                size = 0
                for chunk in r.iter_content(chunk_size=1024*1024): # 1MB chunks
                    if chunk:
                        f.write(chunk)
                        size += len(chunk)
                print(f"  Downloaded {size} bytes to {local_path}")
            return local_path
        else:
            print(f"  Download failed with status {r.status_code}")
    except Exception as e:
        print(f"  Download failed: {e}")
    return None

def import_csv_dataset(file_path, storage, mapping, source_name):
    import csv
    print(f"  Importing {file_path} to storage...")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            count = 0
            batch = []
            for row in reader:
                content = None
                for k, v in mapping.items():
                    if v == 'content':
                        content = row.get(k)
                
                if content:
                    # Clean content if needed
                    batch.append({
                        "url": f"dataset://{source_name}/{count}",
                        "title": f"Social Media Post {count}",
                        "content": content,
                        "category": row.get(mapping.get('sentiment', ''), 'Social'),
                        "tags": ["social-media", "historical", source_name],
                        "metadata": {"source_row": row}
                    })
                    count += 1
                    
                if len(batch) >= 100:
                    storage.insert_articles(batch)
                    batch = []
            
            if batch:
                storage.insert_articles(batch)
            print(f"  Imported {count} records from {source_name}")
    except Exception as e:
        print(f"  Import failed: {e}")
