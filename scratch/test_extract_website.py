from playwright.sync_api import sync_playwright

def test_website(url: str):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-IN"
        )
        page = context.new_page()
        print(f"Opening {url}...")
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        
        text = page.inner_text("body")
        with open("scratch/website_dump.txt", "w", encoding="utf-8") as f:
            f.write(f"Title: {page.title()}\n\n")
            f.write(text)
            
        print(f"Saved text. Length = {len(text)}")
        browser.close()

if __name__ == "__main__":
    test_website("https://www.campusactivewear.com/pages/contact-us")
