import re
from typing import Optional

def extract_email(text: str) -> str:
    if not text:
        return "Not Found"
    emails = re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text)
    if not emails:
        return "Not Found"
    
    # Priority for business prefixes
    for prefix in ["info@", "contact@", "sales@", "support@", "care@"]:
        for e in emails:
            if e.lower().startswith(prefix):
                return e
    return emails[0]

def extract_fssai(text: str, category: str = "") -> str:
    if not text:
        return "N/A" if "grocery" not in category.lower() and "food" not in category.lower() else "Not Found"
    match = re.search(r"\b[12][0-9]{13}\b", text)
    if match:
        return match.group(0)
    return "N/A" if "grocery" not in category.lower() and "food" not in category.lower() else "Not Found"

def extract_phone(text: str) -> str:
    if not text:
        return "Not Found"
    matches = re.findall(r"(?:(?:\+91|91|0)[\s\-]?)?([6-9]\d{4}[\s\-]?\d{5}|[6-9]\d{9})\b", text)
    if matches:
        for m in matches:
            clean = re.sub(r"[^\d]", "", m)
            if len(clean) == 10 and clean[0] in "6789":
                return f"+91{clean}"
    return "Not Found"

def extract_website(text: str) -> str:
    if not text:
        return "Not Found"
    urls = re.findall(r"https?://(?:www\.)?[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:/[^\s]*)?", text)
    valid_urls = [u for u in urls if "amazon." not in u.lower()]
    if valid_urls:
        return valid_urls[0]
    return "Not Found"
