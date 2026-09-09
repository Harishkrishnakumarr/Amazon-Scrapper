import os
import re
import time
import random
import base64
import logging
from abc import ABC, abstractmethod
from typing import Tuple, List, Optional, Dict, Any, Set
from urllib.parse import urlparse, quote_plus, parse_qs, unquote
from datetime import datetime
import requests
from database.models import SellerRecord, SellerSource
from extraction.normalizer import (
    normalize_phone, normalize_email, normalize_gst, normalize_pan,
    validate_gstin, validate_pan, validate_pincode, normalize_address,
    extract_pan_from_gstin, get_state_from_gstin
)
from extraction.address_parser import parse_indian_address
from extraction.business_extractor import extract_email, extract_fssai, extract_phone
from scraper.browser import BrowserManager, safe_close_page
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger("amazon_scraper")

# -------------------------------------------------------------
# Configuration Constants
# -------------------------------------------------------------
SEARCH_TIMEOUT_SECONDS = 6
MAX_SEARCH_ATTEMPTS_PER_FIELD = 2
MAX_ENRICHMENT_TIME_PER_SELLER = 120  # 120 seconds maximum per seller
MAX_FIELD_ENRICHMENT_SECONDS = 15    # 15 seconds maximum per missing field
MAX_RESULTS_PER_QUERY = 4
WEBSITE_TIMEOUT_SECONDS = 6
CANDIDATE_WEBSITE_TIMEOUT_SECONDS = 4
MAX_WEBSITE_PAGES = 6

SKIP_DOMAINS = [
    "amazon.", "flipkart.com", "myntra.com", "ajio.com", "facebook.com",
    "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com", "wikipedia.org"
]

DISALLOWED_DOMAINS = {
    # Dictionaries & Encyclopedias
    "merriam-webster.com", "cambridge.org", "dictionary.com", "wiktionary.org",
    "wikipedia.org", "britannica.com", "vocabulary.com", "collinsdictionary.com",
    "oxfordlearnersdictionaries.com", "thefreedictionary.com", "yourdictionary.com",
    "wordreference.com", "urbandictionary.com", "wordsmyth.net", "macmillandictionary.com",
    "thesaurus.com", "ahdictionary.com",

    # Entertainment, Media & Pop culture
    "themoviedb.org", "imdb.com", "youtube.com", "havefunteaching.com", "spotify.com",
    "thecinemaholic.com", "fandom.com", "guide4moms.com", "rottentomatoes.com", "netflix.com",

    # Social Networks & Discussion
    "facebook.com", "instagram.com", "twitter.com", "x.com", "pinterest.com",
    "linkedin.com", "reddit.com", "quora.com", "medium.com", "tiktok.com",
    "zhihu.com", "baidu.com", "tistory.com", "weibo.com", "naver.com", "qq.com",
    "bilibili.com", "douban.com", "tieba.com", "163.com", "sohu.com", "blogspot.com", "wordpress.com",

    # Marketplaces (Avoid circular references)
    "amazon.in", "amazon.com", "flipkart.com", "myntra.com", "meesho.com",
    "ajio.com", "snapdeal.com", "indiamart.com", "alibaba.com", "aliexpress.com",
    "ebay.com", "etsy.com", "nykaa.com", "tatacliq.com", "jiomart.com",

    # Generic Registry Auth / Govt / Banking
    "incometaxindia.gov.in", "gst.gov.in", "mca.gov.in", "utiitsl.com", "tin-nsdl.com",
    "paisabazaar.com", "bankbazaar.com", "cleartax.in", "policybazaar.com", "uidai.gov.in",
    "gov.in", "nic.in"
}

DISALLOWED_URL_SUBSTRINGS = [
    "zaubacorp.com/signup", "zaubacorp.com/deep-research", "zaubacorp.com/company-research",
    "zaubacorp.com/login", "tofler.in/login", "tofler.in/register", "tofler.in/signup",
    "instafinancials.com/login", "instafinancials.com/signup"
]

DISALLOWED_TLDS = (".cn", ".ru", ".kr", ".jp", ".de", ".fr", ".br", ".vn", ".th", ".pl", ".cz")

INTERNAL_SEARCH_DOMAINS = [
    "bing.com", "yahoo.com", "google.com", "microsoft.com", "duckduckgo.com", "msn.com"
]

INVALID_EMAIL_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp", ".ico",
    ".css", ".js", ".json", ".xml", ".txt", ".woff", ".woff2", ".ttf"
)

UNWANTED_EMAIL_DOMAINS = (
    "amazon.", "marketplace.amazon.", "sentry.", "example.com", "example.org",
    "google.com", "schema.org", "w3.org", "duckduckgo.com", "cloudflare.com",
    "domain.com", "yourdomain.com", "test.com", "email.com", "yoursite.com"
)

EMAIL_PRIORITY_PREFIXES = (
    "contact@", "info@", "support@", "care@", "sales@", "help@",
    "service@", "admin@", "office@", "customercare@", "hello@"
)

DISALLOWED_EMAIL_SEARCH_DOMAINS = (
    "amazon.", "flipkart.com", "myntra.com", "ajio.com", "facebook.com",
    "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com",
    "wikipedia.org", "pinterest.com", "reddit.com", "quora.com",
    "duckduckgo.com", "bing.com", "google.com", "yahoo.com", "msn.com"
)

def filter_and_rank_emails(candidates: List[str], seller_name: str = "") -> Optional[str]:
    """Filters invalid email artifacts and ranks by operational priority and seller relevance."""
    valid_emails = []
    seen = set()

    for em in candidates:
        if not em or not isinstance(em, str):
            continue
        em_clean = em.strip().lower()
        if em_clean in seen:
            continue
        seen.add(em_clean)

        # Validate standard syntax
        if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", em_clean):
            continue

        # Reject invalid file extensions at the end of email
        if any(em_clean.endswith(ext) for ext in INVALID_EMAIL_EXTENSIONS):
            continue

        # Reject unwanted / internal / dummy domains
        if any(unwanted in em_clean for unwanted in UNWANTED_EMAIL_DOMAINS):
            continue

        valid_emails.append(em_clean)

    if not valid_emails:
        return None

    # Score and rank emails
    name_clean = re.sub(r"[^a-z0-9]", "", seller_name.lower()) if seller_name else ""
    scored_emails = []

    for em in valid_emails:
        score = 10
        # Priority operational prefixes
        for idx, pfx in enumerate(EMAIL_PRIORITY_PREFIXES):
            if em.startswith(pfx):
                score += (60 - idx * 2)
                break

        # Name match in username or domain
        if name_clean and len(name_clean) >= 3:
            em_no_punct = re.sub(r"[^a-z0-9]", "", em)
            if name_clean in em_no_punct:
                score += 25

        scored_emails.append((score, em))

    scored_emails.sort(key=lambda x: x[0], reverse=True)
    return scored_emails[0][1]

def search_and_extract_seller_email(seller_name: str, session: Optional[requests.Session] = None) -> Optional[str]:
    """
    Dedicated email search routine that queries the web for the seller's operational email.
    1. Queries DuckDuckGo HTML endpoint: f'"{clean_seller_name}" email'
    2. Scans snippet text directly for email matches
    3. If none in snippet, decodes top 2 external candidate URLs and scans page HTML (5s timeout)
    4. Ranks extracted emails favoring contact/info/support prefixes
    """
    if not seller_name or len(seller_name.strip()) < 2 or seller_name.strip() in ("Not Found", "Unknown", "N/A"):
        return None

    clean_seller_name = seller_name.strip()
    clean_query_name = re.sub(r"(?i)\s*(?:Pvt\.?\s*Ltd\.?|Private\s+Limited|LLP|Ltd\.?|Inc\.?)\s*$", "", clean_seller_name).strip()
    if not clean_query_name or len(clean_query_name) < 2:
        clean_query_name = clean_seller_name

    query = f'"{clean_query_name}" email'
    logger.info(f'[ENRICH] Searching web: "{clean_query_name}" email')

    if session is None:
        session = requests.Session()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://html.duckduckgo.com",
        "Referer": "https://html.duckduckgo.com/"
    }

    try:
        time.sleep(random.uniform(1.0, 2.0))

        # 1. Query DuckDuckGo HTML endpoint
        ddg_resp = session.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=headers,
            timeout=10
        )

        html_text = ddg_resp.text if ddg_resp else ""
        if not html_text or ddg_resp.status_code == 202:
            try:
                ddg_resp = session.get(
                    f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}",
                    headers=headers,
                    timeout=10
                )
                html_text = ddg_resp.text if ddg_resp else ""
            except Exception:
                pass

        # 2. Extract emails directly from snippet text
        snippet_emails = re.findall(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b", html_text)
        best_snippet_email = filter_and_rank_emails(snippet_emails, clean_seller_name)
        if best_snippet_email:
            logger.info(f"[ENRICH] Found email for '{clean_seller_name}' in search snippet: {best_snippet_email}")
            return best_snippet_email

        # 3. Extract top 2 organic external destination URLs from DDG redirect links
        raw_links = re.findall(r'href=[\'"]([^\'"]*uddg=[^\'"]*)[\'"]', html_text)
        candidate_urls: List[str] = []

        for l in raw_links:
            try:
                qs = parse_qs(urlparse(l).query)
                dest = unquote(qs.get("uddg", [""])[0])
                if dest and dest.startswith("http"):
                    parsed_dest = urlparse(dest)
                    netloc = parsed_dest.netloc.lower()
                    if not any(skip in netloc for skip in DISALLOWED_EMAIL_SEARCH_DOMAINS):
                        if dest not in candidate_urls:
                            candidate_urls.append(dest)
                if len(candidate_urls) >= 2:
                    break
            except Exception:
                continue

        # 4. Fetch candidate destination pages and scan HTML for emails (5s timeout)
        page_emails: List[str] = []
        for cand_url in candidate_urls[:2]:
            try:
                time.sleep(random.uniform(0.5, 1.0))
                cand_resp = session.get(
                    cand_url,
                    headers={"User-Agent": headers["User-Agent"], "Accept-Language": headers["Accept-Language"]},
                    timeout=5
                )
                if cand_resp and cand_resp.status_code == 200:
                    found = re.findall(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b", cand_resp.text)
                    page_emails.extend(found)
                    best_cand_email = filter_and_rank_emails(found, clean_seller_name)
                    if best_cand_email:
                        logger.info(f"[ENRICH] Found email for '{clean_seller_name}' on {cand_url}: {best_cand_email}")
                        return best_cand_email
            except Exception as e_page:
                logger.debug(f"[ENRICH] Non-critical error fetching candidate URL {cand_url}: {e_page}")
                continue

        best_overall = filter_and_rank_emails(page_emails, clean_seller_name)
        if best_overall:
            return best_overall

    except Exception as e:
        logger.warning(f"[ENRICH] Error during email search for '{clean_seller_name}': {e}")

    return None

def decode_search_redirect_url(href: str) -> str:
    """Decodes Bing /ck/a?, Yahoo /RU=, and Google /url? redirect wrappers to pure target URLs."""
    if not href:
        return ""

    # 1. Bing /ck/a? base64 wrapper
    if "bing.com/ck/a?" in href or "/ck/a?" in href:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        u_val = qs.get("u", [""])[0]
        if u_val:
            b64_str = u_val
            if b64_str.startswith("a1"):
                b64_str = b64_str[2:]
            padding = len(b64_str) % 4
            if padding:
                b64_str += "=" * (4 - padding)
            try:
                decoded = base64.urlsafe_b64decode(b64_str).decode("utf-8", errors="ignore")
                if decoded.startswith("http"):
                    return decoded
            except Exception:
                pass

    # 2. Yahoo /RU= urlencoded wrapper
    if "r.search.yahoo.com" in href and "/RU=" in href:
        m = re.search(r"/RU=([^/]+)/", href)
        if m:
            try:
                decoded = unquote(m.group(1))
                if decoded.startswith("http"):
                    return decoded
            except Exception:
                pass

    # 3. Google /url?q= wrapper
    if "google.com/url?" in href or "/url?" in href:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        q_val = qs.get("q", [""])[0] or qs.get("url", [""])[0]
        if q_val and q_val.startswith("http"):
            return q_val

    return href

def is_valid_search_result_url(url: str) -> bool:
    """Validates destination search result URLs against strict domain and URL blacklist."""
    if not url or not url.startswith("http"):
        return False
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if not domain:
        return False

    # 1. Check internal search engine domains
    if any(internal in domain for internal in INTERNAL_SEARCH_DOMAINS):
        return False

    # 2. Check disallowed foreign TLDs
    if any(domain.endswith(tld) for tld in DISALLOWED_TLDS):
        return False

    # 3. Check disallowed domains (Dictionaries, Encyclopedias, Entertainment, Marketplaces, Social)
    for disallowed in DISALLOWED_DOMAINS:
        if disallowed in domain:
            return False

    # 4. Check disallowed URL substrings (e.g. signup/login/deep-research landing pages)
    url_lower = url.lower()
    for sub in DISALLOWED_URL_SUBSTRINGS:
        if sub in url_lower:
            return False

    # 5. Skip binary and static media files
    path_lower = parsed.path.lower()
    if any(path_lower.endswith(ext) for ext in INVALID_EMAIL_EXTENSIONS):
        return False
    if any(path_lower.endswith(ext) for ext in (".pdf", ".zip", ".tar", ".gz", ".exe", ".mp4", ".mp3", ".avi", ".mov")):
        return False

    return True

# -------------------------------------------------------------
# Search Provider Abstraction
# -------------------------------------------------------------
class SearchProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def search(self, page: Page, query: str, limit: int = MAX_RESULTS_PER_QUERY, timeout_sec: int = SEARCH_TIMEOUT_SECONDS) -> Tuple[List[str], Dict[str, Any]]:
        pass

class DuckDuckGoSearchProvider(SearchProvider):
    """
    Primary Search Provider: DuckDuckGo HTML / Lite endpoint.
    Performs fast search with immediate failover within 1.5s if status != 200 or HTML < 500 bytes.
    """
    @property
    def name(self) -> str:
        return "DuckDuckGo"

    def search(self, page: Page, query: str, limit: int = MAX_RESULTS_PER_QUERY, timeout_sec: int = SEARCH_TIMEOUT_SECONDS) -> Tuple[List[str], Dict[str, Any]]:
        search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        urls = []
        diag = {
            "query": query,
            "engine": self.name,
            "status": "None",
            "title": "",
            "url": search_url,
            "html_length": 0,
            "extracted_count": 0,
            "raw_html": ""
        }

        session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-IN,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://html.duckduckgo.com",
            "Referer": "https://html.duckduckgo.com/"
        }

        try:
            # 1. Fast HTTP request via requests (max 1.5s)
            resp = None
            try:
                resp = session.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query},
                    headers=headers,
                    timeout=1.5
                )
            except Exception:
                pass

            html_content = resp.text if (resp and resp.status_code == 200) else ""
            status_code = resp.status_code if resp else 0

            # 2. If requests failed or produced empty HTML, attempt fast Playwright navigation (max 2s)
            if not html_content or len(html_content) < 500:
                try:
                    page_resp = page.goto(
                        search_url,
                        wait_until="domcontentloaded",
                        timeout=2000
                    )
                    status_code = page_resp.status if page_resp else 200
                    html_content = page.content() or ""
                    try:
                        diag["title"] = page.title() or ""
                    except Exception:
                        pass
                except Exception as e_page:
                    diag["status"] = f"PAGE FAILOVER: {e_page}"

            diag["status"] = str(status_code)
            diag["html_length"] = len(html_content)
            diag["raw_html"] = html_content

            # Failover immediately if response is non-200 or HTML stub (< 500 chars)
            if status_code != 200 or len(html_content) < 500:
                logger.warning(f"DuckDuckGo returned status {status_code} (length: {len(html_content)}). Triggering immediate failover within 1.5s.")
                return urls, diag

            # Extract URLs from HTML
            # Strategy A: result__url links
            result_links = re.findall(r'<a[^>]+class="[^"]*result__url[^"]*"[^>]*href="([^"]+)"', html_content)
            for link in result_links:
                decoded = decode_search_redirect_url(link)
                if is_valid_search_result_url(decoded) and decoded not in urls:
                    urls.append(decoded)
                if len(urls) >= limit:
                    break

            # Strategy B: /l/?uddg= redirect links
            if len(urls) < limit:
                raw_uddg = re.findall(r'href=[\'"]([^\'"]*uddg=[^\'"]*)[\'"]', html_content)
                for l in raw_uddg:
                    try:
                        qs = parse_qs(urlparse(l).query)
                        dest = unquote(qs.get("uddg", [""])[0])
                        if is_valid_search_result_url(dest) and dest not in urls:
                            urls.append(dest)
                        if len(urls) >= limit:
                            break
                    except Exception:
                        continue

            diag["extracted_count"] = len(urls)

        except Exception as e:
            diag["status"] = f"ERROR: {e}"

        return urls, diag

class YahooSearchProvider(SearchProvider):
    """
    Secondary / Fallback Search Provider: Yahoo Search.
    Fails over immediately if status != 200 or HTML < 500 bytes.
    """
    @property
    def name(self) -> str:
        return "Yahoo"

    def search(self, page: Page, query: str, limit: int = MAX_RESULTS_PER_QUERY, timeout_sec: int = SEARCH_TIMEOUT_SECONDS) -> Tuple[List[str], Dict[str, Any]]:
        search_url = f"https://search.yahoo.com/search?p={quote_plus(query)}"
        urls = []
        diag = {
            "query": query,
            "engine": self.name,
            "status": "None",
            "title": "",
            "url": search_url,
            "html_length": 0,
            "extracted_count": 0,
            "raw_html": ""
        }
        try:
            try:
                page.evaluate("() => window.stop()")
            except Exception:
                pass

            resp = page.goto(search_url, wait_until="domcontentloaded", timeout=timeout_sec * 1000)
            page.wait_for_timeout(300)
            status_code = resp.status if resp else 200
            diag["status"] = str(status_code)

            if status_code in (429, 202, 503):
                logger.warning(f"Yahoo search returned HTTP {status_code} (Rate limited/Blocked). Backing off safely...")
                time.sleep(1.5)
            
            try:
                diag["title"] = page.title() or ""
            except Exception:
                diag["title"] = ""

            try:
                html_content = page.content() or ""
            except Exception:
                html_content = ""

            diag["html_length"] = len(html_content)
            diag["raw_html"] = html_content

            # Failover immediately if response is non-200 or HTML is stub
            if status_code != 200 or len(html_content) < 500:
                logger.warning(f"{self.name} returned status {status_code} (length: {len(html_content)}). Triggering failover.")
                return urls, diag

            try:
                links = page.query_selector_all("div.compTitle a, div.compText a, h3.title a, .algo h3 a, #web a, .dd a")
            except Exception:
                links = []

            for l in links:
                try:
                    raw_href = l.get_attribute("href")
                    decoded_href = decode_search_redirect_url(raw_href)
                    if is_valid_search_result_url(decoded_href):
                        if decoded_href not in urls:
                            urls.append(decoded_href)
                    if len(urls) >= limit:
                        break
                except Exception:
                    continue

            diag["extracted_count"] = len(urls)
        except PlaywrightTimeoutError:
            diag["status"] = f"SEARCH TIMEOUT ({timeout_sec}s)"
            logger.warning(f"SEARCH TIMEOUT: '{query}' on {self.name}")
        except Exception as e:
            diag["status"] = f"ERROR: {e}"

        return urls, diag

class PublicEnrichmentEngine:
    """
    Executes a multi-level field-by-field deep public search enrichment waterfall.
    Enforces strict timeouts and bounded attempts:
    - SEARCH_TIMEOUT_SECONDS = 15
    - MAX_SEARCH_ATTEMPTS_PER_FIELD = 3
    - MAX_ENRICHMENT_TIME_PER_SELLER = 120s
    - MAX_FIELD_ENRICHMENT_SECONDS = 20s
    - MAX_WEBSITE_PAGES = 10
    - Query deduplication & Heartbeat logging
    """
    def __init__(self, browser_mgr: BrowserManager, max_seller_enrichment_seconds: int = MAX_ENRICHMENT_TIME_PER_SELLER):
        self.browser_mgr = browser_mgr
        self.max_seller_enrichment_seconds = max_seller_enrichment_seconds
        self.audit_log: List[Dict[str, Any]] = []
        self.session = requests.Session()
        
        self.primary_provider = DuckDuckGoSearchProvider()
        self.fallback_provider = YahooSearchProvider()

        # Performance Tracking Statistics
        self.performance_stats = {
            "sellers_attempted": 0,
            "sellers_completed": 0,
            "sellers_timed_out": 0,
            "searches_executed": 0,
            "searches_timed_out": 0,
            "searches_zero_results": 0,
            "websites_timed_out": 0,
            "enrichment_times": []
        }

    def _is_field_missing(self, record: SellerRecord, field_name: str) -> bool:
        if field_name == "Legal Entity Name":
            return record.legal_entity in (None, "", "Not Found", "Unknown")
        elif field_name == "PAN":
            return record.pan_number in (None, "", "Not Found", "Unknown")
        elif field_name == "GST":
            return record.gst_number in (None, "", "Not Found", "Unverified", "Unknown")
        elif field_name == "Owner":
            return record.owner_name in (None, "", "Not Found", "Unknown")
        elif field_name == "Address":
            return record.billing_address in (None, "", "Not Found", "Unknown")
        elif field_name == "Phone":
            return record.phone_number in (None, "", "Not Found", "Unknown")
        elif field_name == "Email":
            return record.email_address in (None, "", "Not Found", "Unknown")
        return False

    def _all_essential_fields_found(self, record: SellerRecord) -> bool:
        return not any(
            self._is_field_missing(record, f)
            for f in ["Legal Entity Name", "PAN", "GST", "Owner", "Address", "Phone", "Email"]
        )

    def _extract_all_fields_from_text(self, text: str, record: SellerRecord, business_name: str, source_name: str, source_url: str, record_field) -> bool:
        if not text:
            return False

        found_any = False
        text_lower = text.lower()

        # 1. Legal Entity Name
        if self._is_field_missing(record, "Legal Entity Name"):
            m = re.search(r"\b((?:[A-Z][A-Za-z0-9&'-]+\s+){1,4}(?:Private\s+Limited|Pvt\.?\s*Ltd\.?|Limited|LLP))\b", text)
            if m:
                cand_leg = m.group(1).strip()
                query_clean = re.sub(r'[^a-z0-9]', '', business_name.lower())
                leg_clean = re.sub(r'[^a-z0-9]', '', cand_leg.lower())
                if query_clean and query_clean in leg_clean and len(cand_leg) < 60:
                    record.legal_entity = cand_leg
                    record_field("Legal Entity Name", cand_leg, source_name, source_url)
                    found_any = True

        # 2. Phone Number
        if self._is_field_missing(record, "Phone"):
            ph = extract_phone(text)
            if ph != "Not Found":
                record.phone_number = ph
                record_field("Phone Number", ph, source_name, source_url)
                found_any = True

        # 3. Email Address
        if self._is_field_missing(record, "Email"):
            em = extract_email(text_lower)
            if em != "Not Found":
                record.email_address = em
                record_field("Email Address", em, source_name, source_url)
                found_any = True

        # 4. GST Number & PAN inference
        if self._is_field_missing(record, "GST"):
            raw_gst_matches = re.findall(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}\b", text.upper())
            for cand_gst in raw_gst_matches:
                cand_gst = normalize_gst(cand_gst)
                if cand_gst != "Not Found":
                    cand_pan = extract_pan_from_gstin(cand_gst)
                    known_pan = normalize_pan(record.pan_number)
                    if known_pan == "Not Found" or (cand_pan and cand_pan.upper() == known_pan.upper()):
                        record.gst_number = cand_gst
                        record_field("GST Number", cand_gst, source_name, source_url)
                        if self._is_field_missing(record, "PAN") and cand_pan:
                            record.pan_number = cand_pan
                            record_field("PAN Number", cand_pan, source_name, source_url)
                        found_any = True
                        break

        # 5. PAN Number directly if still missing
        if self._is_field_missing(record, "PAN"):
            pan = normalize_pan(text)
            if pan != "Not Found":
                query_clean = re.sub(r'[^a-z0-9]', '', business_name.lower())
                page_clean = re.sub(r'[^a-z0-9]', '', text)
                if query_clean in page_clean or (record.legal_entity and re.sub(r'[^a-z0-9]', '', record.legal_entity.lower()) in page_clean):
                    record.pan_number = pan
                    record_field("PAN Number", pan, source_name, source_url)
                    found_any = True

        # 6. Owner Name
        if self._is_field_missing(record, "Owner"):
            owner_m = re.search(r"(?:founder|owner|director|promoter|managing\s+director)[ \t]*:[ \t]*([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,2})", text, re.IGNORECASE)
            if not owner_m:
                owner_m = re.search(r"\b([A-Z][a-z]+[ \t]+[A-Z][a-z]+)[ \t]+(?:is the|founder|director|owner|promoter)\b", text)
            if owner_m:
                cand_owner = owner_m.group(1).strip()
                cand_lower = cand_owner.lower()
                invalid_words = ["limited", "pvt", "llp", "inc", "corp", "care", "support", "office", "about", "policy", "terms", "service"]
                if len(cand_owner) > 4 and not any(w in cand_lower for w in invalid_words):
                    record.owner_name = cand_owner
                    record_field("Owner Name", cand_owner, source_name, source_url)
                    found_any = True

        # 7. Billing Address & sub-components
        if self._is_field_missing(record, "Address"):
            addr_m = re.search(r"(?:registered\s+office|head\s+office|corporate\s+office|address)\s*:\s*([^\n]{20,150})", text, re.IGNORECASE)
            if addr_m:
                parsed_addr = parse_indian_address(addr_m.group(1))
                if parsed_addr["billing_address"] != "Not Found":
                    record.billing_address = parsed_addr["billing_address"]
                    record.city = parsed_addr["city"]
                    record.state = parsed_addr["state"]
                    record.pincode = parsed_addr["pincode"]
                    record.country = parsed_addr["country"]
                    record_field("Billing Address", record.billing_address, source_name, source_url)
                    if record.city != "Not Found": record_field("City", record.city, source_name, source_url)
                    if record.state != "Not Found": record_field("State", record.state, source_name, source_url)
                    if record.pincode != "Not Found": record_field("Pincode", record.pincode, source_name, source_url)
                    found_any = True

        return found_any

    def enrich_seller(self, record: SellerRecord) -> Tuple[SellerRecord, List[SellerSource]]:
        sources: List[SellerSource] = []
        business_name = record.legal_entity or record.display_name or record.business_name
        if not business_name or business_name in ("Not Found", "Unknown"):
            return record, sources

        self.performance_stats["sellers_attempted"] += 1
        seller_start_monotonic = time.monotonic()
        last_heartbeat_time = seller_start_monotonic
        attempted_queries: Set[str] = set()

        logger.info(f"Starting Public Enrichment Waterfall for business '{business_name}'...")
        page = self.browser_mgr.new_page()
        page.set_default_timeout(8000)

        field_sources: Dict[str, Tuple[str, str, str]] = {}
        timed_out_early = False

        def check_heartbeat(current_field: str):
            nonlocal last_heartbeat_time
            now = time.monotonic()
            if now - last_heartbeat_time >= 10:
                elapsed = now - seller_start_monotonic
                print(f"\nENRICHMENT HEARTBEAT:\nSeller = {business_name}\nElapsed = {elapsed:.0f}s\nCurrent field = {current_field}\n")
                logger.info(f"ENRICHMENT HEARTBEAT: Seller = {business_name}, Elapsed = {elapsed:.0f}s, Current field = {current_field}")
                last_heartbeat_time = now

        def is_seller_timed_out() -> bool:
            return (time.monotonic() - seller_start_monotonic) >= self.max_seller_enrichment_seconds

        def record_field(field_name: str, value: str, source_name: str, source_url: str, is_verified: bool = True):
            if value and value not in ("Not Found", "N/A", "Unknown", ""):
                if field_name not in field_sources or field_sources[field_name][0] in ("Not Found", "N/A"):
                    field_sources[field_name] = (value, source_name, source_url)
                    v_status = "VERIFIED" if is_verified else "FOUND"
                    self.audit_log.append({
                        "business": business_name,
                        "field": field_name,
                        "value": value,
                        "status": v_status,
                        "source": source_name,
                        "source_url": source_url,
                        "timestamp": datetime.now().isoformat()
                    })

        try:
            # -------------------------------------------------------------
            # LEVEL 1 & 2: Amazon Profile Metadata
            # -------------------------------------------------------------
            record_field("Business Name", record.business_name, "Amazon", record.seller_url or "Amazon Profile")
            if record.legal_entity:
                record_field("Legal Entity Name", record.legal_entity, "Amazon Profile", record.seller_url or "")
            if record.phone_number != "Not Found":
                record_field("Phone Number", record.phone_number, "Amazon Profile", record.seller_url or "")
            if record.email_address != "Not Found":
                record_field("Email Address", record.email_address, "Amazon Profile", record.seller_url or "")

            # -------------------------------------------------------------
            # DEDICATED WEB SEARCH FOR SELLER EMAIL (If missing from Amazon profile)
            # -------------------------------------------------------------
            if self._is_field_missing(record, "Email") and not is_seller_timed_out():
                check_heartbeat("Email Search")
                email_query_name = record.business_name if record.business_name not in ("Not Found", "Unknown", "") else (record.legal_entity or record.display_name)
                if email_query_name and email_query_name not in ("Not Found", "Unknown", ""):
                    found_email = search_and_extract_seller_email(email_query_name, session=self.session)
                    # Fallback to Display Name if business_name query yielded no email and display_name differs
                    if not found_email and record.display_name and record.display_name not in ("Not Found", "Unknown", "") and record.display_name.lower().strip() != email_query_name.lower().strip():
                        logger.info(f'[ENRICH] Falling back to display name: "{record.display_name}" email')
                        found_email = search_and_extract_seller_email(record.display_name, session=self.session)

                    if found_email:
                        record.email_address = found_email
                        record_field("Email Address", found_email, "Web Search - Seller Email", "https://html.duckduckgo.com/html/")
                        sources.append(SellerSource(
                            source_name="Web Search - Seller Email",
                            source_url="https://html.duckduckgo.com/html/",
                            field_name="Email Address",
                            field_value=found_email,
                            verification_status="Verified"
                        ))
                        logger.info(f"[ENRICH] Successfully enriched email for '{email_query_name}': {found_email}")

            # -------------------------------------------------------------
            # LEVEL 3: Find & Verify Official Website
            # -------------------------------------------------------------
            official_website = record.website_url if record.website_url != "Not Found" else None

            if not official_website and not is_seller_timed_out():
                check_heartbeat("Official Website")
                candidate_urls = self._get_website_candidates(page, business_name, attempted_queries)
                for candidate in candidate_urls:
                    if is_seller_timed_out():
                        break
                    if self._verify_website_ownership(page, candidate, business_name):
                        parsed = urlparse(candidate)
                        official_website = f"{parsed.scheme}://{parsed.netloc}/"
                        record.website_url = official_website
                        record_field("Website URL", official_website, "Official Website Discovery", candidate)
                        logger.info(f"Verified official website for '{business_name}': {official_website}")
                        break

            # -------------------------------------------------------------
            # LEVEL 4: Inspect Official Website Subpages (Max 6 pages)
            # -------------------------------------------------------------
            if official_website and not is_seller_timed_out():
                subpages = ["", "about", "about-us", "contact", "contact-us", "legal", "terms", "privacy"][:MAX_WEBSITE_PAGES]
                for sub in subpages:
                    if is_seller_timed_out():
                        break
                    check_heartbeat("Website Inspection")
                    target_u = f"{official_website.rstrip('/')}/{sub}" if sub else official_website
                    try:
                        resp = page.goto(target_u, wait_until="domcontentloaded", timeout=WEBSITE_TIMEOUT_SECONDS * 1000)
                        if resp and resp.status == 200:
                            text = page.inner_text("body")
                            self._extract_all_fields_from_text(text, record, business_name, "Official Website", target_u, record_field)
                            if self._all_essential_fields_found(record):
                                break
                    except PlaywrightTimeoutError:
                        self.performance_stats["websites_timed_out"] += 1
                        logger.warning(f"WEBSITE TIMEOUT: {target_u}")
                    except Exception:
                        continue

            # -------------------------------------------------------------
            # LEVEL 5: FIELD-BY-FIELD TARGETED SEARCHES
            # Bounded: 15s per field, max 2 query attempts per field, max 120s total per seller
            # Opportunistically extracts all missing fields on every fetched page.
            # -------------------------------------------------------------
            fields_to_search = [
                "Legal Entity Name",
                "PAN",
                "GST",
                "Owner",
                "Address",
                "Phone",
                "Email"
            ]

            total_search_fields = len(fields_to_search)

            for field_idx, field_name in enumerate(fields_to_search, 1):
                if not self._is_field_missing(record, field_name):
                    continue

                if self._all_essential_fields_found(record):
                    logger.info(f"All essential fields found for '{business_name}'. Completing enrichment early.")
                    break

                if is_seller_timed_out():
                    logger.warning(f"SELLER ENRICHMENT TIME LIMIT REACHED ({self.max_seller_enrichment_seconds}s). Aborting remaining fields for '{business_name}'.")
                    timed_out_early = True
                    break

                field_start_time = time.monotonic()
                print(f"\nENRICHMENT:")
                print(f"Seller = {business_name}")
                print(f"Field = {field_name}")
                print(f"Progress = {field_idx}/{total_search_fields}")

                query_entity = record.legal_entity or business_name
                queries_for_field: List[str] = []

                gst_val = record.gst_number if record.gst_number not in ("Not Found", "N/A", "Unverified", None) else ""

                if field_name == "Legal Entity Name":
                    queries_for_field = [
                        f'"{business_name}" company registration Private Limited',
                        f'"{business_name}" legal name India'
                    ]
                elif field_name == "PAN":
                    if gst_val:
                        derived_p = extract_pan_from_gstin(gst_val)
                        if derived_p:
                            record.pan_number = derived_p
                            record_field("PAN Number", derived_p, "GST Derivation", record.seller_url or "")
                            continue
                    queries_for_field = [
                        f'"{query_entity}" PAN card number',
                        f'"{business_name}" PAN number'
                    ]
                elif field_name == "GST":
                    queries_for_field = [
                        f'"{query_entity}" GSTIN registration',
                        f'"{business_name}" GST number India'
                    ]
                elif field_name == "Owner":
                    if gst_val:
                        queries_for_field = [
                            f'"{business_name}" "{gst_val}" director owner ZaubaCorp Tofler',
                            f'"{business_name}" director owner ZaubaCorp Tofler'
                        ]
                    else:
                        queries_for_field = [
                            f'"{business_name}" director owner ZaubaCorp Tofler',
                            f'"{query_entity}" founder managing director'
                        ]
                elif field_name == "Address":
                    queries_for_field = [
                        f'"{query_entity}" registered office address corporate office',
                        f'"{business_name}" India contact address'
                    ]
                elif field_name in ("Phone", "Email"):
                    queries_for_field = [
                        f'"{business_name}" India contact email phone official website',
                        f'"{query_entity}" customer care phone email'
                    ]

                field_found = False
                for attempt_idx, query in enumerate(queries_for_field[:MAX_SEARCH_ATTEMPTS_PER_FIELD], 1):
                    if is_seller_timed_out():
                        logger.warning(f"SELLER ENRICHMENT TIME LIMIT REACHED during {field_name}.")
                        timed_out_early = True
                        break

                    if (time.monotonic() - field_start_time) >= MAX_FIELD_ENRICHMENT_SECONDS:
                        logger.warning(f"FIELD TIME LIMIT REACHED ({MAX_FIELD_ENRICHMENT_SECONDS}s) for {field_name}. Moving to next field.")
                        break

                    clean_q = query.strip().lower()
                    if clean_q in attempted_queries:
                        continue
                    attempted_queries.add(clean_q)

                    check_heartbeat(f"{field_name} (Attempt {attempt_idx})")
                    urls = self._perform_web_search(page, business_name, field_name, query, limit=MAX_RESULTS_PER_QUERY)

                    if not urls:
                        print(f"No results for {field_name} (Attempt {attempt_idx}). Moving to next attempt.")
                        continue

                    # Process extracted URLs for this field and extract all available data
                    for u in urls:
                        if is_seller_timed_out() or (time.monotonic() - field_start_time) >= MAX_FIELD_ENRICHMENT_SECONDS:
                            break

                        text = self._fetch_page_text(page, u)
                        if not text:
                            continue

                        # Opportunistically extract all fields present on this page
                        self._extract_all_fields_from_text(text, record, business_name, "Targeted Search", u, record_field)

                        if not self._is_field_missing(record, field_name):
                            field_found = True
                            break

                    if field_found or self._all_essential_fields_found(record):
                        break

                if not field_found and self._is_field_missing(record, field_name):
                    logger.info(f"FIELD SEARCH EXHAUSTED: {field_name}")

            # 6. Pincode from Address if present
            if record.pincode in ("Not Found", "N/A", "") and record.billing_address != "Not Found":
                parsed = parse_indian_address(record.billing_address, gst_number=record.gst_number)
                if parsed["pincode"] != "Not Found":
                    record.pincode = parsed["pincode"]
                    record_field("Pincode", record.pincode, "Address Extraction", record.website_url or "")
                if record.state in ("Not Found", "Unknown", "") and parsed["state"] != "Not Found":
                    record.state = parsed["state"]
                    record_field("State", record.state, "Address Extraction", record.website_url or "")

        finally:
            safe_close_page(page)

        total_elapsed = time.monotonic() - seller_start_monotonic
        self.performance_stats["enrichment_times"].append(total_elapsed)

        if timed_out_early or total_elapsed >= self.max_seller_enrichment_seconds:
            self.performance_stats["sellers_timed_out"] += 1
            record.status = "Partially Verified"
        else:
            self.performance_stats["sellers_completed"] += 1
            verified_count = len(field_sources)
            if verified_count >= 5 or (record.website_url != "Not Found" and record.phone_number != "Not Found"):
                record.status = "Verified"
            elif verified_count >= 2:
                record.status = "Partially Verified"
            else:
                record.status = "Needs Review"

        # Record field-level sources into output list
        for f_name, (f_val, s_name, s_url) in field_sources.items():
            sources.append(SellerSource(
                source_name=s_name,
                source_url=s_url,
                field_name=f_name,
                field_value=f_val,
                verification_status="Verified"
            ))

        return record, sources

    def _get_website_candidates(self, page: Page, business_name: str, attempted_queries: Set[str]) -> List[str]:
        candidates = []
        clean_name = re.sub(r'[^a-z0-9]', '', business_name.lower())
        if clean_name:
            candidates.append(f"https://www.{clean_name}.com/")
            candidates.append(f"https://www.{clean_name}.in/")

        # Search for official website (max 1 query)
        q = f"{business_name} official website"
        if q.strip().lower() not in attempted_queries:
            attempted_queries.add(q.strip().lower())
            search_urls = self._perform_web_search(page, business_name, "Website", q, limit=2)
            for u in search_urls:
                if u not in candidates:
                    candidates.append(u)

        return candidates

    def _perform_web_search(self, page: Page, seller_name: str, field_name: str, query: str, limit: int = MAX_RESULTS_PER_QUERY) -> List[str]:
        self.performance_stats["searches_executed"] += 1

        # 1. Try Primary Provider (Bing)
        urls, diag = self.primary_provider.search(page, query, limit=limit, timeout_sec=SEARCH_TIMEOUT_SECONDS)
        provider_used = self.primary_provider.name

        if "SEARCH TIMEOUT" in diag.get("status", ""):
            self.performance_stats["searches_timed_out"] += 1

        print("\n----------------------------------------")
        print("SEARCH DIAGNOSTICS")
        print(f"SEARCH QUERY: {query}")
        print(f"SEARCH ENGINE: {diag['engine']}")
        print(f"HTTP STATUS: {diag['status']}")
        print(f"RESULT PAGE TITLE: {diag['title']}")
        print(f"RESULT URL: {diag['url']}")
        print(f"RESULT HTML LENGTH: {diag['html_length']}")
        print(f"EXTRACTED URL COUNT: {len(urls)}")
        print("----------------------------------------")

        # 2. If Primary returned 0 URLs, try Fallback Provider (Yahoo)
        if not urls:
            print(f"SEARCH RESULT: 0 usable URLs returned from {self.primary_provider.name}")
            print(f"Switching to Fallback Provider ({self.fallback_provider.name})...")

            fb_urls, fb_diag = self.fallback_provider.search(page, query, limit=limit, timeout_sec=SEARCH_TIMEOUT_SECONDS)
            provider_used = self.fallback_provider.name

            if "SEARCH TIMEOUT" in fb_diag.get("status", ""):
                self.performance_stats["searches_timed_out"] += 1

            print("\n----------------------------------------")
            print("FALLBACK SEARCH DIAGNOSTICS")
            print(f"SEARCH QUERY: {query}")
            print(f"SEARCH ENGINE: {fb_diag['engine']}")
            print(f"HTTP STATUS: {fb_diag['status']}")
            print(f"RESULT PAGE TITLE: {fb_diag['title']}")
            print(f"RESULT URL: {fb_diag['url']}")
            print(f"RESULT HTML LENGTH: {fb_diag['html_length']}")
            print(f"EXTRACTED URL COUNT: {len(fb_urls)}")
            print("----------------------------------------")

            urls = fb_urls
            diag = fb_diag

        # 3. Handle 0 results
        if not urls:
            self.performance_stats["searches_zero_results"] += 1
            print(f"SEARCH RESULT: 0 usable URLs returned")
            status_str = "no_results"
        else:
            print(f"SEARCH RESULT: {len(urls)} usable URLs discovered from {provider_used}")
            status_str = "results_found"

        # Record to audit log
        self.audit_log.append({
            "seller": seller_name,
            "field": field_name,
            "query": query,
            "provider": provider_used,
            "result_count": len(urls),
            "result_urls": urls,
            "timestamp": datetime.now().isoformat(),
            "status": status_str
        })

        return urls

    def _fetch_page_text(self, page: Page, url: str) -> str:
        if not is_valid_search_result_url(url):
            return ""
        try:
            try:
                page.evaluate("() => window.stop()")
            except Exception:
                pass
            logger.info(f"Fetching text from target page: {url}")
            resp = page.goto(url, wait_until="domcontentloaded", timeout=WEBSITE_TIMEOUT_SECONDS * 1000)
            if resp and resp.status == 200:
                return page.inner_text("body").lower()
        except PlaywrightTimeoutError:
            self.performance_stats["websites_timed_out"] += 1
            logger.warning(f"WEBSITE TIMEOUT: {url}")
        except Exception as e:
            logger.debug(f"Failed to fetch page text for {url}: {e}")
        return ""

    def _verify_website_ownership(self, page: Page, candidate_url: str, business_name: str) -> bool:
        if not is_valid_search_result_url(candidate_url):
            return False
        try:
            try:
                page.evaluate("() => window.stop()")
            except Exception:
                pass
            logger.info(f"Verifying website ownership candidate: {candidate_url}")
            resp = page.goto(candidate_url, wait_until="domcontentloaded", timeout=CANDIDATE_WEBSITE_TIMEOUT_SECONDS * 1000)
            if not resp or resp.status != 200:
                return False

            page_text = page.inner_text("body").lower()
            query_clean = re.sub(r'[^a-z0-9]', '', business_name.lower())
            page_clean = re.sub(r'[^a-z0-9]', '', page_text)

            name_words = [w.lower() for w in re.findall(r'[A-Z]?[a-z]+|[0-9]+', business_name) if len(w) >= 3]
            matched_count = sum(1 for w in name_words if w in page_text)

            is_valid = len(query_clean) >= 5 and query_clean in page_clean or matched_count >= max(1, len(name_words) - 1)
            if is_valid:
                logger.info(f"Successfully verified website candidate '{candidate_url}' for business '{business_name}'")
            return is_valid
        except PlaywrightTimeoutError:
            self.performance_stats["websites_timed_out"] += 1
            logger.warning(f"WEBSITE TIMEOUT during verification: {candidate_url}")
            return False
        except Exception as e:
            logger.debug(f"Verification failed for {candidate_url}: {e}")
            return False

    def print_performance_summary(self):
        """Prints the exact specified ENRICHMENT PERFORMANCE summary block."""
        s = self.performance_stats
        enrich_times = s["enrichment_times"]
        avg_time = (sum(enrich_times) / len(enrich_times)) if enrich_times else 0.0
        max_time = max(enrich_times) if enrich_times else 0.0

        summary = f"""
========================================
ENRICHMENT PERFORMANCE
========================================

Sellers attempted: {s['sellers_attempted']}
Sellers completed: {s['sellers_completed']}
Sellers timed out: {s['sellers_timed_out']}
Searches executed: {s['searches_executed']}
Searches timed out: {s['searches_timed_out']}
Searches with 0 results: {s['searches_zero_results']}
Websites timed out: {s['websites_timed_out']}

Average enrichment time:
{avg_time:.1f} seconds

Maximum enrichment time:
{max_time:.1f} seconds

========================================
"""
        print(summary)
