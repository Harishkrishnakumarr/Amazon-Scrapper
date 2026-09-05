import sqlite3
import os
import logging

logger = logging.getLogger("amazon_scraper")

def get_db_connection(db_path: str = "amazon_sellers.db") -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path: str = "amazon_sellers.db"):
    """Initialize SQLite database schema."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # 1. Main sellers table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sellers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sub_sub_category TEXT DEFAULT '',
        sub_sub_sub_category TEXT DEFAULT '',
        s_no INTEGER DEFAULT 1,
        business_name TEXT NOT NULL,
        business_model TEXT DEFAULT 'Marketplace Seller',
        business_category TEXT DEFAULT 'Unknown',
        owner_name TEXT DEFAULT 'Not Found',
        phone_number TEXT DEFAULT 'Not Found',
        email_address TEXT DEFAULT 'Not Found',
        gst_number TEXT DEFAULT 'Not Found',
        pan_number TEXT DEFAULT 'Not Found',
        fssai_number TEXT DEFAULT 'N/A',
        billing_address TEXT DEFAULT 'Not Found',
        city TEXT DEFAULT 'Not Found',
        state TEXT DEFAULT 'Not Found',
        pincode TEXT DEFAULT 'Not Found',
        country TEXT DEFAULT 'Not Found',
        website_url TEXT DEFAULT 'Not Found',
        status TEXT DEFAULT 'Observed on Amazon',
        source TEXT DEFAULT 'Amazon',
        display_name TEXT,
        legal_entity TEXT,
        seller_url TEXT,
        seller_relevance_score REAL DEFAULT 0.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. Category runs table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS category_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT,
        sub_sub_category TEXT,
        sub_sub_sub_category TEXT,
        run_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        businesses_found INTEGER DEFAULT 0,
        businesses_added INTEGER DEFAULT 0,
        status TEXT DEFAULT 'Completed'
    );
    """)

    # 3. Internal seller sources table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS seller_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_id INTEGER NOT NULL,
        source_name TEXT NOT NULL,
        source_url TEXT,
        field_name TEXT NOT NULL,
        field_value TEXT,
        verification_status TEXT DEFAULT 'Observed',
        collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (seller_id) REFERENCES sellers (id) ON DELETE CASCADE
    );
    """)

    # 4. Internal seller offers table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS seller_offers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_id INTEGER NOT NULL,
        asin TEXT,
        product_url TEXT,
        product_title TEXT,
        category TEXT,
        seller_name TEXT,
        seller_profile_url TEXT,
        price TEXT,
        condition TEXT,
        source TEXT DEFAULT 'Amazon',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (seller_id) REFERENCES sellers (id) ON DELETE CASCADE
    );
    """)

    # 5. Internal seller GST registrations table (multiple state GSTINs per legal entity/PAN)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS seller_gst_registrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_id INTEGER NOT NULL,
        legal_entity_name TEXT,
        pan TEXT,
        gstin TEXT NOT NULL,
        state_code TEXT,
        state TEXT,
        registered_address TEXT,
        source_url TEXT,
        verification_status TEXT DEFAULT 'VERIFIED',
        confidence REAL DEFAULT 1.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (seller_id) REFERENCES sellers (id) ON DELETE CASCADE
    );
    """)
    
    conn.commit()
    conn.close()
    logger.info(f"Initialized SQLite database schema at {db_path}")

