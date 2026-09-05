import os
import sys
sys.path.insert(0, os.path.abspath("."))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
from scraper.browser import BrowserManager
from scraper.amazon_product import AmazonProductScraper
from extraction.normalizer import normalize_seller_key

def inspect_asin_dom(asin: str):
    url = f"https://www.amazon.in/dp/{asin}"
    browser_mgr = BrowserManager(headless=True, timeout_ms=30000)
    try:
        browser_mgr.start()
        page = browser_mgr.new_page()
        print(f"Navigating to {url}...")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        scraper = AmazonProductScraper(page)

        asin_val, title = scraper._extract_asin_and_title(url)
        print(f"ASIN: {asin_val} | Title: {title}")

        buybox = scraper._extract_buybox_seller()
        print(f"Buy Box Seller: {buybox}")

        aod_opened = scraper._open_all_offers_panel()
        print(f"AOD opened: {aod_opened}")
        page.wait_for_timeout(2000)

        # Inspect DOM containers in AOD
        containers = page.query_selector_all("#all-offers-display, #aod-offer-list, #aod-container, #all-offers-display-scroller")
        print(f"Found {len(containers)} AOD containers")
        for c in containers:
            c_id = c.get_attribute("id")
            c_class = c.get_attribute("class")
            print(f"Container id='{c_id}' class='{c_class}'")

        offer_containers = page.query_selector_all("#aod-pinned-offer, div#aod-offer, div.aod-offer-container")
        print(f"Found {len(offer_containers)} direct offer containers (#aod-pinned-offer, div#aod-offer, div.aod-offer-container)")

        for idx, oc in enumerate(offer_containers, 1):
            parsed = scraper._parse_aod_offer_element(oc)
            print(f"  Offer #{idx} id='{oc.get_attribute('id')}' class='{oc.get_attribute('class')}': parsed={parsed}")

    finally:
        browser_mgr.close()

if __name__ == "__main__":
    inspect_asin_dom("B075JJ5NQC")
