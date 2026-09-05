import os
from urllib.parse import quote_plus
from playwright.sync_api import sync_playwright

def test_duckduckgo_search(query: str):
    print(f"Testing DDG search for: {query}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-US"
        )
        page = context.new_page()
        search_url = f"https://duckduckgo.com/?q={quote_plus(query)}"
        page.goto(search_url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        
        print(f"Page title: {page.title()}")
        links = page.query_selector_all("a[data-testid='result-title-a'], a.result__url, a.result__a, h2 a, a[href*='http']")
        print(f"Found {len(links)} links")
        for idx, link in enumerate(links[:10], 1):
            href = link.get_attribute("href")
            text = link.inner_text().strip()
            if href and "duckduckgo" not in href:
                print(f"{idx}. [{text}] -> {href}")

        browser.close()

if __name__ == "__main__":
    test_duckduckgo_search("Campusactivewear official website contact")
