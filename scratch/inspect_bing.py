import sys
from pathlib import Path
from urllib.parse import quote_plus, urlparse, parse_qs, unquote
import re

sys.path.insert(0, str(Path(__file__).parent.parent))
from scraper.browser import BrowserManager

def inspect_bing():
    bm = BrowserManager(headless=True, timeout_ms=30000)
    bm.start()
    page = bm.new_page()
    
    query = "Nirbho Traders PAN card number"
    url = f"https://www.bing.com/search?q={quote_plus(query)}"
    
    print(f"Navigating to Bing: {url}")
    resp = page.goto(url, wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)
    
    print(f"Status: {resp.status} | Title: {page.title()} | Content Length: {len(page.content())}")
    
    # Check Bing selectors
    selectors = [
        "li.b_algo h2 a",
        "#b_results li.b_algo a",
        "li.b_algo a",
        ".b_algo h2 a",
        "h2 a",
        "a[href^='http']"
    ]
    
    for sel in selectors:
        elements = page.query_selector_all(sel)
        print(f"Selector '{sel}': {len(elements)} elements")
        for el in elements[:5]:
            href = el.get_attribute("href")
            text = el.inner_text().strip()
            print(f"   -> Text: '{text[:40]}' | Href: {href}")
            
    # Test Bing URL extraction
    results = []
    for el in page.query_selector_all("li.b_algo h2 a, .b_algo h2 a, #b_results h2 a"):
        href = el.get_attribute("href")
        if href:
            results.append(href)
    print(f"\nExtracted {len(results)} search result URLs from Bing:")
    for r in results:
        print(f"  * {r}")
        
    bm.close()

if __name__ == "__main__":
    inspect_bing()
