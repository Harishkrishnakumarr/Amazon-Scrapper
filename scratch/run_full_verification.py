import os
import sys
sys.path.insert(0, os.path.abspath("."))
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import time
import openpyxl
from main import run_single_product_test

def run_verification():
    # 3 test products for verification:
    # 1. B075JJ5NQC - Butterfly Jet Elite Mixer Grinder (3-5 sellers)
    # 2. B008IFXQFU - Philips Citrus Press (1-5 sellers)
    # 3. 9389432470 - Word Power Made Easy or B07WHR5BLH - boAt Earphones / popular multi-offer product
    
    test_products = [
        ("B075JJ5NQC", "Butterfly Jet Elite 750 Watt Mixer Grinder"),
        ("B008IFXQFU", "Philips Citrus Press Juicer"),
        ("9389432470", "Word Power Made Easy Paperback")
    ]

    for asin, desc in test_products:
        print(f"\n==================================================================")
        print(f"STARTING VERIFICATION TEST: {desc} (ASIN: {asin})")
        print(f"==================================================================")
        try:
            run_single_product_test(asin, headless=False, category_name=f"Verified_{asin}")
        except Exception as e:
            print(f"Error during verification test for {asin}: {e}")
        time.sleep(2)

if __name__ == "__main__":
    run_verification()
