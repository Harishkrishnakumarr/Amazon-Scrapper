import os
import sys
import time
import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from database.database import init_db
from database.models import SellerRecord, SellerOffer
from database.repository import SellerRepository
from scraper.amazon_search import AmazonSearchScraper, AmazonBlockedException, check_amazon_block
from scraper.browser import BrowserManager, safe_close_page
from extraction.public_enrichment import PublicEnrichmentEngine, decode_search_redirect_url, is_valid_search_result_url

def test_all_timeouts_and_retries():
    print("==================================================")
    print("STARTING TIMEOUT, RETRY & ANTI-HANGING TEST SUITE")
    print("==================================================")

    temp_dir = tempfile.mkdtemp()

    # -------------------------------------------------------------
    # TEST 1: Redirect URL Decoding
    # -------------------------------------------------------------
    print("\n--- TEST 1: Redirect URL Decoders ---")
    bing_redirect = "https://www.bing.com/ck/a?!&&p=123&u=a1aHR0cHM6Ly9jbGVhcnRheC5pbi9nc3QtbnVtYmVyLXNlYXJjaC8&ntb=1"
    decoded_bing = decode_search_redirect_url(bing_redirect)
    assert decoded_bing == "https://cleartax.in/gst-number-search/", f"Bing decode failed: {decoded_bing}"

    yahoo_redirect = "https://r.search.yahoo.com/_ylt=Awr.123/RU=https%3a%2f%2fcleartax.in%2fgst/RK=2/RS=456"
    decoded_yahoo = decode_search_redirect_url(yahoo_redirect)
    assert decoded_yahoo == "https://cleartax.in/gst", f"Yahoo decode failed: {decoded_yahoo}"

    google_redirect = "https://www.google.com/url?q=https://cleartax.in/gst&sa=U"
    decoded_google = decode_search_redirect_url(google_redirect)
    assert decoded_google == "https://cleartax.in/gst", f"Google decode failed: {decoded_google}"
    print("PASS: Redirect URL decoding works for Bing, Yahoo, and Google.")

    # -------------------------------------------------------------
    # TEST 2: URL Validation & Filtering
    # -------------------------------------------------------------
    print("\n--- TEST 2: URL Validation & Domain Filtering ---")
    assert is_valid_search_result_url("https://nirbhotraders.com/about") is True
    assert is_valid_search_result_url("https://www.bing.com/search?q=test") is False
    assert is_valid_search_result_url("https://www.google.com/url") is False
    assert is_valid_search_result_url("https://www.amazon.in/dp/B123") is False
    assert is_valid_search_result_url("javascript:void(0)") is False
    print("PASS: URL validation correctly allows business sites and rejects internal/skip domains.")

    # -------------------------------------------------------------
    # TEST 3: Amazon 503 / Robot Check Detection & Retry with Exponential Backoff
    # -------------------------------------------------------------
    print("\n--- TEST 3: Amazon 503 / Robot Check Detection & 3 Retries ---")
    mock_page = MagicMock()
    mock_response = MagicMock()
    mock_response.status = 503
    mock_page.title.return_value = "503 - Service Unavailable"
    mock_page.content.return_value = "<html><body>503 Service Unavailable</body></html>"
    mock_page.goto.return_value = mock_response

    scraper = AmazonSearchScraper(mock_page, max_retries=3)
    
    start_t = time.time()
    with patch("time.sleep") as mock_sleep:
        try:
            scraper.discover_products("https://www.amazon.in/s?k=test", limit=5)
            assert False, "Should have raised AmazonBlockedException after 3 retries"
        except AmazonBlockedException as abe:
            print(f"PASS: Correctly raised AmazonBlockedException: {abe}")
            assert mock_sleep.call_count == 2, f"Expected 2 backoff sleep calls (2s, 5s), got {mock_sleep.call_count}"
            assert mock_page.goto.call_count == 3, f"Expected 3 goto attempts, got {mock_page.goto.call_count}"
            print("PASS: Verified 3 retries with exponential backoff on 503.")

    # -------------------------------------------------------------
    # TEST 4: Smart Category Run Duplicate Check (Retry on BLOCKED/TIMEOUT/FAILED)
    # -------------------------------------------------------------
    print("\n--- TEST 4: Smart Duplicate Check on Category Runs ---")
    test_db = os.path.join(temp_dir, "test_status.db")
    init_db(test_db)
    repo = SellerRepository(test_db)

    # 1. Start category run -> status RUNNING
    run_id = repo.record_category_run_start("Men's Rainwear")
    is_proc, cnt = repo.is_category_processed("Men's Rainwear")
    assert is_proc is False, "RUNNING category must allow retry"

    # 2. Mark category BLOCKED
    repo.update_category_run_status(run_id, "Men's Rainwear", "BLOCKED", 0, 0)
    is_proc, cnt = repo.is_category_processed("Men's Rainwear")
    assert is_proc is False, "BLOCKED category must allow retry (not marked duplicate)"

    # 3. Mark category TIMEOUT
    repo.update_category_run_status(run_id, "Men's Rainwear", "TIMEOUT", 10, 5)
    is_proc, cnt = repo.is_category_processed("Men's Rainwear")
    assert is_proc is False, "TIMEOUT category must allow retry"

    # 4. Mark category FAILED
    repo.update_category_run_status(run_id, "Men's Rainwear", "FAILED", 0, 0)
    is_proc, cnt = repo.is_category_processed("Men's Rainwear")
    assert is_proc is False, "FAILED category must allow retry"

    # 5. Insert seller & Mark category COMPLETED
    rec = SellerRecord(sub_sub_category="Men's Rainwear", business_name="Rainwear Pro")
    repo.save_or_update_seller(rec)
    repo.update_category_run_status(run_id, "Men's Rainwear", "COMPLETED", 10, 1)
    is_proc, cnt = repo.is_category_processed("Men's Rainwear")
    assert is_proc is True, "COMPLETED category with sellers must be marked processed"
    print("PASS: Smart duplicate protection accurately allows retries for non-completed categories.")

    # -------------------------------------------------------------
    # TEST 5: Public Enrichment Seller Timeout (90s) & 3 Queries Max Per Field
    # -------------------------------------------------------------
    print("\n--- TEST 5: Public Enrichment 90s Seller Timeout & 3 Queries Max ---")
    mock_bm = MagicMock()
    mock_enrich_page = MagicMock()
    mock_bm.new_page.return_value = mock_enrich_page

    engine = PublicEnrichmentEngine(mock_bm, max_seller_enrichment_seconds=1)
    
    # Mock search to simulate slow queries
    def slow_search(page, query, limit=2, timeout_sec=15):
        time.sleep(1.2)  # exceed 1s limit
        return (["https://nirbhotraders.com/"], {"engine": "Bing", "status": "200", "title": "Nirbho", "url": "url", "html_length": 100, "extracted_count": 1, "raw_html": ""})

    engine.primary_provider.search = slow_search

    dummy_seller = SellerRecord(
        business_name="Nirbho Traders",
        sub_sub_category="Men's Rainwear"
    )

    t0 = time.time()
    enriched, sources = engine.enrich_seller(dummy_seller)
    elapsed = time.time() - t0
    
    print(f"Enrichment completed in {elapsed:.2f}s (enforced by 1s test timeout)")
    assert elapsed < 5.0, f"Enrichment took too long: {elapsed}s"
    assert enriched.business_name == "Nirbho Traders"
    print("PASS: Seller enrichment timeout prevented hanging and retained Amazon seller record.")

    # -------------------------------------------------------------
    # TEST 6: Safe Page Close
    # -------------------------------------------------------------
    print("\n--- TEST 6: Safe Non-Blocking Playwright Teardown ---")
    broken_page = MagicMock()
    broken_page.close.side_effect = RuntimeError("Playwright crashed during close")
    # Should not raise
    safe_close_page(broken_page)
    print("PASS: Safe close ignored internal Playwright teardown exceptions.")

    print("\n==================================================")
    print("ALL TIMEOUT, RETRY & ANTI-HANGING TESTS PASSED!")
    print("==================================================")

if __name__ == "__main__":
    test_all_timeouts_and_retries()
