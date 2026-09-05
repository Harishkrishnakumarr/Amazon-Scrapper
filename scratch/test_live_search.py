import os
import sys
sys.path.insert(0, os.path.abspath("."))
import time
from scraper.browser import BrowserManager
from scraper.amazon_public import AmazonPublicSource
from extraction.seller_extractor import SellerExtractor
from extraction.normalizer import normalize_seller_key

def test_live_search_and_extract():
    browser_mgr = BrowserManager(headless=True, timeout_ms=30000)
    source = AmazonPublicSource(browser_mgr, max_sellers_per_product=100)

    try:
        browser_mgr.start()
        print("Discovering products from Amazon India (Electronics / Kitchen / Footwear)...")
        # Discover products
        products = source.discover_products("https://www.amazon.in/s?k=mixer+grinder", limit=3, max_pages=1, category_name="Mixer Grinder")
        print(f"Found {len(products)} products:")
        for p in products:
            print(f"  - ASIN: {p['asin']} | Title: {p['product_title']} | URL: {p['product_url']}")

        for idx, prod in enumerate(products, 1):
            print(f"\n=======================================================")
            print(f"[{idx}/{len(products)}] TESTING MULTI-SELLER EXTRACTION: {prod['asin']}")
            print(f"=======================================================")
            offers = source.extract_seller_offers(prod)
            print(f"\nOffers extracted for {prod['asin']}: {len(offers)}")
            for i, off in enumerate(offers, 1):
                print(f"  {i}. Seller: {off.get('display_name')} | Source: {off.get('source')} | Price: {off.get('price')} | Condition: {off.get('condition')}")

    finally:
        browser_mgr.close()

if __name__ == "__main__":
    test_live_search_and_extract()
