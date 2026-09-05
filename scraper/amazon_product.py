import logging
import re
import time
from typing import List, Dict, Any, Optional, Tuple
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from scraper.amazon_search import check_amazon_block
from extraction.normalizer import normalize_seller_key

logger = logging.getLogger("amazon_scraper")

class AmazonProductScraper:
    """
    Navigates to an Amazon product detail page (/dp/ASIN),
    extracts the primary Buy-Box seller AND all additional publicly accessible sellers
    from 'Other Sellers on Amazon' / 'All Offers Display' / 'New & Used' panels.
    
    Implements a controlled collection loop that continuously scrolls the offer container,
    waits for lazy-loaded offers, handles pagination/load-more controls, and deduplicates
    sellers up to max_sellers_per_product (default 100).
    """
    def __init__(
        self,
        page: Page,
        max_sellers_per_product: int = 100,
        max_offer_scroll_attempts: int = 60,
        max_no_new_seller_attempts: int = 8,
        offer_load_wait_ms: int = 1500,
        max_product_runtime_seconds: int = 180
    ):
        self.page = page
        self.max_sellers_per_product = max_sellers_per_product
        self.max_offer_scroll_attempts = max_offer_scroll_attempts
        self.max_no_new_seller_attempts = max_no_new_seller_attempts
        self.offer_load_wait_ms = offer_load_wait_ms
        self.max_product_runtime_seconds = max_product_runtime_seconds
        self.duplicates_removed = 0

    def extract_product_sellers(self, product_url: str) -> List[Dict[str, Any]]:
        collected_sellers: Dict[str, Dict[str, Any]] = {}
        self.duplicates_removed = 0
        start_time = time.time()

        logger.info(f"Opening Amazon product page for multi-seller extraction: {product_url}")
        page_loaded = False
        for attempt in range(1, 4):
            try:
                response = self.page.goto(product_url, wait_until="domcontentloaded", timeout=30000)
                is_blocked, block_reason = check_amazon_block(response, self.page)
                if is_blocked:
                    logger.warning(f"Amazon product page blocked ({block_reason}) on attempt {attempt}/3: {product_url}")
                    if attempt < 3:
                        time.sleep(2 * attempt)
                        continue
                    return []
                page_loaded = True
                break
            except PlaywrightTimeoutError:
                logger.warning(f"Timeout opening product page (attempt {attempt}/3): {product_url}")
                if attempt < 3:
                    time.sleep(2 * attempt)
                    continue
            except Exception as e:
                logger.warning(f"Error opening product page (attempt {attempt}/3): {e}")
                if attempt < 3:
                    time.sleep(2 * attempt)
                    continue

        if not page_loaded:
            logger.error(f"Failed to load product page after 3 attempts: {product_url}")
            return []

        try:
            # -------------------------------------------------------------
            # Extract ASIN and Product Title
            # -------------------------------------------------------------
            asin, product_title = self._extract_asin_and_title(product_url)
            logger.info(f"PRODUCT: {product_title}")
            logger.info(f"ASIN: {asin}")
            print(f"\nPRODUCT: {product_title}")
            print(f"ASIN: {asin}")

            # -------------------------------------------------------------
            # STEP 1: Extract Primary Buy-Box Seller
            # -------------------------------------------------------------
            buybox_seller = self._extract_buybox_seller()
            if buybox_seller and buybox_seller["seller_name"]:
                b_name = buybox_seller["seller_name"]
                logger.info(f"BUY BOX SELLER: {b_name}")
                print(f"BUY BOX SELLER: {b_name}")
                
                s_key = normalize_seller_key(b_name)
                if s_key and not s_key.startswith("amazon"):
                    collected_sellers[s_key] = {
                        "asin": asin,
                        "seller_name": b_name,
                        "seller_profile_url": buybox_seller.get("seller_profile_url"),
                        "product_title": product_title,
                        "product_url": product_url,
                        "price": buybox_seller.get("price"),
                        "condition": "New",
                        "source": "Amazon (Buy Box)"
                    }
            else:
                logger.info("BUY BOX SELLER: None")
                print("BUY BOX SELLER: None")

            # Check timeout
            if time.time() - start_time > self.max_product_runtime_seconds:
                logger.warning(f"OFFER EXTRACTION TIMEOUT on {asin} (elapsed: {time.time() - start_time:.1f}s)")
                return list(collected_sellers.values())

            # -------------------------------------------------------------
            # STEP 2: Open "Other sellers" / "All offers" (AOD)
            # -------------------------------------------------------------
            logger.info("AOD: Opening All Offers...")
            print("AOD: Opening All Offers...")
            aod_opened = self._open_all_offers_panel()

            # -------------------------------------------------------------
            # STEP 3: Controlled Multi-Offer Collection Loop
            # -------------------------------------------------------------
            if aod_opened or self.page.query_selector("#all-offers-display, #aod-offer-list, div#aod-offer, div.aod-offer-container"):
                self._collect_all_aod_offers(
                    asin=asin,
                    product_title=product_title,
                    product_url=product_url,
                    collected_sellers=collected_sellers,
                    start_time=start_time
                )
            else:
                logger.warning("WARNING: AOD offer container not found. FALLBACK: Existing seller extraction")

            # -------------------------------------------------------------
            # STEP 4: Inline 'Other Sellers' Fallback (if present & limit not reached)
            # -------------------------------------------------------------
            if len(collected_sellers) < self.max_sellers_per_product and (time.time() - start_time <= self.max_product_runtime_seconds):
                self._collect_inline_other_sellers(
                    asin=asin,
                    product_title=product_title,
                    product_url=product_url,
                    collected_sellers=collected_sellers
                )

            # Completion logging
            total_unique = len(collected_sellers)
            logger.info(f"AOD EXTRACTION COMPLETE | ASIN: {asin} | TOTAL UNIQUE SELLERS: {total_unique}")
            print("\nAOD EXTRACTION COMPLETE")
            print(f"ASIN: {asin}")
            print(f"TOTAL UNIQUE SELLERS: {total_unique}\n")

            return list(collected_sellers.values())

        except PlaywrightTimeoutError:
            logger.error(f"Timeout loading product page (30s): {product_url}")
            return list(collected_sellers.values())
        except Exception as e:
            logger.error(f"Error extracting multi-sellers for {product_url}: {e}", exc_info=True)
            return list(collected_sellers.values())

    def _extract_asin_and_title(self, product_url: str) -> Tuple[str, str]:
        asin = ""
        product_title = ""

        # ASIN from URL
        asin_match = re.search(r"/dp/([A-Z0-9]{10})", product_url)
        if asin_match:
            asin = asin_match.group(1)

        # ASIN from DOM if URL did not match
        if not asin:
            try:
                asin_input = self.page.query_selector("input#ASIN, input[name='ASIN'], div[data-asin]")
                if asin_input:
                    asin = asin_input.get_attribute("value") or asin_input.get_attribute("data-asin") or ""
            except Exception:
                pass

        # Product Title
        try:
            title_elem = self.page.query_selector("span#productTitle, #productTitle, h1#title span, h1")
            if title_elem:
                raw_t = title_elem.inner_text().strip()
                raw_t = re.sub(r"(?i)Product\s*summary\s*presents\s*key.*$", "", raw_t).strip()
                raw_t = re.sub(r"(?i)Keyboard\s*shortcut.*$", "", raw_t).strip()
                if raw_t:
                    product_title = raw_t
        except Exception:
            pass

        if not product_title:
            product_title = f"Amazon Product {asin}"

        return asin, product_title

    def _extract_buybox_seller(self) -> Optional[Dict[str, Any]]:
        seller_name = None
        seller_profile_url = None
        price = None

        try:
            # Price extraction
            price_elem = self.page.query_selector(
                "#corePrice_feature_div .a-offscreen, #corePriceDisplay_desktop_feature_div .a-offscreen, "
                "#priceblock_ourprice, .a-price .a-offscreen, #priceblock_dealprice"
            )
            if price_elem:
                price = price_elem.inner_text().strip()

            # Buy Box pattern 1
            seller_link = self.page.query_selector("#sellerProfileTriggerId, #merchant-info a, #tabular-buybox span.tabular-buybox-text a")
            if seller_link:
                seller_name = seller_link.inner_text().strip()
                href = seller_link.get_attribute("href")
                if href:
                    seller_profile_url = f"https://www.amazon.in{href}" if href.startswith("/") else href

            # Buy Box pattern 2
            if not seller_name:
                merchant_div = self.page.query_selector("#merchant-info")
                if merchant_div:
                    merchant_text = merchant_div.inner_text().strip()
                    match = re.search(r"(?i)sold\s+by\s+([^\n,\.\&\(]+)", merchant_text)
                    if match:
                        candidate = match.group(1).strip()
                        if candidate:
                            seller_name = candidate

            # Buy Box pattern 3
            if not seller_name:
                tabular_rows = self.page.query_selector_all("#tabular-buybox .tabular-buybox-row")
                for row in tabular_rows:
                    if "Sold by" in row.inner_text():
                        value_span = row.query_selector(".tabular-buybox-text")
                        if value_span:
                            candidate = value_span.inner_text().strip()
                            if candidate:
                                seller_name = candidate
                                link_elem = value_span.query_selector("a")
                                if link_elem:
                                    href = link_elem.get_attribute("href")
                                    if href:
                                        seller_profile_url = f"https://www.amazon.in{href}" if href.startswith("/") else href

            if seller_name:
                seller_name = re.sub(r"(?i)\s*(and|&)\s*fulfilled.*$", "", seller_name).strip()
                seller_name = re.sub(r"(?i)^\s*sold\s+by\s*", "", seller_name).strip()
        except Exception as e:
            logger.debug(f"Buy Box extraction exception: {e}")

        return {
            "seller_name": seller_name,
            "seller_profile_url": seller_profile_url,
            "price": price
        }

    def _open_all_offers_panel(self) -> bool:
        """Attempts to find and click the All Offers / Other Sellers ingress link."""
        ingress_selectors = [
            "#aod-ingress-link",
            "#buybox-see-all-buying-choices",
            "a[href*='offer-listing']",
            "#olpLinkWidget a",
            "span:has-text('Other Sellers on Amazon')",
            "a:has-text('See All Buying Options')",
            "a:has-text('Compare Other Sellers')",
            "#all-offers-display-ingress",
            "[data-action='show-all-offers-display']",
            "#olp_feature_div a",
            "a#pinned-de-ingress-link",
            "span.a-declarative[data-action='a-modal'][data-a-modal*='all-offers-display']"
        ]

        for sel in ingress_selectors:
            try:
                elem = self.page.query_selector(sel)
                if elem and elem.is_visible():
                    elem.click(timeout=4000)
                    self.page.wait_for_timeout(1000)
                    try:
                        self.page.wait_for_selector(
                            "#all-offers-display, #aod-offer-list, #all-offers-display-scroller, div#aod-offer, div.aod-offer-container",
                            timeout=5000
                        )
                        return True
                    except Exception:
                        return True
            except Exception:
                continue

        return False

    def _scroll_offer_container(self) -> bool:
        """Scrolls the AOD side panel or window to trigger lazy-loading of additional offers."""
        scrolled = False
        try:
            # Scroll within AOD container if present
            js_scroll = """
            () => {
                const scroller = document.querySelector(
                    '#all-offers-display-scroller, #all-offers-display, #aod-offer-list, .aod-popover-content, .a-popover-inner, #aod-container'
                );
                if (scroller) {
                    scroller.scrollTop = scroller.scrollHeight;
                    return true;
                }
                return false;
            }
            """
            result = self.page.evaluate(js_scroll)
            if result:
                scrolled = True

            # Also scroll the last offer element into view
            offer_elems = self.page.query_selector_all(
                "#aod-pinned-offer, div#aod-offer, div.aod-offer-container, #aod-offer-list > div.a-section"
            )
            if offer_elems:
                try:
                    offer_elems[-1].scroll_into_view_if_needed(timeout=2000)
                    scrolled = True
                except Exception:
                    pass

            # Fallback page scroll
            self.page.evaluate("window.scrollBy(0, 800);")
        except Exception as e:
            logger.debug(f"Scroll offer container note: {e}")

        return scrolled

    def _click_offer_load_more(self) -> bool:
        """Detects and clicks offer load-more / pagination controls if present."""
        load_more_selectors = [
            "#aod-load-more",
            "input[name='submit.load-more']",
            "button:has-text('Load more')",
            "a:has-text('Load more')",
            "a:has-text('See more')",
            "button:has-text('See more')",
            "button:has-text('Show more')",
            "a:has-text('Show more')",
            "#aod-show-more-offers",
            "[data-action='aod-load-more-offers']",
            "a[id*='aod-page-']",
            ".aod-pagination a.s-pagination-next",
            "li.a-last a"
        ]

        for sel in load_more_selectors:
            try:
                btn = self.page.query_selector(sel)
                if btn and btn.is_visible():
                    logger.info(f"Clicking offer load-more control: {sel}")
                    btn.click(timeout=3000)
                    self.page.wait_for_timeout(500)
                    return True
            except Exception:
                continue

        return False

    def _parse_aod_offer_element(self, offer_elem) -> Optional[Dict[str, Any]]:
        """Parses a single offer DOM container to extract seller name, profile URL, price, and condition."""
        try:
            seller_link = offer_elem.query_selector(
                "div#aod-offer-ships-from-sold-by a, div#aod-offer-sold-by a, "
                "a[aria-label*='Seller'], a[href*='seller='], a[href*='/sp?'], "
                "#aod-offer-sold-by a"
            )
            seller_name = None
            seller_profile_url = None

            if seller_link:
                raw_text = seller_link.inner_text().strip()
                if raw_text:
                    seller_name = raw_text
                href = seller_link.get_attribute("href")
                if href:
                    seller_profile_url = f"https://www.amazon.in{href}" if href.startswith("/") else href

            if not seller_name:
                sold_by_div = offer_elem.query_selector(
                    "div#aod-offer-sold-by, div#aod-offer-ships-from-sold-by, "
                    "#aod-offer-sold-by, span.aod-offer-sold-by"
                )
                if sold_by_div:
                    txt = sold_by_div.inner_text().strip()
                    match = re.search(r"(?i)sold\s+by\s+([^\n,]+)", txt)
                    if match:
                        seller_name = match.group(1).strip()

            if not seller_name:
                # Disallow picking unrelated product / review / navigation text
                return None

            # Clean seller name
            seller_name = re.sub(r"(?i)\s*(and|&)\s*fulfilled.*$", "", seller_name).strip()
            seller_name = re.sub(r"(?i)^\s*sold\s+by\s*", "", seller_name).strip()

            if not seller_name or seller_name.lower() in ("amazon", "amazon.in", "amazon retail"):
                return None

            # Price extraction
            price_elem = offer_elem.query_selector(
                "span.a-price span.a-offscreen, span.a-price, div.aod-price-1 .a-price .a-offscreen, .a-price-whole"
            )
            price = price_elem.inner_text().strip() if price_elem else None
            if price:
                price = re.sub(r"\s+", " ", price).replace(" . ", ".").strip()

            # Condition extraction
            cond_elem = offer_elem.query_selector("div#aod-offer-heading, div#aod-offer-condition, #aod-offer-heading span")
            condition = cond_elem.inner_text().strip() if cond_elem else "New"

            return {
                "seller_name": seller_name,
                "seller_profile_url": seller_profile_url,
                "price": price,
                "condition": condition
            }
        except Exception:
            return None

    def _collect_all_aod_offers(
        self,
        asin: str,
        product_title: str,
        product_url: str,
        collected_sellers: Dict[str, Dict[str, Any]],
        start_time: float
    ):
        """
        Executes controlled collection loop over AOD panel:
        Wait -> Scan -> Scroll -> Wait -> Re-scan -> Load more -> Repeat until max_sellers_per_product or exhausted.
        """
        scroll_attempt = 0
        no_new_attempts = 0
        offer_selector = "#aod-pinned-offer, div#aod-offer, div.aod-offer-container, #aod-offer-list > div.a-section, div[id='aod-offer']"

        # Scan Initial Offers
        initial_elems = self.page.query_selector_all(offer_selector)
        initial_valid_offers = 0
        
        for elem in initial_elems:
            s_info = self._parse_aod_offer_element(elem)
            if s_info and s_info["seller_name"]:
                initial_valid_offers += 1
                s_key = normalize_seller_key(s_info["seller_name"])
                if s_key and not s_key.startswith("amazon"):
                    if s_key not in collected_sellers:
                        collected_sellers[s_key] = {
                            "asin": asin,
                            "seller_name": s_info["seller_name"],
                            "seller_profile_url": s_info.get("seller_profile_url"),
                            "product_title": product_title,
                            "product_url": product_url,
                            "price": s_info.get("price"),
                            "condition": s_info.get("condition", "New"),
                            "source": "Amazon (Other Sellers)"
                        }
                    else:
                        self.duplicates_removed += 1

        logger.info(f"AOD: Initial offers found: {initial_valid_offers}")
        print(f"AOD: Initial offers found: {initial_valid_offers}")

        if len(collected_sellers) >= self.max_sellers_per_product:
            logger.info(f"SELLER LIMIT REACHED: {self.max_sellers_per_product}")
            print(f"SELLER LIMIT REACHED: {self.max_sellers_per_product}")
            return

        # Collection Loop
        while scroll_attempt < self.max_offer_scroll_attempts and len(collected_sellers) < self.max_sellers_per_product:
            # Check safety timeout
            elapsed = time.time() - start_time
            if elapsed > self.max_product_runtime_seconds:
                logger.warning(
                    f"OFFER EXTRACTION TIMEOUT\n"
                    f"ASIN: {asin}\n"
                    f"SELLERS COLLECTED: {len(collected_sellers)}\n"
                    f"STATUS: Continuing with collected sellers"
                )
                print(
                    f"\nOFFER EXTRACTION TIMEOUT\n"
                    f"ASIN: {asin}\n"
                    f"SELLERS COLLECTED: {len(collected_sellers)}\n"
                    f"STATUS: Continuing with collected sellers\n"
                )
                break

            scroll_attempt += 1
            before_count = len(collected_sellers)

            # Scroll container
            self._scroll_offer_container()

            # Check for Load more / Pagination
            self._click_offer_load_more()

            # Wait for lazy loading
            self.page.wait_for_timeout(self.offer_load_wait_ms)

            # Re-query DOM for newly loaded offers
            current_elems = self.page.query_selector_all(offer_selector)

            for elem in current_elems:
                s_info = self._parse_aod_offer_element(elem)
                if s_info and s_info["seller_name"]:
                    s_key = normalize_seller_key(s_info["seller_name"])
                    if s_key and not s_key.startswith("amazon"):
                        if s_key not in collected_sellers:
                            collected_sellers[s_key] = {
                                "asin": asin,
                                "seller_name": s_info["seller_name"],
                                "seller_profile_url": s_info.get("seller_profile_url"),
                                "product_title": product_title,
                                "product_url": product_url,
                                "price": s_info.get("price"),
                                "condition": s_info.get("condition", "New"),
                                "source": "Amazon (Other Sellers)"
                            }
                            if len(collected_sellers) >= self.max_sellers_per_product:
                                break
                        else:
                            self.duplicates_removed += 1

            if len(collected_sellers) >= self.max_sellers_per_product:
                logger.info(f"SELLER LIMIT REACHED: {self.max_sellers_per_product}")
                print(f"SELLER LIMIT REACHED: {self.max_sellers_per_product}")
                break

            after_count = len(collected_sellers)

            if after_count > before_count:
                no_new_attempts = 0
                logger.info(f"AOD: Scroll {scroll_attempt} -> total unique sellers: {after_count}")
                print(f"AOD: Scroll {scroll_attempt} -> total unique sellers: {after_count}")
            else:
                no_new_attempts += 1
                logger.info(f"AOD: No new sellers found (attempt {no_new_attempts}/{self.max_no_new_seller_attempts})")
                print(f"AOD: No new sellers found (attempt {no_new_attempts}/{self.max_no_new_seller_attempts})")
                
                if no_new_attempts >= self.max_no_new_seller_attempts:
                    # Final check for load-more button before stopping
                    has_more = self._click_offer_load_more()
                    if has_more:
                        self.page.wait_for_timeout(self.offer_load_wait_ms)
                        no_new_attempts = 0
                    else:
                        logger.info("AMAZON OFFER LIST EXHAUSTED")
                        print("AMAZON OFFER LIST EXHAUSTED")
                        break

    def _collect_inline_other_sellers(
        self,
        asin: str,
        product_title: str,
        product_url: str,
        collected_sellers: Dict[str, Dict[str, Any]]
    ):
        """Fallback to collect sellers from inline product page widgets."""
        try:
            inline_boxes = self.page.query_selector_all(
                "#mbc div.a-box, #olp-upd-new-used a, div#olp-upd-new-used, div.olp-touch-link"
            )
            for elem in inline_boxes:
                if len(collected_sellers) >= self.max_sellers_per_product:
                    break
                link = elem.query_selector("a[href*='seller='], a[href*='/sp?']")
                if link:
                    s_name = link.inner_text().strip()
                    href = link.get_attribute("href")
                    if s_name:
                        s_key = normalize_seller_key(s_name)
                        if s_key and not s_key.startswith("amazon"):
                            if s_key not in collected_sellers:
                                s_profile = f"https://www.amazon.in{href}" if href and href.startswith("/") else href
                                collected_sellers[s_key] = {
                                    "asin": asin,
                                    "seller_name": s_name,
                                    "seller_profile_url": s_profile,
                                    "product_title": product_title,
                                    "product_url": product_url,
                                    "price": None,
                                    "condition": "New",
                                    "source": "Amazon (Other Sellers Widget)"
                                }
                            else:
                                self.duplicates_removed += 1
        except Exception as e:
            logger.debug(f"Inline other sellers extraction note: {e}")
