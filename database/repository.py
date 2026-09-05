import sqlite3
import logging
from typing import List, Optional, Tuple, Dict, Any
from database.models import SellerRecord, SellerSource, SellerOffer
from database.database import get_db_connection

logger = logging.getLogger("amazon_scraper")

class SellerRepository:
    def __init__(self, db_path: str = "amazon_sellers.db"):
        self.db_path = db_path

    def get_all_sellers(self) -> List[SellerRecord]:
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sellers ORDER BY seller_relevance_score DESC, id ASC")
        rows = cursor.fetchall()
        sellers = []
        for r in rows:
            sellers.append(self._row_to_record(r))
        conn.close()
        return sellers

    def get_sellers_by_category(self, category: str) -> List[SellerRecord]:
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sellers WHERE LOWER(sub_sub_category) = LOWER(?) ORDER BY s_no ASC, id ASC", (category.strip(),))
        rows = cursor.fetchall()
        sellers = []
        for r in rows:
            sellers.append(self._row_to_record(r))
        conn.close()
        return sellers

    def find_existing_seller(self, record: SellerRecord) -> Optional[SellerRecord]:
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        category = (record.sub_sub_category or "").strip()
        
        # 1. GSTIN (constrained to category)
        if record.gst_number and record.gst_number not in ("Not Found", "Unverified", "N/A"):
            gst_candidates = [g.strip() for g in record.gst_number.split(";") if g.strip() and g.strip() not in ("Not Found", "Unverified", "N/A")]
            for g in gst_candidates:
                cursor.execute(
                    "SELECT * FROM sellers WHERE (gst_number = ? OR instr(gst_number, ?) > 0) AND LOWER(sub_sub_category) = LOWER(?)",
                    (record.gst_number, g, category)
                )
                row = cursor.fetchone()
                if row:
                    conn.close()
                    return self._row_to_record(row)

        # 2. Seller URL (constrained to category)
        if record.seller_url:
            cursor.execute(
                "SELECT * FROM sellers WHERE seller_url = ? AND LOWER(sub_sub_category) = LOWER(?)",
                (record.seller_url.strip(), category)
            )
            row = cursor.fetchone()
            if row:
                conn.close()
                return self._row_to_record(row)

        # 3. Website domain (constrained to category)
        if record.website_url and record.website_url not in ("Not Found", "N/A"):
            cursor.execute(
                "SELECT * FROM sellers WHERE website_url = ? AND LOWER(sub_sub_category) = LOWER(?)",
                (record.website_url.strip(), category)
            )
            row = cursor.fetchone()
            if row:
                conn.close()
                return self._row_to_record(row)

        # 4. Phone (constrained to category)
        if record.phone_number and record.phone_number not in ("Not Found", "N/A"):
            cursor.execute(
                "SELECT * FROM sellers WHERE phone_number = ? AND LOWER(sub_sub_category) = LOWER(?)",
                (record.phone_number.strip(), category)
            )
            row = cursor.fetchone()
            if row:
                conn.close()
                return self._row_to_record(row)

        # 5. Email (constrained to category)
        if record.email_address and record.email_address not in ("Not Found", "N/A"):
            cursor.execute(
                "SELECT * FROM sellers WHERE email_address = ? AND LOWER(sub_sub_category) = LOWER(?)",
                (record.email_address.strip(), category)
            )
            row = cursor.fetchone()
            if row:
                conn.close()
                return self._row_to_record(row)

        # 6. Business Name matching (constrained to category)
        if record.business_name and record.business_name not in ("Not Found", "Unknown"):
            cursor.execute(
                "SELECT * FROM sellers WHERE LOWER(business_name) = LOWER(?) AND LOWER(sub_sub_category) = LOWER(?)",
                (record.business_name.strip(), category)
            )
            row = cursor.fetchone()
            if row:
                conn.close()
                return self._row_to_record(row)

        conn.close()
        return None

    def get_seller_by_id(self, seller_id: int) -> Optional[SellerRecord]:
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sellers WHERE id = ?", (seller_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return self._row_to_record(row)
        return None

    def save_or_update_seller(self, record: SellerRecord) -> Tuple[SellerRecord, bool]:
        if not record.sub_sub_category or not record.sub_sub_category.strip():
            raise ValueError("Active category (sub_sub_category) is missing")
        if record.sub_sub_category.strip().lower() == "current category":
            raise ValueError("Invalid category: 'Current Category'. Actual active category must be supplied.")

        existing = self.find_existing_seller(record)
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()

        if existing:
            # Safe field-level merging: use verified new value if existing is empty/Not Found/N/A
            merged_category = self._merge_categories(existing.business_category, record.business_category)
            
            b_name = record.business_name if record.business_name not in ("Not Found", "Unknown", "") else existing.business_name
            b_model = record.business_model if record.business_model not in ("Marketplace Seller", "Unknown", "") else existing.business_model
            owner = record.owner_name if record.owner_name not in ("Not Found", "Unknown", "N/A", "") else existing.owner_name
            phone = record.phone_number if record.phone_number not in ("Not Found", "N/A", "Unknown", "") else existing.phone_number
            email = record.email_address if record.email_address not in ("Not Found", "N/A", "Unknown", "") else existing.email_address
            gst = record.gst_number if record.gst_number not in ("Not Found", "N/A", "Unverified", "") else existing.gst_number
            pan = record.pan_number if record.pan_number not in ("Not Found", "N/A", "Unknown", "") else existing.pan_number
            fssai = record.fssai_number if record.fssai_number not in ("N/A", "Not Found", "Unknown", "") else existing.fssai_number
            addr = record.billing_address if record.billing_address not in ("Not Found", "Unknown", "N/A", "") else existing.billing_address
            city = record.city if record.city not in ("Not Found", "Unknown", "N/A", "") else existing.city
            state = record.state if record.state not in ("Not Found", "Unknown", "N/A", "") else existing.state
            pincode = record.pincode if record.pincode not in ("Not Found", "N/A", "Unknown", "") else existing.pincode
            country = record.country if record.country not in ("Not Found", "Unknown", "") else existing.country
            web = record.website_url if record.website_url not in ("Not Found", "N/A", "Unknown", "") else existing.website_url
            
            # Status: prefer higher verification status
            if record.status in ("Verified", "Partially Verified"):
                status = record.status
            elif existing.status in ("Verified", "Partially Verified"):
                status = existing.status
            else:
                status = record.status or existing.status

            src = self._merge_sources(existing.source, record.source)
            disp_name = record.display_name or existing.display_name
            legal_ent = record.legal_entity or existing.legal_entity
            seller_url = record.seller_url or existing.seller_url

            sub_cat = record.sub_sub_category or existing.sub_sub_category
            sub_sub_cat = record.sub_sub_sub_category or existing.sub_sub_sub_category
            sno = record.s_no if record.s_no else existing.s_no

            cursor.execute("""
            UPDATE sellers SET
                sub_sub_category = ?, sub_sub_sub_category = ?, s_no = ?,
                business_name = ?, business_model = ?, business_category = ?,
                owner_name = ?, phone_number = ?, email_address = ?,
                gst_number = ?, pan_number = ?, fssai_number = ?,
                billing_address = ?, city = ?, state = ?, pincode = ?, country = ?,
                website_url = ?, status = ?, source = ?, display_name = ?,
                legal_entity = ?, seller_url = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """, (
                sub_cat, sub_sub_cat, sno,
                b_name, b_model, merged_category, owner, phone, email, gst, pan,
                fssai, addr, city, state, pincode, country, web, status, src,
                disp_name, legal_ent, seller_url, existing.id
            ))
            conn.commit()
            target_id = existing.id
            is_new = False
        else:
            cursor.execute("""
            INSERT INTO sellers (
                sub_sub_category, sub_sub_sub_category, s_no,
                business_name, business_model, business_category, owner_name,
                phone_number, email_address, gst_number, pan_number, fssai_number,
                billing_address, city, state, pincode, country, website_url,
                status, source, display_name, legal_entity, seller_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.sub_sub_category, record.sub_sub_sub_category, record.s_no,
                record.business_name, record.business_model, record.business_category,
                record.owner_name, record.phone_number, record.email_address,
                record.gst_number, record.pan_number, record.fssai_number,
                record.billing_address, record.city, record.state, record.pincode,
                record.country, record.website_url, record.status, record.source,
                record.display_name, record.legal_entity, record.seller_url
            ))
            target_id = cursor.lastrowid
            conn.commit()
            is_new = True

        # Immediate Read-Back directly from SQLite to guarantee 100% accurate object state
        cursor.execute("SELECT * FROM sellers WHERE id = ?", (target_id,))
        row = cursor.fetchone()
        conn.close()
        saved_record = self._row_to_record(row)
        self._update_relevance_score(target_id)
        return saved_record, is_new

    def add_seller_source(self, source: SellerSource):
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
        SELECT id FROM seller_sources
        WHERE seller_id = ? AND source_name = ? AND field_name = ? AND field_value = ?
        """, (source.seller_id, source.source_name, source.field_name, source.field_value))
        if cursor.fetchone():
            conn.close()
            return
        cursor.execute("""
        INSERT INTO seller_sources (seller_id, source_name, source_url, field_name, field_value, verification_status)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (source.seller_id, source.source_name, source.source_url, source.field_name, source.field_value, source.verification_status))
        conn.commit()
        conn.close()

    def add_seller_offer(self, offer: SellerOffer):
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        if offer.seller_id and offer.asin:
            cursor.execute("SELECT id FROM seller_offers WHERE seller_id = ? AND asin = ?", (offer.seller_id, offer.asin))
            if cursor.fetchone():
                conn.close()
                return
        cursor.execute("""
        INSERT INTO seller_offers (seller_id, asin, product_url, product_title, category, seller_name, seller_profile_url, price, condition, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (offer.seller_id, offer.asin, offer.product_url, offer.product_title, offer.category, offer.seller_name, offer.seller_profile_url, offer.price, offer.condition, offer.source))
        conn.commit()
        conn.close()
        if offer.seller_id:
            self._update_relevance_score(offer.seller_id)

    def get_offer_stats(self) -> Dict[str, Any]:
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM seller_offers")
        total_offers = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(DISTINCT asin) FROM seller_offers")
        distinct_asins = cursor.fetchone()[0]
        
        cursor.execute("""
        SELECT COUNT(*) FROM (
            SELECT asin FROM seller_offers GROUP BY asin HAVING COUNT(DISTINCT seller_id) > 1
        )
        """)
        multi_seller_products = cursor.fetchone()[0]

        conn.close()
        
        avg_sellers = (total_offers / distinct_asins) if distinct_asins > 0 else 0.0
        return {
            "total_offers": total_offers,
            "distinct_asins": distinct_asins,
            "multi_seller_products": multi_seller_products,
            "average_sellers_per_product": round(avg_sellers, 1)
        }

    def get_verification_stats(self) -> Dict[str, Any]:
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM sellers WHERE status = 'Verified'")
        verified = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sellers WHERE status = 'Partially Verified'")
        partially_verified = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sellers WHERE status = 'Needs Review'")
        needs_review = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sellers WHERE business_name NOT IN ('Not Found', 'Unknown')")
        business_names = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sellers WHERE owner_name NOT IN ('Not Found', 'Unknown')")
        owners = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sellers WHERE phone_number NOT IN ('Not Found', 'N/A')")
        phones = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sellers WHERE email_address NOT IN ('Not Found', 'N/A')")
        emails = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sellers WHERE gst_number NOT IN ('Not Found', 'N/A', 'Unverified')")
        gst = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sellers WHERE pan_number NOT IN ('Not Found', 'N/A')")
        pan = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sellers WHERE billing_address NOT IN ('Not Found', 'Unknown')")
        addresses = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sellers WHERE city NOT IN ('Not Found', 'Unknown')")
        cities = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sellers WHERE state NOT IN ('Not Found', 'Unknown')")
        states = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sellers WHERE pincode NOT IN ('Not Found', 'N/A')")
        pincodes = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sellers WHERE website_url NOT IN ('Not Found', 'N/A')")
        websites = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT seller_id) FROM seller_sources WHERE source_name LIKE '%Amazon%'")
        profiles_checked = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT seller_id) FROM seller_sources WHERE source_name LIKE '%Official Website%'")
        websites_checked = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT source_url) FROM seller_sources")
        sources_checked = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sellers")
        total_sellers = cursor.fetchone()[0]

        # Total key fields per seller = 11 (Business Name, Owner, Phone, Email, GST, PAN, Address, City, State, Pincode, Website)
        found_fields = business_names + owners + phones + emails + gst + pan + addresses + cities + states + pincodes + websites
        total_possible = total_sellers * 11
        not_found_fields = max(0, total_possible - found_fields)

        conn.close()
        return {
            "verified": verified,
            "partially_verified": partially_verified,
            "needs_review": needs_review,
            "business_names": business_names,
            "owners": owners,
            "phones": phones,
            "emails": emails,
            "gst": gst,
            "pan": pan,
            "addresses": addresses,
            "cities": cities,
            "states": states,
            "pincodes": pincodes,
            "websites": websites,
            "not_found_fields": not_found_fields,
            "profiles_checked": profiles_checked,
            "websites_checked": websites_checked,
            "sources_checked": sources_checked
        }

    def _update_relevance_score(self, seller_id: int):
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM seller_offers WHERE seller_id = ?", (seller_id,))
        offer_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT * FROM sellers WHERE id = ?", (seller_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return

        completeness_score = 0.0
        if row["business_name"] not in ("Not Found", "Unknown"): completeness_score += 2.0
        if row["billing_address"] not in ("Not Found", "Unknown"): completeness_score += 2.0
        if row["gst_number"] not in ("Not Found", "N/A", "Unverified"): completeness_score += 3.0
        if row["phone_number"] not in ("Not Found", "N/A"): completeness_score += 2.0
        if row["email_address"] not in ("Not Found", "N/A"): completeness_score += 2.0

        relevance_score = (offer_count * 1.5) + completeness_score
        
        cursor.execute("UPDATE sellers SET seller_relevance_score = ? WHERE id = ?", (relevance_score, seller_id))
        conn.commit()
        conn.close()

    def _merge_categories(self, cat1: str, cat2: str) -> str:
        if not cat1 or cat1 == "Unknown":
            return cat2 if cat2 else "Unknown"
        if not cat2 or cat2 == "Unknown":
            return cat1
        cats1 = [c.strip() for c in cat1.split(";") if c.strip()]
        cats2 = [c.strip() for c in cat2.split(";") if c.strip()]
        combined = list(dict.fromkeys(cats1 + cats2))
        return "; ".join(combined)

    def _merge_sources(self, src1: str, src2: str) -> str:
        if not src1: return src2 or "Amazon"
        if not src2: return src1
        s1 = [s.strip() for s in src1.split("+")]
        s2 = [s.strip() for s in src2.split("+")]
        combined = list(dict.fromkeys(s1 + s2))
        return " + ".join(combined)

    def is_category_processed(self, sub_sub_category: str) -> Tuple[bool, int]:
        if not sub_sub_category:
            return False, 0
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        
        # Check last category run status
        cursor.execute("SELECT status FROM category_runs WHERE LOWER(category) = LOWER(?) OR LOWER(sub_sub_category) = LOWER(?) ORDER BY id DESC LIMIT 1", (sub_sub_category.strip(), sub_sub_category.strip()))
        run_row = cursor.fetchone()
        last_status = run_row["status"] if run_row else None
        
        cursor.execute("SELECT COUNT(*) FROM sellers WHERE LOWER(sub_sub_category) = LOWER(?)", (sub_sub_category.strip(),))
        count = cursor.fetchone()[0]
        conn.close()

        # If previous run failed, timed out, was blocked, or interrupted (RUNNING), allow retry
        if last_status in ("FAILED", "TIMEOUT", "BLOCKED", "RUNNING"):
            return False, count

        return (count > 0 and (last_status == "COMPLETED" or last_status is None)), count

    def record_category_run_start(self, category: str) -> int:
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO category_runs (category, sub_sub_category, sub_sub_sub_category, businesses_found, businesses_added, status)
            VALUES (?, ?, '', 0, 0, 'RUNNING')
        """, (category.strip(), category.strip()))
        run_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return run_id

    def update_category_run_status(self, run_id: Optional[int], category: str, status: str, businesses_found: int = 0, businesses_added: int = 0):
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        if run_id:
            cursor.execute("""
                UPDATE category_runs SET
                    businesses_found = ?, businesses_added = ?, status = ?, run_date = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (businesses_found, businesses_added, status, run_id))
        else:
            cursor.execute("""
                INSERT INTO category_runs (category, sub_sub_category, sub_sub_sub_category, businesses_found, businesses_added, status)
                VALUES (?, ?, '', ?, ?, ?)
            """, (category.strip(), category.strip(), businesses_found, businesses_added, status))
        conn.commit()
        conn.close()

    def add_category_run(self, run_obj: Any):
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO category_runs (category, sub_sub_category, sub_sub_sub_category, businesses_found, businesses_added, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (run_obj.category, run_obj.sub_sub_category, run_obj.sub_sub_sub_category, run_obj.businesses_found, run_obj.businesses_added, run_obj.status))
        conn.commit()
        conn.close()

    def _row_to_record(self, r: sqlite3.Row) -> SellerRecord:
        return SellerRecord(
            id=r["id"],
            sub_sub_category=r["sub_sub_category"] if "sub_sub_category" in r.keys() else "",
            sub_sub_sub_category=r["sub_sub_sub_category"] if "sub_sub_sub_category" in r.keys() else "",
            s_no=r["s_no"] if "s_no" in r.keys() else 1,
            business_name=r["business_name"],
            business_model=r["business_model"],
            business_category=r["business_category"],
            owner_name=r["owner_name"],
            phone_number=r["phone_number"],
            email_address=r["email_address"],
            gst_number=r["gst_number"],
            pan_number=r["pan_number"],
            fssai_number=r["fssai_number"],
            billing_address=r["billing_address"],
            city=r["city"],
            state=r["state"],
            pincode=r["pincode"],
            country=r["country"],
            website_url=r["website_url"],
            status=r["status"],
            source=r["source"],
            display_name=r["display_name"],
            legal_entity=r["legal_entity"],
            seller_url=r["seller_url"],
            seller_relevance_score=r["seller_relevance_score"],
            created_at=r["created_at"],
            updated_at=r["updated_at"]
        )

    def clean_or_migrate_invalid_categories(self) -> List[Dict[str, Any]]:
        """
        Finds any historical seller records that were mistakenly saved with
        sub_sub_category = 'Current Category' or business_category = 'Current Category'
        and removes them safely.
        """
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, business_name, sub_sub_category, business_category FROM sellers WHERE LOWER(sub_sub_category) = 'current category' OR LOWER(business_category) = 'current category'")
        rows = cursor.fetchall()
        cleaned = []
        for r in rows:
            s_id = r["id"]
            b_name = r["business_name"]
            cleaned.append({
                "id": s_id,
                "business_name": b_name,
                "sub_sub_category": r["sub_sub_category"],
                "business_category": r["business_category"]
            })
            cursor.execute("DELETE FROM seller_sources WHERE seller_id = ?", (s_id,))
            cursor.execute("DELETE FROM seller_offers WHERE seller_id = ?", (s_id,))
            cursor.execute("DELETE FROM sellers WHERE id = ?", (s_id,))
        
        cursor.execute("DELETE FROM category_runs WHERE LOWER(category) = 'current category' OR LOWER(sub_sub_category) = 'current category'")
        conn.commit()
        conn.close()
        if cleaned:
            logger.info(f"Database Auditor cleaned {len(cleaned)} invalid 'Current Category' entries in SQLite: {[c['business_name'] for c in cleaned]}")
        return cleaned

    def audit_and_clean_database_gst_pan(self) -> List[Dict[str, Any]]:
        """
        Audits existing SQLite records to detect and clean any GSTIN whose embedded PAN
        conflicts with the verified business PAN, and cleans invalid category records.
        """
        # Clean any historical invalid "Current Category" records first
        self.clean_or_migrate_invalid_categories()

        from extraction.normalizer import extract_pan_from_gstin, normalize_pan
        conn = get_db_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, business_name, pan_number, gst_number FROM sellers WHERE gst_number NOT IN ('Not Found', 'N/A', 'Unverified')")
        rows = cursor.fetchall()
        
        cleaned_logs = []
        for r in rows:
            s_id = r["id"]
            b_name = r["business_name"]
            pan = normalize_pan(r["pan_number"])
            gst = r["gst_number"]
            
            if pan != "Not Found" and gst != "Not Found":
                embedded_pan = extract_pan_from_gstin(gst)
                if embedded_pan and embedded_pan.upper() != pan.upper():
                    msg = f"Auditor cleaned record #{s_id} '{b_name}': GSTIN '{gst}' embedded PAN '{embedded_pan}' mismatch against verified PAN '{pan}'"
                    logger.warning(msg)
                    cursor.execute("UPDATE sellers SET gst_number = 'Not Found', status = 'Needs Review' WHERE id = ?", (s_id,))
                    cleaned_logs.append({
                        "id": s_id,
                        "business_name": b_name,
                        "removed_gst": gst,
                        "embedded_pan": embedded_pan,
                        "verified_pan": pan,
                        "reason": "GSTIN embedded PAN conflict"
                    })
        conn.commit()
        conn.close()
        if cleaned_logs:
            logger.info(f"Database Auditor cleaned {len(cleaned_logs)} invalid GST/PAN entries in SQLite")
        return cleaned_logs


