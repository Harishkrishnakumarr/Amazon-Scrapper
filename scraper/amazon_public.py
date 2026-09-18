import logging
import re
from typing import List, Dict, Any, Optional
from scraper.base import SellerDiscoverySource
from scraper.browser import BrowserManager, safe_close_page
from scraper.amazon_search import AmazonSearchScraper
from scraper.amazon_product import AmazonProductScraper
from scraper.amazon_seller import AmazonSellerProfileScraper
from extraction.normalizer import normalize_seller_key

logger = logging.getLogger("amazon_scraper")


class AmazonPublicSource(SellerDiscoverySource):
    """
    Initial permitted implementation of SellerDiscoverySource for Amazon public pages.
    Discovers products and extracts publicly accessible seller offers per product.
    Uses standard Playwright browser automation without stealth/anti-bot bypasses.
    """
    def __init__(
        self,
        browser_mgr: BrowserManager,
        max_sellers_per_product: int = 100,
        max_offer_scroll_attempts: int = 30,
        max_no_new_seller_attempts: int = 3,
        offer_load_wait_ms: int = 1000,
        max_product_offer_runtime_seconds: int = 90
    ):
        self.browser_mgr = browser_mgr
        self.max_sellers_per_product = max_sellers_per_product
        self.max_offer_scroll_attempts = max_offer_scroll_attempts
        self.max_no_new_seller_attempts = max_no_new_seller_attempts
        self.offer_load_wait_ms = offer_load_wait_ms
        self.max_product_offer_runtime_seconds = max_product_offer_runtime_seconds

    def discover_products(self, search_url: str, limit: int = 10, max_pages: int = 1, category_name: str = "") -> List[Dict[str, Any]]:
        page = self.browser_mgr.new_page()
        try:
            search_scraper = AmazonSearchScraper(
                page,
                context_reset_callback=self.browser_mgr.reset_context,
            )
            return search_scraper.discover_products(
                search_url,
                limit=limit,
                max_pages=max_pages,
                category_name=category_name,
            )
        finally:
            safe_close_page(page)

    @staticmethod
    def _clean_seller_name(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        value = re.sub(r"\s+", " ", value).strip()
        value = re.sub(r"(?i)^sold\s+by\s*[:\-]?\s*", "", value).strip()
        value = re.sub(r"(?i)\s*(and|&)\s*fulfilled.*$", "", value).strip()
        value = value.strip("|:- ")
        if not value:
            return None
        if value.lower() in {"amazon", "amazon.in", "amazon retail", "amazon seller"}:
            return None
        if len(value) > 160:
            value = value[:160].strip()
        return value

    def _fallback_extract_visible_sellers(self, page, product_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Fallback for Amazon markup variants where the dedicated AOD selectors
        return no offers even though seller information is visible on the page.
        This deliberately reads only publicly rendered page content and seller links.
        """
        results: Dict[str, Dict[str, Any]] = {}
        asin = product_info.get("asin")
        product_url = product_info.get("product_url")
        product_title = product_info.get("product_title", "")

        def add_seller(name: Optional[str], profile_url: Optional[str] = None, price: Optional[str] = None):
            clean_name = self._clean_seller_name(name)
            if not clean_name:
                return
            key = normalize_seller_key(clean_name)
            if not key or key.startswith("amazon") or key in results:
                return
            if profile_url and profile_url.startswith("/"):
                profile_url = f"https://www.amazon.in{profile_url}"
            results[key] = {
                "asin": asin,
                "seller_name": clean_name,
                "seller_profile_url": profile_url,
                "product_title": product_title,
                "product_url": product_url,
                "price": price,
                "condition": "New",
                "source": "Amazon (Visible Seller Fallback)",
            }

        link_selectors = [
            "#sellerProfileTriggerId",
            "#merchant-info a",
            "#tabular-buybox a[href*='seller=']",
            "#tabular-buybox a[href*='/sp?']",
            "#buybox a[href*='seller=']",
            "a[href*='seller=']",
            "a[href*='/sp?']",
        ]
        for selector in link_selectors:
            try:
                for link in page.query_selector_all(selector):
                    text = link.inner_text().strip()
                    href = link.get_attribute("href")
                    if text:
                        add_seller(text, href)
                    if len(results) >= self.max_sellers_per_product:
                        return list(results.values())
            except Exception as exc:
                logger.debug(f"Seller fallback selector failed ({selector}): {exc}")

        text_selectors = [
            "#merchant-info",
            "#tabular-buybox",
            "#buybox",
            "#buyBoxAccordion",
            "#aod-container",
            "#all-offers-display",
            "body",
        ]
        for selector in text_selectors:
            try:
                elem = page.query_selector(selector)
                if not elem:
                    continue
                text = elem.inner_text()
                for match in re.finditer(r"(?im)\bSold\s+by\s*[:\-]?\s*([^\n|]+)", text):
                    candidate = match.group(1).strip()
                    candidate = re.split(r"(?i)\s+(?:and\s+)?fulfilled\s+by\s+", candidate)[0]
                    candidate = re.split(r"(?i)\s+(?:Ships|Condition|Delivery|Price)\s*[:\-]", candidate)[0]
                    add_seller(candidate)
                    if len(results) >= self.max_sellers_per_product:
                        return list(results.values())
            except Exception as exc:
                logger.debug(f"Seller fallback text scan failed ({selector}): {exc}")

        if results:
            logger.info(f"Visible seller fallback recovered {len(results)} seller(s) for {asin}")
            print(f"VISIBLE SELLER FALLBACK: {len(results)} seller(s)")
        else:
            logger.info(f"Visible seller fallback found no seller for {asin}")

        return list(results.values())

    def extract_seller_offers(self, product_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        page = self.browser_mgr.new_page()
        enriched_offers = []
        try:
            product_scraper = AmazonProductScraper(
                page=page,
                max_sellers_per_product=self.max_sellers_per_product,
                max_offer_scroll_attempts=self.max_offer_scroll_attempts,
                max_no_new_seller_attempts=self.max_no_new_seller_attempts,
                offer_load_wait_ms=self.offer_load_wait_ms,
                max_product_runtime_seconds=self.max_product_offer_runtime_seconds,
                context_reset_callback=self.browser_mgr.reset_context
            )
            raw_offers = product_scraper.extract_product_sellers(product_info["product_url"])
            if not raw_offers:
                raw_offers = self._fallback_extract_visible_sellers(page, product_info)
            if not raw_offers:
                return enriched_offers

            profile_scraper = AmazonSellerProfileScraper(page)

            for offer in raw_offers:
                seller_name = offer["seller_name"]
                seller_profile_url = offer.get("seller_profile_url")

                offer_details = {
                    "display_name": seller_name,
                    "legal_entity": None,
                    "business_address_raw": None,
                    "gst_number_raw": None,
                    "phone_raw": None,
                    "email_raw": None,
                    "seller_profile_url": seller_profile_url,
                    "price": offer.get("price"),
                    "condition": offer.get("condition", "New"),
                    "source": offer.get("source", "Amazon"),
                    "asin": offer.get("asin", product_info.get("asin")),
                    "product_url": offer.get("product_url", product_info.get("product_url")),
                    "product_title": offer.get("product_title", product_info.get("product_title"))
                }

                if seller_profile_url:
                    try:
                        details = profile_scraper.extract_seller_details(seller_profile_url)
                        if details:
                            if details.get("display_name"):
                                offer_details["display_name"] = details["display_name"]
                            offer_details["legal_entity"] = details.get("legal_entity")
                            offer_details["business_address_raw"] = details.get("business_address_raw")
                            offer_details["gst_number_raw"] = details.get("gst_number_raw")
                            offer_details["phone_raw"] = details.get("phone_raw")
                            offer_details["email_raw"] = details.get("email_raw")
                    except Exception as pe:
                        logger.debug(f"Error extracting seller details from profile URL {seller_profile_url}: {pe}")

                enriched_offers.append(offer_details)

            return enriched_offers

        finally:
            safe_close_page(page)
