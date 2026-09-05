from scraper.browser import BrowserManager
import re
from urllib.parse import urlparse

def test_enrichment():
    bm = BrowserManager(headless=True)
    bm.start()
    page = bm.new_page()

    business_name = "Campusactivewear"
    query_clean = re.sub(r'[^a-z0-9]', '', business_name.lower())

    # Test direct domain heuristics
    domain_candidates = [
        f"https://www.{query_clean}.com/",
        f"https://www.{query_clean}.in/",
        f"https://{query_clean}.com/"
    ]

    verified = None
    for cand in domain_candidates:
        try:
            print(f"Testing direct candidate domain: {cand}")
            resp = page.goto(cand, wait_until="domcontentloaded", timeout=10000)
            if resp and resp.status == 200:
                page_text = page.inner_text("body").lower()
                if query_clean in re.sub(r'[^a-z0-9]', '', page_text):
                    verified = cand
                    print(f"SUCCESS: Direct Domain Verified! {cand}")
                    break
        except Exception as e:
            print(f"Failed {cand}: {e}")

    bm.close()

if __name__ == "__main__":
    test_enrichment()
