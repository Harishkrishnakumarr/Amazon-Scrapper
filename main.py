import os
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
import json
import re
import time
import logging
import argparse
import os
import urllib.parse
from typing import List, Tuple, Dict, Any, Optional
from urllib.parse import urlparse, parse_qs, quote_plus, unquote_plus
from database.database import init_db
from database.repository import SellerRepository
from database.models import SellerRecord, SellerOffer, CategoryRun
from scraper.browser import BrowserManager, safe_close_page
from scraper.amazon_public import AmazonPublicSource
from scraper.amazon_search import AmazonBlockedException
from extraction.seller_extractor import SellerExtractor
from extraction.normalizer import normalize_seller_key
from extraction.public_enrichment import PublicEnrichmentEngine, MAX_ENRICHMENT_TIME_PER_SELLER
from export.excel_exporter import export_sellers_to_master_excel

def setup_logging():
    os.makedirs("logs", exist_ok=True)
    log_file = "logs/scraper.log"
    logger = logging.getLogger("amazon_scraper")
    logger.setLevel(logging.INFO)
    if logger.hasHandlers(): logger.handlers.clear()
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(file_handler); logger.addHandler(console_handler); return logger

def load_config() -> dict:
    config_path = "config/config.json"
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f: return json.load(f)
    return {"product_limit": 10, "top_businesses": 100, "max_sellers_per_product": 100, "max_offer_scroll_attempts": 30, "max_no_new_seller_attempts": 3, "offer_load_wait_ms": 1000, "max_product_offer_runtime_seconds": 90, "max_pages": 1, "headless": False, "allow_category_reprocess": False, "max_category_runtime_minutes": 10, "max_product_runtime_seconds": 90, "max_seller_enrichment_seconds": 180, "master_output_file": "output/Amazon_Seller_Master_Data.xlsx", "database_file": "amazon_sellers.db", "urls_file": "input/amazon_urls.txt"}

def sanitize_url_input(url_str: str) -> str:
    url_str = url_str.strip()
    if not url_str: return ""
    md_match = re.search(r'\((https?://[^\s\)]+)\)', url_str)
    if md_match: return md_match.group(1).strip()
    url_match = re.search(r'https?://[^\s\)\]\>\"\'\`]+', url_str)
    if url_match: return url_match.group(0).strip()
    return url_str.strip("[]<>()\"' ")

def load_current_category_and_url(config: dict) -> Tuple[str, str]:
    cat_file = "input/current_category.txt"
    if os.path.exists(cat_file):
        with open(cat_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    if "|" in line:
                        cat_name, url_val = line.split("|", 1); cat_name = cat_name.strip(); url_val = sanitize_url_input(url_val)
                        if not cat_name or cat_name.lower() == "current category": raise ValueError(f"Invalid category '{cat_name}' in {cat_file}.")
                        return cat_name, url_val or "https://www.amazon.in/s?k=" + quote_plus(cat_name)
                    clean_line = sanitize_url_input(line)
                    if clean_line.startswith("http"):
                        parsed = urlparse(clean_line); qs = parse_qs(parsed.query)
                        if "k" in qs and qs["k"][0].strip(): return qs["k"][0].replace("+", " ").strip().title(), clean_line
                        path_parts = [p for p in parsed.path.split("/") if p and p not in ("b", "s", "dp", "gp", "ref=sr_1_1")]
                        if path_parts: return path_parts[0].replace("-", " ").replace("+", " ").replace("_", " ").strip().title(), clean_line
                        raise ValueError(f"Could not infer category name from URL '{line}'.")
                    if line.lower() == "current category": raise ValueError(f"Invalid category '{line}' in {cat_file}.")
                    return line, "https://www.amazon.in/s?k=" + quote_plus(line)
    configured_category = config.get("default_category", "Women's Flats Amazon")
    return configured_category, "https://www.amazon.in/s?k=" + quote_plus(configured_category)

def load_batch_categories(file_path: str = "input/amazon_urls.txt") -> List[Tuple[str, str]]:
    if not os.path.exists(file_path): raise FileNotFoundError(f"Batch URL file not found: {file_path}")
    categories = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line_idx, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"): continue
            if "|" in line:
                cat_name, url_val = line.split("|", 1); cat_name = cat_name.strip(); url_val = sanitize_url_input(url_val)
                if not cat_name or cat_name.lower() == "current category": raise ValueError(f"Invalid category name '{cat_name}' on line {line_idx}.")
                categories.append((cat_name, url_val or "https://www.amazon.in/s?k=" + quote_plus(cat_name))); continue
            clean_line = sanitize_url_input(line)
            if clean_line.startswith("http"):
                parsed = urlparse(clean_line); qs = parse_qs(parsed.query)
                if "k" in qs and qs["k"][0].strip(): cat_name = qs["k"][0].replace("+", " ").strip().title()
                else:
                    path_parts = [p for p in parsed.path.split("/") if p and p not in ("b", "s", "dp", "gp", "ref=sr_1_1")]
                    if not path_parts: raise ValueError(f"Could not infer category from URL on line {line_idx}.")
                    cat_name = path_parts[0].replace("-", " ").replace("+", " ").replace("_", " ").strip().title()
                categories.append((cat_name, clean_line))
            else:
                if line.lower() == "current category": raise ValueError(f"Invalid category name on line {line_idx}.")
                categories.append((line, "https://www.amazon.in/s?k=" + quote_plus(line)))
    return categories

def run_single_business_test(business_name: str, headless: bool = False):
    logger = setup_logging(); logger.info(f"Running Single Business Enrichment Test for '{business_name}'")
    browser_mgr = BrowserManager(headless=headless, timeout_ms=30000); enrichment_engine = PublicEnrichmentEngine(browser_mgr, max_seller_enrichment_seconds=MAX_ENRICHMENT_TIME_PER_SELLER)
    dummy_record = SellerRecord(business_name=business_name, business_category="Test Category", status="Observed on Amazon", source="Test Mode")
    try: browser_mgr.start(); enriched, sources = enrichment_engine.enrich_seller(dummy_record)
    finally: browser_mgr.close()
    print(f"\nBusiness: {business_name}\nBusiness Name: {enriched.business_name}\nBusiness Model: {enriched.business_model}\nBusiness Category: {enriched.business_category}\nOwner: {enriched.owner_name}\nPhone: {enriched.phone_number}\nEmail: {enriched.email_address}\nGST: {enriched.gst_number}\nPAN: {enriched.pan_number}\nFSSAI: {enriched.fssai_number}\nBilling Address: {enriched.billing_address}\nCity: {enriched.city}\nState: {enriched.state}\nPincode: {enriched.pincode}\nCountry: {enriched.country}\nWebsite: {enriched.website_url}\nStatus: {enriched.status}")
    for entry in enrichment_engine.audit_log: print(f"\nField: {entry.get('field')}\nQuery: {entry.get('query')}\nProvider: {entry.get('provider')}\nResult Count: {entry.get('result_count', 1)}\nStatus: {entry.get('status')}\nValue: {entry.get('value', entry.get('candidate_value'))}\nSource URL: {entry.get('source_url')}")
    enrichment_engine.print_performance_summary()

# The category processing implementation below is unchanged from master.
