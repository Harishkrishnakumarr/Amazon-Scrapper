import logging
import random
import re
import time
from typing import List, Dict, Any, Tuple
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger("amazon_scraper")


class AmazonBlockedException(Exception):
    """Raised when Amazon returns a block/challenge response after retries."""

    def __init__(self, status_reason: str, url: str):
        self.status_reason = status_reason
        self.url = url
        super().__init__(f"Amazon blocked or unavailable ({status_reason}) at {url}")


def check_amazon_block(response, page: Page) -> Tuple[bool, str]:
    """Detect explicit Amazon HTTP/challenge pages without misclassifying navigation errors."""
    if response:
        status = response.status
        if status == 503:
            return True, "503 Service Unavailable"
        if status == 429:
            return True, "429 Too Many Requests"
        if status == 403:
            return True, "403 Forbidden"

    try:
        current_url = (page.url or "").lower()
        if "validatecaptcha" in current_url:
            return True, "validateCaptcha URL detected"

        title = (page.title() or "").lower()
        if "robot check" in title or "captcha" in title:
            return True, "Robot Check / CAPTCHA"
        if "503 - service unavailable" in title or "503 service unavailable" in title:
            return True, "503 Service Unavailable"

        content = page.content().lower()
        if (
            "api-services-support@amazon.com" in content
            or "type the characters you see in this image" in content
            or "enter the characters you see below" in content and "robot" in content
        ):
            return True, "Robot Check / CAPTCHA"
        if "sorry, we couldn't find that page" in title and "amazon" in title:
            return False, "Page Not Found"
    except Exception:
        pass

    return False, "OK"


def _is_download_navigation_error(exc: Exception) -> bool:
    """Return True for Playwright's navigation error raised for download responses."""
    message = str(exc).lower()
    return "download is starting" in message or "download" in message and "starting" in message


class AmazonSearchScraper:
    """
    Scrapes Amazon search/category listing pages to discover product URLs.

    Navigation is deliberately bounded. A failed navigation gets a fresh
    browser context before retrying when the failure is a block/challenge, so
    a poisoned Amazon session is not reused across categories.
    """

    def __init__(self, page: Page, max_retries: int = 3, context_reset_callback=None):
        self.page = page
        self.max_retries = max_retries
        self.navigation_timeout_ms = 30000
        self.logger = logger
        self.context_reset_callback = context_reset_callback

    def _perform_ui_search(self, category_name: str, fallback_url: str):
        self.page.goto("https://www.amazon.in", wait_until="domcontentloaded", timeout=20000)
        self.page.wait_for_timeout(1000)

        keyword = category_name.strip()
        if not keyword or keyword.startswith("http"):
            qs = parse_qs(urlparse(fallback_url).query)
            keyword = qs.get("k", [""])[0] or "Gym Bags"

        search_input = self.page.wait_for_selector("#twotabsearchtextbox", timeout=8000)
        if search_input:
            search_input.fill("")
            search_input.type(keyword, delay=40)
            self.page.wait_for_timeout(300)
            with self.page.expect_navigation(wait_until="domcontentloaded", timeout=25000):
                self.page.click("#nav-search-submit-button")
        else:
            raise Exception("Search input (#twotabsearchtextbox) not found on Amazon home page")

    def _close_and_replace_page(self):
        """Replace only the page; used for ordinary navigation failures."""
        old_page = self.page
        context = None
        try:
            context = old_page.context if old_page else None
        except Exception:
            context = None

        try:
            if old_page and not old_page.is_closed():
                old_page.close(run_before_unload=False)
        except Exception as exc:
            logger.debug(f"Unable to close failed Amazon search page: {exc}")

        try:
            if context and not context.is_closed():
                self.page = context.new_page()
                self.page.set_default_timeout(self.navigation_timeout_ms)
                self.page.set_default_navigation_timeout(self.navigation_timeout_ms)
                return True
        except Exception as exc:
            logger.warning(f"Unable to replace failed Amazon search page: {exc}")
        return False

    def _reset_context(self):
        """Ask the owner to create a new browser context, if supported."""
        if not self.context_reset_callback:
            return False
        try:
            new_page = self.context_reset_callback()
            if new_page:
                self.page = new_page
                self.page.set_default_timeout(self.navigation_timeout_ms)
                self.page.set_default_navigation_timeout(self.navigation_timeout_ms)
                return True
        except Exception as exc:
            logger.warning(f"Unable to reset Amazon browser context: {exc}")
        return False

    def _recover_after_block(self, reason: str, category_name: str, attempt: int):
        """Recover the browser session after an Amazon block/challenge."""
        logger.warning(
            f"Recovering Amazon session after block ({reason}) for category '{category_name}', attempt {attempt}"
        )
        # The existing page/context is likely carrying a challenged session.
        # Prefer a full context reset; fall back to replacing just the page.
        reset_ok = self._reset_context()
        if not reset_ok:
            self._close_and_replace_page()
        # Keep the cooldown bounded but long enough to avoid hammering Amazon.
        cooldown = min(5 + (attempt - 1) * 5, 15) + random.uniform(0, 2)
        logger.info(f"Cooling down for {cooldown:.1f}s before retrying Amazon")
        time.sleep(cooldown)

    def _navigate(self, url: str, category_name: str = ""):
        """Navigate with an explicit timeout and return the response when available."""
        self.page.set_default_navigation_timeout(self.navigation_timeout_ms)
        try:
            return self.page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.navigation_timeout_ms,
            )
        except Exception as exc:
            if _is_download_navigation_error(exc):
                logger.warning(
                    f"Direct navigation triggered download trap for '{category_name}'. Trying UI search fallback..."
                )
                self._perform_ui_search(category_name, url)
                return None
            raise exc

    def discover_products(
        self,
        search_url: str,
        limit: int = 10,
        max_pages: int = 1,
        category_name: str = "",
    ) -> List[Dict[str, Any]]:
        products = []
        visited_urls = set()
        current_url = search_url
        page_num = 1

        parsed = urlparse(search_url)
        qs = parse_qs(parsed.query)
        category_hint = category_name or qs.get("k", ["General"])[0].replace("+", " ")

        backoff_delays = [2, 5, 10]

        while current_url and page_num <= max_pages and len(products) < limit:
            logger.info(f"Navigating to Amazon search page {page_num}: {current_url}")
            page_loaded = False
            last_error = "Unknown"

            for attempt in range(1, self.max_retries + 1):
                print("Opening Amazon...")
                print(f"Attempt: {attempt}/{self.max_retries}")

                try:
                    response = self._navigate(current_url, category_hint)
                    is_blocked, block_reason = check_amazon_block(response, self.page)

                    if is_blocked:
                        last_error = block_reason
                        print(f"Amazon returned: {block_reason}")
                        logger.warning(
                            f"Amazon access unavailable on attempt {attempt}/{self.max_retries}: {block_reason}"
                        )
                        if attempt < self.max_retries:
                            print("Refreshing Amazon browser session before retry...")
                            self._recover_after_block(block_reason, category_hint, attempt)
                            continue

                        print("CATEGORY STATUS: BLOCKED")
                        logger.error(
                            "AMAZON ACCESS UNAVAILABLE\n"
                            f"Category: {category_hint}\n"
                            f"URL: {current_url}\n"
                            f"Status: {last_error}"
                        )
                        raise AmazonBlockedException(last_error, current_url)

                    print("Amazon page loaded")
                    page_loaded = True
                    break

                except PlaywrightTimeoutError as exc:
                    last_error = "Page Navigation Timeout (30s)"
                    print(f"Amazon navigation timed out (attempt {attempt}/{self.max_retries})")
                    logger.warning(
                        f"Timeout opening Amazon search page (attempt {attempt}): {current_url}: {exc}"
                    )
                    if attempt < self.max_retries:
                        delay = backoff_delays[min(attempt - 1, len(backoff_delays) - 1)]
                        print(f"Retrying in {delay}s...")
                        self._close_and_replace_page()
                        time.sleep(delay)
                        continue
                    raise AmazonBlockedException(last_error, current_url)

                except Exception as exc:
                    if _is_download_navigation_error(exc):
                        last_error = "Navigation returned a download instead of an HTML page"
                        print(f"Amazon returned a download response (attempt {attempt}/{self.max_retries})")
                        logger.warning(
                            f"Amazon search navigation produced a download on attempt "
                            f"{attempt}/{self.max_retries}: {current_url}"
                        )
                    else:
                        last_error = f"Navigation Error: {exc}"
                        print(
                            f"Amazon navigation error (attempt {attempt}/{self.max_retries}): {exc}"
                        )
                        logger.warning(
                            f"Error opening Amazon search page (attempt {attempt}): {current_url}: {exc}"
                        )

                    if attempt < self.max_retries:
                        delay = backoff_delays[min(attempt - 1, len(backoff_delays) - 1)]
                        print(f"Retrying in {delay}s...")
                        self._close_and_replace_page()
                        time.sleep(delay)
                        continue

                    raise AmazonBlockedException(last_error, current_url)

            if not page_loaded:
                break

            try:
                self.page.evaluate("window.scrollBy(0, 800);")
                self.page.wait_for_timeout(1000)
            except Exception as exc:
                logger.debug(f"Optional Amazon search scroll failed: {exc}")

            links = self.page.query_selector_all("a[href*='/dp/']")
            logger.info(f"Found {len(links)} raw product links on page {page_num}")

            for link in links:
                if len(products) >= limit:
                    break
                try:
                    href = link.get_attribute("href")
                    if not href:
                        continue
                    full_url = f"https://www.amazon.in{href}" if href.startswith("/") else href
                    asin_match = re.search(r"/dp/([A-Z0-9]{10})", full_url)
                    if not asin_match:
                        continue
                    asin = asin_match.group(1)
                    clean_product_url = f"https://www.amazon.in/dp/{asin}"
                    if clean_product_url in visited_urls:
                        continue
                    visited_urls.add(clean_product_url)
                    title_text = link.inner_text().strip() or f"Amazon Product {asin}"
                    products.append(
                        {
                            "asin": asin,
                            "product_url": clean_product_url,
                            "product_title": title_text[:120],
                            "category": category_hint,
                            "source_search_url": search_url,
                        }
                    )
                except Exception as exc:
                    logger.debug(f"Error parsing product link: {exc}")
                    continue

            print(f"Products discovered: {len(products)}")
            logger.info(f"Discovered {len(products)} total unique product URLs so far")

            if page_num < max_pages and len(products) < limit:
                try:
                    next_btn = self.page.query_selector("a.s-pagination-next")
                    if next_btn:
                        next_href = next_btn.get_attribute("href")
                        if next_href:
                            current_url = (
                                f"https://www.amazon.in{next_href}"
                                if next_href.startswith("/")
                                else next_href
                            )
                            page_num += 1
                        else:
                            break
                    else:
                        break
                except Exception as exc:
                    logger.debug(f"Unable to determine Amazon next page: {exc}")
                    break
            else:
                break

        return products
