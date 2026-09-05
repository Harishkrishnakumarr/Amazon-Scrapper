import sys
import base64
import re
from pathlib import Path
from urllib.parse import quote_plus, urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parent.parent))
from scraper.browser import BrowserManager

def test_engines_deep():
    bm = BrowserManager(headless=True, timeout_ms=30000)
    bm.start()
    page = bm.new_page()
    
    query = "Nirbho Traders GSTIN registration"
    
    providers = [
        ("Bing", f"https://www.bing.com/search?q={quote_plus(query)}", "li.b_algo h2 a, .b_algo h2 a"),
        ("Yahoo", f"https://search.yahoo.com/search?p={quote_plus(query)}", "div.compTitle a, div.compText a, h3.title a"),
        ("DuckDuckGo HTML POST", "https://html.duckduckgo.com/html/", "a.result__url, a.result__title"),
        ("Google", f"https://www.google.com/search?q={quote_plus(query)}", "div.g a, a[jsname]")
    ]
    
    for name, url, sel in providers:
        print(f"\n========================================")
        print(f"Testing Engine: {name}")
        print(f"URL: {url}")
        print(f"========================================")
        try:
            if name == "DuckDuckGo HTML POST":
                resp = page.goto("https://html.duckduckgo.com/html/", wait_until="domcontentloaded", timeout=15000)
                # Fill search form
                page.fill("input[name='q']", query)
                page.click("input[type='submit']")
                page.wait_for_load_state("domcontentloaded")
            else:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=15000)
                
            page.wait_for_timeout(2000)
            status = resp.status if resp else "None"
            title = page.title()
            content = page.content()
            print(f"Status: {status} | Title: {title} | Length: {len(content)}")
            
            # Print first 500 chars of body or text
            text = page.inner_text("body")[:300].replace("\n", " ")
            print(f"Snippet: {text}")
            
            # Extract links using selector
            links = page.query_selector_all(sel)
            print(f"Matched selector '{sel}': {len(links)} elements")
            for l in links[:5]:
                raw_href = l.get_attribute("href")
                print(f"  - Element href: {raw_href}")
                
            # Extract all anchor hrefs
            all_anchors = page.query_selector_all("a")
            http_hrefs = [a.get_attribute("href") for a in all_anchors if a.get_attribute("href") and a.get_attribute("href").startswith("http")]
            print(f"Total http hrefs on page: {len(http_hrefs)}")
            for h in http_hrefs[:5]:
                print(f"  * {h}")
        except Exception as e:
            print(f"Error testing {name}: {e}")
            
    bm.close()

if __name__ == "__main__":
    test_engines_deep()
