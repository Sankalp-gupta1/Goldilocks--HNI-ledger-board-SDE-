# Goldilocks HNI Ledger Intelligence Platform

## Enterprise-Grade Financial Intelligence & Automated Accounting Engine

Goldilocks HNI Ledger is a comprehensive financial intelligence platform designed to convert unstructured bank statements into fully traceable accounting records, financial statements, cash-flow analytics, and net-worth reports.

Unlike traditional personal finance applications that merely categorize transactions, the platform applies accounting logic, ledger construction, financial classification, reconciliation workflows, and statement generation to build a complete financial picture for High Net-Worth Individuals (HNIs), professionals, consultants, business owners, and family offices.

The system acts as an intelligent accounting layer between raw banking data and decision-ready financial reports.

---

# Key Capabilities

### Multi-Bank Statement Intelligence

The platform supports ingestion of bank statements from multiple institutions and formats.

Supported Inputs:

* PDF Statements
* XLS / XLSX Statements
* Password Protected Statements
* Multi-page Banking PDFs
* Scanned Banking Documents

Supported Banks:

* HDFC Bank
* ICICI Bank
* SBI
* Axis Bank
* PNB
* Union Bank
* AU Bank
* IDFC First Bank
* Additional formats can be integrated through modular parsers

---

# Advanced Financial Classification Engine

The classification engine automatically converts raw transactions into structured accounting entries.

### Rule-Based Classification

Recognizes:

* UPI Transactions
* IMPS
* NEFT
* RTGS
* ACH Debits
* Interest Credits
* Salary Credits
* Merchant Payments
* Internal Transfers
* Investments
* Family Transfers
* Trading Cash Flows

### Machine Learning Classification

A fallback ML engine is used when deterministic rules fail.

Technologies:

* TF-IDF Vectorization
* Linear Support Vector Classifier (LinearSVC)
* Confidence Scoring
* Auto-Learning Dataset Generation

The hybrid architecture ensures both accuracy and scalability.

---

# Three Book Accounting Architecture

The platform automatically creates a complete accounting structure.

## Book I — Income & Expenditure

Captures:

* Salary
* Professional Income
* Interest Income
* Dividend Income
* Rental Income
* Capital Gains
* Household Expenses
* Utility Payments
* Education Expenses
* Lifestyle Expenses

Generates:

* Income Statement
* Profit & Loss View
* Surplus / Deficit Calculation

---

## Book II — Balance Sheet

Tracks true assets and liabilities.

Assets:

* Bank Balances
* Fixed Deposits
* Investments
* Loans Given
* Trading Receivables
* Advances Recoverable

Liabilities:

* Loans
* Credit Obligations
* Outstanding Payables
* Trading Payables

Generates:

* Personal Balance Sheet
* Net Worth Statement
* Asset Allocation Snapshot

---

## Book III — Balance Verification Layer

A dedicated balancing engine continuously validates:

Assets = Liabilities + Equity

The system automatically detects:

* Missing entries
* Unexplained balances
* Reconciliation gaps
* Classification inconsistencies

This creates audit-grade financial integrity.

---

# Review & Approval Workflow

Before posting transactions into the ledger:

1. Transactions are staged
2. Classifications are reviewed
3. Rules can be corrected
4. New rules can be created
5. Entries are approved
6. Ledger posting occurs

This human-in-the-loop workflow dramatically improves classification quality.

---

# Trading Cash Flow Intelligence

One of the most advanced modules in the platform.

The system identifies:

* Broker Deposits
* Broker Withdrawals
* Trading Capital Introduced
* Capital Withdrawn
* Trading Receivables
* Trading Payables

Net Position Logic:

Positive Position:
Trading Receivable → Asset

Negative Position:
Trading Payable → Liability

This ensures financial statements reflect actual economic reality.

---

# Complete Transaction Traceability

Every figure shown in reports can be traced back to:

* Source Statement
* Original Narration
* Transaction Date
* Counterparty
* Classification Rule
* Ledger Entry

This creates full transparency across the accounting pipeline.

---

# Financial Reporting Suite

The platform automatically generates:

### Income Statement

* Income Categories
* Expense Categories
* Net Income

### Balance Sheet

* Assets
* Liabilities
* Equity
* Net Worth

### Profit & Loss Statement

* Operating Performance
* Surplus Analysis

### Consolidated Ledger Report

* Account Wise View
* Combined View
* Traceable Entries

### Statement Analytics

* Year-wise Reports
* Account-wise Reports
* Transaction Drilldowns

---

# Technology Stack

Backend

* Python 3
* SQLite

Data Processing

* Pandas
* NumPy
* OpenPyXL

PDF Processing

* pdfplumber
* PyMuPDF
* pdfminer.six
* Tesseract OCR

Machine Learning

* Scikit-Learn
* TF-IDF
* LinearSVC

Frontend

* HTML
* CSS
* Vanilla JavaScript

Architecture

* Single File Financial Engine
* Modular Ledger Extensions
* Rule-Based + ML Hybrid Classification

---

# System Highlights

✔ Multi-Bank Statement Parsing

✔ Automated Ledger Construction

✔ Income Statement Generation

✔ Balance Sheet Generation

✔ Trading Cash Flow Accounting

✔ Net Worth Computation

✔ Review & Approval Workflow

✔ Machine Learning Assisted Classification

✔ Counterparty Extraction

✔ Financial Traceability Engine

✔ Audit-Oriented Architecture

✔ Fully Local Processing

✔ No External Financial APIs

---

# Future Roadmap

* AI Assisted Financial Review
* Natural Language Financial Queries
* Family Office Consolidation
* Portfolio Tracking
* Capital Gains Automation
* GST & Tax Mapping
* Broker Contract Note Processing
* Multi-Entity Accounting
* Financial Risk Analytics
* Wealth Intelligence Dashboard

---

# Author

Sankalp Gupta

Software Engineer | AI Engineer | Financial Systems Developer

Focused on building intelligent systems that combine Artificial Intelligence, Financial Analytics, Accounting Logic, and Real-World Automation into production-grade software solutions.
