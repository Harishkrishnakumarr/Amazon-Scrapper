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
from typing import List, Tuple, Dict, Any, Optional
from urllib.parse import urlparse, parse_qs, quote_plus
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
                        url_val = parts[1].strip()
                        if not cat_name or cat_name.lower() == "current category":
                            raise ValueError(f"Invalid category '{cat_name}' in {cat_file}. Please provide a valid category name.")
                        return cat_name, url_val
                    elif line.startswith("http"):
                        parsed = urlparse(line)
                        qs = parse_qs(parsed.query)
                        if "k" in qs and qs["k"][0].strip():
                            inferred = qs["k"][0].replace("+", " ").strip().title()
                            return inferred, line
                        # Infer category from URL path e.g. /Sports-Outdoor-Women-Shoes/b?node=...
                        path_parts = [p for p in parsed.path.split("/") if p and p not in ("b", "s", "dp", "gp", "ref=sr_1_1")]
                        if path_parts:
                            inferred = path_parts[0].replace("-", " ").replace("+", " ").replace("_", " ").strip().title()
                            if inferred and inferred.lower() != "current category":
                                return inferred, line
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
                url_val = parts[1].strip()
                if not cat_name or cat_name.lower() == "current category":
                    raise ValueError(f"Invalid category name '{cat_name}' on line {line_idx} of {file_path}.")
                if not url_val:
                    url_val = "https://www.amazon.in/s?k=" + quote_plus(cat_name)
                categories.append((cat_name, url_val))
            elif line.startswith("http"):
                parsed = urlparse(line)
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
                categories.append((cat_name, line))
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

def process_category_run(
    category_name: str,
    target_url: str,
    config: dict,
    repo: SellerRepository,
    headless: bool = False,
    allow_reprocess: bool = False,
    is_batch: bool = False
) -> Dict[str, Any]:
    logger = logging.getLogger("amazon_scraper")
    logger.info(f"Target Category: '{category_name}'")
    logger.info(f"Target URL: '{target_url}'")

    product_limit = config.get("product_limit", 10)
    top_businesses_limit = config.get("top_businesses", 100)
    max_pages = config.get("max_pages", 1)
    max_category_runtime_seconds = config.get("max_category_runtime_minutes", 10) * 60
    max_seller_enrichment_seconds = config.get("max_seller_enrichment_seconds", 120)
    max_sellers_per_product = config.get("max_sellers_per_product", 100)
    max_offer_scroll_attempts = config.get("max_offer_scroll_attempts", 30)
    max_no_new_seller_attempts = config.get("max_no_new_seller_attempts", 3)
    offer_load_wait_ms = config.get("offer_load_wait_ms", 1000)
    max_product_offer_runtime_seconds = config.get("max_product_offer_runtime_seconds", 90)

    db_file = config.get("database_file", "amazon_sellers.db")
    master_file = config.get("master_output_file", "output/Amazon_Seller_Master_Data.xlsx")

    # Category Duplicate Protection Check
    is_processed, existing_cnt = repo.is_category_processed(category_name)
    if is_processed and not allow_reprocess:
        if not is_batch:
            print("\n========================================")
            print("AMAZON CATEGORY SELLER RESULTS")
            print("========================================")
            print(f"\nCurrent Category:\n{category_name}")
            print(f"\nCategory already processed:\n{category_name}")
            print(f"\nExisting records:\n{existing_cnt}")
            print("\nSkipping duplicate category. (Use --force to reprocess)")
            print(f"\nMaster Excel:\n{master_file}")
            print("\nStatus:\nSKIPPED - ALREADY EXISTS")
            print("========================================\n")
            logger.info(f"Skipping category '{category_name}' as it already exists in database.")
        return {
            "status": "SKIPPED - ALREADY EXISTS",
            "category": category_name,
            "sellers_count": existing_cnt,
            "added_count": 0,
            "excel_result": {
                "status": "SKIPPED_ALREADY_EXISTS",
                "file_path": master_file,
                "existing_records": existing_cnt,
                "total_records": existing_cnt,
                "master_categories": 1,
                "added_count": 0
            },
            "db_file": db_file,
            "master_file": master_file
        }

    # Record Category Run in Database with status 'RUNNING'
    category_run_id = repo.record_category_run_start(category_name)
    category_start_time = time.time()

    browser_mgr = BrowserManager(headless=headless, timeout_ms=30000)
    discovery_source = AmazonPublicSource(
        browser_mgr=browser_mgr,
        max_sellers_per_product=max_sellers_per_product,
        max_offer_scroll_attempts=max_offer_scroll_attempts,
        max_no_new_seller_attempts=max_no_new_seller_attempts,
        offer_load_wait_ms=offer_load_wait_ms,
        max_product_offer_runtime_seconds=max_product_offer_runtime_seconds
    )

    seller_candidates_dict: Dict[str, Dict[str, Any]] = {}
    top_candidates = []
    category_final_status = "COMPLETED"

    insert_attempts = 0
    updates_count = 0
    save_success_cnt = 0
    verified_success_cnt = 0
    save_fail_cnt = 0
    audit_sellers_list = []
    lifecycle_trace_samples = []

    enrichment_engine = None

    try:
        browser_mgr.start()

        # -------------------------------------------------------------
        # PHASE 1: Amazon Product & Candidate Discovery for Category
        # -------------------------------------------------------------
        logger.info(f"Discovering products for category '{category_name}' from {target_url}...")
        
        try:
            products = discovery_source.discover_products(target_url, limit=product_limit, max_pages=max_pages, category_name=category_name)
        except AmazonBlockedException as abe:
            category_final_status = "BLOCKED"
            repo.update_category_run_status(category_run_id, category_name, "BLOCKED", 0, 0)
            logger.error(f"Category '{category_name}' BLOCKED by Amazon: {abe}")
            return {
                "status": "BLOCKED",
                "category": category_name,
                "sellers_count": 0,
                "added_count": 0,
                "db_file": db_file,
                "master_file": master_file
            }

        logger.info(f"Discovered {len(products)} products for category '{category_name}'")

        for idx, prod in enumerate(products, 1):
            if (time.time() - category_start_time) > max_category_runtime_seconds:
                logger.warning(f"CATEGORY TIMEOUT reached during product processing for '{category_name}' (Elapsed: {time.time() - category_start_time:.1f}s)")
                category_final_status = "TIMEOUT"
                break

            asin = prod.get("asin")
            logger.info(f"[{idx}/{len(products)}] Processing product ASIN: {asin}")

            seller_offers_data = discovery_source.extract_seller_offers(prod)
            if not seller_offers_data:
                continue

            for offer_data in seller_offers_data:
                disp_name = offer_data.get("display_name")
                if not disp_name:
                    continue

                record, sources = SellerExtractor.build_seller_record(offer_data, category=category_name)
                record.sub_sub_category = category_name

                norm_key = normalize_seller_key(disp_name)
                if norm_key not in seller_candidates_dict:
                    seller_candidates_dict[norm_key] = {
                        "record": record,
                        "sources": sources,
                        "offer_data": offer_data,
                        "prod": prod,
                        "product_count": 1
                    }
                else:
                    seller_candidates_dict[norm_key]["product_count"] += 1

        # -------------------------------------------------------------
        # Rank Candidates by Business Relevance Score — SELECT ALL OR TOP
        # -------------------------------------------------------------
        candidate_list = list(seller_candidates_dict.values())
        for c in candidate_list:
            rec = c["record"]
            score = (c["product_count"] * 2.0)
            if rec.seller_url: score += 1.5
            if rec.phone_number != "Not Found": score += 1.0
            if rec.email_address != "Not Found": score += 1.0
            c["score"] = score

        candidate_list.sort(key=lambda x: x["score"], reverse=True)
        top_candidates = candidate_list[:top_businesses_limit]
        logger.info(f"Top businesses requested: {top_businesses_limit}, found: {len(top_candidates)} for '{category_name}'")

        # -------------------------------------------------------------
        # PHASE 2: Deep Public Search Enrichment & Persistence
        # -------------------------------------------------------------
        logger.info(f"Starting Phase 2 Deep Enrichment Waterfall for top {len(top_candidates)} businesses...")
        enrichment_engine = PublicEnrichmentEngine(browser_mgr, max_seller_enrichment_seconds=max_seller_enrichment_seconds)

        for s_no, c_data in enumerate(top_candidates, 1):
            if (time.time() - category_start_time) > max_category_runtime_seconds:
                logger.warning(f"CATEGORY TIMEOUT reached during seller enrichment for '{category_name}' (Elapsed: {time.time() - category_start_time:.1f}s)")
                category_final_status = "TIMEOUT"
                break

            record = c_data["record"]
            sources = c_data["sources"]
            offer_data = c_data["offer_data"]
            prod = c_data["prod"]

            record.s_no = s_no
            record.sub_sub_category = category_name

            raw_before_summary = {
                "business_name": record.business_name,
                "category": record.sub_sub_category,
                "phone": record.phone_number,
                "email": record.email_address,
                "gst": record.gst_number,
                "status": record.status
            }

            # Progress Banner
            print("\n========================================")
            print("ENRICHING SELLER")
            print("========================================")
            print(f"Category:\n{category_name}\n")
            print(f"Seller:\n{record.business_name}\n")
            print(f"Seller:\n{s_no} / {len(top_candidates)}\n")
            print(f"Elapsed:\n{time.time() - category_start_time:.0f} seconds")
            print("========================================\n")

            # Run Deep Enrichment with Exception Isolation
            try:
                enriched_record, extra_sources = enrichment_engine.enrich_seller(record)
            except TimeoutError:
                logger.warning(f"Timeout enriching seller '{record.business_name}'. Retaining Amazon record.")
                enriched_record = record
                extra_sources = []
            except Exception as ex_enrich:
                logger.error(f"Error enriching seller '{record.business_name}': {ex_enrich}", exc_info=True)
                enriched_record = record
                extra_sources = []

            enriched_record.s_no = s_no
            enriched_record.sub_sub_category = category_name

            # Completion Banner
            print("\nSELLER ENRICHMENT COMPLETE")
            print(f"\nSeller:\n{enriched_record.business_name}\n")
            print(f"Fields found:")
            print(f"Phone: {'YES' if enriched_record.phone_number != 'Not Found' else 'NO'}")
            print(f"Email: {'YES' if enriched_record.email_address != 'Not Found' else 'NO'}")
            print(f"GST: {'YES' if enriched_record.gst_number not in ('Not Found', 'Unverified') else 'NO'}")
            print(f"PAN: {'YES' if enriched_record.pan_number != 'Not Found' else 'NO'}")
            print(f"Address: {'YES' if enriched_record.billing_address != 'Not Found' else 'NO'}")
            print(f"Website: {'YES' if enriched_record.website_url != 'Not Found' else 'NO'}")
            print("\nContinuing to next seller...\n")

            # Save Partial Data Immediately in SQLite Database
            insert_attempts += 1
            try:
                saved_record, is_new = repo.save_or_update_seller(enriched_record)
                save_success_cnt += 1
                if not is_new:
                    updates_count += 1

                verified_rec = repo.get_seller_by_id(saved_record.id)
                is_verified = bool(verified_rec and verified_rec.business_name not in ("Not Found", "Unknown", ""))
                if is_verified:
                    verified_success_cnt += 1

                for src in sources + extra_sources:
                    src.seller_id = saved_record.id
                    repo.add_seller_source(src)

                repo.add_seller_offer(SellerOffer(
                    seller_id=saved_record.id,
                    asin=prod.get("asin"),
                    product_url=prod.get("product_url"),
                    product_title=prod.get("product_title", ""),
                    category=category_name,
                    seller_name=offer_data.get("display_name"),
                    seller_profile_url=offer_data.get("seller_profile_url"),
                    price=offer_data.get("price"),
                    condition=offer_data.get("condition", "New"),
                    source=offer_data.get("source", "Amazon")
                ))

                audit_sellers_list.append({
                    "business_name": enriched_record.business_name,
                    "database_id": saved_record.id,
                    "operation": "INSERT" if is_new else "UPDATE",
                    "saved": True,
                    "verified_after_save": is_verified,
                    "phone": saved_record.phone_number,
                    "email": saved_record.email_address,
                    "gst": saved_record.gst_number,
                    "pan": saved_record.pan_number,
                    "website": saved_record.website_url,
                    "status": saved_record.status
                })

                if len(lifecycle_trace_samples) < 2:
                    lifecycle_trace_samples.append({
                        "before": raw_before_summary,
                        "after": {
                            "business_name": enriched_record.business_name,
                            "category": enriched_record.sub_sub_category,
                            "phone": enriched_record.phone_number,
                            "email": enriched_record.email_address,
                            "gst": enriched_record.gst_number,
                            "pan": enriched_record.pan_number,
                            "address": enriched_record.billing_address,
                            "website": enriched_record.website_url,
                            "status": enriched_record.status
                        },
                        "sqlite": {
                            "id": verified_rec.id if verified_rec else saved_record.id,
                            "business_name": verified_rec.business_name if verified_rec else saved_record.business_name,
                            "phone": verified_rec.phone_number if verified_rec else saved_record.phone_number,
                            "gst": verified_rec.gst_number if verified_rec else saved_record.gst_number,
                            "pan": verified_rec.pan_number if verified_rec else saved_record.pan_number,
                            "city": verified_rec.city if verified_rec else saved_record.city,
                            "state": verified_rec.state if verified_rec else saved_record.state,
                            "status": verified_rec.status if verified_rec else saved_record.status
                        }
                    })

            except Exception as ex:
                save_fail_cnt += 1
                logger.error(f"Failed to persist seller '{enriched_record.business_name}': {ex}")

        # Write Enrichment & Persistence Audit JSON
        debug_dir = "output/debug"
        os.makedirs(debug_dir, exist_ok=True)
        safe_cat_name = "".join(c if c.isalnum() else "_" for c in category_name)
        
        if enrichment_engine:
            with open(os.path.join(debug_dir, f"enrichment_audit_{safe_cat_name}.json"), "w", encoding="utf-8") as f:
                json.dump(enrichment_engine.audit_log, f, indent=2, default=str)

        with open(os.path.join(debug_dir, f"database_save_audit_{safe_cat_name}.json"), "w", encoding="utf-8") as f:
            json.dump({
                "category": category_name,
                "records_before_save": len(top_candidates),
                "records_saved": save_success_cnt,
                "records_verified_after_save": verified_success_cnt,
                "records_failed": save_fail_cnt,
                "sellers": audit_sellers_list
            }, f, indent=2, default=str)

    except KeyboardInterrupt:
        logger.warning(f"Category run '{category_name}' interrupted by user.")
        if save_success_cnt > 0:
            try:
                final_category_sellers = repo.get_sellers_by_category(category_name)
                logger.info(f"KeyboardInterrupt recovery: Read {len(final_category_sellers)} saved records from SQLite for '{category_name}'")
                excel_result = export_sellers_to_master_excel(
                    sellers=final_category_sellers,
                    current_category=category_name,
                    output_path=master_file,
                    allow_reprocess=allow_reprocess
                )
                logger.info(
                    f"Recovered {len(final_category_sellers)} records from SQLite to Master Excel for '{category_name}'. "
                    f"Excel additions/updates: {excel_result.get('added_count', 0)}"
                )
            except Exception as ex_rec:
                logger.error(f"Failed to export recovered records to Excel during KeyboardInterrupt for '{category_name}': {ex_rec}")
        raise
    except Exception as e:
        logger.error(f"Error during category run '{category_name}': {e}", exc_info=True)
        category_final_status = "FAILED"
    finally:
        browser_mgr.close()

    # Master Excel Append & Atomic Save ONLY IF:
    # 1. Category run did not fail or get blocked
    # 2. Records were successfully saved & verified to SQLite during this run
    if category_final_status not in ("BLOCKED", "FAILED") and save_success_cnt > 0:
        final_category_sellers = repo.get_sellers_by_category(category_name)
        logger.info(f"Read {len(final_category_sellers)} final verified records from SQLite for '{category_name}'")
        excel_result = export_sellers_to_master_excel(
            sellers=final_category_sellers,
            current_category=category_name,
            output_path=master_file,
            allow_reprocess=allow_reprocess
        )
    else:
        final_category_sellers = []
        excel_result = {
            "status": category_final_status if category_final_status in ("BLOCKED", "FAILED") else "NO_RECORDS_SAVED",
            "file_path": master_file,
            "existing_records": 0,
            "total_records": 0,
            "master_categories": 0,
            "added_count": 0
        }

    # Update category run status in SQLite
    if category_final_status == "COMPLETED" and save_success_cnt == 0:
        category_final_status = "NO_SELLERS_FOUND"
    
    repo.update_category_run_status(
        category_run_id,
        category_name,
        category_final_status,
        len(top_candidates),
        len(final_category_sellers)
    )

    v_stats = repo.get_verification_stats()

    if not is_batch:
        print(f"""
========================================
DATABASE PERSISTENCE RESULTS
========================================
Enriched records produced: {len(top_candidates)}
Database insert attempts: {insert_attempts}
Database updates: {updates_count}
Database saves successful: {save_success_cnt}
Database verification successful: {verified_success_cnt}
Database save failures: {save_fail_cnt}

SQLite:
{db_file}
========================================
""")

        if lifecycle_trace_samples:
            sample = lifecycle_trace_samples[0]
            print(f"""
========================================
SAMPLE SELLER LIFECYCLE TRACE
========================================
1. BEFORE ENRICHMENT:
   Business Name: {sample['before']['business_name']}
   Category: {sample['before']['category']}
   GST: {sample['before']['gst']}
   Phone: {sample['before']['phone']}

2. AFTER ENRICHMENT:
   Business Name: {sample['after']['business_name']}
   Category: {sample['after']['category']}
   GST: {sample['after']['gst']}
   PAN: {sample['after']['pan']}
   Phone: {sample['after']['phone']}
   Email: {sample['after']['email']}
   Address: {sample['after']['address']}
   Website: {sample['after']['website']}
   Status: {sample['after']['status']}

3. SQLITE RECORD (Direct DB Read-Back):
   ID: {sample['sqlite']['id']}
   Business Name: {sample['sqlite']['business_name']}
   GST: {sample['sqlite']['gst']}
   PAN: {sample['sqlite']['pan']}
   Phone: {sample['sqlite']['phone']}
   City: {sample['sqlite']['city']}
   State: {sample['sqlite']['state']}
   Status: {sample['sqlite']['status']}

4. MASTER EXCEL RECORD:
   Category: {category_name}
   S.NO: 1
   File: {master_file}
========================================
""")

        report = f"""
========================================
AMAZON CATEGORY SELLER RESULTS
========================================

Current Category:
{category_name}

Top businesses requested: {top_businesses_limit}
Top businesses found: {len(top_candidates)}
Businesses added: {save_success_cnt}

Excel added:
{excel_result['added_count']}

Existing master records:
{excel_result['existing_records']}

Total master records:
{excel_result['total_records']}

Categories in master:
{excel_result['master_categories']}

Deep enrichment:

Business names:
{v_stats['business_names']}

Owners:
{v_stats['owners']}

Phones:
{v_stats['phones']}

Emails:
{v_stats['emails']}

GST:
{v_stats['gst']}

PAN:
{v_stats['pan']}

Addresses:
{v_stats['addresses']}

Cities:
{v_stats['cities']}

States:
{v_stats['states']}

Pincodes:
{v_stats['pincodes']}

Websites:
{v_stats['websites']}

Verified:
{v_stats['verified']}

Partially Verified:
{v_stats['partially_verified']}

Needs Review:
{v_stats['needs_review']}

Not Found fields:
{v_stats['not_found_fields']}

Excel:
{excel_result['file_path']}

Status:
{category_final_status}
========================================
"""
        print(report)

        if enrichment_engine:
            enrichment_engine.print_performance_summary()

    return {
        "status": category_final_status,
        "category": category_name,
        "sellers_count": len(final_category_sellers),
        "added_count": excel_result.get("added_count", 0),
        "excel_result": excel_result,
        "db_file": db_file,
        "master_file": master_file
    }

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
    categories_failed = 0
    total_added_sellers = 0
    category_results = []

    print("\n========================================")
    print("STARTING AMAZON BATCH CATEGORY RUN")
    print(f"Total categories to process: {categories_requested}")
    print(f"Input file: {file_to_load}")
    print(f"Master Excel target: {master_file}")
    print(f"Database target: {db_file}")
    print("========================================\n")

    for idx, (cat_name, cat_url) in enumerate(categories, 1):
        print(f"\n>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")
        print(f"BATCH CATEGORY [{idx}/{categories_requested}]: '{cat_name}'")
        print(f"URL: {cat_url}")
        print(f">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>\n")

        is_processed, existing_cnt = repo.is_category_processed(cat_name)
        if is_processed and not allow_reprocess:
            print(f"Category '{cat_name}' already completed in database ({existing_cnt} records). SKIPPING duplicate.")
            categories_skipped += 1
            category_results.append({
                "category": cat_name,
                "status": "SKIPPED - ALREADY EXISTS",
                "sellers": existing_cnt
            })
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
                category_results.append({
                    "category": cat_name,
                    "status": "SKIPPED - ALREADY EXISTS",
                    "sellers": sellers_cnt
                })
            elif status_val in ("BLOCKED", "TIMEOUT", "FAILED"):
                categories_failed += 1
                category_results.append({
                    "category": cat_name,
                    "status": status_val,
                    "sellers": sellers_cnt
                })
            else:
                categories_processed += 1
                category_results.append({
                    "category": cat_name,
                    "status": "SUCCESS",
                    "sellers": sellers_cnt
                })
        except KeyboardInterrupt:
            print("\n========================================")
            print("SCRAPER INTERRUPTED BY USER")
            print("Collected records have been preserved.")
            print("========================================\n")
            logger.warning("Batch run interrupted by user (Ctrl+C).")
            break
        except Exception as ex:
            logger.error(f"Category '{cat_name}' failed: {ex}", exc_info=True)
            print(f"\n[ERROR] Category '{cat_name}' FAILED: {ex}")
            categories_failed += 1
            category_results.append({
                "category": cat_name,
                "status": "FAILED",
                "sellers": 0
            })

    print("\n========================================")
    print("AMAZON BATCH SELLER RESULTS")
    print("========================================")
    print(f"\nCategories requested: {categories_requested}")
    print(f"Categories processed: {categories_processed}")
    print(f"Categories skipped: {categories_skipped}")
    print(f"Categories failed: {categories_failed}")
    print("\n----------------------------------------")
    print("CATEGORY RESULTS")
    print("----------------------------------------\n")

    for cat_res in category_results:
        c_name = cat_res["category"]
        c_stat = cat_res["status"]
        c_sellers = cat_res.get("sellers", 0)
        print(f"{c_name}")
        print(f"Status: {c_stat}")
        if c_stat in ("SUCCESS", "COMPLETED") or (c_stat == "TIMEOUT" and c_sellers > 0):
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

    try:
        process_category_run(
            category_name=current_category,
            target_url=target_url,
            config=config,
            repo=repo,
            headless=headless_mode,
            allow_reprocess=allow_reprocess,
            is_batch=False
        )
    except KeyboardInterrupt:
        print("\n========================================")
        print("SCRAPER INTERRUPTED BY USER")
        print("Collected records have been preserved.")
        print("========================================\n")
        logger.warning("Scraper interrupted by user (Ctrl+C).")

def run_single_product_test(product_url_or_asin: str, headless: bool = False, category_name: str = "Test Category"):
    logger = setup_logging()
    config = load_config()

    raw_input = product_url_or_asin.strip()
    if not raw_input.startswith("http"):
        asin = raw_input
        product_url = f"https://www.amazon.in/dp/{asin}"
    else:
        product_url = raw_input
        m = re.search(r"/dp/([A-Z0-9]{10})", product_url)
        asin = m.group(1) if m else "UNKNOWN"

    logger.info(f"Running Amazon Multi-Seller Extraction Test for ASIN '{asin}' ({product_url})")

    max_sellers_per_product = config.get("max_sellers_per_product", 100)
    max_offer_scroll_attempts = config.get("max_offer_scroll_attempts", 30)
    max_no_new_seller_attempts = config.get("max_no_new_seller_attempts", 3)
    offer_load_wait_ms = config.get("offer_load_wait_ms", 1000)
    max_product_offer_runtime_seconds = config.get("max_product_offer_runtime_seconds", 90)

    db_file = config.get("database_file", "amazon_sellers.db")
    master_file = config.get("master_output_file", "output/Amazon_Seller_Master_Data.xlsx")

    init_db(db_file)
    repo = SellerRepository(db_file)

    browser_mgr = BrowserManager(headless=headless, timeout_ms=30000)
    discovery_source = AmazonPublicSource(
        browser_mgr=browser_mgr,
        max_sellers_per_product=max_sellers_per_product,
        max_offer_scroll_attempts=max_offer_scroll_attempts,
        max_no_new_seller_attempts=max_no_new_seller_attempts,
        offer_load_wait_ms=offer_load_wait_ms,
        max_product_offer_runtime_seconds=max_product_offer_runtime_seconds
    )

    product_info = {
        "asin": asin,
        "product_url": product_url,
        "product_title": f"Amazon Product {asin}",
        "category": category_name
    }

    try:
        browser_mgr.start()
        seller_offers_data = discovery_source.extract_seller_offers(product_info)
    finally:
        browser_mgr.close()

    buy_box_seller = "None"
    aod_sellers_count = 0
    unique_sellers_list = []
    seen_keys = set()
    duplicates_removed = 0
    product_title = f"Amazon Product {asin}"

    for off in seller_offers_data:
        disp_name = off.get("display_name")
        if not disp_name:
            continue
        if off.get("product_title"):
            product_title = off.get("product_title")

        source = off.get("source", "")
        if "Buy Box" in source:
            buy_box_seller = disp_name
        if "Other Sellers" in source or "Widget" in source:
            aod_sellers_count += 1

        norm_k = normalize_seller_key(disp_name)
        if norm_k not in seen_keys:
            seen_keys.add(norm_k)
            record, sources = SellerExtractor.build_seller_record(off, category=category_name)
            record.sub_sub_category = category_name
            unique_sellers_list.append((record, sources, off))
        else:
            duplicates_removed += 1

    # Persist collected sellers to SQLite and export to Master Excel
    saved_records = []
    for s_no, (record, sources, off) in enumerate(unique_sellers_list, 1):
        record.s_no = s_no
        saved_rec, _ = repo.save_or_update_seller(record)
        saved_records.append(saved_rec)
        for src in sources:
            src.seller_id = saved_rec.id
            repo.add_seller_source(src)
        repo.add_seller_offer(SellerOffer(
            seller_id=saved_rec.id,
            asin=asin,
            product_url=product_url,
            product_title=product_title,
            category=category_name,
            seller_name=off.get("display_name"),
            seller_profile_url=off.get("seller_profile_url"),
            price=off.get("price"),
            condition=off.get("condition", "New"),
            source=off.get("source", "Amazon")
        ))

    excel_result = export_sellers_to_master_excel(
        sellers=saved_records,
        current_category=category_name,
        output_path=master_file,
        allow_reprocess=True
    )

    print("\n========================================")
    print("AMAZON SELLER EXTRACTION TEST")
    print("========================================")
    print(f"\nProduct:\n{product_title}")
    print(f"ASIN:\n{asin}")
    print(f"\nBuy Box Seller:\n{buy_box_seller}")
    print(f"\nAOD Sellers:\n{aod_sellers_count}")
    print(f"\nTotal Unique Sellers:\n{len(unique_sellers_list)}")
    print(f"\nDuplicates Removed:\n{duplicates_removed}")
    print(f"\nMaximum Allowed:\n{max_sellers_per_product}")
    print(f"\nExcel Rows Added:\n{excel_result.get('added_count', len(saved_records))}")
    print(f"\nStatus:\nSUCCESS")
    print("========================================\n")

def main():
    parser = argparse.ArgumentParser(description="Amazon Multi-Category Multi-Seller Web Scraper")
    parser.add_argument("--batch", action="store_true", help="Enable Multi-Category Batch Mode (reads amazon_urls.txt)")
    parser.add_argument("--urls-file", type=str, default=None, help="Path to batch category URLs file")
    parser.add_argument("--category", type=str, default=None, help="Target category name (single category mode)")
    parser.add_argument("--url", type=str, default=None, help="Target Amazon URL (single category mode)")
    parser.add_argument("--force", action="store_true", help="Force reprocess category")
    parser.add_argument("--test-business", type=str, default=None, help="Run Single Business Test")
    parser.add_argument("--test-product", type=str, default=None, help="Run Single Product Multi-Seller Extraction Test (ASIN or URL)")
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
        run_batch(
            config=config,
            headless=headless,
            allow_reprocess=allow_reprocess,
            urls_file=args.urls_file
        )
    else:
        run_single(
            config=config,
            category=args.category,
            url=args.url,
            headless=args.headless,
            force=args.force
        )

if __name__ == "__main__":
    main()

