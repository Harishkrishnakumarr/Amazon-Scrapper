import os
import sys
sys.path.insert(0, os.path.abspath("."))
import io
import unittest
from extraction.normalizer import normalize_seller_key
from database.models import SellerRecord
from export.excel_exporter import export_sellers_to_master_excel
import openpyxl

class TestMultiSellerExtraction(unittest.TestCase):
    def test_normalize_seller_key(self):
        self.assertEqual(normalize_seller_key("ABC   Enterprises Pvt. Ltd."), "abc enterprises pvt. ltd.")
        self.assertEqual(normalize_seller_key("  RetailEZ Private Limited  "), "retailez private limited")
        self.assertEqual(normalize_seller_key("Cocoblu Retail"), "cocoblu retail")
        self.assertEqual(normalize_seller_key("Cocoblu  Retail."), "cocoblu retail.")
        self.assertEqual(normalize_seller_key(""), "")
        self.assertEqual(normalize_seller_key(None), "")
        self.assertEqual(normalize_seller_key("Brand & Co."), "brand & co.")

    def test_excel_export_all_sellers_no_truncation(self):
        # Create 35 dummy sellers
        test_file = "output/test_export_35_sellers.xlsx"
        if os.path.exists(test_file):
            try: os.unlink(test_file)
            except Exception: pass

        dummy_sellers = []
        for i in range(1, 36):
            dummy_sellers.append(SellerRecord(
                id=i,
                sub_sub_category="Test Multi Seller Category",
                s_no=i,
                business_name=f"Seller Business {i}",
                phone_number=f"+9198765432{i:02d}",
                email_address=f"seller{i}@example.com",
                gst_number=f"29ABCDE{i:04d}F1Z5",
                pan_number=f"ABCDE{i:04d}F",
                billing_address=f"Street {i}, Bengaluru",
                city="Bengaluru",
                state="Karnataka",
                pincode="560001",
                status="Verified",
                source="Amazon"
            ))

        result = export_sellers_to_master_excel(
            sellers=dummy_sellers,
            current_category="Test Multi Seller Category",
            output_path=test_file,
            allow_reprocess=True
        )

        self.assertEqual(result["added_count"], 35)
        self.assertTrue(os.path.exists(test_file))

        wb = openpyxl.load_workbook(test_file)
        ws = wb["Amazon Sellers"]
        # 1 header row + 35 data rows = 36 total rows
        self.assertEqual(ws.max_row, 36)
        wb.close()

        # Clean up test file
        try: os.unlink(test_file)
        except Exception: pass

if __name__ == "__main__":
    unittest.main()
