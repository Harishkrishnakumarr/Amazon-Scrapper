import re
from typing import Dict, Optional

INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Delhi", "New Delhi", "Chandigarh", "Puducherry", "Jammu & Kashmir", "Ladakh"
]

def parse_indian_address(raw_address: str) -> Dict[str, str]:
    """
    Parses a raw Indian address string into structured components:
    Billing Address, City, State, Pincode, Country.
    """
    result = {
        "billing_address": "Not Found",
        "city": "Not Found",
        "state": "Not Found",
        "pincode": "Not Found",
        "country": "India"
    }

    if not raw_address or raw_address in ("Not Found", "Unknown", "N/A"):
        return result

    clean_addr = raw_address.strip()
    
    # 1. Extract Pincode (6 digits)
    pin_match = re.search(r'\b([1-9][0-9]{5})\b', clean_addr)
    if pin_match:
        result["pincode"] = pin_match.group(1)

    # 2. Extract State
    found_state = None
    for state in INDIAN_STATES:
        pattern = r'\b' + re.escape(state) + r'\b'
        if re.search(pattern, clean_addr, re.IGNORECASE):
            found_state = state
            break
            
    if found_state:
        result["state"] = found_state

    # 3. Extract Country
    if re.search(r'\b(India|Bharat)\b', clean_addr, re.IGNORECASE):
        result["country"] = "India"

    # 4. Extract City & Billing Address from comma-separated tokens
    parts = [p.strip() for p in clean_addr.split(",") if p.strip()]
    if parts:
        # Check tokens backwards for City
        city_candidate = None
        for p in reversed(parts):
            # Ignore tokens that match pincode, country, or state
            p_clean = re.sub(r'\b([1-9][0-9]{5})\b', '', p, flags=re.IGNORECASE).strip()
            p_clean = re.sub(r'[-–]', '', p_clean).strip()
            
            if not p_clean:
                continue
            if p_clean.lower() in ("india", "bharat"):
                continue
            if found_state and p_clean.lower() == found_state.lower():
                continue
                
            # If token looks like a city name (1-3 words, no long numbers)
            if len(p_clean.split()) <= 3 and not re.search(r'\d', p_clean):
                city_candidate = p_clean
                break
                
        if city_candidate:
            result["city"] = city_candidate
            
        # Billing address is street/building portion excluding trailing country/pincode
        street_parts = []
        for p in parts:
            if re.search(r'\b(India|Bharat)\b', p, re.IGNORECASE) and len(p.split()) <= 2:
                continue
            street_parts.append(p)
            
        result["billing_address"] = ", ".join(street_parts) if street_parts else clean_addr

    return result
