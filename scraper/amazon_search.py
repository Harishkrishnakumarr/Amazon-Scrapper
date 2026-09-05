import logging
import re
import time
from typing import List, Dict, Any, Tuple, Optional
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger("amazon_scraper")

class AmazonBlockedException(Exception):
    """Raised when Amazon returns 503, 429, 403, or robot check / captcha across all retries."""
    def __init__(self, status_reason: str, url: str):
        self.status_reason = status_reason
        self.url = url
        super().__init__(f"Amazon blocked or unavailable ({status_reason}) at {url}")

def check_amazon_block(response, page: Page) -> Tuple[bool, str]:
    """
    Checks if an Amazon page response or content represents a block/503/captcha.
    """
    if response:
        if response.status == 503:
            return True, "503 Service Unavailable"
        if response.status == 429:
            return True, "429 Too Many Requests"
        if response.status == 403:
            return True, "403 Forbidden"

    try:
        title = (page.title() or "").lower()
        if "robot check" in title or "captcha" in title:
            return True, "Robot Check / CAPTCHA"
        if "503 - service unavailable" in title or "503 service unavailable" in title:
            return True, "503 Service Unavailable"
            
        content = page.content().lower()
        if "api-services-support@amazon.com" in content or "type the characters you see in this image" in content:
            return True, "Robot Check / CAPTCHA"
        if "sorry, we couldn't find that page" in title and "amazon" in title:
            return False, "Page Not Found"
    except Exception:
        pass

    return False, "OK"

class AmazonSearchScraper:
    """
    Scrapes Amazon search / category listing pages to discover product URLs.
    Handles bounded timeouts, 503/CAPTCHA detection, and exponential backoff retries.
    """
    def __init__(self, page: Page, max_retries: int = 3):
        self.page = page
        self.max_retries = max_retries

    def discover_products(self, search_url: str, limit: int = 10, max_pages: int = 1, category_name: str = "") -> List[Dict[str, Any]]:
        products = []
        visited_urls = set()
        current_url = search_url
        page_num = 1

        # Determine category from search URL query parameter if present
        parsed = urlparse(search_url)
        qs = parse_qs(parsed.query)
        category_hint = category_name or qs.get("k", ["General"])[0].replace("+", " ")

        backoff_delays = [2, 5, 10]

        while current_url and page_num <= max_pages and len(products) < limit:
            logger.info(f"Navigating to Amazon search page {page_num}: {current_url}")
            page_loaded = False
            last_block_reason = "Unknown"

            for attempt in range(1, self.max_retries + 1):
                print(f"Opening Amazon...")
                print(f"Attempt: {attempt}/{self.max_retries}")
                
                try:
                    response = self.page.goto(current_url, wait_until="domcontentloaded", timeout=30000)
                    is_blocked, block_reason = check_amazon_block(response, self.page)

                    if is_blocked:
                        last_block_reason = block_reason
                        print(f"Amazon returned: {block_reason}")
                        logger.warning(f"Amazon access unavailable on attempt {attempt}/{self.max_retries}: {block_reason}")
                        
                        if attempt < self.max_retries:
                            delay = backoff_delays[attempt - 1]
                            print(f"Retrying in {delay}s...")
                            time.sleep(delay)
                            continue
                        else:
                            print(f"CATEGORY STATUS: BLOCKED")
                            logger.error(f"""
AMAZON ACCESS UNAVAILABLE
Category: {category_hint}
URL: {current_url}
Status: {last_block_reason}
""")
                            raise AmazonBlockedException(last_block_reason, current_url)

                    # Page loaded successfully
                    print("Amazon page loaded")
                    page_loaded = True
                    break

                except PlaywrightTimeoutError:
                    last_block_reason = "Page Navigation Timeout (30s)"
                    print(f"Amazon navigation timed out (attempt {attempt}/{self.max_retries})")
                    logger.warning(f"Timeout opening Amazon search page (attempt {attempt}): {current_url}")
                    if attempt < self.max_retries:
                        delay = backoff_delays[attempt - 1]
                        print(f"Retrying in {delay}s...")
                        time.sleep(delay)
                        continue
                    else:
                        raise AmazonBlockedException("Navigation Timeout (30s)", current_url)

            if not page_loaded:
                break

            # Scroll down slightly to trigger lazy-loaded product listings
            try:
                self.page.evaluate("window.scrollBy(0, 800);")
                self.page.wait_for_timeout(1000)
            except Exception:
                pass

            # Extract product links matching /dp/ASIN pattern
            links = self.page.query_selector_all("a[href*='/dp/']")
            logger.info(f"Found {len(links)} raw product links on page {page_num}")

            for link in links:
                if len(products) >= limit:
                    break
                    
                try:
                    href = link.get_attribute("href")
                    if not href:
                        continue

                    # Normalize product URL
                    if href.startswith("/"):
                        full_url = f"https://www.amazon.in{href}"
                    else:
                        full_url = href

                    # Extract ASIN from URL
                    asin_match = re.search(r"/dp/([A-Z0-9]{10})", full_url)
                    if not asin_match:
                        continue

                    asin = asin_match.group(1)
                    clean_product_url = f"https://www.amazon.in/dp/{asin}"

                    if clean_product_url in visited_urls:
                        continue

                    visited_urls.add(clean_product_url)
                    
                    # Try getting title text
                    title_text = link.inner_text().strip() or f"Amazon Product {asin}"

                    products.append({
                        "asin": asin,
                        "product_url": clean_product_url,
                        "product_title": title_text[:120],
                        "category": category_hint,
                        "source_search_url": search_url
                    })
                except Exception as e:
                    logger.debug(f"Error parsing product link: {e}")
                    continue

            print(f"Products discovered: {len(products)}")
            logger.info(f"Discovered {len(products)} total unique product URLs so far")

            # Handle pagination
            if page_num < max_pages and len(products) < limit:
                try:
                    next_btn = self.page.query_selector("a.s-pagination-next")
                    if next_btn:
                        next_href = next_btn.get_attribute("href")
                        if next_href:
                            current_url = f"https://www.amazon.in{next_href}" if next_href.startswith("/") else next_href
                            page_num += 1
                        else:
                            break
                    else:
                        break
                except Exception:
                    break
            else:
                break

        return products
