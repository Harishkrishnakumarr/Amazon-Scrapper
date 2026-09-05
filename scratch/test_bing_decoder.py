import sys
import base64
import re
from pathlib import Path
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

sys.path.insert(0, str(Path(__file__).parent.parent))
from scraper.browser import BrowserManager

def decode_bing_url(href: str) -> str:
    if not href:
        return ""
    if "bing.com/ck/a?" in href or "/ck/a?" in href:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        u_val = qs.get("u", [""])[0]
        if u_val:
            # Bing base64 u parameter usually starts with 'a1' prefix
            b64_str = u_val
            if b64_str.startswith("a1"):
                b64_str = b64_str[2:]
            # Add base64 padding if needed
            padding = len(b64_str) % 4
            if padding:
                b64_str += "=" * (4 - padding)
            try:
                decoded = base64.urlsafe_b64decode(b64_str).decode("utf-8", errors="ignore")
                if decoded.startswith("http"):
                    return decoded
            except Exception as e:
                pass
    return href

def test_search_providers():
    bm = BrowserManager(headless=True, timeout_ms=30000)
    bm.start()
    page = bm.new_page()
    
    queries = [
        "Nirbho Traders PAN card number",
        "Nirbho Traders official website",
        "Nirbho Traders GSTIN registration",
        "Cocoblu Retail Private Limited GSTIN"
    ]
    
    for q in queries:
        print(f"\n========================================")
        print(f"QUERY: {q}")
        print(f"========================================")
        
        # Provider 1: Bing
        bing_url = f"https://www.bing.com/search?q={quote_plus(q)}"
        resp = page.goto(bing_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(1000)
        
        links = page.query_selector_all("li.b_algo h2 a, .b_algo h2 a, #b_results h2 a")
        bing_results = []
        for l in links:
            raw_href = l.get_attribute("href")
            decoded_href = decode_bing_url(raw_href)
            if decoded_href and decoded_href.startswith("http") and "bing.com" not in decoded_href and "microsoft.com" not in decoded_href:
                bing_results.append(decoded_href)
                
        print(f"Bing Provider: Found {len(bing_results)} usable result URLs:")
        for u in bing_results[:5]:
            print(f"  * {u}")
            
    bm.close()

if __name__ == "__main__":
    test_search_providers()
