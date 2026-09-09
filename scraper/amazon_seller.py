import os
import re
import time
import logging
from typing import Dict, Any, Optional, List, Tuple
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from scraper.browser import random_delay

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

# Regex to capture phone number after a label
LABEL_PHONE_REGEX = re.compile(
    rf"(?i)\b{PHONE_LABELS}\b\s*[:\-]?\s*(\+?[\d\s\-()]{{7,25}}\d)"
)

# Standalone Indian phone patterns (Mobile, Landline, Toll-free)
STANDALONE_PHONE_REGEX = re.compile(
    r"(?:\+91[\s\-]*)?(?:\(?0\)?[\s\-]*)?[6-9]\d{4}[\s-]*\d{5}"
    r"|(?:\+91[\s\-]*)?(?:\(?0\)?[\s\-]*)?[6-9]\d{2}[\s-]*\d{3}[\s-]*\d{4}"
    r"|(?:\+91[\s\-]*)?(?:\(?0\)?[\s\-]*)?[6-9]\d{9}"
    r"|(?:\+91[\s\-]*)?(?:\(?0\d{2,4}\)?[\s\-]*)?[1-9]\d{5,7}"
    r"|1800[\s\-]*\d{3}[\s\-]*\d{3,4}"
)

# General phone candidate pattern for fallback scoring
PHONE_CANDIDATE_REGEX = re.compile(
    r"(?:\+91[\s\-]*)?(?:\(?0\)?[\s\-]*)?[1-9][\d\s\-()]{7,20}\d"
)

# Regex patterns for business attributes
BUSINESS_NAME_REGEX = re.compile(
    r"(?i)(?:Business\s*Name|Legal\s*Name|Trade\s*Name|Detailed\s*Seller\s*Information)\s*[:\-]?\s*([^\n\r]+)"
)
GST_REGEX = re.compile(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b")
PAN_REGEX = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z]{1})\b")
FSSAI_REGEX = re.compile(r"\b([12]\d{13})\b")
EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

DISALLOWED_EMAIL_DOMAINS = [
    "marketplace.amazon.in",
    "amazon.com",
    "amazon.in",
    "amazon.co.uk",
    "api-services-support"
]

def _clean_phone_candidate(raw_str: str) -> str:
    """Cleans harmless prefix/suffix characters from phone candidate."""
    if not raw_str:
        return ""
    cand = re.sub(r"^(?:tel:|ph:|phone:)\s*", "", raw_str.strip(), flags=re.IGNORECASE).strip()
    cand = re.sub(r"^[^\d+]+", "", cand)
    cand = re.sub(r"[^\d]+$", "", cand)
    return cand.strip()

def _is_valid_indian_phone(raw_candidate: str) -> bool:
    """Validates whether a raw candidate string is a valid Indian phone number."""
    if not raw_candidate or not isinstance(raw_candidate, str):
        return False

    candidate = _clean_phone_candidate(raw_candidate)
    if not candidate:
        return False

    if re.search(r"[a-zA-Z]", candidate):
        return False

    digits = re.sub(r"\D", "", candidate)
    digit_len = len(digits)

    if digit_len < 10 or digit_len > 13:
        return False

    if len(set(digits)) <= 2:
        return False

    if digits in ("1234567890", "0123456789", "0987654321", "123456789012"):
        return False

    if digit_len == 10 and re.match(r"^[1-9]\d{5}20[1-3]\d$", digits):
        return False

    core_10 = ""
    if digit_len == 10:
        core_10 = digits
    elif digit_len == 11:
        if digits.startswith("0") or digits.startswith("1800") or digits.startswith("1860"):
            core_10 = digits[1:]
        else:
            return False
    elif digit_len == 12:
        if digits.startswith("91") or digits.startswith("01800") or digits.startswith("01860"):
            core_10 = digits[2:]
        else:
            return False
    elif digit_len == 13:
        if digits.startswith("091"):
            core_10 = digits[3:]
        else:
            return False

    if len(core_10) != 10 or core_10[0] == "0":
        return False

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

def _is_valid_seller_email(email_str: str) -> bool:
    """Validates email and filters out Amazon internal and customer service support addresses."""
    if not email_str:
        return False
    em_lower = email_str.lower().strip()
    return not any(domain in em_lower for domain in DISALLOWED_EMAIL_DOMAINS)


class AmazonSellerProfileScraper:
    """
    Scrapes publicly accessible Amazon seller detail pages (/sp?seller=...)
    to extract business display name, legal entity, address, GSTIN, PAN, Phone, Email, FSSAI.
    Features robust bot-wall detection, multi-container waits, and full-page text parsing.
    """
    def __init__(self, page: Page, timeout_ms: int = 20000):
        self.page = page
        self.timeout_ms = timeout_ms

    def _check_bot_wall(self, url: str) -> Tuple[bool, str]:
        """Checks for Amazon CAPTCHA or bot-wall blocking."""
        current_url = (self.page.url or "").lower()
        if "validatecaptcha" in current_url or "validatecaptcha" in url.lower():
            return True, "validateCaptcha URL detected"

        try:
            title = (self.page.title() or "").lower()
            if "robot check" in title or "captcha" in title or "503 - service unavailable" in title:
                return True, f"Title indicates block: {title}"

            body_text = self.page.inner_text("body").lower()
            if "api-services-support@amazon.com" in body_text or "type the characters you see in this image" in body_text:
                return True, "CAPTCHA text prompt detected in body"
        except Exception:
            pass

        return False, "OK"

    def _wait_for_seller_containers(self):
        """Attempts to wait up to 6s for common seller information containers."""
        common_selectors = [
            "#page-section-detail-seller-info",
            "#seller-profile-container",
            "div.a-box-inner",
            "div#feedback-summary-table",
            "#seller-info",
            "h1#sellerName",
            "#seller-name"
        ]
        for sel in common_selectors:
            try:
                self.page.wait_for_selector(sel, timeout=1500)
                break
            except Exception:
                continue

    def _extract_phone_multi_strategy(self, body_text: str, seller_info_text: str) -> Optional[str]:
        """Multi-strategy phone extraction from seller container and full body text."""
        # Strategy 1: DOM tel: links & contact selectors
        try:
            tel_links = self.page.query_selector_all('a[href^="tel:"]')
            for link in tel_links:
                href = link.get_attribute("href") or ""
                phone_val = _clean_phone_candidate(re.sub(r"^tel:\s*", "", href, flags=re.IGNORECASE))
                if _is_valid_indian_phone(phone_val):
                    return phone_val
                text = _clean_phone_candidate(link.inner_text())
                if _is_valid_indian_phone(text):
                    return text
        except Exception:
            pass

        # Strategy 2: Labeled & line-by-line regex in seller container text
        if seller_info_text:
            labeled = _extract_labeled_phone(seller_info_text)
            if labeled:
                return labeled
            standalone = _extract_standalone_phone(seller_info_text)
            if standalone:
                return standalone

        # Strategy 3: Labeled phone in body text
        if body_text:
            labeled_body = _extract_labeled_phone(body_text)
            if labeled_body:
                return labeled_body

        # Strategy 4: Candidate scoring on full page text
        try:
            if body_text:
                candidates = []
                for match in PHONE_CANDIDATE_REGEX.finditer(body_text):
                    cand_str = _clean_phone_candidate(match.group(0))
                    if not _is_valid_indian_phone(cand_str):
                        continue

                    start_pos, end_pos = match.span()
                    line_start = body_text.rfind("\n", 0, start_pos)
                    line_start = 0 if line_start == -1 else line_start + 1
                    line_end = body_text.find("\n", end_pos)
                    line_end = len(body_text) if line_end == -1 else line_end
                    line = body_text[line_start:line_end].lower()

                    ctx_start = max(0, start_pos - 80)
                    ctx_end = min(len(body_text), end_pos + 80)
                    context = body_text[ctx_start:ctx_end].lower()

                    score = 10
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

                    if re.search(r"\b(₹|inr|rs\.?|price|mrp|discount|save|gstin|gst|pan|cin|pincode|asin|order|ratings?|reviews?)\b", line):
                        score -= 40

                    if score >= 25:
                        candidates.append((score, cand_str))

                if candidates:
                    candidates.sort(key=lambda x: x[0], reverse=True)
                    return candidates[0][1]
        except Exception:
            pass

        return None

    def extract_seller_details(self, seller_profile_url: str) -> Dict[str, Any]:
        """
        Extracts comprehensive seller business attributes from an Amazon seller profile page.
        Uses full-page text parsing with multi-layer fallback strategies.
        """
        result = {
            "display_name": None,
            "legal_entity": None,
            "business_address_raw": None,
            "gst_number_raw": None,
            "pan_number_raw": None,
            "fssai_number_raw": None,
            "phone_raw": None,
            "email_raw": None,
            "seller_profile_url": seller_profile_url
        }

        if not seller_profile_url:
            return result

        logger.info(f"Opening Amazon seller profile page: {seller_profile_url}")
        random_delay(1.0, 2.0)

        try:
            response = self.page.goto(seller_profile_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            self._wait_for_seller_containers()

            # Check for Bot Wall / CAPTCHA
            is_blocked, block_msg = self._check_bot_wall(seller_profile_url)
            if is_blocked or (response and response.status in (403, 429, 503)):
                logger.warning(f"Amazon Bot Wall / CAPTCHA encountered: {block_msg} on {seller_profile_url}")
                try:
                    os.makedirs("output", exist_ok=True)
                    self.page.screenshot(path="output/captcha_detected.png")
                except Exception:
                    pass
                return result

            # 1. Full page text and dedicated seller container text
            body_text = ""
            try:
                body_text = self.page.inner_text("body")
            except Exception:
                pass

            seller_info_text = ""
            seller_info_div = self.page.query_selector(
                "#page-section-detail-seller-info, #seller-info, div.a-box-group, div#seller-profile-container"
            )
            if seller_info_div:
                try:
                    seller_info_text = seller_info_div.inner_text()
                except Exception:
                    pass

            combined_text = (seller_info_text + "\n" + body_text).strip()

            # 2. Extract Display Name & Business Name / Legal Name
            # Check seller name header
            header_elem = self.page.query_selector("h1#seller-name, h1#sellerName, h1.a-size-extra-large, h1")
            if header_elem:
                result["display_name"] = header_elem.inner_text().strip()

            biz_match = BUSINESS_NAME_REGEX.search(combined_text)
            if biz_match:
                cleaned_biz = biz_match.group(1).strip()
                # Clean up any trailing unwanted labels
                cleaned_biz = re.split(r"(?i)\b(?:Business\s*Address|Address|GSTIN|GST|Phone|Contact|Email)\b", cleaned_biz)[0].strip()
                if cleaned_biz:
                    result["legal_entity"] = cleaned_biz

            if not result["display_name"] and result["legal_entity"]:
                result["display_name"] = result["legal_entity"]
            elif not result["legal_entity"] and result["display_name"]:
                result["legal_entity"] = result["display_name"]

            # 3. Extract Address Block
            addr_match = re.search(
                r"(?i)(?:Detailed\s*Seller\s*Information|Business\s*Address|Address)\s*[:\-]?\s*([\s\S]+?)(?=\n\s*(?:GSTIN|GST|Phone|Contact|Customer|Email|PAN|FSSAI|\n\n)|$)",
                combined_text
            )
            if addr_match:
                raw_addr = addr_match.group(1).strip()
                # Strip out any embedded label prefix if matched
                raw_addr = re.sub(r"(?i)^(?:Business\s*Address|Address)\s*[:\-]?", "", raw_addr).strip()
                if len(raw_addr) >= 10:
                    result["business_address_raw"] = raw_addr

            # Fallback line extraction for address
            if not result["business_address_raw"] and seller_info_text:
                lines = [l.strip() for l in seller_info_text.split("\n") if l.strip()]
                addr_lines = []
                capture = False
                for line in lines:
                    if re.search(r"(?i)\b(Business\s*Address|Address)\b", line):
                        capture = True
                        clean_l = re.sub(r"(?i)\b(Business\s*Address|Address)\s*[:\-]?", "", line).strip()
                        if clean_l:
                            addr_lines.append(clean_l)
                        continue
                    if capture:
                        if re.search(r"(?i)\b(GSTIN|GST|Phone|Contact|Email|Customer\s*Care|PAN|FSSAI)\b", line):
                            break
                        addr_lines.append(line)
                if addr_lines:
                    result["business_address_raw"] = ", ".join(addr_lines)

            # 4. Extract GST Number
            gst_match = GST_REGEX.search(combined_text)
            if gst_match:
                gst_val = gst_match.group(1).strip()
                result["gst_number_raw"] = gst_val
                # Automatically derive PAN from GSTIN (characters 3 to 12)
                if len(gst_val) == 15:
                    result["pan_number_raw"] = gst_val[2:12]

            # 5. Extract PAN Number (if not already derived from GST)
            if not result["pan_number_raw"]:
                pan_match = PAN_REGEX.search(combined_text)
                if pan_match:
                    result["pan_number_raw"] = pan_match.group(1).strip()

            # 6. Extract Phone Number
            result["phone_raw"] = self._extract_phone_multi_strategy(body_text, seller_info_text)

            # 7. Extract Email Address
            for em_match in EMAIL_REGEX.finditer(combined_text):
                cand_em = em_match.group(0).strip()
                if _is_valid_seller_email(cand_em):
                    result["email_raw"] = cand_em
                    break

            # 8. Extract FSSAI License Number
            fssai_match = FSSAI_REGEX.search(combined_text)
            if fssai_match:
                result["fssai_number_raw"] = fssai_match.group(1).strip()

            # 9. Zero Attributes Diagnostic Dump
            has_any_attr = any([
                result["legal_entity"],
                result["gst_number_raw"],
                result["business_address_raw"],
                result["phone_raw"],
                result["email_raw"]
            ])

            if not has_any_attr:
                logger.warning(f"Seller profile produced ZERO business attributes: {seller_profile_url}")
                try:
                    os.makedirs("output/debug", exist_ok=True)
                    clean_id = "".join(c if c.isalnum() else "_" for c in seller_profile_url[-30:])
                    dump_html_path = f"output/debug/seller_zero_{clean_id}.html"
                    dump_png_path = f"output/debug/seller_zero_{clean_id}.png"
                    with open(dump_html_path, "w", encoding="utf-8") as f:
                        f.write(self.page.content() or "")
                    self.page.screenshot(path=dump_png_path)
                    logger.info(f"Saved diagnostic dump: {dump_html_path} and {dump_png_path}")
                except Exception as dump_err:
                    logger.debug(f"Failed to save diagnostic dump: {dump_err}")

            return result

        except PlaywrightTimeoutError:
            logger.warning(f"Timeout opening seller profile page ({self.timeout_ms}ms): {seller_profile_url}")
            return result
        except Exception as e:
            logger.error(f"Error scraping seller profile page {seller_profile_url}: {e}")
            return result
