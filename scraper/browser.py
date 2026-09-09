import logging
import random
import time
from typing import Optional
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Playwright

logger = logging.getLogger("amazon_scraper")

def random_delay(min_sec: float = 1.5, max_sec: float = 3.5):
    """Introduces a safe randomized delay between page transitions."""
    delay = random.uniform(min_sec, max_sec)
    time.sleep(delay)

def safe_close_page(page: Optional[Page]):
    """Safely closes a Playwright Page without throwing or hanging."""
    if page:
        try:
            if not page.is_closed():
                page.close()
        except Exception as e:
            logger.debug(f"Non-critical error closing page: {e}")

class BrowserManager:
    """
    Manages Playwright browser lifecycle with anti-fingerprinting hardening,
    India locale, Asia/Kolkata timezone, and bounded timeouts.
    """
    def __init__(self, headless: bool = False, timeout_ms: int = 30000):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None

    def start(self):
        try:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-infobars"
                ]
            )
            self.context = self.browser.new_context(
                viewport={"width": 1920, "height": 1080},
                accept_downloads=False,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                extra_http_headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": '"Windows"',
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1",
                }
            )
            # Stealth initialization scripts
            self.context.add_init_script("""
                // Mask webdriver
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                // Mock chrome runtime
                window.chrome = { runtime: {} };
                // Mock plugins and languages
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-IN', 'en-GB', 'en'] });
            """)
            self.context.set_default_timeout(self.timeout_ms)
            logger.info(f"Started Playwright Chromium session with stealth hardening (headless={self.headless}, timeout={self.timeout_ms}ms)")
        except Exception as e:
            logger.error(f"Failed to start Playwright browser: {e}")
            self.close()
            raise e

    def new_page(self) -> Page:
        if not self.context:
            self.start()
        page = self.context.new_page()
        page.set_default_timeout(self.timeout_ms)
        return page

    def close(self):
        """Safe non-blocking cleanup that guarantees the process never hangs on exit."""
        if self.context:
            try:
                self.context.close()
            except Exception as e:
                logger.debug(f"Error closing context: {e}")
            finally:
                self.context = None

        if self.browser:
            try:
                if self.browser.is_connected():
                    self.browser.close()
            except Exception as e:
                logger.debug(f"Error closing browser: {e}")
            finally:
                self.browser = None

        if self.playwright:
            try:
                self.playwright.stop()
            except Exception as e:
                logger.debug(f"Error stopping playwright: {e}")
            finally:
                self.playwright = None

        logger.info("Closed Playwright browser session safely")
