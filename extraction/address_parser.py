import re
from typing import Dict, Optional

INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Delhi", "New Delhi", "Chandigarh", "Puducherry", "Jammu and Kashmir",
    "Jammu & Kashmir", "Ladakh", "Dadra and Nagar Haveli and Daman and Diu",
    "Andaman and Nicobar Islands", "Lakshadweep"
]

GST_STATE_PREFIXES = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory"
}

def clean_text_field(val: Optional[str]) -> str:
    """Strips excessive whitespace, newlines, tabs, and trailing commas/punctuation."""
    if not val:
        return ""
    cleaned = re.sub(r"[\r\n\t]+", " ", str(val))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"^[,;:\-\s]+|[,;:\-\s]+$", "", cleaned).strip()
    return cleaned

def parse_indian_address(raw_address: str, gst_number: str = "") -> Dict[str, str]:
    """
    Parses a raw Indian address string into structured components:
    Billing Address, City, State, Pincode, Country.
    Auto-derives State and Country from GSTIN if missing from the raw address string.
    """
    result = {
        "billing_address": "Not Found",
        "city": "Not Found",
        "state": "Not Found",
        "pincode": "Not Found",
        "country": "India"
    }

    if not raw_address or raw_address.strip() in ("Not Found", "Unknown", "N/A", ""):
        # Check if GST state can be derived
        if gst_number and len(gst_number.strip()) >= 2:
            code = gst_number.strip()[:2]
            if code in GST_STATE_PREFIXES:
                result["state"] = GST_STATE_PREFIXES[code]
                result["country"] = "India"
        return result

    # Normalize newlines and excess spaces
    clean_addr = clean_text_field(raw_address)

    # 1. Extract Pincode (6 digits, optional space e.g. 560 001)
    pin_match = re.search(r'\b([1-9][0-9]{2}\s?[0-9]{3})\b', clean_addr)
    if pin_match:
        norm_pin = re.sub(r'\s+', '', pin_match.group(1))
        result["pincode"] = norm_pin

    # 2. Extract State from address text
    found_state = None
    for state in INDIAN_STATES:
        pattern = r'\b' + re.escape(state) + r'\b'
        if re.search(pattern, clean_addr, re.IGNORECASE):
            found_state = state
            break

    # Fallback state derivation via GST prefix
    if not found_state and gst_number and len(gst_number.strip()) >= 2:
        code = gst_number.strip()[:2]
        if code in GST_STATE_PREFIXES:
            found_state = GST_STATE_PREFIXES[code]

    if found_state:
        result["state"] = found_state

    # 3. Extract Country (Default to India if GSTIN or Pincode is present)
    if re.search(r'\b(India|Bharat)\b', clean_addr, re.IGNORECASE) or result["pincode"] != "Not Found" or (gst_number and gst_number != "Not Found"):
        result["country"] = "India"

    # 4. Extract City & Billing Address from comma-separated tokens
    parts = [clean_text_field(p) for p in clean_addr.split(",") if clean_text_field(p)]
    if parts:
        city_candidate = None
        for p in reversed(parts):
            p_clean = re.sub(r'\b([1-9][0-9]{2}\s?[0-9]{3})\b', '', p, flags=re.IGNORECASE).strip()
            p_clean = re.sub(r'[-–]', '', p_clean).strip()

            if not p_clean:
                continue
            if p_clean.lower() in ("india", "bharat"):
                continue
            if found_state and p_clean.lower() == found_state.lower():
                continue

            # If token looks like a city name (1-3 words, no long numbers)
            if len(p_clean.split()) <= 3 and not re.search(r'\d', p_clean):
                city_candidate = clean_text_field(p_clean)
                break

        if city_candidate:
            result["city"] = city_candidate

        # Clean street parts
        street_parts = []
        for p in parts:
            if re.search(r'\b(India|Bharat)\b', p, re.IGNORECASE) and len(p.split()) <= 2:
                continue
            street_parts.append(p)

        result["billing_address"] = clean_text_field(", ".join(street_parts)) if street_parts else clean_addr

    return result
