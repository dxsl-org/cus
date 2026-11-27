import json
import os
import subprocess
import time
import sys

# Force UTF-8 for Windows console
sys.stdout.reconfigure(encoding='utf-8')

CONFIG_FILE = "scraping_config.json"
LOG_DIR = "logs"

def load_config():
    if not os.path.exists(CONFIG_FILE):
        print(f"Config file {CONFIG_FILE} not found.")
        return None
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def run_scraper(source, global_settings):
    source_id = source.get('id')
    source_name = source.get('name')
    
    # Determine the script to run
    # Priority 1: source.plugin (new format)
    # Priority 2: source.type (old format)
    source_type = source.get('plugin') or source.get('type')
    
    if not source_type:
        print(f"No plugin/type defined for {source_name}. Skipping.")
        return

    # Check if a standalone run_*.py exists for this source
    run_script = f"run_{source_id}.py"
    if not os.path.exists(run_script):
        # Fallback to general scraper.py logic if specialized run_*.py doesn't exist
        # But we'll mostly use the run_*.py scripts if they exist
        run_script = "scraper.py"
    
    log_file_path = os.path.join(LOG_DIR, f"{source_id}.log")
    print(f"Starting {source_name} ({source_id}) using {run_script}... Log: {log_file_path}")
    
    try:
        # Start the process in the background with redirection
        # We use shell=True and shell redirection to ensure output is captured in the log file.
        # This avoiding the file handle issues with CREATE_NEW_CONSOLE on Windows.
        # We use 'start /B' to run it backgrounded within the same console session usually, 
        # or just simple shell redirection which works well with Popen.
        cmd = f'{sys.executable} {run_script} > "{log_file_path}" 2>&1'
        process = subprocess.Popen(
            cmd,
            shell=True,
            cwd=os.getcwd()
        )
        return process
    except Exception as e:
        print(f"Failed to start {source_name}: {e}")
        return None

def main():
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)
        
    config = load_config()
    if not config:
        return
        
    global_settings = config.get('global_settings', {})
    sources = config.get('sources', [])
    
    active_processes = []
    
    for source in sources:
        # Check both old 'status' and new 'enabled' flags
        is_active = source.get('status') == 'active'
        is_enabled = source.get('enabled') is True
        
        if is_active or is_enabled:
            proc = run_scraper(source, global_settings)
            if proc:
                active_processes.append((source.get('id'), proc))
            # Small delay to prevent CPU spike
            time.sleep(1)
            
    print(f"\nStarted {len(active_processes)} scraper processes.")
    print("Check the 'logs/' directory for output.")

if __name__ == "__main__":
    main()
