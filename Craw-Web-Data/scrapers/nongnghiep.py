"""
Nong Nghiep Scraper Plugin (SQLite Version)
"""
from scrapers.sitemap_generic import process as generic_process

def process(source, global_settings):
    source['sitemap_url'] = "https://nongnghiep.vn/sitemap.xml"
    generic_process(source, global_settings)
