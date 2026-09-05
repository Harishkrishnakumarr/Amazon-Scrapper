import logging
import re
from typing import Dict, Any, Optional, List, Tuple
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from scraper.amazon_search import check_amazon_block

logger = logging.getLogger("amazon_scraper")

# Supported phone label patterns (case-insensitive)
PHONE_LABELS = (
    r"(?:Phone(?:\s*(?:Number|No\.?))?"
    r"|Mobile(?:\s*(?:Number|No\.?))?"
    r"|Contact(?:\s*(?:Number|No\.?|Phone|Details))?"
    r"|Customer\s*Service"
    r"|Customer\s*Care"
    r"|Telephone(?:\s*(?:Number|No\.?))?"
    r"|Tel(?:\.?|\s*(?:Number|No\.?))?"
    r"|Business\s*Phone"
    r"|Seller\s*Phone"
    r"|Helpline"
    r"|Toll\s*Free)"
)

# Regex to capture phone number after a label (handles :, -, newlines, spaces)
LABEL_PHONE_REGEX = re.compile(
    rf"(?i)\b{PHONE_LABELS}\b\s*[:\-]?\s*(\+?[\d\s\-()]{{7,25}}\d)"
)

# Standalone Indian phone patterns (Mobile, Landline, Toll-free)
STANDALONE_PHONE_REGEX = re.compile(
    r"(?:\+91[\s\-]*)?(?:\(?0\)?[\s\-]*)?[6-9]\d{4}[\s-]*\d{5}"  # 10-digit mobile (5+5)
    r"|(?:\+91[\s\-]*)?(?:\(?0\)?[\s\-]*)?[6-9]\d{2}[\s-]*\d{3}[\s-]*\d{4}"  # 10-digit mobile (3+3+4)
    r"|(?:\+91[\s\-]*)?(?:\(?0\)?[\s\-]*)?[6-9]\d{9}"  # 10-digit mobile continuous
    r"|(?:\+91[\s\-]*)?(?:\(?0\d{2,4}\)?[\s\-]*)?[1-9]\d{5,7}"  # Landline / STD
    r"|1800[\s\-]*\d{3}[\s\-]*\d{3,4}"  # Toll free 1800
)

# General phone candidate pattern for fallback scoring
PHONE_CANDIDATE_REGEX = re.compile(
    r"(?:\+91[\s\-]*)?(?:\(?0\)?[\s\-]*)?[1-9][\d\s\-()]{7,20}\d"
)


def _clean_phone_candidate(raw_str: str) -> str:
    """Cleans harmless prefix/suffix characters from phone candidate."""
    if not raw_str:
        return ""
    cand = re.sub(r"^(?:tel:|ph:|phone:)\s*", "", raw_str.strip(), flags=re.IGNORECASE).strip()
    # Strip leading/trailing non-digit and non-plus characters
    cand = re.sub(r"^[^\d+]+", "", cand)
    cand = re.sub(r"[^\d]+$", "", cand)
    return cand.strip()


def _is_valid_indian_phone(raw_candidate: str) -> bool:
    """
    Validates whether a raw candidate string is a valid Indian phone number.
    Strictly avoids false positives:
    - 6-digit pincodes (560001, 400001)
    - 15-char GSTINs (29ABCDE1234F1Z5)
    - 10-char PANs (ABCDE1234F)
    - 21-char CINs (L72200KA2012PLC065294)
    - Concatenated pincode + year patterns (5600712026 = 560071 + 2026)
    - Repetitive dummy numbers (0000000000, 1111111111)
    - Dummy sequences (123456789012, 1234567890)
    - Prices, order IDs, ASINs
    """
    if not raw_candidate or not isinstance(raw_candidate, str):
        return False

    candidate = _clean_phone_candidate(raw_candidate)
    if not candidate:
        return False

    # Disallow alphabetic characters (GSTIN, PAN, ASIN, CIN, etc.)
    if re.search(r"[a-zA-Z]", candidate):
        return False

    # Extract digits only
    digits = re.sub(r"\D", "", candidate)
    digit_len = len(digits)

    # Valid Indian phone numbers must have 10 to 13 digits
    if digit_len < 10 or digit_len > 13:
        return False

    # Reject repetitive single/double digits (e.g. 0000000000, 9999999999, 1111111111)
    if len(set(digits)) <= 2:
        return False

    # Reject dummy test sequences
    if digits in ("1234567890", "0123456789", "0987654321", "123456789012"):
        return False

    # Reject pincode + year concatenations (e.g. 5600712026 = 560071 + 2026)
    if digit_len == 10 and re.match(r"^[1-9]\d{5}20[1-3]\d$", digits):
        return False

    # Determine core 10 digits
    core_10 = ""
    if digit_len == 10:
        core_10 = digits
    elif digit_len == 11:
        if digits.startswith("0"):
            core_10 = digits[1:]
        elif digits.startswith("1800") or digits.startswith("1860"):
            core_10 = digits[1:]
        else:
            return False
    elif digit_len == 12:
        if digits.startswith("91"):
            core_10 = digits[2:]
        elif digits.startswith("01800") or digits.startswith("01860"):
            core_10 = digits[2:]
        else:
            return False
    elif digit_len == 13:
        if digits.startswith("091"):
            core_10 = digits[3:]
        else:
            return False

    if len(core_10) != 10:
        return False

    # Core 10 cannot start with 0
    if core_10[0] == "0":
        return False

    # Check for pincode + year in core_10
    if re.match(r"^[1-9]\d{5}20[1-3]\d$", core_10):
        return False

    return True


def _extract_labeled_phone(text: str) -> Optional[str]:
    """Searches for labeled phone patterns in text."""
    if not text:
        return None
    for match in LABEL_PHONE_REGEX.finditer(text):
        raw_val = match.group(1).strip()
        cand = _clean_phone_candidate(raw_val)
        if _is_valid_indian_phone(cand):
            return cand
    return None


def _extract_standalone_phone(text: str) -> Optional[str]:
    """Searches for standalone Indian phone number patterns in text."""
    if not text:
        return None
    for match in STANDALONE_PHONE_REGEX.finditer(text):
        raw_val = match.group(0).strip()
        cand = _clean_phone_candidate(raw_val)
        if _is_valid_indian_phone(cand):
            return cand
    return None


class AmazonSellerProfileScraper:
    """
    Scrapes publicly accessible Amazon seller detail pages (/sp?seller=...)
    to extract business display name, legal entity, business address, and registered details.
    Enforces 20s timeout and 503/CAPTCHA checks.
    """
    def __init__(self, page: Page, timeout_ms: int = 20000):
        self.page = page
        self.timeout_ms = timeout_ms

    def _extract_from_dom_selectors(self) -> Optional[str]:
        """Strategy 1: Inspect explicit phone/contact/customer-service DOM elements."""
        try:
            # 1a. Check tel: links
            tel_links = self.page.query_selector_all('a[href^="tel:"]')
            for link in tel_links:
                href = link.get_attribute("href") or ""
                phone_val = _clean_phone_candidate(re.sub(r"^tel:\s*", "", href, flags=re.IGNORECASE))
                if _is_valid_indian_phone(phone_val):
                    return phone_val
                text = _clean_phone_candidate(link.inner_text())
                if _is_valid_indian_phone(text):
                    return text

            # 1b. Check elements with phone/contact selectors
            selectors = [
                "#seller-phone",
                ".seller-phone",
                "[data-testid*='phone' i]",
                "[aria-label*='phone' i]",
                "[aria-label*='contact' i]",
                "[id*='seller-contact' i]",
                "[class*='seller-contact' i]",
                "[id*='customer-service-phone' i]",
                "[class*='customer-service-phone' i]",
            ]
            for sel in selectors:
                elems = self.page.query_selector_all(sel)
                for elem in elems:
                    text = elem.inner_text().strip()
                    if not text:
                        continue
                    labeled = _extract_labeled_phone(text)
                    if labeled:
                        return labeled
                    standalone = _extract_standalone_phone(text)
                    if standalone:
                        return standalone
        except Exception as e:
            logger.debug(f"DOM selector phone extraction error: {e}")
        return None

    def _extract_from_seller_info_text(self, text_content: str) -> Optional[str]:
        """Strategy 2: Extract phone number from seller information container text."""
        if not text_content:
            return None

        # 2a. Labeled regex search
        labeled = _extract_labeled_phone(text_content)
        if labeled:
            return labeled

        # 2b. Line-by-line inspection for labels and adjacent values
        lines = [l.strip() for l in text_content.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            if re.search(rf"(?i)\b{PHONE_LABELS}\b", line):
                clean_line = _clean_phone_candidate(re.sub(rf"(?i)\b{PHONE_LABELS}\b\s*[:\-]?", "", line))
                if _is_valid_indian_phone(clean_line):
                    return clean_line
                cand = _extract_standalone_phone(clean_line)
                if cand:
                    return cand
                if i + 1 < len(lines):
                    next_line = _clean_phone_candidate(lines[i + 1])
                    if _is_valid_indian_phone(next_line):
                        return next_line
                    cand_next = _extract_standalone_phone(next_line)
                    if cand_next:
                        return cand_next

        # 2c. Standalone search within seller info text
        return _extract_standalone_phone(text_content)

    def _extract_from_other_seller_elements(self) -> Optional[str]:
        """Strategy 3: Inspect other relevant Amazon seller-page DOM elements."""
        try:
            container_selectors = [
                "#page-section-detail-seller-info",
                "#seller-info",
                "div.a-box-group",
                "div#page-section-detail-seller-info div.a-box",
                "div.a-section.a-spacing-medium",
                "div#sellerName ~ div"
            ]
            for sel in container_selectors:
                elems = self.page.query_selector_all(sel)
                for elem in elems:
                    text = elem.inner_text().strip()
                    if not text:
                        continue
                    cand = self._extract_from_seller_info_text(text)
                    if cand:
                        return cand
        except Exception as e:
            logger.debug(f"Other seller elements extraction error: {e}")
        return None

    def _extract_from_full_page_fallback(self) -> Optional[str]:
        """
        Strategy 4: Final fallback on full visible page text using candidate scoring.
        Rejects false positives (prices, GST, PAN, pincode, ASIN, order numbers, reviews).
        """
        try:
            page_text = self.page.inner_text("body")
            if not page_text:
                return None

            candidates = []
            for match in PHONE_CANDIDATE_REGEX.finditer(page_text):
                cand_str = _clean_phone_candidate(match.group(0))
                if not _is_valid_indian_phone(cand_str):
                    continue

                start_pos, end_pos = match.span()

                # Extract line containing candidate
                line_start = page_text.rfind("\n", 0, start_pos)
                line_start = 0 if line_start == -1 else line_start + 1
                line_end = page_text.find("\n", end_pos)
                line_end = len(page_text) if line_end == -1 else line_end
                line = page_text[line_start:line_end].lower()

                # Extract immediate context window (80 characters around candidate)
                ctx_start = max(0, start_pos - 80)
                ctx_end = min(len(page_text), end_pos + 80)
                context = page_text[ctx_start:ctx_end].lower()

                score = 10  # base score

                # Positive context scoring (line level and context level)
                if re.search(r"\b(phone|mobile|contact|telephone|customer\s*service|customer\s*care|tel|helpline|call)\b", line):
                    score += 40
                elif re.search(r"\b(phone|mobile|contact|telephone|customer\s*service|customer\s*care|tel|helpline|call)\b", context):
                    score += 25

                if re.search(r"\b(seller|merchant|business)\b", context):
                    score += 10

                if cand_str.startswith("+91") or cand_str.startswith("91 "):
                    score += 10

                digits = re.sub(r"\D", "", cand_str)
                if (digits.startswith("91") and len(digits) == 12 and digits[2] in "6789") or (len(digits) == 10 and digits[0] in "6789"):
                    score += 10

                # Negative context penalties / rejections
                if re.search(r"\b(₹|inr|rs\.?|price|mrp|discount|save)\b", line):
                    score -= 40
                if re.search(r"\b(gstin|gst|pan|cin|pincode|pin\s*code|postal)\b", line):
                    score -= 40
                if re.search(r"\b(asin|order|shipment|delivery|tracking|tracking_id)\b", line):
                    score -= 40
                if re.search(r"\b(rating|ratings|star|stars|feedback|review|reviews|positive|critical)\b", line):
                    score -= 40
                if re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b", line):
                    score -= 30

                if score >= 25:
                    candidates.append((score, cand_str))

            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                return candidates[0][1]

        except Exception as e:
            logger.debug(f"Full-page fallback extraction error: {e}")
        return None

    def extract_seller_details(self, seller_profile_url: str) -> Dict[str, Any]:
        result = {
            "display_name": None,
            "legal_entity": None,
            "business_address_raw": None,
            "gst_number_raw": None,
            "phone_raw": None,
            "email_raw": None,
            "seller_profile_url": seller_profile_url
        }

        if not seller_profile_url:
            return result

        logger.info(f"Opening Amazon seller profile page: {seller_profile_url}")
        try:
            response = self.page.goto(seller_profile_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            
            is_blocked, block_reason = check_amazon_block(response, self.page)
            if is_blocked:
                logger.warning(f"Amazon seller profile page blocked ({block_reason}): {seller_profile_url}")
                return result

            # 1. Seller Header / Display Name
            header_elem = self.page.query_selector("#seller-name, h1#sellerName, h1")
            if header_elem:
                result["display_name"] = header_elem.inner_text().strip()

            # 2. Detailed Business Information Section (Detailed Seller Information block)
            seller_info_div = self.page.query_selector("#seller-info, div.a-box-group, div#page-section-detail-seller-info")
            text_content = ""
            if seller_info_div:
                text_content = seller_info_div.inner_text()
                
                # Extract Legal Entity Name
                legal_match = re.search(r"(?i)(Business\s*Name|Detailed\s*Seller\s*Information|Legal\s*Name|Trade\s*Name):\s*([^\n]+)", text_content)
                if legal_match:
                    result["legal_entity"] = legal_match.group(2).strip()

                # Extract Business Address
                addr_match = re.search(r"(?i)(Business\s*Address|Address):\s*([\s\S]+?)(?=\n\n|\n[A-Z][a-z]+:|$)", text_content)
                if addr_match:
                    result["business_address_raw"] = addr_match.group(2).strip()
                else:
                    # Fallback line extraction for address block
                    lines = [l.strip() for l in text_content.split("\n") if l.strip()]
                    if len(lines) > 2:
                        address_lines = [line.strip() for line in lines[1:] if line.strip()]
                        result["business_address_raw"] = ", ".join(address_lines)

                # Extract GSTIN if displayed
                gst_match = re.search(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}\b", text_content)
                if gst_match:
                    result["gst_number_raw"] = gst_match.group(0)

                # Extract Email if displayed
                email_match = re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text_content)
                if email_match:
                    result["email_raw"] = email_match.group(0)

            # 3. Multi-Strategy Phone Number Extraction
            phone_extracted = None

            # Strategy 1: Explicit phone/contact DOM elements
            phone_extracted = self._extract_from_dom_selectors()

            # Strategy 2: Seller Information Container Text
            if not phone_extracted and text_content:
                phone_extracted = self._extract_from_seller_info_text(text_content)

            # Strategy 3: Relevant Amazon seller-page DOM elements
            if not phone_extracted:
                phone_extracted = self._extract_from_other_seller_elements()

            # Strategy 4: Full visible seller-page text fallback with candidate scoring
            if not phone_extracted:
                phone_extracted = self._extract_from_full_page_fallback()

            # Strategy 5: Legacy regex fallback
            if not phone_extracted and text_content:
                phone_match = re.search(r"(?i)(Phone|Contact|Customer\s*Service):\s*([+\d\s-]{10,15})", text_content)
                if phone_match:
                    legacy_cand = _clean_phone_candidate(phone_match.group(2))
                    if _is_valid_indian_phone(legacy_cand):
                        phone_extracted = legacy_cand

            result["phone_raw"] = phone_extracted
            return result

        except PlaywrightTimeoutError:
            logger.warning(f"Timeout opening seller profile page ({self.timeout_ms}ms): {seller_profile_url}")
            return result
        except Exception as e:
            logger.error(f"Error scraping seller profile page {seller_profile_url}: {e}")
            return result
