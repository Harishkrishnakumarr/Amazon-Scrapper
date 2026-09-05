from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime

@dataclass
class SellerRecord:
    id: Optional[int] = None
    sub_sub_category: str = ""
    sub_sub_sub_category: str = ""
    s_no: int = 1
    business_name: str = "Not Found"
    business_model: str = "Marketplace Seller"
    business_category: str = "Unknown"
    owner_name: str = "Not Found"
    phone_number: str = "Not Found"
    email_address: str = "Not Found"
    gst_number: str = "Not Found"
    pan_number: str = "Not Found"
    fssai_number: str = "N/A"
    billing_address: str = "Not Found"
    city: str = "Not Found"
    state: str = "Not Found"
    pincode: str = "Not Found"
    country: str = "Not Found"
    website_url: str = "Not Found"
    status: str = "Observed on Amazon"
    source: str = "Amazon"
    
    # Internal fields
    display_name: Optional[str] = None
    legal_entity: Optional[str] = None
    seller_url: Optional[str] = None
    seller_relevance_score: float = 0.0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

@dataclass
class SellerSource:
    id: Optional[int] = None
    seller_id: Optional[int] = None
    source_name: str = "Amazon"
    source_url: str = ""
    field_name: str = ""
    field_value: str = ""
    verification_status: str = "Observed"
    collected_at: Optional[str] = None

@dataclass
class SellerOffer:
    id: Optional[int] = None
    seller_id: Optional[int] = None
    asin: str = ""
    product_url: str = ""
    product_title: str = ""
    category: str = ""
    seller_name: str = ""
    seller_profile_url: Optional[str] = None
    price: Optional[str] = None
    condition: Optional[str] = None
    source: str = "Amazon"
    created_at: Optional[str] = None

@dataclass
class CategoryRun:
    id: Optional[int] = None
    category: str = ""
    sub_sub_category: str = ""
    sub_sub_sub_category: str = ""
    run_date: Optional[str] = None
    businesses_found: int = 0
    businesses_added: int = 0
    status: str = "Completed"
