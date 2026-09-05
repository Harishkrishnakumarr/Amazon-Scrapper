# Amazon Multi-Category Top 20 Seller Web Scraper

A specialized Python web-scraping application for collecting publicly displayed Amazon seller and business information, saving to SQLite database (`amazon_sellers.db`), and exporting structured Master Excel reports (`output/Amazon_Seller_Master_Data.xlsx`).

## Features

- **Multi-Category Batch Mode**: Sequentially process multiple category search/browse URLs from `input/amazon_urls.txt`.
- **Top 20 Limit Per Category**: Discovers products, ranks candidates by relevance score, and selects top 20 sellers independently for each category.
- **Strict Category Isolation**: Uniqueness key is `SELLER IDENTITY + CATEGORY`. The same business across multiple categories retains distinct category records.
- **S.NO Reset**: Resets S.NO to `1..20` for every category in both SQLite and Master Excel.
- **Permanent Master Excel Append**: Appends completed categories to `output/Amazon_Seller_Master_Data.xlsx` without overwriting prior records, using safety backups and atomic file replacement.
- **Category Duplicate Protection**: Automatically skips categories that have already been processed in SQLite/Master Excel.
- **Failure Isolation**: Isolates errors per category so any network/browser failure on one category does not stop the batch or compromise successful categories.
- **Deep Public Search Enrichment**: Enriches business details (Legal Entity, GST, PAN, State, Pincode, Address, Phone, Email, Website) using public sources with PAN cross-verification.

## Input File Formats

### 1. Batch URLs File (`input/amazon_urls.txt`)
Format: `CATEGORY NAME|AMAZON URL` (one per line)
```text
Women's Flats Amazon|https://www.amazon.in/s?k=women+flats
Men's Shoes|https://www.amazon.in/s?k=men+shoes
School Shoes|https://www.amazon.in/s?k=school+shoes
Home Decor|https://www.amazon.in/s?k=home+decor
Electronics|https://www.amazon.in/s?k=electronics
```

### 2. Single Category File (`input/current_category.txt`)
Format: `CATEGORY NAME|AMAZON URL` or browse/search URL.

## Usage

### 1. Run Multi-Category Batch Mode
```bash
python main.py --batch
```
With custom file or headless browser:
```bash
python main.py --batch --urls-file input/amazon_urls.txt --headless
```

### 2. Run Single Category Mode
```bash
python main.py
```
Or specify category directly:
```bash
python main.py --category "Men's Shoes" --url "https://www.amazon.in/s?k=men+shoes"
```

### 3. Force Reprocess Category
```bash
python main.py --force
```

### 4. Output Files
- Master Excel: `output/Amazon_Seller_Master_Data.xlsx`
- SQLite Database: `amazon_sellers.db`
- Logs: `logs/scraper.log`
- Debug Audits: `output/debug/`
