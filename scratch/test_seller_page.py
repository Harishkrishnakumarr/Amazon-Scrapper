import os
import json
from playwright.sync_api import sync_playwright

def inspect_seller_page(seller_url: str):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-IN"
        )
        page = context.new_page()
        page.goto(seller_url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        
        with open("scratch/seller_page_dump.txt", "w", encoding="utf-8") as f:
            f.write(f"Title: {page.title()}\n\n")
            boxes = page.query_selector_all("div.a-box-group, div#seller-info, div.a-box, #sellerName, #page-section-detail-seller-info")
            for idx, box in enumerate(boxes, 1):
                f.write(f"\n--- Box {idx} ---\n")
                f.write(box.inner_text())

        browser.close()

if __name__ == "__main__":
    inspect_seller_page("https://www.amazon.in/gp/help/seller/at-a-glance.html?seller=A2JTUMWXNY2SJF")
