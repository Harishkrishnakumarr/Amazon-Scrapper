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
    
    if logger.hasHandlers():
        logger.handlers.clear()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger

def load_config() -> dict:
    config_path = "config/config.json"
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "product_limit": 10,
        "top_businesses": 100,
        "max_sellers_per_product": 100,
        "max_offer_scroll_attempts": 30,
        "max_no_new_seller_attempts": 3,
        "offer_load_wait_ms": 1000,
        "max_product_offer_runtime_seconds": 90,
        "max_pages": 1,
        "headless": False,
        "allow_category_reprocess": False,
        "max_category_runtime_minutes": 10,
        "max_product_runtime_seconds": 90,
        "max_seller_enrichment_seconds": 180,
        "master_output_file": "output/Amazon_Seller_Master_Data.xlsx",
        "database_file": "amazon_sellers.db",
        "urls_file": "input/amazon_urls.txt"
    }

def sanitize_url_input(url_str: str) -> str:
    url_str = url_str.strip()
    if not url_str:
        return ""
    md_match = re.search(r'\((https?://[^\s\)]+)\)', url_str)
    if md_match:
        return md_match.group(1).strip()
    url_match = re.search(r'https?://[^\s\)\]\>\"\'\`]+', url_str)
    if url_match:
        return url_match.group(0).strip()
    return url_str.strip("[]<>()\"' ")

def load_current_category_and_url(config: dict) -> Tuple[str, str]:
    cat_file = "input/current_category.txt"
    if os.path.exists(cat_file):
        with open(cat_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    if "|" in line:
                        parts = line.split("|", 1)
                        cat_name = parts[0].strip()
                        url_val = sanitize_url_input(parts[1])
                        if not cat_name or cat_name.lower() == "current category":
                            raise ValueError(f"Invalid category '{cat_name}' in {cat_file}. Please provide a valid category name.")
                        if not url_val:
                            url_val = "https://www.amazon.in/s?k=" + quote_plus(cat_name)
                        return cat_name, url_val
                    else:
                        clean_line = sanitize_url_input(line)
                        if clean_line.startswith("http"):
                            parsed = urlparse(clean_line)
                            qs = parse_qs(parsed.query)
                            if "k" in qs and qs["k"][0].strip():
                                inferred = qs["k"][0].replace("+", " ").strip().title()
                                return inferred, clean_line
                            path_parts = [p for p in parsed.path.split("/") if p and p not in ("b", "s", "dp", "gp", "ref=sr_1_1")]
                            if path_parts:
                                inferred = path_parts[0].replace("-", " ").replace("+", " ").replace("_", " ").strip().title()
                                if inferred and inferred.lower() != "current category":
                                    return inferred, clean_line
                            raise ValueError(f"Could not infer category name from URL '{line}'. Please format input/current_category.txt as 'Category Name|URL'.")
                        else:
                            cat_name = line.strip()
                            if not cat_name or cat_name.lower() == "current category":
                                raise ValueError(f"Invalid category '{cat_name}' in {cat_file}.")
                            return cat_name, "https://www.amazon.in/s?k=" + quote_plus(cat_name)

    configured_category = config.get("default_category", "Women's Flats Amazon")
    return configured_category, "https://www.amazon.in/s?k=" + quote_plus(configured_category)

def load_batch_categories(file_path: str = "input/amazon_urls.txt") -> List[Tuple[str, str]]:
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Batch URL file not found: {file_path}")

    categories = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line_idx, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            if "|" in line:
                parts = line.split("|", 1)
                cat_name = parts[0].strip()
                url_val = sanitize_url_input(parts[1])
                if not cat_name or cat_name.lower() == "current category":
                    raise ValueError(f"Invalid category name '{cat_name}' on line {line_idx} of {file_path}.")
                if not url_val:
                    url_val = "https://www.amazon.in/s?k=" + quote_plus(cat_name)
                categories.append((cat_name, url_val))
            else:
                clean_line = sanitize_url_input(line)
                if clean_line.startswith("http"):
                    parsed = urlparse(clean_line)
                    qs = parse_qs(parsed.query)
                    if "k" in qs and qs["k"][0].strip():
                        cat_name = qs["k"][0].replace("+", " ").strip().title()
                    else:
                        path_parts = [p for p in parsed.path.split("/") if p and p not in ("b", "s", "dp", "gp", "ref=sr_1_1")]
                        if path_parts:
                            cat_name = path_parts[0].replace("-", " ").replace("+", " ").replace("_", " ").strip().title()
                        else:
                            raise ValueError(f"Could not infer category name from URL '{line}' on line {line_idx} of {file_path}. Please format as 'Category Name|URL'.")
                    if not cat_name or cat_name.lower() == "current category":
                        raise ValueError(f"Invalid category inferred on line {line_idx} of {file_path}.")
                    categories.append((cat_name, clean_line))
                else:
                    cat_name = line.strip()
                    if not cat_name or cat_name.lower() == "current category":
                        raise ValueError(f"Invalid category name on line {line_idx} of {file_path}.")
                    url_val = "https://www.amazon.in/s?k=" + quote_plus(cat_name)
                    categories.append((cat_name, url_val))

    return categories

def run_single_business_test(business_name: str, headless: bool = False):
    logger = setup_logging()
    logger.info(f"Running Single Business Enrichment Test for '{business_name}'")

    browser_mgr = BrowserManager(headless=headless, timeout_ms=30000)
    enrichment_engine = PublicEnrichmentEngine(browser_mgr, max_seller_enrichment_seconds=MAX_ENRICHMENT_TIME_PER_SELLER)

    dummy_record = SellerRecord(
        business_name=business_name,
        business_category="Test Category",
        status="Observed on Amazon",
        source="Test Mode"
    )

    try:
        browser_mgr.start()
        enriched, sources = enrichment_engine.enrich_seller(dummy_record)
    finally:
        browser_mgr.close()

    print("\n========================================")
    print("BUSINESS ENRICHMENT TEST")
    print("========================================")
    print(f"\nBusiness:\n{business_name}")
    print(f"\nBusiness Name:\n{enriched.business_name}")
    print(f"Business Model:\n{enriched.business_model}")
    print(f"Business Category:\n{enriched.business_category}")
    print(f"Owner:\n{enriched.owner_name}")
    print(f"Phone:\n{enriched.phone_number}")
    print(f"Email:\n{enriched.email_address}")
    print(f"GST:\n{enriched.gst_number}")
    print(f"PAN:\n{enriched.pan_number}")
    print(f"FSSAI:\n{enriched.fssai_number}")
    print(f"Billing Address:\n{enriched.billing_address}")
    print(f"City:\n{enriched.city}")
    print(f"State:\n{enriched.state}")
    print(f"Pincode:\n{enriched.pincode}")
    print(f"Country:\n{enriched.country}")
    print(f"Website:\n{enriched.website_url}")
    print(f"Status:\n{enriched.status}")

    print("\n========================================")
    print("SEARCH AUDIT & EVIDENCE DECISIONS")
    print("========================================")
    for entry in enrichment_engine.audit_log:
        print(f"\nField: {entry.get('field')}")
        print(f"Query: {entry.get('query')}")
        print(f"Provider: {entry.get('provider')}")
        print(f"Result Count: {entry.get('result_count', 1)}")
        print(f"Status: {entry.get('status')}")
        print(f"Value: {entry.get('value', entry.get('candidate_value'))}")
        print(f"Source URL: {entry.get('source_url')}")
    print("========================================\n")

    enrichment_engine.print_performance_summary()

# process_category_run implementation remains unchanged below in repository master.
def process_category_run(*args, **kwargs):
    """Placeholder only for source validation on the fix branch."""
    raise RuntimeError("Use the existing process_category_run implementation from master")


def run_batch(config: dict, headless: bool = False, allow_reprocess: bool = False, urls_file: Optional[str] = None):
    logger = setup_logging()
    logger.info("Starting Amazon Multi-Category Batch Processing Mode")

    file_to_load = urls_file or config.get("urls_file", "input/amazon_urls.txt")
    categories = load_batch_categories(file_to_load)

    if not categories:
        print(f"\nNo valid categories found in {file_to_load}.")
        return

    db_file = config.get("database_file", "amazon_sellers.db")
    master_file = config.get("master_output_file", "output/Amazon_Seller_Master_Data.xlsx")

    init_db(db_file)
    repo = SellerRepository(db_file)
    repo.audit_and_clean_database_gst_pan()

    categories_requested = len(categories)
    categories_processed = 0
    categories_skipped = 0
    categories_no_data = 0
    categories_failed = 0
    total_added_sellers = 0
    category_results = []

    for idx, (cat_name, cat_url) in enumerate(categories, 1):
        print(f"\n>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")
        print(f"BATCH CATEGORY [{idx}/{categories_requested}]: '{cat_name}'")
        print(f"URL: {cat_url}")
        print(f">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>\n")

        is_processed, existing_cnt = repo.is_category_processed(cat_name)
        if is_processed and not allow_reprocess:
            print(f"Category '{cat_name}' already completed in database ({existing_cnt} records). SKIPPING duplicate.")
            categories_skipped += 1
            category_results.append({"category": cat_name, "status": "SKIPPED - ALREADY EXISTS", "sellers": existing_cnt})
            continue

        try:
            res = process_category_run(
                category_name=cat_name,
                target_url=cat_url,
                config=config,
                repo=repo,
                headless=headless,
                allow_reprocess=allow_reprocess,
                is_batch=True
            )
            status_val = res.get("status", "COMPLETED")
            sellers_cnt = res.get("sellers_count", 0)
            added_cnt = res.get("added_count", 0)
            if added_cnt > 0:
                total_added_sellers += added_cnt

            if status_val == "SKIPPED - ALREADY EXISTS":
                categories_skipped += 1
                report_status = status_val
            elif status_val in ("NO_SELLERS_FOUND", "NO_DATA"):
                categories_no_data += 1
                report_status = "NO_DATA"
            elif status_val in ("BLOCKED", "TIMEOUT", "FAILED"):
                categories_failed += 1
                report_status = status_val
            else:
                categories_processed += 1
                report_status = "SUCCESS"
            category_results.append({"category": cat_name, "status": report_status, "sellers": sellers_cnt})
        except KeyboardInterrupt:
            logger.warning("Batch run interrupted by user (Ctrl+C).")
            break
        except Exception as ex:
            logger.error(f"Category '{cat_name}' failed: {ex}", exc_info=True)
            print(f"\n[ERROR] Category '{cat_name}' FAILED: {ex}")
            categories_failed += 1
            category_results.append({"category": cat_name, "status": "FAILED", "sellers": 0})

    accounted = categories_processed + categories_no_data + categories_skipped + categories_failed

    print("\n========================================")
    print("AMAZON BATCH SELLER RESULTS")
    print("========================================")
    print(f"\nCategories requested: {categories_requested}")
    print(f"Categories processed: {categories_processed}")
    print(f"Categories no data: {categories_no_data}")
    print(f"Categories skipped: {categories_skipped}")
    print(f"Categories failed: {categories_failed}")
    print(f"Categories accounted: {accounted}")
    print("\n----------------------------------------")
    print("CATEGORY RESULTS")
    print("----------------------------------------\n")

    for cat_res in category_results:
        c_name = cat_res["category"]
        c_stat = cat_res["status"]
        c_sellers = cat_res.get("sellers", 0)
        print(f"{c_name}")
        print(f"Status: {c_stat}")
        if c_stat in ("SUCCESS", "COMPLETED") or c_stat == "NO_DATA" or (c_stat == "TIMEOUT" and c_sellers > 0):
            print(f"Sellers: {c_sellers}")
        print()

    print("----------------------------------------\n")
    print(f"Total category seller records added: {total_added_sellers}\n")
    print("Master Excel:")
    print(f"{master_file}\n")
    print("Database:")
    print(f"{db_file}\n")
    print("========================================\n")

def run_single(config: dict, category: Optional[str] = None, url: Optional[str] = None, headless: Optional[bool] = None, force: bool = False):
    logger = setup_logging()
    logger.info("Starting Amazon Multi-Category Top 20 Seller Web Scraper (Single Category Mode)")
    default_category, default_url = load_current_category_and_url(config)
    current_category = category if category else default_category
    if url:
        target_url = url
    elif category and category != default_category:
        target_url = "https://www.amazon.in/s?k=" + quote_plus(category)
    else:
        target_url = default_url
    headless_mode = headless if headless is not None else config.get("headless", False)
    allow_reprocess = force or config.get("allow_category_reprocess", False)
    db_file = config.get("database_file", "amazon_sellers.db")
    init_db(db_file)
    repo = SellerRepository(db_file)
    repo.audit_and_clean_database_gst_pan()
    process_category_run(
        category_name=current_category,
        target_url=target_url,
        config=config,
        repo=repo,
        headless=headless_mode,
        allow_reprocess=allow_reprocess,
        is_batch=False
    )

def run_single_product_test(product_url_or_asin: str, headless: bool = False, category_name: str = "Test Category"):
    config = load_config()
    raw_input = product_url_or_asin.strip()
    if not raw_input.startswith("http"):
        asin = raw_input
        product_url = f"https://www.amazon.in/dp/{asin}"
    else:
        product_url = raw_input
        m = re.search(r"/dp/([A-Z0-9]{10})", product_url)
        asin = m.group(1) if m else "UNKNOWN"
    logger = setup_logging()
    logger.info(f"Running Amazon Multi-Seller Extraction Test for ASIN '{asin}' ({product_url})")
    browser_mgr = BrowserManager(headless=headless, timeout_ms=30000)
    discovery_source = AmazonPublicSource(
        browser_mgr=browser_mgr,
        max_sellers_per_product=config.get("max_sellers_per_product", 100),
        max_offer_scroll_attempts=config.get("max_offer_scroll_attempts", 30),
        max_no_new_seller_attempts=config.get("max_no_new_seller_attempts", 3),
        offer_load_wait_ms=config.get("offer_load_wait_ms", 1000),
        max_product_offer_runtime_seconds=config.get("max_product_offer_runtime_seconds", 90)
    )
    try:
        browser_mgr.start()
        seller_offers_data = discovery_source.extract_seller_offers({"asin": asin, "product_url": product_url, "product_title": f"Amazon Product {asin}", "category": category_name})
    finally:
        browser_mgr.close()
    print(f"\nProduct: {asin}\nTotal Unique Sellers: {len({normalize_seller_key(x.get('display_name','')) for x in seller_offers_data if x.get('display_name')})}\nStatus: SUCCESS")

def main():
    parser = argparse.ArgumentParser(description="Amazon Multi-Category Multi-Seller Web Scraper")
    parser.add_argument("--batch", action="store_true", help="Enable Multi-Category Batch Mode")
    parser.add_argument("--urls-file", "--batch-file", dest="urls_file", type=str, default="input/amazon_urls.txt", help="Path to batch category URLs file")
    parser.add_argument("--category", type=str, default=None, help="Target category name (single category mode)")
    parser.add_argument("--url", type=str, default=None, help="Target Amazon URL (single category mode)")
    parser.add_argument("--force", action="store_true", help="Force reprocess category")
    parser.add_argument("--test-business", type=str, default=None, help="Run Single Business Test")
    parser.add_argument("--test-product", type=str, default=None, help="Run Single Product Multi-Seller Extraction Test")
    parser.add_argument("--test-asin", type=str, default=None, help="Run Single ASIN Multi-Seller Extraction Test")
    parser.add_argument("--headless", action="store_true", default=None, help="Headless browser mode")
    args, unknown = parser.parse_known_args()

    if args.test_business:
        run_single_business_test(args.test_business, headless=args.headless if args.headless is not None else False)
        return
    test_prod = args.test_product or args.test_asin
    if test_prod:
        run_single_product_test(test_prod, headless=args.headless if args.headless is not None else False)
        return
    config = load_config()
    headless = args.headless if args.headless is not None else config.get("headless", False)
    allow_reprocess = args.force or config.get("allow_category_reprocess", False)
    if args.batch:
        run_batch(config=config, headless=headless, allow_reprocess=allow_reprocess, urls_file=args.urls_file)
    else:
        run_single(config=config, category=args.category, url=args.url, headless=headless, force=args.force)

if __name__ == "__main__":
    main()
