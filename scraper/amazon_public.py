import logging
from typing import List, Dict, Any, Optional
from scraper.base import SellerDiscoverySource
from scraper.browser import BrowserManager, safe_close_page
from scraper.amazon_search import AmazonSearchScraper
from scraper.amazon_product import AmazonProductScraper
from scraper.amazon_seller import AmazonSellerProfileScraper

logger = logging.getLogger("amazon_scraper")

class AmazonPublicSource(SellerDiscoverySource):
    """
    Initial permitted implementation of SellerDiscoverySource for Amazon public pages.
    Discovers products and extracts ALL publicly accessible seller offers per product.
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
            search_scraper = AmazonSearchScraper(page)
            products = search_scraper.discover_products(search_url, limit=limit, max_pages=max_pages, category_name=category_name)
            return products
        finally:
            safe_close_page(page)

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
                max_product_runtime_seconds=self.max_product_offer_runtime_seconds
            )
            raw_offers = product_scraper.extract_product_sellers(product_info["product_url"])
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

                # Extract seller business details from seller profile URL if present
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
