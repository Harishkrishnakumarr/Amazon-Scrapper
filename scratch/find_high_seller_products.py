import os
import sys
sys.path.insert(0, os.path.abspath("."))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
import re
from scraper.browser import BrowserManager
from scraper.amazon_product import AmazonProductScraper

def find_asins_with_many_offers():
    # List of known popular Amazon India products across different categories with multiple third-party sellers
    test_asins = [
        "9389432470", # Word Power Made Easy (Bestselling book, usually 15-40 sellers)
        "8172234988", # The Alchemist (Bestselling book, usually 20-50 sellers)
        "B08N5WRWNW", # Apple iPhone / AirPods / electronics
        "B07WHR5BLH", # boAt Bassheads 100 Wired Earphones
        "B08696XM4P", # SanDisk Ultra Dual Drive Go Type-C
        "B00E3821HS", # Cello Gripper Ball Pen Pack
        "B089MS3GLM", # Dettol Liquid Handwash Refill
        "B01M1CZSBC"  # Pigeon by Stovekraft Handy Chopper
    ]

    browser_mgr = BrowserManager(headless=True, timeout_ms=30000)
    try:
        browser_mgr.start()
        page = browser_mgr.new_page()
        scraper = AmazonProductScraper(page, max_sellers_per_product=100)

        for asin in test_asins:
            url = f"https://www.amazon.in/dp/{asin}"
            print(f"\n--- Checking ASIN: {asin} ---")
            offers = scraper.extract_product_sellers(url)
            print(f"--> RESULT for {asin}: Found {len(offers)} unique sellers")
            if len(offers) >= 5:
                print(f"*** FOUND MULTI-SELLER PRODUCT ({len(offers)} sellers) ***")
                for i, o in enumerate(offers[:10], 1):
                    print(f"    {i}. {o.get('seller_name')} ({o.get('source')})")

    finally:
        browser_mgr.close()

if __name__ == "__main__":
    find_asins_with_many_offers()
