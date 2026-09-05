import sys
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).parent.parent))
from scraper.browser import BrowserManager

def test_search():
    bm = BrowserManager(headless=True, timeout_ms=30000)
    bm.start()
    page = bm.new_page()
    
    query = "Nirbho Traders PAN card number"
    
    engines = [
        ("DuckDuckGo Standard", f"https://duckduckgo.com/?q={quote_plus(query)}"),
        ("DuckDuckGo HTML", f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"),
        ("DuckDuckGo Lite", f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}"),
        ("Bing", f"https://www.bing.com/search?q={quote_plus(query)}"),
        ("Google", f"https://www.google.com/search?q={quote_plus(query)}")
    ]
    
    for name, url in engines:
        print(f"\n--- Testing {name}: {url} ---")
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            status = resp.status if resp else "None"
            title = page.title()
            html_len = len(page.content())
            print(f"Status: {status} | Title: {title} | HTML Length: {html_len}")
            
            # Check links
            all_a = page.query_selector_all("a")
            hrefs = []
            for a in all_a:
                h = a.get_attribute("href")
                if h and h.startswith("http") and "duckduckgo" not in h and "bing.com" not in h and "google.com" not in h and "microsoft.com" not in h:
                    hrefs.append(h)
            print(f"Found {len(hrefs)} external http hrefs. First 5:")
            for h in hrefs[:5]:
                print(f"  - {h}")
        except Exception as e:
            print(f"Error testing {name}: {e}")
            
    bm.close()

if __name__ == "__main__":
    test_search()
