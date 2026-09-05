import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database.database import init_db
from database.repository import SellerRepository
from database.models import SellerRecord
from export.excel_exporter import export_sellers_to_master_excel

def test_recovery_and_timeout():
    temp_dir = tempfile.mkdtemp()
    db_file = os.path.join(temp_dir, "test.db")
    master_file = os.path.join(temp_dir, "test_master.xlsx")

    init_db(db_file)
    repo = SellerRepository(db_file)

    # 1. Save 3 records for "Category A" in SQLite
    for i in range(1, 4):
        rec = SellerRecord(
            s_no=i,
            sub_sub_category="Category A",
            business_name=f"Seller A{i}",
            phone_number=f"+91987654320{i}"
        )
        repo.save_or_update_seller(rec)

    # Test recovery when save_success_cnt > 0 (as in KeyboardInterrupt / Timeout)
    recovered = repo.get_sellers_by_category("Category A")
    assert len(recovered) == 3, f"Expected 3 records in DB, got {len(recovered)}"

    res = export_sellers_to_master_excel(
        sellers=recovered,
        current_category="Category A",
        output_path=master_file,
        allow_reprocess=False
    )
    assert res["status"] == "SUCCESS"
    assert res["added_count"] == 3
    assert res["total_records"] == 3

    # 2. Simulate second category "Category B" with Timeout where 2 sellers saved
    for i in range(1, 3):
        rec = SellerRecord(
            s_no=i,
            sub_sub_category="Category B",
            business_name=f"Seller B{i}",
            phone_number=f"+91987654330{i}"
        )
        repo.save_or_update_seller(rec)

    recovered_b = repo.get_sellers_by_category("Category B")
    assert len(recovered_b) == 2

    res_b = export_sellers_to_master_excel(
        sellers=recovered_b,
        current_category="Category B",
        output_path=master_file,
        allow_reprocess=False
    )
    assert res_b["status"] == "APPENDED SUCCESSFULLY"
    assert res_b["added_count"] == 2
    assert res_b["total_records"] == 5

    # 3. Simulate batch total count logic
    results = [
        {"category": "Category A", "status": "SUCCESS", "added_count": res["added_count"]},
        {"category": "Category B", "status": "TIMEOUT", "added_count": res_b["added_count"]},
        {"category": "Category C", "status": "SKIPPED - ALREADY EXISTS", "added_count": 0},
        {"category": "Category D", "status": "FAILED", "added_count": 0},
    ]

    total_added_sellers = 0
    for r in results:
        added_cnt = r.get("added_count", 0)
        if added_cnt > 0:
            total_added_sellers += added_cnt

    assert total_added_sellers == 5, f"Expected 5 total added sellers, got {total_added_sellers}"
    print("\nPASS: Recovery, Timeout export, and Batch total calculation verified successfully!")

if __name__ == "__main__":
    test_recovery_and_timeout()
