import logging
from typing import Dict, Any, Tuple, List
from database.models import SellerRecord, SellerSource
from extraction.normalizer import (
    normalize_category, normalize_phone, normalize_gst,
    normalize_pan, normalize_address
)
from extraction.business_extractor import extract_email, extract_fssai, extract_website

logger = logging.getLogger("amazon_scraper")

class SellerExtractor:
    """
    Parses and builds validated SellerRecord data structure from raw discovery outputs.
    Ensures zero data invention / hallucination.
    """
    
    @staticmethod
    def build_seller_record(raw_data: Dict[str, Any], category: str = "Unknown") -> Tuple[SellerRecord, List[SellerSource]]:
        disp_name = raw_data.get("display_name") or "Not Found"
        legal_ent = raw_data.get("legal_entity")
        
        # Priority for Business Name: Display Name or Legal Entity
        b_name = disp_name if disp_name != "Not Found" else (legal_ent or "Not Found")
        
        # Normalize category
        norm_category = normalize_category(category)
        
        # Normalize Address
        addr_info = normalize_address(raw_data.get("business_address_raw") or "")
        
        # Normalize Phone
        phone = normalize_phone(raw_data.get("phone_raw") or "")
        
        # Normalize GST
        gst = normalize_gst(raw_data.get("gst_number_raw") or "")
        
        # Extract Email
        email = extract_email(raw_data.get("email_raw") or "")
        
        # Extract FSSAI
        fssai = extract_fssai(raw_data.get("business_address_raw") or "", norm_category)
        
        # Extract Website
        website = extract_website(raw_data.get("business_address_raw") or "")

        seller_url = raw_data.get("seller_profile_url")

        record = SellerRecord(
            sub_sub_category=category,
            business_name=b_name,
            business_model="Seller",
            business_category=norm_category,
            owner_name="Not Found",
            phone_number=phone,
            email_address=email,
            gst_number=gst,
            pan_number="Not Found",
            fssai_number=fssai,
            billing_address=addr_info["billing_address"],
            city=addr_info["city"],
            state=addr_info["state"],
            pincode=addr_info["pincode"],
            country=addr_info["country"],
            website_url=website,
            status="Observed on Amazon",
            source="Amazon",
            display_name=disp_name,
            legal_entity=legal_ent,
            seller_url=seller_url
        )

        sources = []
        if seller_url:
            sources.append(SellerSource(
                source_name="Amazon",
                source_url=seller_url,
                field_name="Business Name",
                field_value=b_name,
                verification_status="Observed"
            ))
            if addr_info["billing_address"] != "Not Found":
                sources.append(SellerSource(
                    source_name="Amazon",
                    source_url=seller_url,
                    field_name="Billing Address",
                    field_value=addr_info["billing_address"],
                    verification_status="Observed"
                ))
            if gst != "Not Found":
                sources.append(SellerSource(
                    source_name="Amazon",
                    source_url=seller_url,
                    field_name="GST Number",
                    field_value=gst,
                    verification_status="Observed"
                ))

        return record, sources
