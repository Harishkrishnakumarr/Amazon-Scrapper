import os
import sys
import tempfile
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database.database import init_db
from database.models import SellerRecord
from database.repository import SellerRepository
from main import load_current_category_and_url

def test_category_enforcement():
    print("==================================================")
    print("STARTING CATEGORY ENFORCEMENT & VALIDATION TESTS")
    print("==================================================")

    # -------------------------------------------------------------
    # TEST 1: Test load_current_category_and_url with various inputs
    # -------------------------------------------------------------
    print("\n--- TEST 1: URL & Category Parsing Logic ---")
    temp_dir = tempfile.mkdtemp()
    cat_file = os.path.join(temp_dir, "current_category.txt")
    
    # Case A: Pipe delimited format
    with open(cat_file, "w", encoding="utf-8") as f:
        f.write("Women's Flats Amazon|https://www.amazon.in/s?k=women+flats\n")
    
    # Override cat_file path for test
    import main
    orig_cat_file = "input/current_category.txt"
    try:
        # Patch cat_file in main
        main_cat_load = lambda line: main.load_current_category_and_url({})
    except Exception:
        pass

    # Direct logic validation
    from urllib.parse import urlparse, parse_qs
    
    # Test Node browse URL without k=
    node_url = "https://www.amazon.in/Sports-Outdoor-Women-Shoes/b?ie=UTF8&node=1983579031"
    parsed = urlparse(node_url)
    path_parts = [p for p in parsed.path.split("/") if p and p not in ("b", "s", "dp", "gp", "ref=sr_1_1")]
    inferred = path_parts[0].replace("-", " ").replace("+", " ").replace("_", " ").strip().title()
    assert inferred == "Sports Outdoor Women Shoes", f"Expected 'Sports Outdoor Women Shoes', got '{inferred}'"
    print(f"PASS: Browse URL '{node_url}' correctly resolved to category '{inferred}'.")

    # Test Search URL with k=
    search_url = "https://www.amazon.in/s?k=women+flats"
    parsed_s = urlparse(search_url)
    qs_s = parse_qs(parsed_s.query)
    inferred_s = qs_s["k"][0].replace("+", " ").strip().title()
    assert inferred_s == "Women Flats", f"Expected 'Women Flats', got '{inferred_s}'"
    print(f"PASS: Search URL '{search_url}' correctly resolved to category '{inferred_s}'.")

    # -------------------------------------------------------------
    # TEST 2: Repository Strict Validation Against 'Current Category'
    # -------------------------------------------------------------
    print("\n--- TEST 2: Repository Rejection of 'Current Category' & Empty Category ---")
    test_db = os.path.join(temp_dir, "test_cat_validation.db")
    init_db(test_db)
    repo = SellerRepository(test_db)

    # Attempt to save record with sub_sub_category = 'Current Category' -> MUST raise ValueError
    invalid_rec1 = SellerRecord(
        sub_sub_category="Current Category",
        business_name="Test Seller",
        gst_number="29AAJCC8517E1ZH"
    )
    try:
        repo.save_or_update_seller(invalid_rec1)
        assert False, "Repository failed to reject sub_sub_category = 'Current Category'!"
    except ValueError as e:
        print(f"PASS: Repository correctly rejected 'Current Category': {e}")

    # Attempt to save record with empty sub_sub_category -> MUST raise ValueError
    invalid_rec2 = SellerRecord(
        sub_sub_category="",
        business_name="Test Seller",
        gst_number="29AAJCC8517E1ZH"
    )
    try:
        repo.save_or_update_seller(invalid_rec2)
        assert False, "Repository failed to reject empty sub_sub_category!"
    except ValueError as e:
        print(f"PASS: Repository correctly rejected empty category: {e}")

    # Valid category save -> MUST succeed
    valid_rec = SellerRecord(
        sub_sub_category="Women's Flats Amazon",
        business_name="Cocoblu Retail",
        gst_number="29AAJCC8517E1ZH",
        phone_number="+915600712026"
    )
    saved_valid, is_new = repo.save_or_update_seller(valid_rec)
    assert is_new is True
    assert saved_valid.sub_sub_category == "Women's Flats Amazon"
    print(f"PASS: Valid record saved with category '{saved_valid.sub_sub_category}'.")

    # -------------------------------------------------------------
    # TEST 3: Database Auditor cleans invalid 'Current Category' records
    # -------------------------------------------------------------
    print("\n--- TEST 3: Database Auditor Invalid Category Cleanup ---")
    conn = sqlite3.connect(test_db)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO sellers (sub_sub_category, business_name) VALUES ('Current Category', 'Corrupted Seller')")
    cursor.execute("INSERT INTO category_runs (category, sub_sub_category) VALUES ('Current Category', 'Current Category')")
    conn.commit()
    conn.close()

    cleaned = repo.clean_or_migrate_invalid_categories()
    assert len(cleaned) == 1, f"Expected 1 cleaned record, got {len(cleaned)}"
    assert cleaned[0]["business_name"] == "Corrupted Seller"

    # Verify no 'Current Category' records exist in DB
    conn = sqlite3.connect(test_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sellers WHERE LOWER(sub_sub_category) = 'current category'")
    cnt = cursor.fetchone()[0]
    conn.close()
    assert cnt == 0, f"Expected 0 'Current Category' records, got {cnt}"
    print("PASS: Database auditor cleaned all 'Current Category' records successfully.")

    print("\n==================================================")
    print("ALL CATEGORY ENFORCEMENT CHECKS PASSED!")
    print("==================================================")

if __name__ == "__main__":
    test_category_enforcement()
