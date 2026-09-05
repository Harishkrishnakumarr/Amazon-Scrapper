import os
import sys
sys.path.insert(0, os.path.abspath("."))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
from scraper.browser import BrowserManager
from scraper.amazon_search import AmazonSearchScraper
from scraper.amazon_product import AmazonProductScraper

def find_multi_offer_asins():
    browser_mgr = BrowserManager(headless=True, timeout_ms=30000)
    try:
        browser_mgr.start()
        page = browser_mgr.new_page()
        search_scraper = AmazonSearchScraper(page)

        search_urls = [
            "https://www.amazon.in/s?k=mixer+grinder",
            "https://www.amazon.in/s?k=bluetooth+headphones",
            "https://www.amazon.in/s?k=books+bestseller",
            "https://www.amazon.in/s?k=water+bottle",
            "https://www.amazon.in/s?k=men+shoes"
        ]

        found_asins = []
        for surl in search_urls:
            print(f"Searching {surl}...")
            prods = search_scraper.discover_products(surl, limit=5, max_pages=1)
            for p in prods:
                found_asins.append(p)

        print(f"\nDiscovered {len(found_asins)} candidate products. Testing offer counts...")
        scraper = AmazonProductScraper(page)

        for p in found_asins:
            url = p["product_url"]
            asin = p["asin"]
            try:
                offers = scraper.extract_product_sellers(url)
                print(f"ASIN: {asin} -> Extracted {len(offers)} unique sellers")
            except Exception as e:
                print(f"ASIN: {asin} -> Error: {e}")

    finally:
        browser_mgr.close()

if __name__ == "__main__":
    find_multi_offer_asins()
