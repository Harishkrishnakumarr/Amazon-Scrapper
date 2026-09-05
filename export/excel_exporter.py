import os
import io
import shutil
import zipfile
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Dict, Any, Optional
import openpyxl
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from database.models import SellerRecord

logger = logging.getLogger("amazon_scraper")

COLUMNS = [
    "SUB SUB CATEGORY",
    "SUB SUB SUB CATEGORY",
    "S.NO",
    "Business Name",
    "Business Model",
    "Business Category",
    "Owner Name",
    "Phone Number",
    "Email Address",
    "GST Number",
    "PAN Number",
    "FSSAI Number",
    "Billing Address",
    "x",
    "City",
    "State",
    "Pincode",
    "Country",
    "Website URL",
    "Status",
    "Source"
]

def export_sellers_to_master_excel(
    sellers: List[SellerRecord],
    current_category: str,
    output_path: str = "output/Amazon_Seller_Master_Data.xlsx",
    allow_reprocess: bool = False
) -> Dict[str, Any]:
    """
    Appends new category records (top 20) to the permanent master Excel workbook.
    Strictly preserves all existing categories, worksheets, and formatting.
    Performs deep backup validation, write lock testing, temporary workbook validation,
    atomic replacement, and post-save verification.
    """
    final_path = Path(output_path).resolve()
    output_dir = final_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    backup_dir = output_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    temp_path = output_dir / f".Amazon_Seller_Master_Data_tmp_{os.getpid()}.xlsx"
    if temp_path.exists():
        try:
            temp_path.unlink()
        except Exception:
            pass

    # Styling definitions
    header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )

    # Text format column indices (1-based): Phone(8), GST(10), PAN(11), FSSAI(12), Pincode(17)
    text_cols = {8, 10, 11, 12, 17}

    existing_master_records = 0
    existing_categories = set()
    file_existed = final_path.exists()

    if file_existed:
        # Step 1: Validate Source Master Before Modification or Backup
        source_size = os.path.getsize(final_path)
        if source_size == 0:
            raise RuntimeError("MASTER EXCEL IS EMPTY OR INVALID (size is 0 bytes).")
        
        if not zipfile.is_zipfile(str(final_path)):
            raise RuntimeError("MASTER EXCEL IS NOT A VALID XLSX (invalid ZIP structure).")

        # Read source master bytes into memory
        with open(final_path, "rb") as f:
            source_bytes = f.read()

        try:
            source_wb = load_workbook(io.BytesIO(source_bytes), data_only=True)
        except Exception as e:
            raise RuntimeError(f"MASTER EXCEL FAILED OPENPYXL VALIDATION: {e}")

        if "Amazon Sellers" not in source_wb.sheetnames:
            source_wb.close()
            raise RuntimeError("MASTER EXCEL IS MISSING 'Amazon Sellers' WORKSHEET.")

        source_ws = source_wb["Amazon Sellers"]
        source_row_count = source_ws.max_row
        
        # Collect existing rows and seller names
        source_sellers = []
        existing_rows = []
        for row in source_ws.iter_rows(min_row=2, values_only=True):
            if row and row[0]:
                cat_val = str(row[0]).strip()
                if cat_val:
                    existing_categories.add(cat_val.lower())
                    existing_rows.append(row)
                    existing_master_records += 1
                if len(row) > 3 and row[3]:
                    source_sellers.append(str(row[3]).strip())
        source_wb.close()

        # Step 2: Lock / Exclusivity Check before any mutation
        try:
            with open(final_path, "a+b") as test_f:
                pass
        except (PermissionError, OSError) as e:
            print("\n========================================")
            print("MASTER EXCEL IS CURRENTLY OPEN/LOCKED.")
            print("\nPlease close:")
            print(f"{final_path}")
            print("\nThen run the category again.")
            print("========================================\n")
            raise RuntimeError(f"MASTER EXCEL IS CURRENTLY OPEN/LOCKED ({e}). Please close the file in Excel and retry.")

        # Step 3: Create & Validate Safety Backup
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"Amazon_Seller_Master_Data_{timestamp}_backup.xlsx"

        try:
            shutil.copy2(str(final_path), str(backup_path))
        except Exception as e:
            raise RuntimeError(f"FAILED TO COPY MASTER TO BACKUP: {e}")

        # Deep Backup Verification
        if not backup_path.exists():
            raise RuntimeError("BACKUP VALIDATION FAILED: Backup file does not exist after copy.")
        
        backup_size = os.path.getsize(backup_path)
        if backup_size == 0:
            raise RuntimeError("BACKUP VALIDATION FAILED: Backup file size is 0 bytes.")

        if not zipfile.is_zipfile(str(backup_path)):
            raise RuntimeError("BACKUP VALIDATION FAILED: Backup file is not a valid ZIP/XLSX.")

        try:
            with open(backup_path, "rb") as bf:
                backup_bytes = bf.read()
            backup_wb = load_workbook(io.BytesIO(backup_bytes), data_only=True)
        except Exception as e:
            raise RuntimeError(f"BACKUP VALIDATION FAILED: openpyxl failed to open backup file: {e}")

        if "Amazon Sellers" not in backup_wb.sheetnames:
            backup_wb.close()
            raise RuntimeError("BACKUP VALIDATION FAILED: 'Amazon Sellers' worksheet missing in backup.")

        backup_ws = backup_wb["Amazon Sellers"]
        backup_row_count = backup_ws.max_row
        backup_wb.close()

        if backup_row_count != source_row_count:
            raise RuntimeError(f"BACKUP VALIDATION FAILED: Row count mismatch (Source: {source_row_count}, Backup: {backup_row_count})")

        # Print formatted backup verification block
        first_sellers_str = "\n".join([f"{i}. {name}" for i, name in enumerate(source_sellers[:5], 1)])
        if not first_sellers_str:
            first_sellers_str = "None (Header only)"

        print("\n========================================")
        print("BACKUP VALIDATION")
        print("=================")
        print(f"Backup:\n{backup_path}\n")
        print(f"Source rows:\n{source_row_count}\n")
        print(f"Backup rows:\n{backup_row_count}\n")
        print(f"Source size:\n{source_size} bytes\n")
        print(f"Backup size:\n{backup_size} bytes\n")
        print("Backup XLSX:\nVALID\n")
        print("Worksheet:\nAmazon Sellers\n")
        print(f"First sellers:\n{first_sellers_str}\n")
        print("BACKUP VERIFIED SUCCESSFULLY")
        print("========================================\n")
        logger.info(f"Created safety backup of master Excel at {backup_path}")

        # Check Category Duplicate Protection
        if not allow_reprocess and current_category.strip().lower() in existing_categories:
            cat_count = sum(1 for r in existing_rows if r[0] and str(r[0]).strip().lower() == current_category.strip().lower())
            return {
                "status": "SKIPPED_ALREADY_EXISTS",
                "file_path": str(final_path),
                "existing_records": existing_master_records,
                "total_records": existing_master_records,
                "master_categories": len(existing_categories),
                "added_count": 0,
                "cat_count": cat_count
            }

        # Load existing workbook for modifications
        wb = load_workbook(io.BytesIO(source_bytes))
        ws = wb["Amazon Sellers"]

        # If allow_reprocess and category already existed in workbook, rebuild rows excluding old category rows
        if allow_reprocess and current_category.strip().lower() in existing_categories:
            other_rows = [r for r in existing_rows if str(r[0]).strip().lower() != current_category.strip().lower()]
            ws.delete_rows(2, ws.max_row)
            for r in other_rows:
                ws.append(list(r))
            existing_master_records = len(other_rows)
            existing_categories = set(str(r[0]).strip().lower() for r in other_rows if r and r[0])
    else:
        # Create brand new master workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Amazon Sellers"

        # Write Header Row
        ws.append(COLUMNS)
        for col_idx in range(1, len(COLUMNS) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_alignment
            cell.border = thin_border
        
        ws.row_dimensions[1].height = 28
        ws.freeze_panes = "A2"

    # Step 4: Append ONLY New Category Records (All collected sellers)
    added_count = 0

    for idx, s in enumerate(sellers, 1):
        row_data = [
            s.sub_sub_category or current_category,
            s.sub_sub_sub_category or "",
            idx,  # S.NO resets to 1..N for each category
            s.business_name,
            s.business_model,
            s.business_category,
            s.owner_name,
            str(s.phone_number) if s.phone_number else "Not Found",
            s.email_address,
            str(s.gst_number) if s.gst_number else "Not Found",
            str(s.pan_number) if s.pan_number else "Not Found",
            str(s.fssai_number) if s.fssai_number else "N/A",
            s.billing_address,
            "",  # x column
            s.city,
            s.state,
            str(s.pincode) if s.pincode else "Not Found",
            s.country,
            s.website_url,
            s.status,
            s.source
        ]
        ws.append(row_data)
        added_count += 1
        
        row_idx = ws.max_row
        for col_idx in range(1, len(COLUMNS) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = thin_border
            cell.font = Font(name="Calibri", size=10)
            
            if col_idx in text_cols:
                cell.number_format = '@'
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif col_idx == 13: # Billing Address
                cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            elif col_idx == 3: # S.NO
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    # Enable Auto Filter
    ws.auto_filter.ref = ws.dimensions

    # Auto-adjust column widths
    for col in ws.columns:
        max_len = 0
        col_idx = col[0].column
        col_letter = get_column_letter(col_idx)
        for cell in col:
            val_str = str(cell.value or '')
            if col_idx == 13: # Billing Address
                max_len = 35
                break
            max_len = max(max_len, len(val_str))
        
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    # Step 5: Save to Temporary XLSX
    try:
        wb.save(str(temp_path))
        wb.close()
    except Exception as e:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        raise RuntimeError(f"Failed to write temporary Excel file: {e}")

    # Step 6: Validate Temporary XLSX before modifying Master
    if not temp_path.exists() or not zipfile.is_zipfile(str(temp_path)):
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        raise RuntimeError("Generated temporary Excel file is invalid/corrupted ZIP.")

    try:
        with open(temp_path, "rb") as tf:
            test_wb = load_workbook(io.BytesIO(tf.read()), read_only=True)
            if "Amazon Sellers" not in test_wb.sheetnames:
                test_wb.close()
                raise RuntimeError("Temporary Excel file missing 'Amazon Sellers' sheet.")
            test_wb.close()
    except Exception as e:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        raise RuntimeError(f"Generated temporary Excel file failed openpyxl validation: {e}")

    # Step 7: Atomic Replacement of Master Excel
    try:
        os.replace(str(temp_path), str(final_path))
    except (PermissionError, OSError) as e:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        print("\n========================================")
        print("MASTER REPLACEMENT FAILED — ORIGINAL MASTER PRESERVED.")
        print("MASTER EXCEL IS CURRENTLY OPEN/LOCKED.")
        print("\nPlease close:")
        print(f"{final_path}")
        print("\nThen run again.")
        print("========================================\n")
        raise RuntimeError(f"MASTER REPLACEMENT FAILED — ORIGINAL MASTER PRESERVED ({e}). Please close Excel and retry.")

    # Step 8: Final Master Excel Deep Read-Back & Verification
    if not final_path.exists() or not zipfile.is_zipfile(str(final_path)):
        raise RuntimeError("Master file is invalid or missing after replacement.")

    with open(final_path, "rb") as f:
        final_bytes = f.read()

    final_size = len(final_bytes)
    check_wb = load_workbook(io.BytesIO(final_bytes), data_only=True)
    if "Amazon Sellers" not in check_wb.sheetnames:
        check_wb.close()
        raise RuntimeError("Final Master Excel missing 'Amazon Sellers' sheet.")

    check_ws = check_wb["Amazon Sellers"]
    final_total_rows = check_ws.max_row
    
    master_categories = set()
    category_rows_count = 0
    first_cat_sellers = []

    for row in check_ws.iter_rows(min_row=2, values_only=True):
        if row and row[0]:
            cat_name = str(row[0]).strip()
            master_categories.add(cat_name)
            if cat_name.lower() == current_category.strip().lower():
                category_rows_count += 1
                if len(row) > 3 and row[3]:
                    first_cat_sellers.append(str(row[3]).strip())

    check_wb.close()

    seller_records_count = max(0, final_total_rows - 1)

    first_cat_sellers_str = "\n".join([f"{i}. {name}" for i, name in enumerate(first_cat_sellers[:5], 1)])
    if not first_cat_sellers_str:
        first_cat_sellers_str = "None"

    cat_list_str = "\n".join([f"- {c}" for c in sorted(master_categories)])

    print("\n========================================")
    print("FINAL MASTER EXCEL VALIDATION")
    print("=============================\n")
    print(f"File:\n{final_path}\n")
    print(f"File size:\n{final_size} bytes\n")
    print(f"Excel rows including header:\n{final_total_rows}\n")
    print(f"Seller records:\n{seller_records_count}\n")
    print(f"Categories:\n{cat_list_str}\n")
    print(f"{current_category} rows:\n{category_rows_count}\n")
    print(f"First 5 {current_category} sellers:\n{first_cat_sellers_str}\n")
    print("Excel validation:\nPASSED")
    print("========================================\n")

    total_master_records = seller_records_count
    existing_categories.add(current_category.strip().lower())

    status_str = "SUCCESS" if not file_existed else "APPENDED SUCCESSFULLY"
    logger.info(f"Master Excel updated: Added {added_count} rows for category '{current_category}'. Total seller records={total_master_records}")

    return {
        "status": status_str,
        "file_path": str(final_path),
        "existing_records": existing_master_records,
        "total_records": total_master_records,
        "master_categories": len(existing_categories),
        "added_count": added_count
    }
