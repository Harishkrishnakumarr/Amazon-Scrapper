import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scraper.browser import BrowserManager
from scraper.amazon_search import AmazonSearchScraper

def test_flip_flops():
    url = "https://www.amazon.in/flip-flops-for-women-office-wear/s?k=flip+flops+for+women+office+wear"
    print(f"Testing URL: {url}")
    
    bm = BrowserManager(headless=True, timeout_ms=30000)
    bm.start()
    page = bm.new_page()
    
    try:
        t0 = time.time()
        print(f"Navigating to {url} with wait_until='domcontentloaded', timeout=30000...")
        resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        elapsed = time.time() - t0
        print(f"Response status: {resp.status if resp else 'None'} in {elapsed:.2f}s")
        print(f"Page title: {page.title()}")
        print(f"Content length: {len(page.content())}")
        
        scraper = AmazonSearchScraper(page)
        products = scraper.discover_products(url, limit=10, max_pages=1)
        print(f"Discovered {len(products)} products:")
        for p in products:
            print(f"  - ASIN: {p['asin']} | Title: {p['product_title'][:50]} | URL: {p['product_url']}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        bm.close()

if __name__ == "__main__":
    test_flip_flops()
