import os
import sys
sys.path.insert(0, os.path.abspath("."))
import time
import openpyxl
from main import run_single_product_test

def main():
    # Let's test 3 ASINs on Amazon India
    # Test ASIN 1: Popular book / electronics / essentials with multiple sellers
    test_asins = [
        ("B008IFXQFU", "Philips Citrus Press Juicer"), # Popular kitchen appliance usually with multiple sellers
        ("B07JW9H4J1", "Prestige Iris 750 Watt Mixer Grinder"), # Popular home appliance with many offers
        ("014342412X", "The Monk Who Sold His Ferrari") # Bestselling book with many offers
    ]

    for asin, description in test_asins:
        print(f"\n=======================================================")
        print(f"RUNNING LIVE TEST FOR: {description} (ASIN: {asin})")
        print(f"=======================================================")
        try:
            run_single_product_test(asin, headless=True, category_name=f"Test_{asin}")
        except Exception as e:
            print(f"Error testing {asin}: {e}")

if __name__ == "__main__":
    main()
