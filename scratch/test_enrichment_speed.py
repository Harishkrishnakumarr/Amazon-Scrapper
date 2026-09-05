import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from unittest.mock import MagicMock
from database.models import SellerRecord
from extraction.public_enrichment import (
    PublicEnrichmentEngine,
    is_valid_search_result_url,
    decode_search_redirect_url
)

def test_public_enrichment_optimizations():
    print("\n--- TEST: Junk Domain and Disallowed TLD Filtering ---")
    assert not is_valid_search_result_url("https://www.zhihu.com/question/123"), "Zhihu should be filtered"
    assert not is_valid_search_result_url("https://zhidao.baidu.com/question/123.html"), "Baidu should be filtered"
    assert not is_valid_search_result_url("https://soso-forum.tistory.com/6"), "Tistory should be filtered"
    assert not is_valid_search_result_url("https://incometaxindia.gov.in/pan"), "Income tax gov should be filtered"
    assert not is_valid_search_result_url("https://reg.gst.gov.in/registration/"), "GST gov should be filtered"
    assert not is_valid_search_result_url("https://www.guide4moms.com/something"), "Guide4moms should be filtered"
    assert not is_valid_search_result_url("https://example.cn/page"), ".cn TLD should be filtered"
    assert not is_valid_search_result_url("https://example.ru/page"), ".ru TLD should be filtered"
    assert is_valid_search_result_url("https://www.phbrandsofficial.in/contact"), "Valid business site should be accepted"
    print("PASS: Junk domains and foreign TLDs correctly filtered.")

    print("\n--- TEST: Opportunistic Multi-Field Extraction ---")
    mock_bm = MagicMock()
    mock_page = MagicMock()
    mock_bm.new_page.return_value = mock_page

    engine = PublicEnrichmentEngine(mock_bm, max_seller_enrichment_seconds=120)

    sample_text = """
    Welcome to PHBrands Retail Private Limited.
    For customer care and queries:
    Contact Number: +91 98765 43210
    Email: support@phbrands.in
    GSTIN: 07AAAAA1234A1Z5
    Director: Rajesh Sharma
    Registered Office Address: Plot 42, Sector 18, Udyog Vihar, Gurugram, Haryana 122001, India.
    """

    rec = SellerRecord(business_name="PHBrands")
    field_records = {}
    def dummy_record_field(field_name, val, src_name, src_url):
        field_records[field_name] = val

    found = engine._extract_all_fields_from_text(sample_text, rec, "PHBrands", "Targeted Search", "https://test.com", dummy_record_field)

    assert found is True
    assert rec.legal_entity == "PHBrands Retail Private Limited"
    assert "9876543210" in rec.phone_number.replace("+91", "").replace(" ", "").replace("-", "")
    assert rec.email_address == "support@phbrands.in"
    assert rec.gst_number == "07AAAAA1234A1Z5"
    assert rec.pan_number == "AAAAA1234A"
    assert rec.owner_name == "Rajesh Sharma"
    assert rec.billing_address != "Not Found"
    assert rec.pincode == "122001"
    assert engine._all_essential_fields_found(rec) is True

    print("PASS: Opportunistic multi-field extraction extracted all fields in a single pass.")

    print("\n==================================================")
    print("ALL ENRICHMENT OPTIMIZATION TESTS PASSED!")
    print("==================================================")

if __name__ == "__main__":
    test_public_enrichment_optimizations()
