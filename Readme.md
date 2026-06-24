# HNI Accounting System

A lightweight, end-to-end financial analysis system that transforms raw bank statements into structured accounting outputs including Income Statement, Balance Sheet, and transaction-level traceability.

Designed for high-net-worth individuals and advanced personal finance tracking, the system combines rule-based logic with machine learning to ensure accurate financial classification and reporting.

---

## Core Features

### 1. Bank Statement Parsing

* Supports Excel (.xlsx, .xls) and PDF uploads
* Handles multiple bank formats (HDFC, ICICI, Axis, SBI, AU, IDFC)
* Multi-stage PDF parsing pipeline:

  * Table extraction using pdfplumber
  * Structured text parsing fallback
  * pdfminer / PyMuPDF fallback
  * OCR support for scanned PDFs using Tesseract

---

### 2. Intelligent Transaction Classification

* Hybrid classification engine:

  * Rule-based classification (UPI, NEFT, ACH, merchant patterns)
  * Machine learning fallback (TF-IDF + LinearSVC)

* Automatically detects:

  * Income vs Expense
  * Investment flows
  * Transfers and internal movements
  * Charges and financial costs

---

### 3. Review & Approve Workflow

* All transactions are staged before final posting

* Interactive UI enables:

  * Reclassification
  * Manual correction
  * Rule creation

* Ensures accuracy and user validation before financial reporting

---

### 4. Financial Statement Generation

#### Income Statement

* Salary, interest, dividends
* Capital gains
* Categorized operating expenses

#### Balance Sheet

* Investments (Equity, Mutual Funds, Fixed Deposits)
* Current assets (bank balance, receivables, transfers)
* Liabilities (loans, overdrafts, payables)

#### Trading Account Treatment

The trading account is dynamically classified based on net position:

* Net positive → shown as **Asset (Receivable)**
* Net negative → shown as **Liability (Payable)**
* Net zero → not displayed

This ensures correct financial representation based on actual cash flows.

---

### 5. Traceability (Key Feature)

Every reported figure is fully traceable to:

* Individual transactions
* Source narration
* Classification logic

This ensures transparency and auditability across all outputs.

---

## Tech Stack

* Backend: Python
* Database: SQLite
* Frontend: HTML + JavaScript (served via Python HTTP server)
* Machine Learning: scikit-learn (TF-IDF + LinearSVC)

### Parsing Libraries

* pandas, openpyxl, xlrd
* pdfplumber, pdfminer.six, PyMuPDF
* pytesseract (OCR support)

---

## Project Structure

```
.
├── hni_accounting_system.py       # Main server and core logic
├── hni_ledger_extension.py        # Ledger and classification logic
├── generate_dataset.py            # Dataset generation for ML training
├── requirements.txt               # Dependencies
├── README.md
├── .gitignore
```

---

## Setup Instructions

### 1. Install dependencies

```
pip install -r requirements.txt
```

---

### 2. Install OCR (for scanned PDFs)

```
brew install tesseract            # macOS
sudo apt install tesseract-ocr    # Linux
```

---

### 3. Run the application

```
python hni_accounting_system.py
```

---

### 4. Access the UI

```
http://localhost:8082
```

---

## Important Notes

### Database

* SQLite database is created locally at runtime
* `.db` files are excluded from the repository to prevent exposure of sensitive financial data

---

### Machine Learning Model

* The trained model (`.joblib`) is not included in the repository
* It is generated locally during runtime or training

---

### Data Privacy

* All processing is local
* No external APIs are used
* Users should ensure sensitive financial data is not committed to version control

---

## Known Limitations

* Some transactions may fall into **Suspense** due to noisy or incomplete narrations
* Counterparty extraction may fail for certain bank-specific formats
* Broker and trading-related transactions may require manual validation
* Duplicate detection may occasionally trigger incorrectly

---

## Future Improvements

* Improved narration cleaning and tokenization
* Enhanced counterparty extraction using hybrid regex + ML
* Smarter duplicate detection using hashing
* Stronger reconciliation between Income Statement and Balance Sheet
* UI optimization for large datasets

---

## Author

Developed as a financial intelligence and accounting system focused on:

* Accuracy
* Transparency
* Traceability
* Real-world financial logic

---

## License

This project is intended for educational and personal use. Add an appropriate license if distributing publicly.


