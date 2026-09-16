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
    return {"product_limit": 10, "top_businesses": 100, "max_sellers_per_product": 100, "max_offer_scroll_attempts": 30, "max_no_new_seller_attempts": 3, "offer_load_wait_ms": 1000, "max_product_offer_runtime_seconds": 90, "max_pages": 1, "headless": False, "allow_category_reprocess": False, "max_category_runtime_minutes": 10, "max_product_runtime_seconds": 90, "max_seller_enrichment_seconds": 180, "master_output_file": "output/Amazon_Seller_Master_Data.xlsx", "database_file": "amazon_sellers.db", "urls_file": "input/amazon_urls.txt"}

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
                        parts = line.split("|", 1); cat_name = parts[0].strip(); url_val = sanitize_url_input(parts[1])
                        if not cat_name or cat_name.lower() == "current category": raise ValueError(f"Invalid category '{cat_name}' in {cat_file}. Please provide a valid category name.")
                        return cat_name, (url_val or "https://www.amazon.in/s?k=" + quote_plus(cat_name))
                    clean_line = sanitize_url_input(line)
                    if clean_line.startswith("http"):
                        parsed = urlparse(clean_line); qs = parse_qs(parsed.query)
                        if "k" in qs and qs["k"][0].strip(): return qs["k"][0].replace("+", " ").strip().title(), clean_line
                        path_parts = [p for p in parsed.path.split("/") if p and p not in ("b", "s", "dp", "gp", "ref=sr_1_1")]
                        if path_parts:
                            inferred = path_parts[0].replace("-", " ").replace("+", " ").replace("_", " ").strip().title()
                            if inferred and inferred.lower() != "current category": return inferred, clean_line
                        raise ValueError(f"Could not infer category name from URL '{line}'. Please format input/current_category.txt as 'Category Name|URL'.")
                    if clean_line and clean_line.lower() != "current category": return clean_line, "https://www.amazon.in/s?k=" + quote_plus(clean_line)
                    raise ValueError(f"Invalid category '{clean_line}' in {cat_file}.")
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
                parts = line.split("|", 1); cat_name = parts[0].strip(); url_val = sanitize_url_input(parts[1])
                if not cat_name or cat_name.lower() == "current category": raise ValueError(f"Invalid category name '{cat_name}' on line {line_idx} of {file_path}.")
                categories.append((cat_name, url_val or "https://www.amazon.in/s?k=" + quote_plus(cat_name)))
            else:
                clean_line = sanitize_url_input(line)
                if clean_line.startswith("http"):
                    parsed = urlparse(clean_line); qs = parse_qs(parsed.query)
                    if "k" in qs and qs["k"][0].strip(): cat_name = qs["k"][0].replace("+", " ").strip().title()
                    else:
                        path_parts = [p for p in parsed.path.split("/") if p and p not in ("b", "s", "dp", "gp", "ref=sr_1_1")]
                        if not path_parts: raise ValueError(f"Could not infer category name from URL '{line}' on line {line_idx} of {file_path}. Please format as 'Category Name|URL'.")
                        cat_name = path_parts[0].replace("-", " ").replace("+", " ").replace("_", " ").strip().title()
                    if not cat_name or cat_name.lower() == "current category": raise ValueError(f"Invalid category inferred on line {line_idx} of {file_path}.")
                    categories.append((cat_name, clean_line))
                else:
                    cat_name = clean_line
                    if not cat_name or cat_name.lower() == "current category": raise ValueError(f"Invalid category name on line {line_idx} of {file_path}.")
                    categories.append((cat_name, "https://www.amazon.in/s?k=" + quote_plus(cat_name)))
    return categories

def run_single_business_test(business_name: str, headless: bool = False):
    logger = setup_logging(); logger.info(f"Running Single Business Enrichment Test for '{business_name}'")
    browser_mgr = BrowserManager(headless=headless, timeout_ms=30000)
    enrichment_engine = PublicEnrichmentEngine(browser_mgr, max_seller_enrichment_seconds=MAX_ENRICHMENT_TIME_PER_SELLER)
    dummy_record = SellerRecord(business_name=business_name, business_category="Test Category", status="Observed on Amazon", source="Test Mode")
    try:
        browser_mgr.start(); enriched, sources = enrichment_engine.enrich_seller(dummy_record)
    finally: browser_mgr.close()
    print("\n========================================\nBUSINESS ENRICHMENT TEST\n========================================")
    print(f"\nBusiness:\n{business_name}\n\nBusiness Name:\n{enriched.business_name}\nBusiness Model:\n{enriched.business_model}\nBusiness Category:\n{enriched.business_category}\nOwner:\n{enriched.owner_name}\nPhone:\n{enriched.phone_number}\nEmail:\n{enriched.email_address}\nGST:\n{enriched.gst_number}\nPAN:\n{enriched.pan_number}\nFSSAI:\n{enriched.fssai_number}\nBilling Address:\n{enriched.billing_address}\nCity:\n{enriched.city}\nState:\n{enriched.state}\nPincode:\n{enriched.pincode}\nCountry:\n{enriched.country}\nWebsite:\n{enriched.website_url}\nStatus:\n{enriched.status}")
    print("\n========================================\nSEARCH AUDIT & EVIDENCE DECISIONS\n========================================")
    for entry in enrichment_engine.audit_log:
        print(f"\nField: {entry.get('field')}\nQuery: {entry.get('query')}\nProvider: {entry.get('provider')}\nResult Count: {entry.get('result_count', 1)}\nStatus: {entry.get('status')}\nValue: {entry.get('value', entry.get('candidate_value'))}\nSource URL: {entry.get('source_url')}")
    print("========================================\n"); enrichment_engine.print_performance_summary()

def process_category_run(category_name: str, target_url: str, config: dict, repo: SellerRepository, headless: bool = False, allow_reprocess: bool = False, is_batch: bool = False) -> Dict[str, Any]:
    logger = logging.getLogger("amazon_scraper")
    product_limit = config.get("product_limit", 10); top_businesses_limit = config.get("top_businesses", 100); max_pages = config.get("max_pages", 1)
    max_category_runtime_seconds = config.get("max_category_runtime_minutes", 10) * 60; max_seller_enrichment_seconds = config.get("max_seller_enrichment_seconds", 120)
    max_sellers_per_product = config.get("max_sellers_per_product", 100); max_offer_scroll_attempts = config.get("max_offer_scroll_attempts", 30)
    max_no_new_seller_attempts = config.get("max_no_new_seller_attempts", 3); offer_load_wait_ms = config.get("offer_load_wait_ms", 1000)
    max_product_offer_runtime_seconds = config.get("max_product_offer_runtime_seconds", 90); db_file = config.get("database_file", "amazon_sellers.db"); master_file = config.get("master_output_file", "output/Amazon_Seller_Master_Data.xlsx")
    is_processed, existing_cnt = repo.is_category_processed(category_name)
    if is_processed and not allow_reprocess:
        return {"status": "SKIPPED - ALREADY EXISTS", "category": category_name, "sellers_count": existing_cnt, "added_count": 0, "excel_result": {"status": "SKIPPED_ALREADY_EXISTS", "file_path": master_file, "existing_records": existing_cnt, "total_records": existing_cnt, "master_categories": 1, "added_count": 0}, "db_file": db_file, "master_file": master_file}
    category_run_id = repo.record_category_run_start(category_name); category_start_time = time.time()
    browser_mgr = BrowserManager(headless=headless, timeout_ms=30000)
    discovery_source = AmazonPublicSource(browser_mgr=browser_mgr, max_sellers_per_product=max_sellers_per_product, max_offer_scroll_attempts=max_offer_scroll_attempts, max_no_new_seller_attempts=max_no_new_seller_attempts, offer_load_wait_ms=offer_load_wait_ms, max_product_offer_runtime_seconds=max_product_offer_runtime_seconds)
    seller_candidates_dict: Dict[str, Dict[str, Any]] = {}; top_candidates = []; category_final_status = "COMPLETED"; insert_attempts = 0; updates_count = 0; save_success_cnt = 0; verified_success_cnt = 0; save_fail_cnt = 0; audit_sellers_list = []; lifecycle_trace_samples = []; enrichment_engine = None
    try:
        browser_mgr.start()
        try:
            products = discovery_source.discover_products(target_url, limit=product_limit, max_pages=max_pages, category_name=category_name)
        except AmazonBlockedException as abe:
            repo.update_category_run_status(category_run_id, category_name, "BLOCKED", 0, 0); logger.error(f"Category '{category_name}' BLOCKED by Amazon: {abe}")
            return {"status": "BLOCKED", "category": category_name, "sellers_count": 0, "added_count": 0, "db_file": db_file, "master_file": master_file}
        for idx, prod in enumerate(products, 1):
            if (time.time() - category_start_time) > max_category_runtime_seconds: category_final_status = "TIMEOUT"; break
            seller_offers_data = discovery_source.extract_seller_offers(prod)
            for offer_data in seller_offers_data or []:
                disp_name = offer_data.get("display_name")
                if not disp_name: continue
                record, sources = SellerExtractor.build_seller_record(offer_data, category=category_name); record.sub_sub_category = category_name; norm_key = normalize_seller_key(disp_name)
                if norm_key not in seller_candidates_dict: seller_candidates_dict[norm_key] = {"record": record, "sources": sources, "offer_data": offer_data, "prod": prod, "product_count": 1}
                else: seller_candidates_dict[norm_key]["product_count"] += 1
        candidate_list = list(seller_candidates_dict.values())
        for c in candidate_list:
            rec = c["record"]; c["score"] = (c["product_count"] * 2.0) + (1.5 if rec.seller_url else 0) + (1.0 if rec.phone_number != "Not Found" else 0) + (1.0 if rec.email_address != "Not Found" else 0)
        candidate_list.sort(key=lambda x: x["score"], reverse=True); top_candidates = candidate_list[:top_businesses_limit]
        enrichment_engine = PublicEnrichmentEngine(browser_mgr, max_seller_enrichment_seconds=max_seller_enrichment_seconds)
        for s_no, c_data in enumerate(top_candidates, 1):
            if (time.time() - category_start_time) > max_category_runtime_seconds: category_final_status = "TIMEOUT"; break
            record, sources, offer_data, prod = c_data["record"], c_data["sources"], c_data["offer_data"], c_data["prod"]; record.s_no = s_no; record.sub_sub_category = category_name
            try: enriched_record, extra_sources = enrichment_engine.enrich_seller(record)
            except TimeoutError: enriched_record, extra_sources = record, []
            except Exception as ex_enrich: logger.error(f"Error enriching seller '{record.business_name}': {ex_enrich}", exc_info=True); enriched_record, extra_sources = record, []
            enriched_record.s_no = s_no; enriched_record.sub_sub_category = category_name; insert_attempts += 1
            try:
                saved_record, is_new = repo.save_or_update_seller(enriched_record); save_success_cnt += 1; updates_count += 0 if is_new else 1; verified_rec = repo.get_seller_by_id(saved_record.id); verified_success_cnt += 1 if verified_rec and verified_rec.business_name not in ("Not Found", "Unknown", "") else 0
                for src in sources + extra_sources: src.seller_id = saved_record.id; repo.add_seller_source(src)
                repo.add_seller_offer(SellerOffer(seller_id=saved_record.id, asin=prod.get("asin"), product_url=prod.get("product_url"), product_title=prod.get("product_title", ""), category=category_name, seller_name=offer_data.get("display_name"), seller_profile_url=offer_data.get("seller_profile_url"), price=offer_data.get("price"), condition=offer_data.get("condition", "New"), source=offer_data.get("source", "Amazon")))
                audit_sellers_list.append({"business_name": enriched_record.business_name, "database_id": saved_record.id, "operation": "INSERT" if is_new else "UPDATE", "saved": True, "verified_after_save": bool(verified_rec), "phone": saved_record.phone_number, "email": saved_record.email_address, "gst": saved_record.gst_number, "pan": saved_record.pan_number, "website": saved_record.website_url, "status": saved_record.status})
            except Exception as ex: save_fail_cnt += 1; logger.error(f"Failed to persist seller '{enriched_record.business_name}': {ex}")
    except KeyboardInterrupt: raise
    except Exception as e: logger.error(f"Error during category run '{category_name}': {e}", exc_info=True); category_final_status = "FAILED"
    finally: browser_mgr.close()
    if category_final_status not in ("BLOCKED", "FAILED") and save_success_cnt > 0:
        final_category_sellers = repo.get_sellers_by_category(category_name)
        excel_result = export_sellers_to_master_excel(sellers=final_category_sellers, current_category=category_name, output_path=master_file, allow_reprocess=allow_reprocess)
    else:
        final_category_sellers = []
        excel_result = {"status": category_final_status if category_final_status in ("BLOCKED", "FAILED") else "NO_RECORDS_SAVED", "file_path": master_file, "existing_records": 0, "total_records": 0, "master_categories": 0, "added_count": 0}
    if category_final_status == "COMPLETED" and save_success_cnt == 0: category_final_status = "NO_SELLERS_FOUND"
    repo.update_category_run_status(category_run_id, category_name, category_final_status, len(top_candidates), len(final_category_sellers))
    return {"status": category_final_status, "category": category_name, "sellers_count": len(final_category_sellers), "added_count": excel_result.get("added_count", 0), "excel_result": excel_result, "db_file": db_file, "master_file": master_file}

def run_batch(config: dict, headless: bool = False, allow_reprocess: bool = False, urls_file: Optional[str] = None):
    logger = setup_logging(); file_to_load = urls_file or config.get("urls_file", "input/amazon_urls.txt"); categories = load_batch_categories(file_to_load)
    if not categories: print(f"\nNo valid categories found in {file_to_load}."); return
    db_file = config.get("database_file", "amazon_sellers.db"); master_file = config.get("master_output_file", "output/Amazon_Seller_Master_Data.xlsx"); init_db(db_file); repo = SellerRepository(db_file); repo.audit_and_clean_database_gst_pan()
    categories_requested = len(categories); categories_processed = 0; categories_skipped = 0; categories_no_data = 0; categories_failed = 0; total_added_sellers = 0; category_results = []
    for idx, (cat_name, cat_url) in enumerate(categories, 1):
        is_processed, existing_cnt = repo.is_category_processed(cat_name)
        if is_processed and not allow_reprocess:
            categories_skipped += 1; category_results.append({"category": cat_name, "status": "SKIPPED - ALREADY EXISTS", "sellers": existing_cnt}); continue
        try:
            res = process_category_run(cat_name, cat_url, config, repo, headless, allow_reprocess, True); status_val = res.get("status", "COMPLETED"); sellers_cnt = res.get("sellers_count", 0); added_cnt = res.get("added_count", 0); total_added_sellers += max(added_cnt, 0)
            if status_val == "SKIPPED - ALREADY EXISTS": categories_skipped += 1; category_results.append({"category": cat_name, "status": "SKIPPED - ALREADY EXISTS", "sellers": sellers_cnt})
            elif status_val in ("NO_SELLERS_FOUND", "NO_DATA"): categories_no_data += 1; category_results.append({"category": cat_name, "status": "NO_DATA", "sellers": sellers_cnt})
            elif status_val in ("BLOCKED", "TIMEOUT", "FAILED"): categories_failed += 1; category_results.append({"category": cat_name, "status": status_val, "sellers": sellers_cnt})
            else: categories_processed += 1; category_results.append({"category": cat_name, "status": "SUCCESS", "sellers": sellers_cnt})
        except KeyboardInterrupt: logger.warning("Batch run interrupted by user (Ctrl+C)."); break
        except Exception as ex: logger.error(f"Category '{cat_name}' failed: {ex}", exc_info=True); categories_failed += 1; category_results.append({"category": cat_name, "status": "FAILED", "sellers": 0})
    terminal_count = categories_processed + categories_no_data + categories_skipped + categories_failed
    if terminal_count != categories_requested: logger.warning(f"Category status accounting mismatch: requested={categories_requested} terminal={terminal_count}")
    print("\n========================================\nAMAZON BATCH SELLER RESULTS\n========================================")
    print(f"\nCategories requested: {categories_requested}\nCategories processed: {categories_processed}\nCategories no data: {categories_no_data}\nCategories skipped: {categories_skipped}\nCategories failed: {categories_failed}")
    print("\n----------------------------------------\nCATEGORY RESULTS\n----------------------------------------\n")
    for cat_res in category_results: print(f"{cat_res['category']}\nStatus: {cat_res['status']}\nSellers: {cat_res.get('sellers', 0)}\n")
    print("----------------------------------------\n"); print(f"Total category seller records added: {total_added_sellers}\n"); print("Master Excel:"); print(f"{master_file}\n"); print("Database:"); print(f"{db_file}\n"); print("========================================\n")

def run_single(config: dict, category: Optional[str] = None, url: Optional[str] = None, headless: Optional[bool] = None, force: bool = False):
    logger = setup_logging(); default_category, default_url = load_current_category_and_url(config); current_category = category if category else default_category; target_url = url if url else (("https://www.amazon.in/s?k=" + quote_plus(category)) if category and category != default_category else default_url); headless_mode = headless if headless is not None else config.get("headless", False); allow_reprocess = force or config.get("allow_category_reprocess", False); db_file = config.get("database_file", "amazon_sellers.db"); init_db(db_file); repo = SellerRepository(db_file); repo.audit_and_clean_database_gst_pan(); process_category_run(current_category, target_url, config, repo, headless_mode, allow_reprocess, False)

def run_single_product_test(product_url_or_asin: str, headless: bool = False, category_name: str = "Test Category"):
    logger = setup_logging(); config = load_config(); raw_input = product_url_or_asin.strip(); asin = raw_input if not raw_input.startswith("http") else ((re.search(r"/dp/([A-Z0-9]{10})", raw_input) or [None, "UNKNOWN"])[1]); product_url = raw_input if raw_input.startswith("http") else f"https://www.amazon.in/dp/{asin}"; logger.info(f"Running Amazon Multi-Seller Extraction Test for ASIN '{asin}' ({product_url})")
    max_sellers_per_product = config.get("max_sellers_per_product", 100); browser_mgr = BrowserManager(headless=headless, timeout_ms=30000); discovery_source = AmazonPublicSource(browser_mgr=browser_mgr, max_sellers_per_product=max_sellers_per_product, max_offer_scroll_attempts=config.get("max_offer_scroll_attempts", 30), max_no_new_seller_attempts=config.get("max_no_new_seller_attempts", 3), offer_load_wait_ms=config.get("offer_load_wait_ms", 1000), max_product_offer_runtime_seconds=config.get("max_product_offer_runtime_seconds", 90)); init_db(config.get("database_file", "amazon_sellers.db")); repo = SellerRepository(config.get("database_file", "amazon_sellers.db")); browser_mgr.start(); seller_offers_data = discovery_source.extract_seller_offers({"asin": asin, "product_url": product_url, "product_title": f"Amazon Product {asin}", "category": category_name}); browser_mgr.close(); unique_sellers_list = []; seen_keys = set()
    for off in seller_offers_data:
        disp_name = off.get("display_name");
        if not disp_name: continue
        norm_k = normalize_seller_key(disp_name)
        if norm_k not in seen_keys: seen_keys.add(norm_k); record, sources = SellerExtractor.build_seller_record(off, category=category_name); unique_sellers_list.append((record, sources, off))
    saved_records = []
    for s_no, (record, sources, off) in enumerate(unique_sellers_list, 1):
        record.s_no = s_no; saved_rec, _ = repo.save_or_update_seller(record); saved_records.append(saved_rec)
        for src in sources: src.seller_id = saved_rec.id; repo.add_seller_source(src)
    print(f"\nProduct: {asin}\nTotal Unique Sellers: {len(unique_sellers_list)}\nStatus: SUCCESS")

def main():
    parser = argparse.ArgumentParser(description="Amazon Multi-Category Multi-Seller Web Scraper"); parser.add_argument("--batch", action="store_true"); parser.add_argument("--urls-file", "--batch-file", dest="urls_file", type=str, default="input/amazon_urls.txt"); parser.add_argument("--category", type=str, default=None); parser.add_argument("--url", type=str, default=None); parser.add_argument("--force", action="store_true"); parser.add_argument("--test-business", type=str, default=None); parser.add_argument("--test-product", type=str, default=None); parser.add_argument("--test-asin", type=str, default=None); parser.add_argument("--headless", action="store_true", default=None); args, unknown = parser.parse_known_args();
    if args.test_business: run_single_business_test(args.test_business, headless=args.headless if args.headless is not None else False); return
    test_prod = args.test_product or args.test_asin
    if test_prod: run_single_product_test(test_prod, headless=args.headless if args.headless is not None else False); return
    config = load_config(); headless = args.headless if args.headless is not None else config.get("headless", False); allow_reprocess = args.force or config.get("allow_category_reprocess", False)
    if args.batch: run_batch(config, headless, allow_reprocess, args.urls_file)
    else: run_single(config, args.category, args.url, headless, args.force)

if __name__ == "__main__": main()
