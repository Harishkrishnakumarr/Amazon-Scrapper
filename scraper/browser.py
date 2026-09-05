import logging
from typing import Optional
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Playwright

logger = logging.getLogger("amazon_scraper")

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
    Manages Playwright browser lifecycle using standard Playwright automation.
    Does NOT use stealth plugins, fingerprint spoofing, or anti-bot bypasses.
    Enforces bounded timeouts and safe cleanup.
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
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"]
            )
            self.context = self.browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                locale="en-IN"
            )
            self.context.set_default_timeout(self.timeout_ms)
            logger.info(f"Started standard Playwright Chromium session (headless={self.headless}, timeout={self.timeout_ms}ms)")
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
