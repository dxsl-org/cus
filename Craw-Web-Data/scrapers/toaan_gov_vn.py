"""
Court Judgments Scraper for congbobanan.toaan.gov.vn
Strategy: Sequential ID enumeration with concurrent workers.
URL pattern: /2ta{ID}t1cvn/chi-tiet-ban-an (detail) and /3ta{ID}t1cvn/ (PDF)
"""
import os
import sys
import json
import re
import time
import traceback
import concurrent.futures
import fitz  # PyMuPDF
import bs4
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = 'https://congbobanan.toaan.gov.vn'
DETAIL_URL = BASE_URL + '/2ta{id}t1cvn/chi-tiet-ban-an'
PDF_URL = BASE_URL + '/3ta{id}t1cvn/'

def sanitize_filename(text):
    if not text:
        return "Untitled"
    text = re.sub(r'[\\/:*?"<>|]', '_', text)
    text = re.sub(r'\s+', '_', text).strip('_')
    return text[:150]

def extract_pdf_text(pdf_content):
    try:
        doc = fitz.open(stream=pdf_content, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        return text.strip()
    except Exception as e:
        return ""

def get_metadata(soup):
    metadata = {}
    
    # Method 1: Look for labeled info areas
    info_divs = soup.select('.info_pharse_pub label, .list-group-item label')
    for label in info_divs:
        key = label.get_text().strip().rstrip(':').strip()
        # Try next sibling text
        val = ""
        ns = label.next_sibling
        if ns:
            val = ns.get_text().strip() if hasattr(ns, 'get_text') else str(ns).strip()
        if not val:
            parent = label.parent
            if parent:
                full_text = parent.get_text()
                val = full_text.replace(label.get_text(), '').strip().rstrip(':').strip()
        if key and val and len(val) < 300:
            metadata[key] = val
    
    # Method 2: Look for general table rows
    for tr in soup.select('table tr'):
        tds = tr.find_all('td')
        if len(tds) == 2:
            key = tds[0].get_text().strip().rstrip(':').strip()
            val = tds[1].get_text().strip()
            if key and val and len(val) < 300:
                metadata[key] = val
    
    return metadata

from scrapers.storage_utils import get_storage

def scrape_judgment(judgment_id, session, storage):
    """Scrape a single judgment by ID. Returns (success, judgment_id)."""
    detail_url = DETAIL_URL.format(id=judgment_id)
    pdf_url = PDF_URL.format(id=judgment_id)
    
    try:
        # 1. Fetch detail page
        resp = session.get(detail_url, timeout=30, verify=False)
        if resp.status_code != 200:
            return (False, judgment_id, f'HTTP {resp.status_code}')
        
        # Check if page has actual judgment content
        if len(resp.text) < 2000 or 'chi-tiet-ban-an' not in resp.text:
            return (False, judgment_id, 'empty')
        
        soup = bs4.BeautifulSoup(resp.text, 'html.parser')
        
        # Extract title
        title_el = soup.find('title')
        title = title_el.get_text().strip() if title_el else ''
        if not title or 'Danh sách' in title or 'Trang chủ' in title:
            return (False, judgment_id, 'not_judgment')
        
        # Extract metadata
        metadata = get_metadata(soup)
        
        # 2. Download PDF
        pdf_text = ""
        try:
            resp_pdf = session.get(pdf_url, timeout=60, verify=False)
            if resp_pdf.status_code == 200 and len(resp_pdf.content) > 500:
                pdf_text = extract_pdf_text(resp_pdf.content)
        except:
            pass
        
        # 3. Insert into SQLite
        content_body = f"## Nội dung văn bản\n\n{pdf_text}" if pdf_text else "*Không có nội dung PDF.*"
        
        success = storage.insert_article(
            url=detail_url,
            title=title,
            content=content_body,
            metadata={
                "pdf_url": pdf_url,
                "judgment_id": judgment_id,
                **metadata
            }
        )
        
        return (success, judgment_id, title[:60])
        
    except requests.exceptions.Timeout:
        return (False, judgment_id, 'timeout')
    except Exception as e:
        return (False, judgment_id, str(e)[:60])


def process(source, global_settings):
    progress_file = source.get('progress_file', f"progress_{source['id']}.json")
    
    # Initialize SQLite Storage
    storage = get_storage(source['id'])
    
    # Load progress
    processed_ids = set()
    failed_ids = set()
    last_id = 0
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
                processed_ids = set(progress.get('processed_ids', []))
                failed_ids = set(progress.get('failed_ids', []))
                last_id = progress.get('last_id', 0)
                print(f"Resuming. Processed: {len(processed_ids)}, Last ID: {last_id}")
        except:
            pass

    # Config
    start_id = max(last_id + 1, source.get('start_id', 1))
    end_id = source.get('end_id', 2100000)
    workers = source.get('workers', 8)
    batch_size = source.get('batch_size', 100)
    
    print(f"=== Court Judgments Scraper (Storage: SQLite) ===")
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    
    total_extracted = len(processed_ids)
    consecutive_empty = 0
    max_consecutive_empty = 500
    
    current_id = start_id
    while current_id <= end_id:
        batch_ids = []
        for i in range(batch_size):
            bid = current_id + i
            if bid > end_id:
                break
            if bid not in processed_ids and bid not in failed_ids:
                batch_ids.append(bid)
        
        if not batch_ids:
            current_id += batch_size
            continue
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(scrape_judgment, jid, session, storage): jid
                for jid in batch_ids
            }
            
            batch_success = 0
            batch_empty = 0
            for future in concurrent.futures.as_completed(futures):
                success, jid, info = future.result()
                if success:
                    processed_ids.add(jid)
                    total_extracted += 1
                    batch_success += 1
                    consecutive_empty = 0
                    if batch_success % 10 == 0: print(f"  [OK] Processed {total_extracted} judgments...")
                else:
                    if info in ('empty', 'not_judgment'):
                        batch_empty += 1
                        consecutive_empty += 1
                    else:
                        failed_ids.add(jid)
                        if info != 'timeout':
                            consecutive_empty += 1
        
        current_id += batch_size
        
        # Save progress every batch
        with open(progress_file, 'w', encoding='utf-8') as pf:
            json.dump({
                'processed_ids': list(processed_ids),
                'failed_ids': list(failed_ids)[-1000:],
                'last_id': current_id - 1,
                'total_extracted': total_extracted
            }, pf)
        
        if consecutive_empty >= max_consecutive_empty:
            print(f"  Stopping: {consecutive_empty} consecutive empty IDs.")
            break
        
        time.sleep(1)

    print(f"\n=== FINISHED Court Judgments Extraction ===")
    print(f"Total Extracted: {total_extracted}")
    print(f"Last ID: {current_id - 1}")
