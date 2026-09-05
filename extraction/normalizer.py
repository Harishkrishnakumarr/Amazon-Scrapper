import re
from typing import Dict, Optional, Tuple
from extraction.address_parser import parse_indian_address

CATEGORY_MAPPINGS = {
    r"(?i)\bmen'?s?\s*(shoes|footwear|foot\s*wear)\b": "Men's Footwear",
    r"(?i)\bwomen'?s?\s*(shoes|footwear|foot\s*wear)\b": "Women's Footwear",
    r"(?i)\bhome\s*(decor|decoration|furnishing)\b": "Home Decor",
    r"(?i)\belectronics?|gadgets?\b": "Electronics",
    r"(?i)\bkitchen(ware)?|cookware\b": "Kitchen",
    r"(?i)\bbeaut(y|iful)|cosmetics?\b": "Beauty",
    r"(?i)\bgrocer(y|ies)|gourmet\b": "Grocery",
    r"(?i)\bpet\s*(supplies|care)\b": "Pet Supplies",
    r"(?i)\bcloth(ing|es)|apparel|garments?\b": "Clothing",
    r"(?i)\bbooks?|literature\b": "Books",
    r"(?i)\btoys?|games?\b": "Toys",
}

def normalize_category(category_raw: str) -> str:
    if not category_raw or category_raw.strip() in ("", "Unknown", "Not Found"):
        return "Unknown"
    
    clean_cat = category_raw.strip()
    for pattern, normalized in CATEGORY_MAPPINGS.items():
        if re.search(pattern, clean_cat):
            return normalized
            
    return clean_cat.title()

def normalize_phone(phone_raw: str) -> str:
    if not phone_raw or phone_raw in ("Not Found", "N/A"):
        return "Not Found"
    
    digits = re.sub(r"[^\d]", "", phone_raw)
    if len(digits) == 10:
        return f"+91{digits}"
    elif len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    elif len(digits) > 10 and digits.endswith(digits[-10:]):
        return f"+91{digits[-10:]}"
    
    return "Not Found"

def normalize_email(email_raw: str) -> str:
    if not email_raw or email_raw in ("Not Found", "N/A"):
        return "Not Found"
    
    match = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", email_raw)
    if match:
        return match.group(0).lower()
    return "Not Found"

GST_STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan",
    "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura",
    "17": "Meghalaya", "18": "Assam", "19": "West Bengal", "20": "Jharkhand",
    "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
    "34": "Puducherry", "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh"
}

def normalize_gst(gst_raw: str) -> str:
    if not gst_raw or gst_raw in ("Not Found", "N/A", "Unverified"):
        return "Not Found"
    
    match = re.search(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}\b", gst_raw.upper())
    if match:
        return match.group(0)
    return "Not Found"

def validate_gstin(gst_raw: str) -> bool:
    return normalize_gst(gst_raw) != "Not Found"

def extract_pan_from_gstin(gst_raw: str) -> Optional[str]:
    """Extracts the 10-character PAN embedded in characters 3-12 of a 15-character GSTIN."""
    gstin = normalize_gst(gst_raw)
    if gstin != "Not Found" and len(gstin) == 15:
        return gstin[2:12]
    return None

def validate_gstin_pan_match(gst_raw: str, target_pan: str) -> bool:
    """Returns True ONLY if the PAN embedded in GSTIN exactly matches target_pan."""
    extracted_pan = extract_pan_from_gstin(gst_raw)
    norm_target = normalize_pan(target_pan)
    if extracted_pan and norm_target != "Not Found":
        return extracted_pan.upper() == norm_target.upper()
    return False

def get_state_from_gstin(gst_raw: str) -> Tuple[str, str]:
    """Returns (state_code, state_name) from GSTIN."""
    gstin = normalize_gst(gst_raw)
    if gstin != "Not Found" and len(gstin) >= 2:
        code = gstin[:2]
        return code, GST_STATE_CODES.get(code, "Unknown")
    return "", "Unknown"

def normalize_pan(pan_raw: str) -> str:
    if not pan_raw or pan_raw in ("Not Found", "N/A"):
        return "Not Found"
    
    match = re.search(r"\b[A-Z]{5}[0-9]{4}[A-Z]{1}\b", pan_raw.upper())
    if match:
        return match.group(0)
    return "Not Found"

def validate_pan(pan_raw: str) -> bool:
    return normalize_pan(pan_raw) != "Not Found"

def validate_pincode(pincode_raw: str) -> bool:
    if not pincode_raw or pincode_raw in ("Not Found", "N/A"):
        return False
    return bool(re.search(r"\b[1-9][0-9]{5}\b", str(pincode_raw)))

def normalize_address(addr_raw: str) -> Dict[str, str]:
    return parse_indian_address(addr_raw)

def normalize_seller_key(name: str) -> str:
    """
    Normalizes seller name string for unique deduplication comparison
    while preserving original display name for storage and exports.
    """
    if not name:
        return ""
    name = name.lower().strip()
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"[^\w\s&.-]", "", name)
    return name.strip()

