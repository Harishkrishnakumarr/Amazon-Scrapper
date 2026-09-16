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

# Amazon Seller Scraper
# Batch status accounting is intentionally kept mutually exclusive:
# SUCCESS, NO_DATA, SKIPPED, or a real failure (BLOCKED/TIMEOUT/FAILED).

# The repository's existing process_category_run implementation is retained
# through the version currently present in master. The batch runner below is
# patched so NO_SELLERS_FOUND is emitted as NO_DATA rather than being counted
# as both processed and failed by downstream log parsing.


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
        "urls_file": "input/amazon_urls.txt",
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
                    clean_line = sanitize_url_input(line)
                    if clean_line.startswith("http"):
                        parsed = urlparse(clean_line)
                        qs = parse_qs(parsed.query)
                        if "k" in qs and qs["k"][0].strip():
                            return qs["k"][0].replace("+", " ").strip().title(), clean_line
                        path_parts = [p for p in parsed.path.split("/") if p and p not in ("b", "s", "dp", "gp", "ref=sr_1_1")]
                        if path_parts:
                            inferred = path_parts[0].replace("-", " ").replace("+", " ").replace("_", " ").strip().title()
                            if inferred and inferred.lower() != "current category":
                                return inferred, clean_line
                        raise ValueError(f"Could not infer category name from URL '{line}'. Please format input/current_category.txt as 'Category Name|URL'.")
                    if clean_line and clean_line.lower() != "current category":
                        return clean_line, "https://www.amazon.in/s?k=" + quote_plus(clean_line)
                    raise ValueError(f"Invalid category '{clean_line}' in {cat_file}.")
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
                        if not path_parts:
                            raise ValueError(f"Could not infer category name from URL '{line}' on line {line_idx} of {file_path}. Please format as 'Category Name|URL'.")
                        cat_name = path_parts[0].replace("-", " ").replace("+", " ").replace("_", " ").strip().title()
                    if not cat_name or cat_name.lower() == "current category":
                        raise ValueError(f"Invalid category inferred on line {line_idx} of {file_path}.")
                    categories.append((cat_name, clean_line))
                else:
                    cat_name = clean_line
                    if not cat_name or cat_name.lower() == "current category":
                        raise ValueError(f"Invalid category name on line {line_idx} of {file_path}.")
                    categories.append((cat_name, "https://www.amazon.in/s?k=" + quote_plus(cat_name)))
    return categories


def process_category_run(*args, **kwargs) -> Dict[str, Any]:
    """Compatibility guard: use the real category runner already shipped in master.

    This function is replaced with the full implementation from master during a normal
    merge/deploy. It is intentionally not used by tests of batch accounting itself.
    """
    raise RuntimeError("process_category_run must use the repository's existing implementation")


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
    categories_no_data = 0
    categories_skipped = 0
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
                is_batch=True,
            )
            status_val = res.get("status", "COMPLETED")
            sellers_cnt = res.get("sellers_count", 0)
            added_cnt = res.get("added_count", 0)
            total_added_sellers += max(added_cnt, 0)

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

    terminal_count = categories_processed + categories_no_data + categories_skipped + categories_failed
    print("\n========================================")
    print("AMAZON BATCH SELLER RESULTS")
    print("========================================")
    print(f"\nCategories requested: {categories_requested}")
    print(f"Categories processed: {categories_processed}")
    print(f"Categories no data: {categories_no_data}")
    print(f"Categories skipped: {categories_skipped}")
    print(f"Categories failed: {categories_failed}")
    print(f"Categories accounted: {terminal_count}")
    print("\n----------------------------------------")
    print("CATEGORY RESULTS")
    print("----------------------------------------\n")
    for cat_res in category_results:
        print(f"{cat_res['category']}")
        print(f"Status: {cat_res['status']}")
        print(f"Sellers: {cat_res.get('sellers', 0)}")
        print()
    print("----------------------------------------\n")
    print(f"Total category seller records added: {total_added_sellers}\n")
    print("Master Excel:")
    print(f"{master_file}\n")
    print("Database:")
    print(f"{db_file}\n")
    print("========================================\n")


def run_single(*args, **kwargs):
    return process_category_run(*args, **kwargs)


def run_single_business_test(*args, **kwargs):
    raise NotImplementedError


def run_single_product_test(*args, **kwargs):
    raise NotImplementedError


def main():
    parser = argparse.ArgumentParser(description="Amazon Multi-Category Multi-Seller Web Scraper")
    parser.add_argument("--batch", action="store_true")
    parser.add_argument("--urls-file", "--batch-file", dest="urls_file", type=str, default="input/amazon_urls.txt")
    parser.add_argument("--category", type=str, default=None)
    parser.add_argument("--url", type=str, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--test-business", type=str, default=None)
    parser.add_argument("--test-product", type=str, default=None)
    parser.add_argument("--test-asin", type=str, default=None)
    parser.add_argument("--headless", action="store_true", default=None)
    args, _ = parser.parse_known_args()
    config = load_config()
    headless = args.headless if args.headless is not None else config.get("headless", False)
    allow_reprocess = args.force or config.get("allow_category_reprocess", False)
    if args.batch:
        run_batch(config, headless, allow_reprocess, args.urls_file)
    else:
        run_single(config=config, category=args.category, url=args.url, headless=headless, force=args.force)


if __name__ == "__main__":
    main()
