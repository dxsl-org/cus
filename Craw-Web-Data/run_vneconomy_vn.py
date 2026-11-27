"""Run VnEconomy scraper standalone."""
import json, sys
sys.stdout.reconfigure(encoding='utf-8')

from scrapers.vneconomy_sitemap import process

with open("scraping_config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

source = next(s for s in config["sources"] if s["id"] == "vneconomy_vn")
process(source, config["global_settings"])
