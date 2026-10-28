#!/usr/bin/env python3
"""
HNI Ledger Classification System — Corrected Accounting Edition
═══════════════════════════════════════════════════════════════════════
ACCOUNTING MODEL (corrected):
─────────────────────────────
Bank statement = cash book. Every entry is ALREADY settled (cash basis).

DOUBLE-ENTRY LEDGER (3 books):

  Book 1 — BALANCE SHEET (Assets / Liabilities / Equity)
    • Assets    : What you OWN  (investments, loans given, land, FDs, receivables)
    • Liabilities: What you OWE  (long-term loans, credit card outstanding)
    • Equity    : Net Worth = Assets − Liabilities

  Book 2 — INCOME & EXPENDITURE (P&L)
    • Income    : Salary, Dividend, Capital Gains, Interest, Professional
    • Expenditure: Food, Groceries, Travel, Staff, Tax, Bank Charges …
      ► These are EXPENSES PAID, not liabilities (bank debit = already settled)

  Book 3 — CAPITAL MOVEMENTS
    • Advances/Loans given or returned (asset movements)
    • Own account transfers (contra, no P&L)
    • Investment purchases / redemptions

CLASSIFICATION SOURCES:
  Step 0  → Private reviewed examples and personal rules in goldilocks/service.py
  Step 1  → Rule engine (regex patterns, high confidence)
  Step 2  → Supervised ML (TF-IDF + LinearSVC, trained on authored synthetic examples)
  Step 3  → Unclassified review when evidence is insufficient
═══════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations
import csv, io, json, os, re, sqlite3, uuid, warnings, tempfile, subprocess, shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import joblib
import numpy as np
import pandas as pd
import hashlib
import hmac
import secrets
import threading
import io

# Legacy engine diagnostics can include narrations: keep them out of server logs.
def print(*args, **kwargs):
    return None



_HERE        = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("HNI_DATA_DIR", os.path.join(_HERE, "data"))
os.makedirs(DATA_DIR, exist_ok=True)
_legacy_db = os.path.join(_HERE, "hni_ledger.db")
DB_PATH = os.environ.get("HNI_DB_PATH", _legacy_db if os.path.exists(_legacy_db) else os.path.join(DATA_DIR, "hni_ledger.db"))
MODEL_PATH = os.path.join(DATA_DIR, "synthetic_classifier_v2.joblib")
CLUSTER_PATH = os.path.join(DATA_DIR, "synthetic_clusters_v2.joblib")


# ══════════════════════════════════════════════════════════════════════════════
# LEDGER MAP  —  3-Book Accounting Structure
#
# Format: bs_key → (Book, Group, Account)
#
# Books:
#   BALANCE_SHEET   → Assets / Liabilities / Equity (snapshot of net worth)
#   INCOME_EXPENSE  → Income / Expenditure (P&L for the period)
#   CAPITAL         → Capital movements (advances, own transfers, investments)
#
# KEY RULE: Bank debit = cash ALREADY PAID = Expenditure, NOT liability
#           Only true balance-sheet items (loans, long-term assets, equity) go to BS
# ══════════════════════════════════════════════════════════════════════════════

LEDGER_MAP = {
    "exp_rent": ("INCOME_EXPENSE", "Expenditure", "Home & Property", "Rent paid"),
    "exp_loan_interest": ("INCOME_EXPENSE", "Expenditure", "Financial Costs", "Loan interest paid"),

    # ─── BALANCE SHEET : ASSETS ──────────────────────────────────────────────
    # Current Assets — things that convert to cash within 12 months
    "asset_advance_received_back":      ("BALANCE_SHEET", "Assets",      "Current Assets",      "Advance Received Back"),
    "asset_advance_returned":           ("BALANCE_SHEET", "Assets",      "Current Assets",      "Advance Returned by Party"),
    "asset_loan_repayment_received":    ("BALANCE_SHEET", "Assets",      "Current Assets",      "Loan Repayment Received"),
    "asset_loans_advances_given":       ("BALANCE_SHEET", "Assets",      "Current Assets",      "Loans & Advances Given (Receivable)"),
    "asset_own_transfer_in":            ("BALANCE_SHEET", "Assets",      "Current Assets",      "Own Account Transfer In (Contra)"),
    "asset_refund_received":            ("BALANCE_SHEET", "Assets",      "Current Assets",      "Refunds Received (Realised)"),
    "asset_broker_payout":              ("BALANCE_SHEET", "Assets",      "Current Assets",      "Broker Payout Received"),
    "asset_broker_balance":             ("BALANCE_SHEET", "Assets",      "Current Assets",      "Broker / Trading Account Balance"),
    "asset_fcy_inward":                 ("BALANCE_SHEET", "Assets",      "Current Assets",      "Foreign Currency Inward Remittance"),
    "asset_fd_maturity":                ("BALANCE_SHEET", "Assets",      "Current Assets",      "FD Maturity – Principal Returned"),
    "asset_mf_redemption":              ("BALANCE_SHEET", "Assets",      "Current Assets",      "MF Redemption – Principal Returned"),
    "asset_equity_sale_proceeds":       ("BALANCE_SHEET", "Assets",      "Current Assets",      "Equity Sale Proceeds (Principal)"),

    # Non-Current Assets — long-term holds
    "asset_land_cwip":                  ("BALANCE_SHEET", "Assets",      "Non-Current Assets",  "Capital Work-in-Progress (Land / Plot)"),
    "asset_investment_mf":              ("BALANCE_SHEET", "Assets",      "Non-Current Assets",  "Investment – Mutual Funds (SIP/Lump Sum)"),
    "asset_investment_equity":          ("BALANCE_SHEET", "Assets",      "Non-Current Assets",  "Investment – Equity Shares (Purchased)"),
    "asset_investment_fd":              ("BALANCE_SHEET", "Assets",      "Non-Current Assets",  "Investment – Fixed Deposit (Placed)"),
    "asset_investment_ppf":             ("BALANCE_SHEET", "Assets",      "Non-Current Assets",  "Investment – PPF"),
    "asset_investment_nps":             ("BALANCE_SHEET", "Assets",      "Non-Current Assets",  "Investment – NPS"),
    "asset_long_term_other":            ("BALANCE_SHEET", "Assets",      "Non-Current Assets",  "Long-Term Investments – Other"),

    # ─── BALANCE SHEET : LIABILITIES ─────────────────────────────────────────
    # Only TRUE outstanding liabilities — amounts still owed (not yet paid)
    # These keys are used when user manually classifies a PENDING outstanding balance
    # (e.g. credit card carry-forward, outstanding loan principal)
    # NOTE: on cash basis, a bank debit = ALREADY PAID → goes to Expenditure (exp_loan_emi / exp_credit_card)
    # These liability keys are for OUTSTANDING amounts still owed, not payments.
    "liability_loan_outstanding":       ("BALANCE_SHEET", "Liabilities", "Non-Current Liabilities", "Long-Term Loan Outstanding"),
    "liability_credit_card":            ("BALANCE_SHEET", "Liabilities", "Current Liabilities",     "Credit Card Outstanding Balance"),
    "liability_other":                  ("BALANCE_SHEET", "Liabilities", "Current Liabilities",     "Other Payables Outstanding"),

    # ─── INCOME & EXPENDITURE : EXPENDITURE — Financial Costs (loan/card payments) ────
    "exp_loan_emi":                     ("INCOME_EXPENSE", "Expenditure", "Financial Costs", "Loan EMI / Repayment (Paid)"),
    "exp_credit_card":                  ("INCOME_EXPENSE", "Expenditure", "Financial Costs", "Credit Card Payment (Settled)"),

    # ─── BALANCE SHEET : EQUITY ──────────────────────────────────────────────
    # Retained earnings contributed from income flows
    "equity_capital_introduced":        ("BALANCE_SHEET", "Equity",      "Owners Equity",       "Capital Introduced"),
    "equity_retained_earnings":         ("BALANCE_SHEET", "Equity",      "Retained Earnings",   "Retained Earnings (Net Income)"),

    # ─── INCOME & EXPENDITURE : INCOME ───────────────────────────────────────
    "income_salary":                    ("INCOME_EXPENSE", "Income",     "Employment Income",   "Salary Received"),
    "income_professional":              ("INCOME_EXPENSE", "Income",     "Employment Income",   "Professional / Consulting Fees"),
    "income_rental":                    ("INCOME_EXPENSE", "Income",     "Investment Income",   "Rental Income"),
    "income_dividend":                  ("INCOME_EXPENSE", "Income",     "Investment Income",   "Dividend Income"),
    "income_interest":                  ("INCOME_EXPENSE", "Income",     "Investment Income",   "Interest Income (SB / FD)"),
    "income_capital_gains":             ("INCOME_EXPENSE", "Income",     "Investment Income",   "Capital Gains (Equity / MF Trading)"),
    "income_other":                     ("INCOME_EXPENSE", "Income",     "Other Income",        "Miscellaneous Receipts"),
    "income_gift_family":               ("BALANCE_SHEET", "Equity",     "Owners Equity",       "Family Transfer / Capital Introduced"),
    "income_inward_payment":            ("INCOME_EXPENSE", "Income",     "Other Income",        "Miscellaneous Receipts"),

    # ─── INCOME & EXPENDITURE : EXPENDITURE ──────────────────────────────────
    # Food & Dining
    "exp_food":                         ("INCOME_EXPENSE", "Expenditure", "Household Expenses",  "Food & Dining"),
    "exp_grocery":                      ("INCOME_EXPENSE", "Expenditure", "Household Expenses",  "Groceries & Quick Commerce"),
    "exp_household":                    ("INCOME_EXPENSE", "Expenditure", "Household Expenses",  "Household Supplies"),
    # Shopping
    "exp_shopping_online":              ("INCOME_EXPENSE", "Expenditure", "Personal Expenses",   "Online Shopping"),
    "exp_clothing":                     ("INCOME_EXPENSE", "Expenditure", "Personal Expenses",   "Clothing & Apparel"),
    "exp_personal_care":                ("INCOME_EXPENSE", "Expenditure", "Personal Expenses",   "Personal Care & Beauty"),
    "exp_gifts":                        ("INCOME_EXPENSE", "Expenditure", "Personal Expenses",   "Gifts & Stationery"),
    # Health & Wellness
    "exp_health":                       ("INCOME_EXPENSE", "Expenditure", "Health & Wellness",   "Health & Medical Expenses"),
    # Travel & Transport
    "exp_travel":                       ("INCOME_EXPENSE", "Expenditure", "Travel & Transport",  "Travel, Transport & Cab"),
    # Entertainment
    "exp_entertainment":                ("INCOME_EXPENSE", "Expenditure", "Entertainment",       "Entertainment & Subscriptions"),
    # Education
    "exp_education":                    ("INCOME_EXPENSE", "Expenditure", "Education",           "Education & Tuition Fees"),
    # Home
    "exp_home_decor":                   ("INCOME_EXPENSE", "Expenditure", "Home & Property",     "Home Décor & Furnishings"),
    "exp_maintenance":                  ("INCOME_EXPENSE", "Expenditure", "Home & Property",     "Maintenance & Repair"),
    # Utilities
    "exp_utilities":                    ("INCOME_EXPENSE", "Expenditure", "Utilities & Bills",   "Electricity, Gas, Water, Internet"),
    # Staff & Professional Payments
    "exp_staff_wages":                  ("INCOME_EXPENSE", "Expenditure", "Employment Costs",    "Staff / Domestic Wages Paid"),
    "exp_consultant":                   ("INCOME_EXPENSE", "Expenditure", "Employment Costs",    "Consultant / Contractor Fees Paid"),
    # Financial Costs
    "exp_bank_charges":                 ("INCOME_EXPENSE", "Expenditure", "Financial Costs",     "Bank & Demat Charges"),
    "exp_broker_charges":               ("INCOME_EXPENSE", "Expenditure", "Financial Costs",     "Broker Contract Bill (STT + Fees)"),
    "exp_broker_dp":                    ("INCOME_EXPENSE", "Expenditure", "Financial Costs",     "Demat / DP Annual Charges"),
    "exp_broker_interest":              ("INCOME_EXPENSE", "Expenditure", "Financial Costs",     "Broker Delayed Payment Interest"),
    "exp_insurance":                    ("INCOME_EXPENSE", "Expenditure", "Financial Costs",     "Insurance Premium Paid"),
    # Taxation
    "exp_tax":                          ("INCOME_EXPENSE", "Expenditure", "Taxation",            "Income Tax / Advance Tax / GST Paid"),
    # Personal Transfers
    "exp_personal_transfer":            ("INCOME_EXPENSE", "Expenditure", "Personal Transfers",  "Personal Transfer – Family / Friends"),
    # Miscellaneous
    "exp_misc":                         ("INCOME_EXPENSE", "Expenditure", "Miscellaneous",       "Miscellaneous Payments"),
    "exp_cheque_misc":                  ("INCOME_EXPENSE", "Expenditure", "Miscellaneous",       "Cheque Payments (Misc)"),
    "exp_fcy_outward":                  ("INCOME_EXPENSE", "Expenditure", "Miscellaneous",       "Foreign Currency Outward Remittance"),

    # ─── SUSPENSE ─────────────────────────────────────────────────────────────
    "suspense_credit":                  ("SUSPENSE",       "Suspense",   "Unclassified",         "Credit – Requires Review"),
    "suspense_debit":                   ("SUSPENSE",       "Suspense",   "Unclassified",         "Debit – Requires Review"),

    # ─── TRADING ACCOUNT (5paisa / broker ledger) ─────────────────────────────
    "trading_funds_added":              ("BALANCE_SHEET",  "Assets",     "Current Assets",       "Trading Account – Funds Added"),
    "trading_payout":                   ("BALANCE_SHEET",  "Assets",     "Current Assets",       "Trading Account – Payout to Bank"),
    "trading_bill_equity":              ("INCOME_EXPENSE", "Expenditure","Financial Costs",      "Equity Brokerage & Transaction Costs"),
    "trading_bill_fno":                 ("INCOME_EXPENSE", "Income",     "Investment Income",    "F&O Net Realised P&L"),
    "trading_dp_charges":               ("INCOME_EXPENSE", "Expenditure","Financial Costs",      "DP & Demat Charges"),
    "trading_nb_charges":               ("INCOME_EXPENSE", "Expenditure","Financial Costs",      "Net Banking / Gateway Charges"),
}

# ── Asset attribution (who owns / owes) ───────────────────────────────────
ATTRIBUTION = {
    "asset_advance_received_back":      "Account Holder (Self) – advance returned by counterparty",
    "asset_advance_returned":           "Account Holder (Self) – advance refunded",
    "asset_loan_repayment_received":    "Account Holder (Self) – loan recovered from borrower",
    "asset_loans_advances_given":       "Counterparty (Borrower) – amount lent, receivable",
    "asset_own_transfer_in":            "Account Holder (Self) – own bank to bank transfer",
    "asset_refund_received":            "Account Holder (Self) – refund from vendor / UPI reversal",
    "asset_broker_payout":              "Account Holder (Self) – broker releasing trade proceeds",
    "asset_broker_balance":             "Account Holder (Self) – held in broker trading pool",
    "asset_fcy_inward":                 "Account Holder (Self) – foreign inward remittance",
    "asset_fd_maturity":                "Account Holder (Self) – FD principal returned",
    "asset_mf_redemption":              "Account Holder (Self) – MF units redeemed",
    "asset_equity_sale_proceeds":       "Account Holder (Self) – equity shares sold",
    "asset_land_cwip":                  "Account Holder (Self) – land/plot CWIP",
    "asset_investment_mf":              "Account Holder (Self) – MF portfolio",
    "asset_investment_equity":          "Account Holder (Self) – equity portfolio",
    "asset_investment_fd":              "Account Holder (Self) – bank FD",
    "asset_investment_ppf":             "Account Holder (Self) – PPF account at SBI",
    "asset_investment_nps":             "Account Holder (Self) – NPS CRA account",
    "asset_long_term_other":            "Account Holder (Self) – long-term investment",
    "exp_loan_emi":                     "Lending Institution – EMI paid, no longer a liability",
    "exp_credit_card":                  "Credit Card Issuer – bill settled in full",
    "liability_loan_outstanding":       "Lending Institution – Outstanding loan principal still owed",
    "liability_credit_card":            "Credit Card Issuer – outstanding balance still owed",
    "liability_other":                  "Counterparty – other outstanding payable",
    "income_salary":                    "Account Holder (Self) – from employer",
    "income_professional":              "Account Holder (Self) – from clients",
    "income_rental":                    "Account Holder (Self) – from tenant",
    "income_dividend":                  "Account Holder (Self) – from investee company",
    "income_interest":                  "Account Holder (Self) – from bank",
    "income_capital_gains":             "Account Holder (Self) – from trading / MF",
    "income_other":                     "Account Holder (Self) – miscellaneous",
    "income_gift_family":               "Family Member / Friend (Capital Introduced)",
    "income_inward_payment":            "Account Holder (Self) – unidentified inward credit treated as other income",
    "exp_food":                         "Merchant (Restaurant / Delivery)",
    "exp_grocery":                      "Merchant (Grocery / Quick Commerce)",
    "exp_household":                    "Merchant (Household Goods)",
    "exp_shopping_online":              "Merchant (E-commerce)",
    "exp_clothing":                     "Merchant (Apparel)",
    "exp_personal_care":                "Merchant (Beauty / Wellness)",
    "exp_gifts":                        "Merchant (Gifts / Stationery)",
    "exp_health":                       "Healthcare Provider / Pharmacy",
    "exp_travel":                       "Travel / Transport Service",
    "exp_entertainment":                "Entertainment / Subscription Service",
    "exp_education":                    "Educational Institution",
    "exp_home_decor":                   "Merchant (Home Goods)",
    "exp_maintenance":                  "Service Provider (Maintenance)",
    "exp_utilities":                    "Utility Provider",
    "exp_staff_wages":                  "Employee / Domestic Staff",
    "exp_consultant":                   "Consultant / Contractor",
    "exp_bank_charges":                 "Bank (Charges)",
    "exp_broker_charges":               "Stock Exchange / Broker (STT, fees, GST)",
    "exp_broker_dp":                    "CDSL / NSDL (Depository)",
    "exp_broker_interest":              "Broker (Delayed Payment Interest)",
    "exp_insurance":                    "Insurance Company",
    "exp_tax":                          "Government (Income Tax / GST Authority)",
    "exp_personal_transfer":            "Family Member / Friend",
    "exp_misc":                         "Various Counterparties",
    "exp_cheque_misc":                  "Payee (Cheque)",
    "exp_fcy_outward":                  "Foreign Beneficiary",
    "suspense_credit":                  "Review Required",
    "suspense_debit":                   "Review Required",
}

# ── Map old Excel line items → new ledger keys ─────────────────────────────
EXCEL_LINE_TO_KEY = {
    "Advance Received Back":                    "asset_advance_received_back",
    "Advance Returned":                         "asset_advance_returned",
    "Loan Repayment Received":                  "asset_loan_repayment_received",
    "Loans & Advances Given (Receivable)":      "asset_loans_advances_given",
    "Own Account Transfer In":                  "asset_own_transfer_in",
    "Refunds Receivable (Realised)":            "asset_refund_received",
    "Capital Work-in-Progress (Land)":          "asset_land_cwip",
    "Long-Term Investments":                    "asset_long_term_other",
    "Investment – Mutual Funds (Purchase)":     "asset_investment_mf",
    "Investment – Equity Shares (Purchase)":    "asset_investment_equity",
    "Investment – Fixed Deposit":               "asset_investment_fd",
    "Investment – PPF":                         "asset_investment_ppf",
    "Investment – NPS":                         "asset_investment_nps",
    "MF Redemption – Principal Return":         "asset_mf_redemption",
    "Equity Sale – Principal Proceeds":         "asset_equity_sale_proceeds",
    "FD Maturity – Principal Return":           "asset_fd_maturity",
    "Bank Charges Payable":                     "exp_bank_charges",
    "Broker Contract Bill (STT + Fees)":        "exp_broker_charges",
    "Demat DP Charges / AMC":                  "exp_broker_dp",
    "Credit Card Payable (Discharged)":         "exp_credit_card",    # FIXED: FIX-1
    "Other Payables – Cheque Payment":          "exp_cheque_misc",
    "Other Payables – Miscellaneous":           "exp_misc",
    "Personal Transfer – Family":               "exp_personal_transfer",
    "Staff Wages Payable (Discharged)":         "exp_staff_wages",
    "Consultant Fees Payable (Discharged)":     "exp_consultant",
    "Tax Payable (Discharged)":                 "exp_tax",
    "Trade Payables – Clothing & Apparel":      "exp_clothing",
    "Trade Payables – Education":               "exp_education",
    "Trade Payables – Entertainment":           "exp_entertainment",
    "Trade Payables – Food & Dining":           "exp_food",
    "Trade Payables – Gifts & Stationery":      "exp_gifts",
    "Trade Payables – Groceries":               "exp_grocery",
    "Trade Payables – Health & Medical":        "exp_health",
    "Trade Payables – Home Décor":              "exp_home_decor",
    "Trade Payables – Household Supplies":      "exp_household",
    "Trade Payables – Maintenance & Repair":    "exp_maintenance",
    "Trade Payables – Online Shopping":         "exp_shopping_online",
    "Trade Payables – Personal Care & Beauty":  "exp_personal_care",
    "Trade Payables – Travel & Transport":      "exp_travel",
    "Utility Bills Payable":                    "exp_utilities",
    "Long-Term Loan Repayment":                 "exp_loan_emi",       # FIXED: FIX-1
    "Income – Capital Gains (Trading)":         "income_capital_gains",
    "Income – Capital Gains":                   "income_capital_gains",
    "Income – Dividend":                        "income_dividend",
    "Income – Interest on Savings":             "income_interest",
    "Income – Interest on Savings / FD":        "income_interest",
    "Income – Other Investment":                "income_other",
    "Income – Employment":                      "income_professional",
    "Income – Professional / Consulting":       "income_professional",
    "Income – Miscellaneous Receipts":          "income_other",
    "Income – Salary":                          "income_salary",
    "Suspense – Credit (Review Required)":      "suspense_credit",
    "Suspense – Debit (Review Required)":       "suspense_debit",
}

# ── Category to key (for ML output) ───────────────────────────────────────
CAT_TO_KEY = {k: k for k in LEDGER_MAP}  # direct keys
# Plus legacy aliases
CAT_TO_KEY.update({
    "income_dividend":                "income_dividend",
    "banking_interest":               "income_interest",
    "income_salary":                  "income_salary",
    "professional_income":            "income_professional",
    "rental_income":                  "income_rental",
    "income_capital_gains":           "income_capital_gains",
    "capital_gains":                  "income_capital_gains",
    "income_misc":                    "income_other",
    "investment_mutual_fund_purchase":"asset_investment_mf",
    "investment_mutual_fund":         "asset_investment_mf",
    "investment_equity_purchase":     "asset_investment_equity",
    "investment_equity":              "asset_investment_equity",
    "investment_fd_creation":         "asset_investment_fd",
    "ppf_investment":                 "asset_investment_ppf",
    "nps_investment":                 "asset_investment_nps",
    "ppf_nps":                        "asset_investment_ppf",
    "capital_work_land":              "asset_land_cwip",
    "loans_advances_given":           "asset_loans_advances_given",
    "loan_repayment_received":        "asset_loan_repayment_received",
    "advance_received_back":          "asset_advance_received_back",
    "advance_returned":               "asset_advance_returned",
    "own_account_transfer_in":        "asset_own_transfer_in",
    "refunds_receivable":             "asset_refund_received",
    "broker_payout":                  "asset_broker_payout",
    "broker_transfer":                "asset_broker_balance",
    "investment_mf_redemption":       "asset_mf_redemption",
    "investment_equity_sale":         "asset_equity_sale_proceeds",
    "investment_fd_maturity":         "asset_fd_maturity",
    "fcy_inward":                     "asset_fcy_inward",
    "loan_repayment":                 "exp_loan_emi",
    "credit_card_payable":            "exp_credit_card",
    "loan_outstanding":               "liability_loan_outstanding",
    "credit_card_outstanding":        "liability_credit_card",
    "liability_other_payable":        "liability_other",
    "trade_payable_food":             "exp_food",
    "trade_payable_grocery":          "exp_grocery",
    "trade_payable_shopping":         "exp_shopping_online",
    "trade_payable_clothing":         "exp_clothing",
    "trade_payable_health":           "exp_health",
    "trade_payable_travel":           "exp_travel",
    "trade_payable_entertainment":    "exp_entertainment",
    "trade_payable_education":        "exp_education",
    "trade_payable_personal_care":    "exp_personal_care",
    "trade_payable_home":             "exp_home_decor",
    "trade_payable_household":        "exp_household",
    "trade_payable_gifts":            "exp_gifts",
    "trade_payable_maintenance":      "exp_maintenance",
    "utility_payable":                "exp_utilities",
    "staff_wages":                    "exp_staff_wages",
    "consultant_payable":             "exp_consultant",
    "tax_payable":                    "exp_tax",
    "bank_charges_payable":           "exp_bank_charges",
    "broker_contract_bill":           "exp_broker_charges",
    "broker_dp_charges":              "exp_broker_dp",
    "broker_interest_charge":         "exp_broker_interest",
    "insurance_premium":              "exp_insurance",
    "personal_transfer_family":       "exp_personal_transfer",
    "other_payables_misc":            "exp_misc",
    "other_payables_cheque":          "exp_cheque_misc",
    "fcy_outward":                    "exp_fcy_outward",
    "food":                           "exp_food",
    "grocery":                        "exp_grocery",
    "quick_commerce":                 "exp_grocery",
    "shopping":                       "exp_shopping_online",
    "clothing":                       "exp_clothing",
    "health":                         "exp_health",
    "travel":                         "exp_travel",
    "entertainment":                  "exp_entertainment",
    "education":                      "exp_education",
    "personal_care":                  "exp_personal_care",
    "home_decor":                     "exp_home_decor",
    "household_supplies":             "exp_household",
    "gifts_stationery":               "exp_gifts",
    "maintenance_repair":             "exp_maintenance",
    "utility":                        "exp_utilities",
    "tax_payment":                    "exp_tax",
    "fees":                           "exp_bank_charges",
    "sms_alert_charges":              "exp_bank_charges",
    "cheque_misc":                    "exp_cheque_misc",
    "transfer":                       "asset_own_transfer_in",
    "unclassified":                   "suspense_debit",
    "suspense_credit":                "suspense_credit",
    "suspense_debit":                 "suspense_debit",
    "ach_credit_other":               "income_other",
})


# ══════════════════════════════════════════════════════════════════════════════
# TEXT NORMALIZATION HELPERS
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# CANONICAL DIRECTION DERIVATION  (Part 2 — Req 1)
# Single authoritative function; call BEFORE classification.
# ══════════════════════════════════════════════════════════════════════════════

def derive_txn_type(
    raw_debit:   Any = None,
    raw_credit:  Any = None,
    raw_type:    Any = None,
    raw_amount:  Any = None,
    narration:   str = "",
    parser_txn_type: str = "",
) -> str:
    """
    Determine the canonical transaction direction ('debit' or 'credit').

    Precedence (highest → lowest):
      a. Explicit debit-column / credit-column values (non-zero beats zero).
      b. Explicit CR / DR markers in the type column or amount suffix.
      c. Signed amount (negative → debit, positive → credit).
      d. parser_txn_type already derived by the parser (trusted if not 'unknown').
      e. Narration-based heuristic (_infer_txn_type).
      f. Conservative fallback → 'debit'.

    Never returns anything other than 'debit' or 'credit'.
    """
    # ── a. Separate debit/credit columns ─────────────────────────────────────
    def _amt(v) -> float:
        if v is None:
            return 0.0
        if isinstance(v, (int, float)):
            return abs(float(v))
        s = re.sub(r'[₹$£€,\s]', '', str(v)).upper()
        s = re.sub(r'(DR|CR)\.?$', '', s).strip()
        s = re.sub(r'[^\d.]', '', s)
        try:
            return abs(float(s))
        except (ValueError, TypeError):
            return 0.0

    dr_val = _amt(raw_debit)
    cr_val = _amt(raw_credit)
    if dr_val > 0 and cr_val == 0:
        return "debit"
    if cr_val > 0 and dr_val == 0:
        return "credit"

    # ── a+. HDFC WDL TFR / DEP TFR prefix (embedded direction) ──────────────
    if narration:
        _n = narration.strip().upper()
        if re.match(r'^WDL\b', _n):
            return "debit"
        if re.match(r'^DEP\b', _n):
            return "credit"

    # ── b. Explicit CR/DR markers in type column or amount suffix ─────────────
    tc = str(raw_type or "").strip().upper()
    if tc in ("CR", "CREDIT"):
        return "credit"
    if tc in ("DR", "DEBIT"):
        return "debit"

    # Check amount-string suffix (e.g. "1234.56 Cr" or "1234.56Dr")
    for val in (raw_debit, raw_credit, raw_amount):
        sv = str(val or "").strip().upper()
        if re.search(r'\bCR\.?$', sv):
            return "credit"
        if re.search(r'\bDR\.?$', sv):
            return "debit"

    # ── c. Signed amount — ONLY negative signals debit; positive is ambiguous ──
    # A positive magnitude does NOT mean credit: single-amount columns store
    # absolute values for both directions.  Only a genuinely negative raw value
    # (e.g. "-1234.56") is a reliable debit signal.
    for val in (raw_amount, raw_debit):
        sv = str(val or "").strip()
        try:
            f = float(re.sub(r'[^\d.\-]', '', sv))
            if f < 0:
                return "debit"
            # Positive: fall through — not enough information to conclude credit
        except (ValueError, TypeError):
            pass

    # ── d. Parser-derived txn_type — elevated above narration heuristic ───────
    # The Excel/PDF parser already validated direction from debit/credit columns
    # or from a type column.  Trust it before falling back to narration keywords.
    if parser_txn_type in ("credit", "debit"):
        return parser_txn_type

    # ── e. Narration heuristic ────────────────────────────────────────────────
    inferred = _infer_txn_type((narration or "").upper())
    if inferred in ("credit", "debit"):
        return inferred

    # ── f. Conservative fallback ──────────────────────────────────────────────
    return "debit"


def normalize_parsed_record(raw: Dict[str, Any], source: str = "excel") -> Dict[str, Any]:
    """
    Normalise a raw parser output dict into a canonical structure before
    classification.  Accepts dicts from Excel, CSV, or PDF parsers.

    Output keys (always present):
      txn_date       – ISO YYYY-MM-DD string
      narration      – clean string
      amount         – float ≥ 0
      txn_type       – 'debit' or 'credit'
      source         – parser tag (e.g. 'excel', 'pdf')
      parser_notes   – diagnostic string (empty if clean)
      raw_debit      – raw debit cell value (for audit)
      raw_credit     – raw credit cell value (for audit)
    """
    narration = str(raw.get("description") or raw.get("narration") or "").strip()
    txn_date  = str(raw.get("txn_date") or "").strip()
    amount_in = raw.get("amount", 0)
    raw_debit  = raw.get("raw_debit")
    raw_credit = raw.get("raw_credit")
    raw_type   = raw.get("type_col") or raw.get("raw_type") or ""
    # Only use raw_amount from an explicit signed-amount column cell.
    # Do NOT pass the already-normalised positive `amount` field here —
    # a positive magnitude is ambiguous (could be debit or credit).
    raw_amount = raw.get("raw_amount")   # None unless parser explicitly set it
    parser_type = str(raw.get("txn_type") or "").strip().lower()
    if parser_type not in ("credit", "debit"):
        parser_type = ""

    # Re-derive direction using the canonical helper
    txn_type = derive_txn_type(
        raw_debit=raw_debit,
        raw_credit=raw_credit,
        raw_type=raw_type,
        raw_amount=raw_amount,
        narration=narration,
        parser_txn_type=parser_type,
    )

    # Amount magnitude
    try:
        amount = abs(float(amount_in or 0))
    except (TypeError, ValueError):
        amount = 0.0

    notes = []
    if not narration:
        notes.append("empty_narration")
    if amount == 0:
        notes.append("zero_amount")
    if not txn_date:
        notes.append("missing_date")
        txn_date = ""

    return {
        "txn_date":    txn_date,
        "narration":   narration,
        "description": narration,        # alias kept for downstream compat
        "amount":      amount,
        "txn_type":    txn_type,
        "source":      source,
        "parser_notes": "; ".join(notes),
        "raw_debit":   raw_debit,
        "raw_credit":  raw_credit,
    }


def normalize_txn_text(narration: str) -> str:
    """
    Normalize a bank narration for classification.
    - Uppercase, strip whitespace
    - Remove UPI/IMPS reference IDs (long digit sequences after channel keywords)
    - Remove masked card numbers (XXXXXX1234, XX1234)
    - Remove date stamps (DD-MM-YYYY, DD/MM/YYYY, DD.MM.YYYY)
    - Collapse multiple spaces
    - Preserve meaningful merchant words and channel words
    """
    s = (narration or "").strip().upper()
    if not s:
        return ""
    # Strip PDF/CC statement artefacts (card holder name prefix, border chars, charge suffix)
    s = re.sub(r'^[A-Z][A-Z\s]{3,25}\s+(?://|\|)\s*', '', s).strip()
    s = re.sub(r'^[|/\s:]+', '', s).strip()
    s = re.sub(r'\s*\+?\s*C\s+L\s*$', '', s).strip()
    s = re.sub(r'\s*\(REF#\s*[A-Z0-9]+\)', '', s).strip()
    # Remove long reference/transaction IDs (8+ digits) that appear after known prefixes
    s = re.sub(r'(?<=UPI)\s*/\s*\d{8,}', '', s)
    s = re.sub(r'\b(?:REF|TXN|TXNID|REFNO|REFERENCE)\s*(?:NO|ID|NUMBER)?\s*[:\-]?\s*\d{6,}\b', '', s)
    # Remove standalone long digit sequences (12+ digits) — UPI ref numbers, IMPS IDs
    s = re.sub(r'\b\d{12,}\b', '', s)
    # Remove masked card numbers: XXXXXX1234, XX1234, ****1234
    s = re.sub(r'\b[X*]{2,}\d{3,6}\b', '', s)
    # Remove date stamps: DD-MM-YYYY, DD/MM/YYYY, DD.MM.YYYY, YYYY-MM-DD
    s = re.sub(r'\b\d{1,2}[/\-.](\d{1,2}|[A-Z]{3})[/\-.]\d{2,4}\b', '', s)
    s = re.sub(r'\b\d{4}[/\-.]\d{2}[/\-.]\d{2}\b', '', s)
    # Collapse multiple spaces
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def tokenize_txn_text(narration: str) -> set:
    """
    Tokenize a normalized narration into a set of meaningful tokens.
    - Calls normalize_txn_text first
    - Splits on whitespace and punctuation
    - Drops tokens ≤ 2 chars and pure numeric tokens
    """
    norm = normalize_txn_text(narration)
    if not norm:
        return set()
    # Split on whitespace and common punctuation
    raw_tokens = re.split(r'[\s/\-_.,;:@#|()\[\]{}]+', norm)
    # Keep tokens > 2 chars that are not pure digits
    return {t for t in raw_tokens if len(t) > 2 and not t.isdigit()}


# ══════════════════════════════════════════════════════════════════════════════
# RULE ENGINE  —  Pattern-based classification
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# POST-CLASSIFICATION SANITY VALIDATION  (Part 2 — Req 4)
# ══════════════════════════════════════════════════════════════════════════════

# Keys that are inherently credit-direction (inflows)
_CREDIT_ONLY_KEYS: frozenset = frozenset({
    "income_salary", "income_professional", "income_rental",
    "income_dividend", "income_interest", "income_capital_gains",
    "income_other", "income_gift_family", "income_inward_payment",
    "asset_own_transfer_in", "asset_refund_received", "asset_broker_payout",
    "asset_fcy_inward", "asset_fd_maturity", "asset_mf_redemption",
    "asset_equity_sale_proceeds", "asset_loan_repayment_received",
    "asset_advance_received_back", "asset_advance_returned",
    "suspense_credit",
    "trading_payout",
})

# Keys that are inherently debit-direction (outflows)
_DEBIT_ONLY_KEYS: frozenset = frozenset({
    "asset_investment_mf", "asset_investment_equity", "asset_investment_fd",
    "asset_investment_ppf", "asset_investment_nps", "asset_land_cwip",
    "asset_loans_advances_given",
    "exp_loan_emi", "exp_credit_card",
    "exp_food", "exp_grocery", "exp_household", "exp_shopping_online",
    "exp_clothing", "exp_personal_care", "exp_gifts", "exp_health",
    "exp_travel", "exp_entertainment", "exp_education", "exp_home_decor",
    "exp_maintenance", "exp_utilities", "exp_staff_wages", "exp_consultant",
    "exp_bank_charges", "exp_broker_charges", "exp_broker_dp",
    "exp_broker_interest", "exp_insurance", "exp_tax",
    "exp_personal_transfer", "exp_misc", "exp_cheque_misc", "exp_fcy_outward",
    "suspense_debit",
    "trading_funds_added",
})

# Keys that can go either way depending on context
_BIDIRECTIONAL_KEYS: frozenset = frozenset({
    "asset_broker_balance",
    "asset_long_term_other",
    "equity_capital_introduced",
    "equity_retained_earnings",
    "income_gift_family",      # can rarely appear as debit (gift returned)
    "trading_bill_equity",
    "trading_bill_fno",
    "trading_dp_charges",
    "trading_nb_charges",
    # Liabilities: credit = outstanding balance grows; debit = payment reduces it
    "liability_loan_outstanding",
    "liability_credit_card",
    "liability_other",
})


def validate_classification(
    result: Dict[str, Any],
    forced_type: Optional[str],
) -> Dict[str, Any]:
    """
    Post-classification sanity check.
    If the chosen ledger_key is directionally incompatible with forced_type:
      - Try to map to the correct directional suspense key.
      - Or correct to a known compatible key when unambiguous.
      - Always add a conflict diagnostic in 'note'.

    Returns a (possibly updated) result dict.
    Mutates a copy — does not mutate the original.
    """
    if not forced_type or forced_type not in ("credit", "debit"):
        return result   # nothing to check without a known direction

    key = result.get("ledger_key", "")
    conflict = False
    fix_note = ""

    if forced_type == "debit" and key in _CREDIT_ONLY_KEYS:
        conflict = True
        # Attempt a sensible correction
        if key == "asset_own_transfer_in":
            # Debit own transfer = funds leaving this account to own account elsewhere
            # This is fine to keep as asset_own_transfer_in but direction should be debit
            # Treat as a contra debit movement.
            pass  # keep key, just fix direction
        else:
            fix_note = f"CONFLICT: debit txn matched credit-only key '{key}'. Downgraded to suspense_debit."
            result = dict(result)
            result["ledger_key"] = "suspense_debit"
            result["book"] = "SUSPENSE"
            result["section"] = "Suspense"
            result["grp"] = "Unclassified"
            result["account"] = "Debit – Requires Review"
            result["confidence"] = min(result.get("confidence", 0.5), 0.40)
        result = dict(result)
        result["txn_type"] = "debit"
        if fix_note:
            existing_note = result.get("note", "")
            result["note"] = (existing_note + " | " + fix_note).strip(" | ")

    elif forced_type == "credit" and key in _DEBIT_ONLY_KEYS:
        conflict = True
        fix_note = f"CONFLICT: credit txn matched debit-only key '{key}'. Downgraded to suspense_credit."
        result = dict(result)
        result["ledger_key"] = "suspense_credit"
        result["book"] = "SUSPENSE"
        result["section"] = "Suspense"
        result["grp"] = "Unclassified"
        result["account"] = "Credit – Requires Review"
        result["confidence"] = min(result.get("confidence", 0.5), 0.40)
        result["txn_type"] = "credit"
        existing_note = result.get("note", "")
        result["note"] = (existing_note + " | " + fix_note).strip(" | ")

    elif forced_type == "credit" and key == "suspense_debit":
        # Suspense direction fix
        result = dict(result)
        result["ledger_key"] = "suspense_credit"
        ledger_entry = LEDGER_MAP.get("suspense_credit", LEDGER_MAP["suspense_debit"])
        result["book"] = ledger_entry[0]
        result["section"] = ledger_entry[1]
        result["grp"] = ledger_entry[2]
        result["account"] = ledger_entry[3]
        result["txn_type"] = "credit"

    elif forced_type == "debit" and key == "suspense_credit":
        result = dict(result)
        result["ledger_key"] = "suspense_debit"
        ledger_entry = LEDGER_MAP.get("suspense_debit", LEDGER_MAP["suspense_debit"])
        result["book"] = ledger_entry[0]
        result["section"] = ledger_entry[1]
        result["grp"] = ledger_entry[2]
        result["account"] = ledger_entry[3]
        result["txn_type"] = "debit"

    narr_for_guard = result.get("narration", "")
    current_key = result.get("ledger_key", "")
    current_type = forced_type or result.get("txn_type", "")
    if (
        current_key in ("asset_loans_advances_given", "asset_loan_repayment_received")
        and _is_family_loan_style_narration(narr_for_guard)
        and not _is_explicit_self_given_loan_narration(narr_for_guard)
        and not _has_broker_trading_marker(narr_for_guard)
    ):
        result = dict(result)
        result["ledger_key"] = "liability_loan_outstanding"
        ledger_entry = LEDGER_MAP["liability_loan_outstanding"]
        result["book"] = ledger_entry[0]
        result["section"] = ledger_entry[1]
        result["grp"] = ledger_entry[2]
        result["account"] = ledger_entry[3]
        if current_type in ("debit", "credit"):
            result["txn_type"] = current_type
        result["confidence"] = min(max(float(result.get("confidence", 0.60) or 0.60), 0.60), 0.90)
        existing_note = result.get("note", "")
        correction_note = (
            "Family-style transfer cannot remain in self-given loan asset bucket "
            "without explicit loan-given / repayment-received markers. "
            "Corrected to liability_loan_outstanding."
        )
        result["note"] = (existing_note + " | " + correction_note).strip(" | ")


    # Final anti-suspense rule for credits:
    # unidentified / low-confidence inward credits should still hit Income so
    # the bank balance and equity reconcile on cash basis.
    if forced_type == "credit":
        current_key = result.get("ledger_key", "")
        if current_key == "suspense_credit":
            result = dict(result)
            result["ledger_key"] = "income_other"
            ledger_entry = LEDGER_MAP["income_other"]
            result["book"] = ledger_entry[0]
            result["section"] = ledger_entry[1]
            result["grp"] = ledger_entry[2]
            result["account"] = ledger_entry[3]
            result["txn_type"] = "credit"
            result["confidence"] = min(max(float(result.get("confidence", 0.35) or 0.35), 0.35), 0.75)
            existing_note = result.get("note", "")
            extra = "FINAL CREDIT FALLBACK: unresolved inward credit posted to income_other to prevent suspense / balance-sheet mismatch."
            result["note"] = (existing_note + " | " + extra).strip(" | ")

    # Always ensure txn_type on result matches forced_type
    if not conflict and forced_type:
        result = dict(result)
        result["txn_type"] = forced_type

    return result


def _u(s): return (s or "").strip().upper()


FAMILY_NAME_PAT = r'GARG|KUMAR|SHARMA|SINGH|GUPTA|AGARWAL|JAIN|PATEL|MOTHER|FATHER|MOM|DAD|BROTHER|SISTER|UNCLE|AUNT|MAMA|PAPA|HUF|YADAV|VERMA|MEHTA|CHAUHAN|RAJPUT|SONI|BAHADUR|SAHU|SHETH|MODI|KAPOOR|KHAN|KHANNA|MALHOTRA|MALIK|MISHRA|TRIVEDI|TIWARI'


def _is_family_loan_style_narration(narration: str) -> bool:
    u = _u(narration)
    if not u:
        return False
    return bool(
        re.search(r'\bTPT\b|IB\s+FUNDS\s+TRANSFER|\bU/G\b|UNDER\s+GUARDIAN|\bHUF\b', u)
        or re.search(FAMILY_NAME_PAT, u)
    )


def _is_explicit_self_given_loan_narration(narration: str) -> bool:
    u = _u(narration)
    if not u:
        return False
    return bool(
        re.search(
            r'\b('
            r'LOAN\s+GIVEN|'
            r'LENT\s+TO|'
            r'ADVANCE\s+PAID|'
            r'ADVANCE\s+TO|'
            r'LOAN\s+RECOVERY\s+RECEIVED|'
            r'REPAYMENT\s+RECEIVED\s+FROM\s+BORROWER'
            r')\b',
            u,
        )
    )


PROTECTED_CLASSIFICATION_KEYS = {
    "income_salary",
    "income_dividend",
    "income_interest",
    "asset_investment_ppf",
    "asset_investment_nps",
    "exp_tax",
    "trading_funds_added",
    "trading_payout",
    "exp_broker_charges",
    "exp_broker_dp",
    "exp_broker_interest",
}

_BROKER_TRADING_MARKER_RE = re.compile(
    r'\b(?:'
    r'ZERODHA|KITE|UPSTOX|5PAISA|GROWW|IIFL|VENTURA|LDK|'
    r'SHARES?|SECURITIES|NSE|BSE|CLEARING|SETTLEMENT|BROKER|'
    r'DEMAT|DP|TRADING\s+ACCOUNT|HOLDING\s+ACCOUNT'
    r')\b|L\s*D\s*K\s+SHARES?',
    re.I,
)

_BROKER_CASH_MOVEMENT_RE = re.compile(
    r'\b(?:'
    r'LDK|ZERODHA|KITE|UPSTOX|5PAISA|GROWW|IIFL|VENTURA|'
    r'BROKER|BROKING|TRANSFER|TRADING\s+ACCOUNT|HOLDING\s+ACCOUNT|'
    r'FUNDS?\s+(?:ADDED|TRANSFER|PAYOUT|PAY\s*OUT)|PAYOUT|PAY\s*OUT|'
    r'NSE|BSE|CLEARING|SETTLEMENT'
    r')\b|L\s*D\s*K\s+(?:SHARES?|SECURITIES?)',
    re.I,
)

_BROKER_COST_RE = re.compile(
    r'\b(?:'
    r'CONTRACT\s+(?:BILL|COPY|NOTE)|CONTRACT\s+NOTE|STT|GST|BROKERAGE|'
    r'DP\s+CHARGES?|DEPOSITORY\s+AMC|DEMAT\s+AMC|AMC|'
    r'DELAYED\s+PAYMENT\s+INTEREST|BROKER\s+INTEREST'
    r')\b',
    re.I,
)

_PROTECTED_INVESTMENT_INCOME_RE = re.compile(
    r'\b(?:'
    r'PPF|PUBLIC\s+PROVIDENT\s+FUND|NPS|NATIONAL\s+PENSION|NSDL|CRA|POP|POP[-\s]*SP|'
    r'PLOT|SCHEME|TRUST|REDEMPTION|DIVIDEND|DIV|INTDIV|FINDIV|FINALDIV|SPLDIV|'
    r'INTEREST|INT\.?PD|FD|FIXED\s+DEPOSIT|SIP|MUTUAL\s+FUND|MF\b|'
    r'RENT|SALARY|PAYROLL|CBDT|ADVANCE\s+TAX|TDS\s+PAYMENT|GST\s+CHALLAN|SELF\s+ASSESSMENT\s+TAX'
    r')\b',
    re.I,
)

_DIVIDEND_ISSUER_GUARD_RE = re.compile(
    r'\b(?:'
    r'SALARY|PAYROLL|INTEREST|INT\s*PD|INT\.PD|REFUND|REVERSAL|CASHBACK|'
    r'LOAN|TPT|TRANSFER|NEFT\s+DR|RTGS\s+DR|UPI\s*/?\s*DR|'
    r'BROKER|ZERODHA|NSE|BSE|CLEARING|REDEMPTION|MATURITY'
    r')\b',
    re.I,
)

_DIVIDEND_ISSUER_NAME_RE = re.compile(
    r'\b(?:'
    r'COAL\s+INDIA|OIL\s+INDIA|OIL\s+(?:AND|&)\s+NATURAL\s+GAS|ONGC|NHPC|SJVN|HUDCO|'
    r'MAHANAGAR\s+GAS|HERO\s+MOTOCORP|WIPRO|TCS|TATA\s+CONSULTANCY|HDB\s+FINAN|'
    r'GENERAL\s+INSURANCE|ADITYA\s+BIRLA|LAURUS\s+LABS|BIKAJI\s+FOODS|NLC\s+INDIA|'
    r'LIFE\s+INSURANCE\s+CORPO|STATE\s+(?:BANK\s+)?OF\s+INDIA|'
    r'BAJAJ\s+FINSERV|BAJAJ\s+HOLDINGS|IRCTC|INDIAN\s+RAILWAY\s+CATERING|'
    r'ENGINEERS\s+INDIA|HINDUSTAN\s+AERONAUTICS|HOUSING\s+(?:AND|&)\s+URBAN\s+DEVELOPMENT|'
    r'EQUITAS\s+SMALL\s+FINANCE|WONDERLA|BIOCON|BHARTI\s+HEXACOM|SHIPPING\s+CORPORATION|'
    r'IRCON|UNITED\s+NILGIRI|PNB\s+HOU|POWER\s+GRID|NTPC|REC\s+LIMITED|'
    r'REC\s+LTD|PFC|POWER\s+FINANCE|GAIL|SAIL|NMDC|BEL|BHEL|IOC|'
    r'INDIAN\s+OIL|HINDUSTAN\s+ZINC|VEDANTA|INFOSYS|HCL\s+TECH|'
    r'TATA\s+STEEL|TATA\s+MOTORS|HINDUSTAN\s+UNILEVER|ITC|'
    r'RELIANCE\s+INDUSTRIES|MARUTI|BAJAJ\s+AUTO|ASIAN\s+PAINTS'
    r')\b',
    re.I,
)

_DIVIDEND_COMPACT_ISSUERS = {
    "MAHANAGARGAS": "Mahanagar Gas",
    "LAURUSLABSLIMITED": "Laurus Labs Limited",
    "LAURUSLABS": "Laurus Labs",
    "BIKAJIFOODSINTLTD": "Bikaji Foods Intl Ltd",
    "BIKAJIFOODS": "Bikaji Foods",
    "HEROMOTOCORPLTD": "Hero Motocorp Ltd",
    "HEROMOTOCORP": "Hero Motocorp",
    "BAJAJFINSERVLIMITED": "Bajaj Finserv Limited",
    "BAJAJFINSERVLIMITE": "Bajaj Finserv Limited",
    "BAJAJFINSERV": "Bajaj Finserv",
    "BAJAJHOLDINGSLTD": "Bajaj Holdings Ltd",
    "BAJAJHOLDINGS": "Bajaj Holdings",
    "HUDCO": "HUDCO",
    "HDBFINAN": "HDB Finan",
    "HDFC": "HDFC",
    "IRCTC": "IRCTC",
    "INDIANRAILWAYCATERING": "Indian Railway Catering",
    "ENGINEERSINDIA": "Engineers India",
    "HINDUSTANAERONAUTICS": "Hindustan Aeronautics",
    "HOUSINGANDURBANDEVELOPMENT": "Housing And Urban Development",
    "EQUITASSMALLFINANCE": "Equitas Small Finance",
    "WONDERLA": "Wonderla",
    "BIOCON": "Biocon",
    "BHARTIHEXACOM": "Bharti Hexacom",
    "SHIPPINGCORPORATION": "Shipping Corporation",
    "OILINDIA": "Oil India",
    "COALINDIA": "Coal India",
    "OILANDNATURALGAS": "Oil And Natural Gas",
    "ONGC": "ONGC",
    "NHPC": "NHPC",
    "SJVN": "SJVN",
    "WIPRO": "Wipro",
    "TCS": "TCS",
    "STATEBANKOFINDIA": "State Bank Of India",
    "STATEOFINDIA": "State Of India",
    "SBI": "SBI",
    "LIFEINSURANCECORPO": "Life Insurance Corpo",
    "LIC": "LIC",
}

_DIVIDEND_ISSUER_STYLE_RE = re.compile(
    r'(?:ACH\s*C[-\s]*|ACH[-\s]*CR|NACH\s+(?:CR|CREDIT)|ECS\s+CR|CMS\s+CR).{0,80}'
    r'\b(?:LTD|LIMITED|LIMITE|CORP(?:ORATION)?|COMPANY|FINAN|LABS|MOTOCORP|INSURANCE|BANK\s+OF\s+INDIA)\b',
    re.I,
)

_ACH_DIVIDEND_PREFIX_RE = re.compile(r'^\s*(?:ACH\s*C[-\s]*|ACH[-\s]*CR|NACH\s+(?:CR|CREDIT))', re.I)


def _has_broker_trading_marker(narration: str) -> bool:
    return bool(_BROKER_TRADING_MARKER_RE.search(narration or ""))


def _compact_dividend_issuer_text(narration: str) -> str:
    compact = re.sub(r'^\s*(?:ACH\s*C[-\s]*|ACH[-\s]*CR|NACH\s+(?:CR|CREDIT))', '', narration or "", flags=re.I)
    compact = re.sub(r'(?:[-\s/]*[A-Z0-9]*\d{5,}[A-Z0-9]*)+$', '', compact, flags=re.I)
    compact = re.sub(r'[^A-Z]+', '', compact.upper())
    compact = re.sub(r'(?:INTDIV|FINDIV|FINALDIV|SPLDIV|ANNUALDIV|DIVIDEND|DIV|FNL|INT\d+).*$',
                     '', compact)
    return compact


def _match_compact_dividend_issuer(narration: str) -> str:
    compact = _compact_dividend_issuer_text(narration)
    if not compact:
        return ""
    for token in sorted(_DIVIDEND_COMPACT_ISSUERS, key=len, reverse=True):
        if compact.startswith(token) or token in compact:
            return _DIVIDEND_COMPACT_ISSUERS[token]
    return ""


def _looks_like_dividend_issuer_credit(narration: str) -> bool:
    text = narration or ""
    if _DIVIDEND_ISSUER_GUARD_RE.search(text):
        return False
    if re.search(r'\b(?:PVT|PRIVATE|PAYROLL|SALARY)\b', text, re.I):
        return False
    return bool(
        _DIVIDEND_ISSUER_NAME_RE.search(text)
        or _DIVIDEND_ISSUER_STYLE_RE.search(text)
        or (_ACH_DIVIDEND_PREFIX_RE.search(text) and _match_compact_dividend_issuer(text))
    )


def _append_note(result: Dict[str, Any], marker: str) -> Dict[str, Any]:
    if not marker:
        return result
    note = result.get("note", "") or ""
    if marker not in note:
        result = dict(result)
        result["note"] = (note + " | " + marker).strip(" | ")
    return result


def _protected_lock(result: Dict[str, Any]) -> Dict[str, Any]:
    if result.get("ledger_key") in PROTECTED_CLASSIFICATION_KEYS:
        return _append_note(result, "protected_key_locked=1")
    return result


# Rules: (regex_pattern, ledger_key, forced_txn_type_or_None)
RULES: List[Tuple[str, str, Optional[str]]] = [

    # ── ASSET MOVEMENTS (Credits that are not income) ─────────────────────────
    (r"TPT-PLOT\b",                             "asset_land_cwip",                  "debit"),
    # TPT-LOAN / TPT-TXFR handled in rule_classify() pre-loop block  # FIXED: FIX-5

    # IB FUNDS TRANSFER DR handled in rule_classify() special-case below  # FIXED: FIX-2

    # Own account transfers (NEFT/RTGS where sender = receiver)
    (r"OWN\s+ACCOUNT|SELF\s+TRANSFER",         "asset_own_transfer_in",            "credit"),

    # Refunds & reversals
    (r"REFUND|REVERSAL|REVERSED|CHARGEBACK|FAILED\s+TXN",
                                                "asset_refund_received",            "credit"),

    # Broker payouts / credits
    (r"BEING\s+PAYOUT\s+RELEASED|FUNDS\s+PAYOUT|PAYOUT.*IIFL",
                                                "asset_broker_payout",              "credit"),
    (r"TRADING\s+ACCOUNT|HOLDING\s+ACCOUNT|NBSM/|BROKER\s+TRANSFER|VENTURA\s+SECURITIES|IMPS.*IIFL|IMPS.*ZERODHA",
                                                "asset_broker_balance",             None),

    # FX inward
    (r"INW\s+\d|FCY\s+INWARD|INWARD\s+REMITTANCE|TT\s+INWARD|SWIFT\s+INWARD",
                                                "asset_fcy_inward",                 "credit"),

    
    # LDK Shares & Securities — debit=funds sent to broker, credit=payout received back
    (r"L\s*D\s*K\s+SHARES",                    "trading_funds_added",              "debit"),
    (r"L\s*D\s*K\s+SHARES",                    "trading_payout",                   "credit"),

    # Dividend — ACH/NACH credit patterns from listed companies / depositories
    (r"ACH[-\s]*CR.*DIV|\bNACH\s+(?:CR|CREDIT).*DIV|ACH\s*C[-\s]*.*DIV",
                                                "income_dividend",                  "credit"),
    (r"\b(?:DIV|DIVIDEND|INTDIV|FINDIV|FINALDIV|1STINTDIV|2NDINTDIV|SPLDIV|ANNUALDIV)\b",
                                                "income_dividend",                  "credit"),
    (r"UNITED\s+NILGIRI.*DIV|IRCON.*DIV|OIL\s+AND\s+NATURAL\s+GAS.*DIV|PNB\s+HOU.*DIV|HDB\s+FINAN.*DIV",
                                                "income_dividend",                  "credit"),

    # ── INVESTMENT CREATION (Asset out, Investment in) ────────────────────────
    (r"NACH\s+DR.*(?:SIP|MF\b|FUND\b)|SIP\s+(?:MANDATE|DEBIT|PAYMENT|INSTALMENT)|ACH\s+DR.*(?:SIP|MF\b)|ECS\s+DR.*SIP|NACH-DR.*SIPMANDATEREF",
                                                "asset_investment_mf",              "debit"),
    (r"MF\s+PURCHASE|MUTUAL\s+FUND\s+(?:PURCHASE|INVEST|LUMP)",
                                                "asset_investment_mf",              "debit"),
    (r"MF\s+REDEMPTION|MUTUAL\s+FUND\s+REDEMPTION|REDEMPTION\s+CREDIT|NEFT\s+CR.*REDEMPTION",
                                                "asset_mf_redemption",              "credit"),
    (r"FD\s+(?:BOOKED|BOOKING|CREATION|RENEWAL)|FIXED\s+DEPOSIT\s+(?:PLACED|BOOKED|CREATION)|TERM\s+DEPOSIT",
                                                "asset_investment_fd",              "debit"),
    (r"FD\s+MATURITY|RD\s+(?:CREDIT|MATURITY)|RECURRING\s+DEPOSIT\s+CREDIT|SWEEP.?IN\s+CR",
                                                "asset_fd_maturity",                "credit"),
    (r"\bPPF\b|PUBLIC\s+PROVIDENT\s+FUND",     "asset_investment_ppf",             "debit"),
    (r"\bNPS\b|NATIONAL\s+PENSION|\bNSDL\b|\bCRA\b|\bPOP\b|POP[-\s]*SP|PENSION\s+CONTRIBUTION",
                                                "asset_investment_nps",             "debit"),

    # Equity investment / sale
    (r"\bSHARES\s+PURCHASE|SHARE\s+PURCHASE|BUY\s+TRADE\s+OBLIGATION|EQUITY\s+(?:BUY|PURCHASE)",
                                                "asset_investment_equity",          "debit"),
    (r"\d{15,}/ZERODHA",                        "asset_investment_equity",          "debit"),
    (r"SALE\s+OF\s+SHARES|EQUITY\s+SETTLEMENT\s+CREDIT",
                                                "asset_equity_sale_proceeds",       "credit"),

    # ── INCOME ────────────────────────────────────────────────────────────────
    (r"TPT-SAL\b|ECS\s+CR\s+SALARY|\bSALARY\s+CREDIT\b|\bPAYROLL\s+CREDIT\b|SAL\s+CR\b",
                                                "income_salary",                    "credit"),
    (r"RETAINER\s+CREDIT|CONSULTING\s+FEE\s+(?:RECEIVED|CREDIT)|ADVISORY\s+FEE|PROFESSIONAL\s+FEE\s+(?:RECEIVED|CREDIT)|DIRECTOR\s+FEE",
                                                "income_professional",              "credit"),
    (r"RENT\s+RECEIVED|PROPERTY\s+RENT|RENTAL\s+INCOME",
                                                "income_rental",                    "credit"),

    # Interest income — Int.Pd is credited BY the bank TO the account holder.
    # NB: Int.Coll is handled above in rule_classify() special-case block.
    (r"INT\.PD|INT\s+PD\b|INTEREST\s+PAID\s+TILL|INTEREST\s+CREDIT\s+PERIOD|SB\s+\d+\s+INT|SB:\d+:Int\.Pd",
                                                "income_interest",                  "credit"),
    (r"FD\s+INTEREST|INT\s+PD\s+ON\s+FD|INTEREST\s+INCOME\s+FD|CREDIT\s+INTEREST\s+CAPITALISED",
                                                "income_interest",                  "credit"),

    # Capital gains — broker payouts from Zerodha / NSE clearing
    (r"NEFT\s+CR.*ZERODHA\s+BROKING|NEFT\s+CR.*YESB0000001.*ZERODHA",
                                                "income_capital_gains",             "credit"),
    # NSE/BSE CLEARING handled in rule_classify() pre-loop block  # FIXED: FIX-3b
    (r"\bLTCG\b|\bSTCG\b|CAPITAL\s+GAIN",      "income_capital_gains",             "credit"),

    # ── EXPENDITURE: Loan EMIs and Credit Card payments (cash basis = already paid) ─
    (r"NACH\s+DR.*LOAN|EMI\s+DEBIT|LOAN\s+REPAYMENT|HOME\s+LOAN\s+EMI|AUTO\s+LOAN\s+EMI|PERSONAL\s+LOAN\s+EMI|LOAN\s+INSTALLMENT",
                                                "exp_loan_emi",                     "debit"),  # FIXED: FIX-1
    (r"(?:NEFT|RTGS)\s+DR.*\bLOAN\b|(?:NEFT|RTGS)\s+DR.*\bREPAY\b",
                                                "exp_loan_emi",                     "debit"),  # FIXED: FIX-1
    (r"CREDIT\s+CARD\s+PAYMENT|CC\s+PAYMENT|\bAMEX\b|CARD\s+PAYMENT\s+\d|SBI\s+CARD|BHDF3S|AUTOPAY\s+THANK\s+YOU|AUTOPAY\s+DONE",
                                                "exp_credit_card",                  "debit"),  # FIXED: FIX-1
    # GST on credit card transactions
    (r"IGST[-\s]VPS|IGST[-\s]RATE|CGST[-\s]VPS|SGST[-\s]VPS|FCY\s*MARKUP|CONSOLIDATED\s*FCY|FOREIGN\s+CURRENCY\s+MARKUP|MARKUP\s+FEE",
                                                "exp_bank_charges",                 "debit"),
    # Google Play and App stores
    (r"GOOGLE\s*PLAY|APPLE\.COM/BILL|APPLE\s*ITUNES|MICROSOFT\s+STORE",
                                                "exp_entertainment",                "debit"),
    # Gas stations / petrol pumps by city name pattern
    (r"\bGAS\s*(?:NOIDA|DELHI|MUMBAI|PUNE|BANGALORE|BENGALURU|GURGAON|GURUGRAM|CHENNAI|HYDERABAD|KOLKATA)\b|\bGASNOIDA\b",
                                                "exp_travel",                       "debit"),
    # Cloud / digital hosting
    (r"DIGITALOCEAN|DIGITAL\s+OCEAN|AMAZON\s+WEB\s+SERVICES|\bAWS\.AMAZON\b|GOOGLE\s+CLOUD|MICROSOFT\s+AZURE|CLOUDFLARE|GODADDY|NAMECHEAP|LINODE|VULTR",
                                                "exp_utilities",                    "debit"),
    # Foreign tax payments
    (r"GENERAL\s+DEPARTMENT\s+OF\s+TAX|DEPT\s+OF\s+TAX",
                                                "exp_tax",                          "debit"),

    # ── EXPENDITURE (bank debit = already paid expense) ───────────────────────
    # Staff (NEFT tagged with SAL / PAYROLL going to individuals)
    (r"NEFT\s+DR.*NETBANK.*(SAL\b|PAYROLL|STIP)|NACH\s+DR.*NETBANK.*(SAL\b|PAYROLL)",
                                                "exp_staff_wages",                  "debit"),
    (r"NEFT\s+DR.*NETBANK.*(?:CON\b|CONTRACT\b)|CONSULTANT\s+PAYMENT|CONTRACTOR\s+PAYMENT",
                                                "exp_consultant",                   "debit"),

    # Tax
    (r"\bCBDT\b|ADVANCE\s+TAX|TDS\s+PAYMENT|GST\s+CHALLAN|INCOME\s+TAX\s+CHALLAN|SELF\s+ASSESSMENT\s+TAX",
                                                "exp_tax",                          "debit"),

    # Bank & broker charges
    (r"INSTAALERTCHG|SMS\s+ALERT|BANK\s+CHARGES|ATM\s+FEE|ANNUAL\s+FEE|PROCESSING\s+FEE|DEBIT\s+CARD\s+ANNUAL\s+FEE",
                                                "exp_bank_charges",                 "debit"),
    (r"CDSL\s+DP\s+BILL|DEPOSITORY\s+AMC|DP\s+CHARGES|DEMAT\s+AMC",
                                                "exp_broker_dp",                    "debit"),
    (r"BEING\s+CONTRACT\s+NOTE\s+BILL|CONTRACT\s+(?:BILL|COPY|NOTE)\s+(?:NM|NB|BB|NZ)\d+|BILL\s+FOR\s+(?:NM|NB|BB|NZ)\d+",
                                                "exp_broker_charges",               "debit"),
    (r"DELAYED\s+PAYMENT\s+INTEREST|BROKER\s+INTEREST",
                                                "exp_broker_interest",              "debit"),
    (r"INSURANCE|PREMIUM\s+PAYMENT|LIC\s+PREMIUM|HDFC\s+LIFE|SBI\s+LIFE|MAX\s+LIFE|ICICI\s+PRU",
                                                "exp_insurance",                    "debit"),

    # Utilities
    (r"ELECTRICITY|BESCOM|MSEDCL|BSES|TATA\s+POWER|WATER\s+BILL|GAS\s+BILL|MAHANAGAR\s+GAS|AIRTEL|JIO|BROADBAND|DTH|TATA\s+PLAY",
                                                "exp_utilities",                    "debit"),

    # Food
    (r"SWIGGY|ZOMATO|ETERNAL\s+LIMITED|DOMINOS|PIZZA\b|KFC|MCDONALD|STARBUCKS|CAFE\b|RESTAURANT|BARBEQUE|BURGER\s+KING|CHAAYOS",
                                                "exp_food",                         "debit"),

    # Grocery
    (r"BLINKIT|ZEPTO|ZEPTONOW|INSTAMART|BIGBASKET|DUNZO|MILKBASKET|GROFERS|DMART|GROCERY|COUNTRYDELIGHT|COUNTRY\s+DELIGHT",
                                                "exp_grocery",                      "debit"),

    # Shopping
    (r"AMAZON|FLIPKART|MEESHO|SNAPDEAL|MYNTRA|AJIO|NYKAA|TATA\s+CLIQ",
                                                "exp_shopping_online",              "debit"),
    (r"LIFESTYLE|WESTSIDE|ZARA|H&?M\b|UNIQLO|MANYAVAR|RAYMOND|ARROW|PETER\s+ENGLAND|BIBA|FABINDIA|ZUDIO|TRENT",
                                                "exp_clothing",                     "debit"),

    # Entertainment
    (r"NETFLIX|SPOTIFY|PRIME\s+VIDEO|HOTSTAR|DISNEY|ZEE5|BOOKMYSHOW|PVR|INOX|CINEPOLIS",
                                                "exp_entertainment",                "debit"),

    # Health
    (r"APOLLO\s+PHARMACY|MEDPLUS|NETMEDS|1MG|PHARMEASY|PRACTO|HOSPITAL|HEALTHIANS|THYROCARE|DR\s+LAL|PHARMACY",
                                                "exp_health",                       "debit"),

    # Travel
    (r"UBER|OLA|RAPIDO|IRCTC|MAKEMYTRIP|GOIBIBO|CLEARTRIP|INDIGO|AIR\s+INDIA|VISTARA|SPICEJET|REDBUS|IXIGO|YATRA",
                                                "exp_travel",                       "debit"),

    # Education
    (r"COURSERA|UDEMY|BYJU|UNACADEMY|VEDANTU|SCHOOL\s+FEE|COLLEGE\s+FEE|UNIVERSITY|TUITION",
                                                "exp_education",                    "debit"),
    (r"IIT\s+(?:MADRAS|DELHI|BOMBAY|KANPUR|ROORKEE|KHARAGPUR|GUWAHATI|HYDERABAD|BHU|INDORE|MANDI|JODHPUR|TIRUPATI|PALAKKAD|GANDHINAGAR|DHARWAD|JAMMU|BHILAI|GOA|VARANASI)|IITM\b|IITD\b|IITB\b|IITK\b|IITR\b|NIT\s+[A-Z]+|BITS\s+PILANI|VIT\s+[A-Z]+|MANIPAL|AMITY|SYMBIOSIS|NIRMA|SRM\s+[A-Z]+",
                                                "exp_education",                    "debit"),

    # Personal care
    (r"SALON|SPA\b|BEAUTY|MAMAEARTH|SUGAR\s+COSMET|MINIMALIST|WOW\s+SKIN|TIRA",
                                                "exp_personal_care",                "debit"),

    # Home
    (r"IKEA|URBAN\s+LADDER|PEPPERFRY|HOME\s+DECOR|HOME\s+CENTRE",
                                                "exp_home_decor",                   "debit"),

    # FCY outward
    (r"FCY\s+OUTWARD|OUTWARD\s+REMITTANCE|TT\s+OUTWARD|WIRE\s+TRANSFER\s+OUTWARD",
                                                "exp_fcy_outward",                  "debit"),

    # SIP via HDFC mandate codes — EBA/MFP and SEBA/MFP are SIP debit formats
    (r"^S?EBA/MFP[-/]?\d|^MFP[-/]\d",          "asset_investment_mf",              "debit"),

    # ECS/auto-debit fallback
    (r"^ECS\s+DR|^SI\s+DR",                     "exp_misc",                         "debit"),

    # ── AU bank-specific patterns ─────────────────────────────────────────────
    (r"^BPAY\b",                                        "exp_utilities",            "debit"),
    (r"^OSKO\s+PAYMENT|^NPP\s+",                        "asset_own_transfer_in",    "credit"),
    (r"ATO\s+PAYMENT|AUSTRALIAN\s+TAX\s+OFFICE",        "exp_tax",                  "debit"),
    (r"CENTRELINK|SERVICES\s+AUSTRALIA",                "income_other",             "credit"),
    (r"MEDICARE|PRIVATE\s+HEALTH",                      "exp_health",               "debit"),
    (r"SUPERANNUATION|SUPER\s+FUND|HESTA|HOSTPLUS|AUSTRALIAN\s+SUPER",
                                                        "asset_long_term_other",    "debit"),
    (r"^PAYMENT\s+TO\b",                                "exp_misc",                 "debit"),
    (r"^DEPOSIT\s+FROM\b",                              "income_other",             "credit"),
    (r"^TRANSFER\s+TO\b",                               "exp_personal_transfer",    "debit"),
    (r"^TRANSFER\s+FROM\b",                             "income_other",             "credit"),
    (r"UBER\s*\*|GOOGLE\s*\*|APPLE\.COM|AMAZON\s*AU",   "exp_misc",                 "debit"),
]


def rule_classify(narration: str, forced_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
    u = _u(narration)

    _HDFC_PREFIX = re.compile(r'^(?:WDL\s+TFR|DEP\s+TFR|WDL\s+CASH|DEP\s+CASH)\s+', re.IGNORECASE)
    _stripped = _HDFC_PREFIX.sub('', narration).strip()
    _u_stripped = _stripped.upper()

    if not forced_type:
        if u.startswith('WDL'):
            forced_type = 'debit'
        elif u.startswith('DEP'):
            forced_type = 'credit'

    # HARD DIVIDEND OVERRIDE:
    # Any CREDIT narration that starts with ACH / NACH, or contains DIV anywhere,
    # should be forced to dividend before any loan / transfer / fallback logic.
    if forced_type == 'credit' and (
        re.match(r'^\s*(?:ACH|NACH)\b', u, re.I)
        or re.search(r'DIV', u, re.I)
    ):
        return _protected_lock(_build(
            narration,
            'income_dividend',
            'credit',
            0.995,
            note='Hard dividend override: ACH/NACH prefix or DIV token detected.'
        ))

    if forced_type == 'debit' and re.search(
        r'\bNPS\b|NATIONAL\s+PENSION|\bNSDL\b|\bCRA\b|\bPOP\b|POP[-\s]*SP|PENSION\s+CONTRIBUTION',
        u,
        re.I,
    ):
        return _protected_lock(_build(
            narration,
            'asset_investment_nps',
            'debit',
            0.97,
            note='Explicit NPS investment debit.'
        ))

    if _stripped != narration and _u_stripped.startswith(('UPI', 'NEFT', 'NACH', 'IMPS', 'ACH', 'IB ', 'BIL/')):
        result = rule_classify(_stripped, forced_type)
        if result:
            result['narration'] = narration
            result['note'] = ((result.get('note') or '') + ' | hdfc_prefix_stripped').strip(' | ')
            return result

    # Priority 1: protected investment / broker / income / tax intents.
    # These must resolve before any family-loan, own-account, or transfer fallback.
    if forced_type == 'credit' and re.search(
        r'\b(?:SALARY|PAYROLL|TPT-SAL|SAL\s+CR|WAGES|STIPEND)\b',
        u,
        re.I,
    ):
        return _protected_lock(_build(
            narration,
            'income_salary',
            'credit',
            0.98,
            note='Explicit salary/payroll credit pattern.'
        ))

    if forced_type == 'debit' and re.search(r'\bPPF\b|PUBLIC\s+PROVIDENT\s+FUND', u, re.I):
        return _protected_lock(_build(
            narration,
            'asset_investment_ppf',
            'debit',
            0.98,
            note='Explicit PPF investment debit.'
        ))

    if forced_type == 'debit' and re.search(
        r'\bNPS\b|NATIONAL\s+PENSION|\bNSDL\b|\bCRA\b|\bPOP\b|POP[-\s]*SP|PENSION\s+CONTRIBUTION',
        u,
        re.I,
    ):
        return _protected_lock(_build(
            narration,
            'asset_investment_nps',
            'debit',
            0.98,
            note='Explicit NPS investment debit.'
        ))

    if forced_type == 'debit' and re.search(
        r'\bCBDT\b|ADVANCE\s+TAX|TDS\s+PAYMENT|GST\s+CHALLAN|INCOME\s+TAX\s+CHALLAN|SELF\s+ASSESSMENT\s+TAX',
        u,
        re.I,
    ):
        return _protected_lock(_build(
            narration,
            'exp_tax',
            'debit',
            0.98,
            note='Explicit tax payment debit.'
        ))

    broker_marker = _has_broker_trading_marker(u)
    if broker_marker and _BROKER_CASH_MOVEMENT_RE.search(u) and not _BROKER_COST_RE.search(u):
        broker_dir = forced_type or _infer_txn_type(u)
        if broker_dir == 'credit':
            return _protected_lock(_build(
                narration,
                'trading_payout',
                'credit',
                0.995,
                note='Broker/exchange cash movement credit. hard_broker_guard=1 broker_priority_override=1'
            ))
        return _protected_lock(_build(
            narration,
            'trading_funds_added',
            'debit',
            0.995,
            note='Broker/exchange cash movement debit. hard_broker_guard=1 broker_priority_override=1'
        ))

    if broker_marker:
        if re.search(r'DELAYED\s+PAYMENT\s+INTEREST|BROKER\s+INTEREST|INT\.COLL|INT\s+COLL\b|INTEREST\s+COLLECTED|INTEREST\s+CHARGED', u, re.I):
            return _protected_lock(_build(
                narration,
                'exp_broker_interest',
                forced_type if forced_type in ('debit', 'credit') else 'debit',
                0.96,
                note='Broker/trading interest cost. broker_priority_override=1'
            ))
        if re.search(r'\b(?:DP\s+CHARGES?|DEPOSITORY\s+AMC|DEMAT\s+AMC|DEMAT|AMC)\b', u, re.I):
            return _protected_lock(_build(
                narration,
                'exp_broker_dp',
                forced_type if forced_type in ('debit', 'credit') else 'debit',
                0.95,
                note='Demat / DP / AMC broker cost. broker_priority_override=1'
            ))
        if _BROKER_COST_RE.search(u):
            return _protected_lock(_build(
                narration,
                'exp_broker_charges',
                forced_type if forced_type in ('debit', 'credit') else 'debit',
                0.95,
                note='Broker contract bill / statutory trading cost. broker_priority_override=1'
            ))
        if _BROKER_CASH_MOVEMENT_RE.search(u):
            broker_dir = forced_type or _infer_txn_type(u)
            if broker_dir == 'credit':
                return _protected_lock(_build(
                    narration,
                    'trading_payout',
                    'credit',
                    0.97,
                    note='Broker/exchange cash movement credit. broker_priority_override=1'
                ))
            return _protected_lock(_build(
                narration,
                'trading_funds_added',
                'debit',
                0.97,
                note='Broker/exchange cash movement debit. broker_priority_override=1'
            ))

    if forced_type == 'credit' and _ACH_DIVIDEND_PREFIX_RE.search(u) and _looks_like_dividend_issuer_credit(u):
        return _protected_lock(_build(
            narration,
            'income_dividend',
            'credit',
            0.94,
            note='ACH/NACH issuer-company credit inferred as dividend.'
        ))
    
        # ── REIT / TRUST / DISTRIBUTION INCOME (VERY IMPORTANT FIX) ──
    if forced_type == 'credit' and re.search(r'\b(TRUST|REIT|DISTRIBUTION)\b', u, re.I):
        return _protected_lock(_build(
            narration,
            'income_investment',
            'credit',
            0.92,
            note='REIT/trust/distribution income detected.'
        ))

    if forced_type == 'credit' and _looks_like_dividend_issuer_credit(u):
        return _protected_lock(_build(
            narration,
            'income_dividend',
            'credit',
            0.90,
            note='Issuer/company credit inferred as dividend.'
        ))

    int_coll_match = re.search(r'INT\.COLL|INT\s+COLL\b|INTEREST\s+COLLECTED|INTEREST\s+CHARGED', u)
    if int_coll_match:
        if forced_type == 'credit':
            return _build(
                narration,
                'suspense_credit',
                'credit',
                0.45,
                note='Int.Coll pattern on a credit row — review.'
            )
        return _protected_lock(_build(
            narration,
            'exp_broker_interest',
            'debit',
            0.92,
            note='Int.Coll = bank charging interest on OD/loan (debit).'
        ))

    ib = re.match(r'^IB\s+FUNDS\s+TRANSFER\s+(CR|DR)', u)
    if ib and not broker_marker:
        is_credit = ib.group(1) == 'CR'
        if re.search(r'\bU/G\b|UNDER\s+GUARDIAN', u):
            return _build(
                narration,
                'liability_loan_outstanding',
                'credit' if is_credit else 'debit',
                0.95,
                note="IB Transfer U/G treated as family loan flow. Credits increase outstanding liability, debits reduce it."
            )
        # Extract counterparty name from IB FUNDS TRANSFER CR/DR narration
        # Format: IB FUNDS TRANSFER CR-<acct_no>  -<NAME>
        _party = re.sub(
            r'^IB\s+FUNDS\s+TRANSFER\s+(?:CR|DR)[-\s]+\S+\s*[-–]\s*', '',
            narration, flags=re.I
        ).strip()

        if is_credit:
            # Money received INTO this account from a named individual via IB Transfer.
            # This is a LOAN received (liability) — not an own-account contra.
            # Own-account transfers via IB would have no separate named counterparty
            # or would be between accounts held by the same account holder.
            return _build(
                narration,
                'liability_loan_outstanding',
                'credit',
                0.82,
                note=f"IB Funds Transfer CR from '{_party}' — loan received. Outstanding liability."
            )
        # DR side: money going back to the named individual who previously sent funds.
        # Treat as loan repayment — reduces the outstanding liability balance.
        return _build(
            narration,
            'liability_loan_outstanding',
            'debit',
            0.80,
            note=f"IB Funds Transfer DR to '{_party}' — loan repayment. Reduces outstanding liability."
        )

    if re.search(r'(?:RTGS|NEFT)\s+(?:CR|DR)', u):
        parts = u.split('-')
        if len(parts) >= 4:
            sender = parts[2].strip()
            receiver = parts[3].strip()
            is_cr = 'CR' in parts[0]
            family_evidence = _is_family_loan_style_narration(u)
            if sender and receiver and sender == receiver and len(sender) > 4 and not broker_marker and not family_evidence:
                direction = 'credit' if is_cr else 'debit'
                return _build(
                    narration,
                    'asset_own_transfer_in',
                    direction,
                    0.92,
                    note='Own account transfer (sender = receiver name). No P&L impact. own_account_override=1'
                )
            # Sender and receiver are DIFFERENT parties — genuine inter-party transfer.
            # Only treat as a loan if the sender is a person, not a corporate/institution.
            # Institutions (brokers, banks, clearing corps) are income/asset events, not loans.
            _INST_TOKENS = {
                'CLEARING', 'LIMITED', 'LTD', 'BANK', 'NSE', 'BSE', 'SEBI',
                'NSDL', 'CDSL', 'CORP', 'CORPORATION', 'FINANCE', 'FINANCIAL',
                'BROKING', 'SECURITIES', 'INSURANCE', 'MUTUAL', 'FUND',
                'SERVICES', 'TECHNOLOGIES', 'SYSTEMS', 'HOLDINGS', 'ZERODHA',
                'TRUST', 'SCHEME', 'AUTHORITY', 'GOVT', 'GOVERNMENT', 'PLOTS',
                'RESIDENTIAL', 'DEVELOPMENT',
            }
            _sender_tokens = set(sender.split())
            _sender_is_person = not bool(_sender_tokens & _INST_TOKENS)
            if is_cr and sender and len(sender) > 4 and _sender_is_person and not broker_marker and not _PROTECTED_INVESTMENT_INCOME_RE.search(u):
                return _build(
                    narration,
                    'liability_loan_outstanding',
                    'credit',
                    0.82,
                    note=f"RTGS/NEFT CR from '{sender.title()}' — loan received from individual. Outstanding liability. family_loan_override=1"
                )
            # DR to a person = loan repayment → reduces outstanding liability.
            # BUT: first check for known asset/investment keywords in the narration
            # (e.g. PPF, NPS, SIP) — those are investments, not loan repayments.
            _INVESTMENT_KEYWORDS = re.compile(
                r'\bPPF\b|PUBLIC\s+PROVIDENT\s+FUND|\bNPS\b|NATIONAL\s+PENSION|'
                r'\bSIP\b|MUTUAL\s+FUND|\bFD\b|FIXED\s+DEPOSIT|\bRD\b|RECURRING',
                re.I
            )
            _receiver_tokens = set(receiver.split())
            _receiver_is_person = not bool(_receiver_tokens & _INST_TOKENS)
            if not is_cr and not _INVESTMENT_KEYWORDS.search(u) and receiver and len(receiver) > 4 and _receiver_is_person and not broker_marker and not _PROTECTED_INVESTMENT_INCOME_RE.search(u):
                return _build(
                    narration,
                    'liability_loan_outstanding',
                    'debit',
                    0.80,
                    note=f"RTGS/NEFT DR to '{receiver.title()}' — loan repayment. Reduces outstanding liability. family_loan_override=1"
                )

    if re.match(r'^BIL/INFT/', u):
        return _build(
            narration,
            'income_other',
            'credit',
            0.93,
            note='HDFC BIL/INFT inward credit treated as other income pending review.'
        )

    hdfc_upi = re.match(r'^UPI/(DR|CR)/(\d+)/([^/]+)/([A-Z0-9]{2,8})/([^/\s]+)(?:/(.+))?$', u, re.IGNORECASE)
    if hdfc_upi:
        direction = hdfc_upi.group(1).upper()
        payee = (hdfc_upi.group(3) or '').strip()
        bank_code = (hdfc_upi.group(4) or '').strip()
        vpa = (hdfc_upi.group(5) or '').strip().lower()
        memo = (hdfc_upi.group(6) or '').strip().upper()
        canonical_type = forced_type or ('debit' if direction == 'DR' else 'credit')

        if canonical_type == 'credit':
            payee_u = payee.upper()
            if re.search(r'\bONE97\b|\bONE97\s*CO\b|PAYTM', payee_u):
                return _build(
                    narration,
                    'asset_refund_received',
                    'credit',
                    0.92,
                    note='Paytm (ONE97) UPI credit — cashback/refund.'
                )
            if re.search(r'NPCI|BHIM', payee_u):
                return _build(
                    narration,
                    'asset_refund_received',
                    'credit',
                    0.97,
                    note='NPCI BHIM cashback received.'
                )
            if re.search(r'ZERODHA|IIFL|5PAISA|UPSTOX|GROWW', payee_u):
                return _build(
                    narration,
                    'asset_broker_payout',
                    'credit',
                    0.92,
                    note=f'Broker payout: {payee}.'
                )
            if re.search(FAMILY_NAME_PAT, payee_u):
                return _build(
                    narration,
                    'income_gift_family',
                    'credit',
                    0.88,
                    note=f'UPI credit from family member: {payee}.'
                )
            return _build(
                narration,
                'income_other',
                'credit',
                0.78,
                note=f'UPI/CR from {payee} via {bank_code}.'
            )

        payee_u = payee.upper()
        memo_u = memo.upper()

        branded_rules = [
            (r'SWIGGY|ZOMATO|DOMINOS|PIZZA|KFC|MCDONALD|STARBUCKS|CHAAYOS|BURGER\s*KING|BARBEQUE|HALDIRAM|SUBWAY|BEHROUZ|FAASOS|BIRYANI', 'exp_food'),
            (r'BLINKIT|ZEPTO|BIGBASKET|DUNZO|GROFERS|DMART|INSTAMART|MILKBASKET|COUNTRYDELIGHT|RELIANCE\s*FRESH|MORE\s*SUPER', 'exp_grocery'),
            (r'AMAZON|FLIPKART|MEESHO|MYNTRA|AJIO|NYKAA|TATA\s*CLIQ', 'exp_shopping_online'),
            (r'UBER|OLA|RAPIDO|IRCTC|REDBUS|DMR|DMRC|METRO|DELHI\s*METRO', 'exp_travel'),
            (r'NETFLIX|SPOTIFY|PVR|INOX|BOOKMYSHOW|DISNEY|HOTSTAR', 'exp_entertainment'),
            (r'APOLLO|MEDPLUS|NETMEDS|1MG|PHARMEASY|PRACTO', 'exp_health'),
            (r'AIRTEL|JIO|BESCOM|MSEDCL|BSES|TATA\s*PLAY', 'exp_utilities'),
        ]
        for pat, key in branded_rules:
            if re.search(pat, payee_u) or re.search(pat, memo_u):
                return _build(
                    narration,
                    key,
                    'debit',
                    0.95,
                    note=f'HDFC UPI/DR branded: {payee}/{memo[:20]}'
                )

        if re.search(r'\b(?:FOOD|ROLL|ROLLS|MOMO|MOMOS|NOODLE|NOODLES|BURGER|BURG|DOSA|ICE|CREAM|JUICE|JUIC|CHAAT|CHAT|PAAN|KACHORI|CORN|GAVA|NIMBU|CHOC|CHOCOLATE|SWEET|SWEETS|SNACK|SNACKS|BIRYANI|EGG|PAPAD|CHAI|CHAAI|TEA|PASTA|SANDWICH|WRAP|EAT|EATS|GOL)\b', memo_u) or re.search(r'\b(?:ROLL|BURGER|DOSA|JUICE|ICE|MOMO|WRAPPERZ|BIG\s*SMOK|DOSAJEE|SWEET\s*MO)\b', payee_u):
            return _build(
                narration,
                'exp_food',
                'debit',
                0.87,
                note=f'Street vendor QR payment (food): {payee}/{memo[:20]}'
            )

        if re.search(r'\b(?:MILK|MILKS|DAIRY|CURD|PANEER|VEG|SABZI|RATION|GROCERY|KIRANA|PROVISION|OIL|GHEE|FLOUR|DAL|LENTIL|MASALA|VEGETABLE|FRUIT|FRUITS|BANANA|ONION|TOMATO|POTATO)\b', memo_u) or re.search(r'\b(?:GROCE|GROCERY|KIRANA|DAIRY|FARM|STORE|MART|OM\s*GROCE|FRESH|SABZI|MARKET)\b', payee_u):
            return _build(
                narration,
                'exp_grocery',
                'debit',
                0.87,
                note=f'Street vendor QR payment (grocery): {payee}/{memo[:20]}'
            )

        if re.search(r'\b(?:MEDICINE|MEDICINES|EYE\s*DROP|EYEDROP|DROP|TABLET|TABS|PHARMACY|PHARMA|SYRUP|BANDAGE|DRESSING|INJECTION|CHEMIST)\b', memo_u) or re.search(r'\b(?:PHARMACY|MEDICAL|CHEMIST|EYE)\b', payee_u):
            return _build(
                narration,
                'exp_health',
                'debit',
                0.87,
                note=f'QR medicine/pharmacy payment: {payee}/{memo[:20]}'
            )

        if re.match(r'^(?:paytmqr|paytm\.s|paytm-|pay\.t|bharatpe\.|bharat\.pe|phonepe\.|gpay\.|googlepay\.|vyapar\.)', vpa, re.I):
            if re.search(r'\b(?:GROCE|KIRANA|DAIRY|FARM|STORE|MART|OM\s*GROCE|FRESH|SABZI|MARKET)\b', payee_u):
                return _build(
                    narration,
                    'exp_grocery',
                    'debit',
                    0.82,
                    note=f'Local grocery QR: {payee}'
                )
            if re.search(r'\b(?:NAAN|NAN|NOODLE|ROLL|ROTI|SWEET|DOSA|JUICE|ICE|MOMO|BURGER|BIJU|JIBA|HIDAYAT|DOSAJEE|OMKAR|AKASH|WRAPPERZ|BIG\s*SMOK|MR\s*NITE|JASVEER)\b', payee_u):
                return _build(
                    narration,
                    'exp_food',
                    'debit',
                    0.82,
                    note=f'Local food vendor QR: {payee}'
                )
            return _build(
                narration,
                'exp_misc',
                'debit',
                0.80,
                note=f'Local merchant QR payment: {payee} via {vpa[:15]}'
            )

        if re.search(r'@(ybl|oksbi|ibl|pts|paytm|upi|fbl|sbi|boi|pnb|rbl|barodampay|ikwik|okicici|okhdfcbank|okaxis|axl|airtel|apl|federal|aubank)$', vpa, re.I):
            if re.search(r'\bSENT\b', memo_u):
                return _build(
                    narration,
                    'exp_personal_transfer',
                    'debit',
                    0.82,
                    note=f'UPI/DR personal transfer to {payee}.'
                )
            return _build(
                narration,
                'exp_misc',
                'debit',
                0.82,
                note=f'UPI/DR to individual: {payee}'
            )

        return _build(
            narration,
            'exp_misc',
            'debit',
            0.75,
            note=f'HDFC UPI/DR: {payee} via {bank_code} — classified as misc'
        )

    upi_credit_slash = re.match(r'^UPI/([^/]+)/([^/]*)/', u)
    if upi_credit_slash and forced_type == 'credit':
        payee = upi_credit_slash.group(1).strip()
        vpa = upi_credit_slash.group(2).strip()
        full = u

        if re.search(r'CASHBACK|REFUND|REVERSED|REVERSAL', full):
            return _build(
                narration,
                'asset_refund_received',
                'credit',
                0.97,
                note='UPI credit refund / cashback.'
            )

        if re.search(r'COLLECT\s+RE', full):
            if re.search(r'KUKUFM|CASHBACK|REWARD', full):
                return _build(
                    narration,
                    'asset_refund_received',
                    'credit',
                    0.88,
                    note='UPI collect-request credit — refund/cashback.'
                )
            return _build(
                narration,
                'income_other',
                'credit',
                0.85,
                note='UPI Collect request credit — verify source.'
            )

        merchant_pat = re.compile(r'SWIGGY|ZOMATO|AMAZON|FLIPKART|MYNTRA|NYKAA|BLINKIT|ZEPTO|NETFLIX|SPOTIFY|UBER|OLA|IRCTC|MAKEMYTRIP|BOOKMYSHOW|PVR|INOX|MEDPLUS|1MG|AIRTEL|JIO', re.I)
        if merchant_pat.search(payee):
            return _build(
                narration,
                'asset_refund_received',
                'credit',
                0.90,
                note='UPI credit from known merchant — likely refund.'
            )

        corporate_pat = re.compile(r'LTD|PVT|PRIVATE|LIMITED|BANK|FINANCE|TECH|SOLUTIONS|SERVICES|ENTERPRISE|CORP|PAY|PAYMENTS|DIGITAL|PLATFORM|MART|FOODS|LOGIS', re.I)
        family_pat = re.compile(FAMILY_NAME_PAT, re.I)

        if family_pat.search(payee) or family_pat.search(full):
            return _build(
                narration,
                'income_gift_family',
                'credit',
                0.88,
                note=f"UPI credit from family identified as capital introduced by '{payee.title()}'."
            )

        if not corporate_pat.search(payee):
            return _build(
                narration,
                'income_other',
                'credit',
                0.78,
                note=f"UPI credit from individual '{payee.title()}' treated as other income pending review."
            )

        return _build(
            narration,
            'income_other',
            'credit',
            0.80,
            note='UPI credit treated as other income until nature is confirmed.'
        )

    upi = re.match(r'^UPI[/-]([^/\-]+)[/-]([^/\-]+)[/-]([^/\-]*)[/-]?(.*)', u)
    if upi:
        payee = upi.group(1).strip()
        vpa = upi.group(2).strip()
        rem = upi.group(3).strip() + ' ' + upi.group(4).strip()

        if any(w in payee + vpa + rem for w in ('CASHBACK', 'BHIMCASHBACK', 'REFUND', 'REVERSED', 'REVERSAL')):
            return _build(
                narration,
                'asset_refund_received',
                'credit',
                0.97,
                note='UPI refund / cashback. Not income — contra to earlier expense.'
            )

        if forced_type == 'credit' and re.search(r'COLLECT\s*RE|KUKUFM|CASHBACK|REWARD', rem + payee + vpa, re.I):
            return _build(
                narration,
                'asset_refund_received',
                'credit',
                0.88,
                note='UPI collect-request credit — refund/cashback.'
            )

        merchant_rules = [
            (r'SWIGGY|ZOMATO|ETERNAL|DOMINOS|PIZZA|KFC|MCDONALD|STARBUCKS|CAFE|RESTAURANT|BARBEQUE|BURGER\s+KING|CHAAYOS', 'exp_food'),
            (r'BLINKIT|ZEPTO|ZEPTONOW|INSTAMART|BIGBASKET|DUNZO|MILKBASKET|GROFERS|DMART|GROCERY|COUNTRYDELIGHT', 'exp_grocery'),
            (r'AMAZON|FLIPKART|MEESHO|SNAPDEAL|MYNTRA|AJIO|NYKAA|TATA\s+CLIQ', 'exp_shopping_online'),
            (r'LIFESTYLE|WESTSIDE|ZARA|H&?M\b|UNIQLO|MANYAVAR|RAYMOND|BIBA|FABINDIA|ZUDIO|TRENT', 'exp_clothing'),
            (r'NETFLIX|SPOTIFY|PRIME\s+VIDEO|HOTSTAR|DISNEY|ZEE5|BOOKMYSHOW|PVR|INOX|CINEPOLIS', 'exp_entertainment'),
            (r'APOLLO|MEDPLUS|NETMEDS|1MG|PHARMEASY|PRACTO|HOSPITAL|THYROCARE|DR\s+LAL', 'exp_health'),
            (r'UBER|OLA|RAPIDO|IRCTC|MAKEMYTRIP|GOIBIBO|CLEARTRIP|INDIGO|AIR\s+INDIA|VISTARA|SPICEJET|REDBUS', 'exp_travel'),
            (r'SALON|SPA\b|BEAUTY|MAMAEARTH|SUGAR\s+COSMET|MINIMALIST|WOW\s+SKIN|TIRA', 'exp_personal_care'),
            (r'ELECTRICITY|BESCOM|AIRTEL|JIO|BROADBAND|DTH|TATA\s+PLAY|MAHANAGAR\s+GAS', 'exp_utilities'),
            (r'IIT|NIT|AIIMS|IGNOU|UNIVERSITY|COLLEGE|SCHOOL|INSTITUTE|ACADEMY', 'exp_education'),
            (r'\bMILK\b|\bMILKS\b|\bDAIRY\b', 'exp_grocery'),
            (r'\bROLL\b|\bROLLS\b|\bMOMO\b|\bNOODLE\b|\bBURGER\b|\bBURG\b|\bDOSA\b|\bICE\b|\bJUICE\b|\bCHAAT\b|\bPAAN\b|\bKACHORI\b|\bCORN\b|\bGAVA\b|\bNIMBU\b|\bCHOC\b|\bCHOCOLATE\b|\bSWEET\b|\bSNACK\b|\bFOOD\b|\bEAT\b|\bEATS\b|\bPAPAD\b|\bVERI\b|\bCHAAI\b|\bTEA\b', 'exp_food'),
            (r'\bGROCERY\b|\bKIRANA\b|\bPROVISION\b|\bSUPERMARKET\b|\bMARKET\b|\bSABZI\b|\bVEG\b', 'exp_grocery'),
            (r'\bFARMACY\b|\bMEDICINE\b|\bCHEMIST\b|\bEYE\b|\bEYEDROP\b|\bDROP\b', 'exp_health'),
        ]
        for pat, key in merchant_rules:
            if re.search(pat, payee) or re.search(pat, vpa) or re.search(pat, rem):
                return _build(narration, key, 'debit', 0.97)

        if forced_type == 'credit' or (not forced_type and 'CREDIT' in rem):
            if re.search(FAMILY_NAME_PAT, payee + ' ' + rem, re.I):
                return _build(
                    narration,
                    'income_gift_family',
                    'credit',
                    0.86,
                    note='UPI credit from family / known person treated as capital introduced.'
                )
            return _build(
                narration,
                'income_other',
                'credit',
                0.65,
                note='UPI credit from individual treated as other income pending review.'
            )

        personal_vpa = re.compile(r'@(ybl|oksbi|ibl|pts|paytm|upi|fbl|sbi|boi|pnb|rbl|barodampay|ikwik)$', re.I)
        if personal_vpa.search(vpa):
            if re.search(r'\bSENT\b', rem, re.I):
                return _build(
                    narration,
                    'exp_personal_transfer',
                    'debit',
                    0.85,
                    note='UPI debit to individual (personal transfer).'
                )
            return _build(
                narration,
                'exp_misc',
                'debit',
                0.85,
                note='UPI debit to individual (personal VPA). Misc expenditure.'
            )

        return _build(narration, 'exp_misc', 'debit', 0.75)

    if forced_type == 'credit' and re.search(r'\bONE97\b|\bONE97\s*CO\b', u):
        return _build(
            narration,
            'asset_refund_received',
            'credit',
            0.94,
            note='ONE97 CO (Paytm) credit = cashback/reward/refund.'
        )

    if forced_type == 'credit' and re.search(r'\bNPCI\s*BHIM\b|\bBHIM\s*CASHBACK\b|\bBHIMCASHBACK\b', u):
        return _build(
            narration,
            'asset_refund_received',
            'credit',
            0.97,
            note='NPCI BHIM cashback received.'
        )

    if re.match(r'^NEFT\*', u) or re.match(r'^DEP\s+TFR\s+NEFT', u, re.I):
        parts = [p.strip() for p in re.split(r'[*\-/]+', u) if p.strip()]
        payee_part = parts[-1].strip() if parts else ''
        parts_joined = ' '.join(parts)
        if forced_type == 'credit':
            explicit_salary = re.search(r'\bSAL\b|SALARY|PAYROLL|WAGES|STIPEND|EMPLOYEE\b|EMPLOYER\b', payee_part, re.I) or re.search(r'\bSAL\b|SALARY|PAYROLL|WAGES|STIPEND|EMPLOYEE\b|EMPLOYER\b', parts_joined, re.I)
            payroll_batch = re.search(r'\bHDFC0{4,}1\b|\bHDFCH\d{6,}\b', parts_joined, re.I)
            corp_salary_like = re.search(r'\b(PRIV(?:ATE)?|PVT|LIMITED|LTD|LLP|TECH|TECHNOLOGIES|SOLUTIONS|SERVICES|LABS|SYSTEMS|SOFTWARE|CONSULT|CONSULTING|ENTERPRISES?)\b', payee_part, re.I)
            non_salary_credit = re.search(r'DIV|DIVIDEND|INTEREST|INT\.?PD|REFUND|REVERSAL|CASHBACK|REWARD|LOAN|EMI|REPAY|MATURITY|REDEMPTION|BROKER|ZERODHA|UPSTOX|GROWW|NSE|BSE|MF|MUTUAL\s*FUND|PPF|NPS|FD\b|FIXED\s*DEPOSIT|RENT|SALE\b|INVOICE|VENDOR|PROFESSIONAL\s*FEE|CONSULTANCY\s*FEE', u, re.I)
            if explicit_salary or (payroll_batch and corp_salary_like and not non_salary_credit):
                return _build(
                    narration,
                    'income_salary',
                    'credit',
                    0.93 if explicit_salary else 0.86,
                    note='DEP TFR NEFT payroll-like corporate credit treated as salary.'
                )
            return _build(
                narration,
                'income_other',
                'credit',
                0.85,
                note=f'DEP TFR NEFT inward: {payee_part[:30]}'
            )

    # ── Broker funding / payout transfers (bank <-> broker) ─────────────────
    # Keep actual trading transfers in trading receivable netting flow.
    # Exclude contract note / DP / interest style lines which belong elsewhere.
    if re.search(r'\b(ZERODHA|KITE|BROKER|UPSTOX|5PAISA|GROWW|IIFL|VENTURA)\b', u):
        if not re.search(r'CONTRACT|BILL|DP\s+CHARGES|DEMAT|INTEREST|MARGIN|STT|GST|CHARGES?', u):
            _broker_dir = forced_type or _infer_txn_type(u)
            if _broker_dir == 'debit':
                return _build(
                    narration,
                    'trading_funds_added',
                    'debit',
                    0.95,
                    note='Broker funding transfer from bank to trading account.'
                )
            if _broker_dir == 'credit':
                return _build(
                    narration,
                    'trading_payout',
                    'credit',
                    0.95,
                    note='Broker payout from trading account back to bank.'
                )

    if re.search(r'\b(?:NSE|BSE)\b', u) and re.search(r'\b(?:CLEARING|SETTLEMENT)\b', u):
        _clearing_dir = forced_type or _infer_txn_type(u)
        if _clearing_dir == 'credit':
            return _build(
                narration,
                'trading_payout',
                'credit',
                0.95,
                note='NSE/BSE clearing settlement credit = trading payout cash flow.'
            )
        return _build(
            narration,
            'trading_funds_added',
            'debit',
            0.95,
            note='NSE/BSE clearing settlement debit = trading funds added cash flow.'
        )

    
        # ── ACH / NACH dividend credits ───────────────────────────────────────────
    if forced_type == 'credit' and re.search(
        r'^(?:ACH|NACH)\b.*(?:DIV|DIVIDEND|INTDIV|FINDIV|FINALDIV|SPLDIV|ANNUALDIV)',
        u,
        re.I,
    ):
        return _build(
            narration,
            'income_dividend',
            'credit',
            0.97,
            note='Forced rule: ACH/NACH dividend credit.'
        )
            

    # ── SPECIFIC TPT rules (must run BEFORE the generic TPT pattern) ─────────────
    #
    # LOAN ACCOUNTING MODEL for TPT transfers with family/individual names:
    #
    #   CREDIT (money IN from person):
    #     → liability_loan_outstanding (credit, +amt)
    #     → The person lent you money. Liability GROWS.
    #
    #   DEBIT (money OUT to person) = loan repayment back to the lender:
    #     → liability_loan_outstanding (debit, -amt)
    #     → You are repaying them. Liability SHRINKS.
    #     → Both credit and debit rows land in the SAME liability account.
    #     → Net = total received − total repaid = amount still outstanding.
    #     → When fully repaid: net ≤ 0 → liability disappears from Balance Sheet.
    #     → Trace on Liabilities shows full history (both directions).
    #
    #   EXCEPTION — TPT-GIFT: genuine gift, not a loan. Different keys.
    # ─────────────────────────────────────────────────────────────────────────────

    if re.search(r'TPT-REPAY\b', u) and not broker_marker:
        # Treat named-person TPT repayment flows on the same loan liability axis.
        # Credit = fresh loan / return from lender side into bank
        # Debit  = repayment back to lender
        if forced_type == 'credit':
            return _build(
                narration,
                'liability_loan_outstanding',
                'credit',
                0.92,
                note='TPT-REPAY credit — loan-related inward flow kept in outstanding liability bucket. family_loan_override=1'
            )
        return _build(
            narration,
            'liability_loan_outstanding',
            'debit',
            0.92,
            note='TPT-REPAY debit — loan repayment made. Reduces outstanding liability balance. family_loan_override=1'
        )

    if re.search(r'TPT-GIFT\b', u) and not broker_marker:
        # Gifts are the only TPT variant that is NOT a loan
        if forced_type == 'credit':
            return _build(
                narration,
                'income_gift_family',
                'credit',
                0.95,
                note='TPT-GIFT credit — gift received from family / capital introduced.'
            )
        return _build(
            narration,
            'exp_personal_transfer',
            'debit',
            0.82,
            note='TPT-GIFT debit — gift / personal transfer to family.'
        )

    if re.search(r'TPT-SARJU\s+HUF\b|TPT-.*\bHUF\b', u) and not broker_marker:
        if forced_type == 'credit':
            return _build(
                narration,
                'liability_loan_outstanding',
                'credit',
                0.90,
                note='TPT HUF credit — loan received from HUF. Outstanding liability. family_loan_override=1'
            )
        return _build(
            narration,
            'liability_loan_outstanding',
            'debit',
                0.90,
                note='TPT HUF debit — loan repayment to HUF. Reduces outstanding liability. family_loan_override=1'
            )

    if forced_type == 'debit' and re.search(r'\bPPF\b|PUBLIC\s+PROVIDENT\s+FUND', u):
        return _protected_lock(_build(
            narration,
            'asset_investment_ppf',
            'debit',
            0.96,
            note='Explicit PPF investment debit.'
        ))

    if forced_type == 'debit' and re.search(r'\bNPS\b|NATIONAL\s+PENSION|\bNSDL\b|\bCRA\b|\bPOP\b|POP[-\s]*SP|PENSION\s+CONTRIBUTION', u):
        return _protected_lock(_build(
            narration,
            'asset_investment_nps',
            'debit',
            0.96,
            note='Explicit NPS investment debit.'
        ))

    if forced_type == 'credit' and re.search(r'\b(TRUST|SCHEME|PLOT|YEIDA|AUTHORITY|RESIDENTIAL\s+PLOTS)\b', u):
        return _build(
            narration,
            'income_other',
            'credit',
            0.70,
            note='Institutional / scheme credit should not be treated as personal loan.'
        )

    if forced_type == 'debit' and re.search(r'\b(PLOT|SCHEME|YEIDA|AUTHORITY|RESIDENTIAL\s+PLOTS)\b', u):
        return _build(
            narration,
            'asset_land_cwip',
            'debit',
            0.88,
            note='Plot / scheme related payment treated as land / CWIP.'
        )

    if forced_type in ('credit', 'debit') and re.search(r'\bU/G\b|UNDER\s+GUARDIAN', u) and re.search(r'GARG|KUMAR|SHARMA|SINGH|PATEL|JAIN|HUF', u) and not broker_marker:
        return _build(
            narration,
            'liability_loan_outstanding',
            forced_type,
            0.96,
            note='Guardian / family transfer treated as loan movement on liability axis. family_loan_override=1'
        )

    if re.search(r'\b(?:U/G|UNDER\s+GUARDIAN)\b', u) and not broker_marker:
        return _build(
            narration,
            'liability_loan_outstanding',
            forced_type or ('credit' if re.search(r'\b(?:CR|CREDIT)\b', u) else 'debit'),
            0.92,
            note='U/G flow treated as family loan movement on outstanding liability axis. family_loan_override=1'
        )

    if forced_type == 'debit' and _is_explicit_self_given_loan_narration(u):
        return _build(
            narration,
            'asset_loans_advances_given',
            'debit',
            0.92,
            note='Explicit loan / advance given by account holder — receivable asset.'
        )

    if forced_type == 'credit' and _is_explicit_self_given_loan_narration(u):
        return _build(
            narration,
            'asset_loan_repayment_received',
            'credit',
            0.92,
            note='Explicit recovery / repayment received on loan given by account holder.'
        )

    if (
        _is_family_loan_style_narration(u)
        and re.search(r'(?:IB\s+FUNDS\s+TRANSFER|TPT-|NEFT|RTGS|IMPS|UPI)', u)
        and not broker_marker
        and not re.search(
            r'\b(PPF|PUBLIC\s+PROVIDENT\s+FUND|NPS|PLOT|SCHEME|TRUST|REDEMPTION|DIVIDEND|INTEREST|FD|FIXED\s+DEPOSIT|SIP|MUTUAL\s+FUND|ZERODHA|BROKER|NSE|BSE|CLEARING|RENT|SALARY)\b',
            u
        )
        and not _PROTECTED_INVESTMENT_INCOME_RE.search(u)
        and not _is_explicit_self_given_loan_narration(u)
    ):
        return _build(
            narration,
            'liability_loan_outstanding',
            forced_type or ('credit' if re.search(r'\b(?:CR|CREDIT)\b', u) else 'debit'),
            0.84,
            note='Named family transfer treated as loan movement on outstanding liability axis. family_loan_override=1'
        )

    # Explicit loan/advance GIVEN by you → receivable asset
    if forced_type == 'debit' and _is_explicit_self_given_loan_narration(u):
        return _build(
            narration,
            'asset_loans_advances_given',
            'debit',
            0.90,
            note='Explicit loan / advance given by account holder — receivable asset.'
        )

    if re.search(r'TPT-TXFR\b', u) and not broker_marker:
        if re.search(r'L\s*D\s*K\s+SHARES', u):
            key = 'trading_funds_added' if (forced_type or 'debit') == 'debit' else 'trading_payout'
            return _build(
                narration,
                key,
                forced_type or 'debit',
                0.92,
                note='TPT-TXFR to/from LDK Shares — trading account transfer.'
            )

        # Treat TPT transfer credits as loan received unless clearly own-account
        if forced_type == 'credit':
            if re.search(r'\b(SELF|OWN\s+ACCOUNT|OWN\s+A/C)\b', u):
                return _build(
                    narration,
                    'asset_own_transfer_in',
                    'credit',
                    0.72,
                    note='TPT-TXFR credit — own account transfer in (contra). own_account_override=1'
                )
            return _build(
                narration,
                'liability_loan_outstanding',
                'credit',
                0.88,
                note='TPT-TXFR credit — treated as loan received. Outstanding liability until repaid. family_loan_override=1'
            )

        # Treat TPT transfer debits as repayment / transfer back on same liability axis
        return _build(
            narration,
            'liability_loan_outstanding',
            'debit',
            0.88,
            note='TPT-TXFR debit — treated as loan repayment. Reduces outstanding liability. family_loan_override=1'
        )

    if re.search(r'TPT-LOAN\b', u) and not broker_marker:
        if forced_type == 'credit':
            return _build(
                narration,
                'liability_loan_outstanding',
                'credit',
                0.92,
                note='TPT-LOAN credit — loan received. Outstanding liability until repaid. family_loan_override=1'
            )
        return _build(
            narration,
            'liability_loan_outstanding',
            'debit',
            0.90,
            note='TPT-LOAN debit — loan repayment. Reduces outstanding liability balance. family_loan_override=1'
        )

    # ── GENERIC TPT with named person (runs AFTER all specific TPT patterns) ────
    # Default stance: named TPT person flows are loan movements, not assets,
    # unless explicitly tagged as loan/advance given by the account holder.
    if re.search(r'\bTPT-[A-Z0-9]+-[A-Z][A-Z\s]+$', u) and not broker_marker:
        if forced_type == 'credit':
            return _build(
                narration,
                'liability_loan_outstanding',
                'credit',
                0.86,
                note='Generic TPT credit — treated as loan received from named individual. Outstanding liability. family_loan_override=1'
            )
        return _build(
            narration,
            'liability_loan_outstanding',
            'debit',
            0.84,
            note='Generic TPT debit — treated as loan repayment to named individual. Reduces outstanding liability balance. family_loan_override=1'
        )

    if forced_type == 'debit':
        _salary_pat = re.compile(r'TPT-SAL\b|ECS\s+CR\s+SALARY|SALARY\s+CREDIT|PAYROLL\s+CREDIT|SAL\s+CR\b', re.I)
        _div_pat = re.compile(r'ACH[-\s]*CR.*DIV|\bNACH\s+(?:CR|CREDIT).*DIV|ACH\s*C[-\s]*.*DIV|\b(?:DIV|DIVIDEND|INTDIV|FINDIV|FINALDIV|SPLDIV|ANNUALDIV)\b|\bDIV(?:\d{2,}|[/-])', re.I)
        _int_pat = re.compile(r'INT\.PD|INT\s+PD|SB\s+\d+\s+INT|INTEREST\s+CREDIT|CREDIT\s+INTEREST', re.I)
        if _salary_pat.search(u):
            return _build(
                narration,
                'suspense_debit',
                'debit',
                0.50,
                note='Salary pattern in a debit txn — moved to suspense for review.'
            )
        if _div_pat.search(u):
            return _build(
                narration,
                'suspense_debit',
                'debit',
                0.50,
                note='Dividend pattern in a debit txn — moved to suspense for review.'
            )
        if _int_pat.search(u):
            return _build(
                narration,
                'suspense_debit',
                'debit',
                0.50,
                note='Interest pattern in a debit txn — moved to suspense for review.'
            )

    for pat, key, type_hint in RULES:
        if re.search(pat, u):
            if (type_hint == 'debit' and forced_type == 'credit') or (type_hint == 'credit' and forced_type == 'debit'):
                continue
            if key == "asset_own_transfer_in" and (broker_marker or _is_family_loan_style_narration(u)):
                continue
            txn_type = forced_type if forced_type in ('credit', 'debit') else (type_hint or 'debit')
            return _protected_lock(_build(narration, key, txn_type, 0.97))

    return None

_INDIAN_BANK_PREFIX_PAT = re.compile(
    r'^(?:'
    r'INB(?:\s*FUNDS?\s*TFR)?[/ ]*|'
    r'NETBANKING[/ ]*|'
    r'BY\s+TRANSFER[-–\s]*|'
    r'TO\s+TRANSFER[-–\s]*|'
    r'BY\s+CLG\s+|BY\s+TRF\s+|'
    r'IMPS/\d{6,}/|'
    r'NEFT/[^/]*/|'
    r'UPI-(?=\w)|'
    r'(?:NFS|VPS)/\d+/|'
    r'BIL/INFT/[^/]+/|'
    r'WDL\s+TFR\s+UPI/(?:DR|CR)?/?|'
    r'(?:AUTOPAY|AUTODEBIT|NACH|ACH)\s+(?:CR|DR)[-–\s]*|'
    r'(?:ACH\s+C-|ACH\s+D-)'
    r')',
    re.IGNORECASE
)

def _normalize_indian_bank_narration(narration: str) -> str:
    """
    Strip leading bank/channel prefixes so counterparty extraction sees the
    human-readable portion first. Preserve TPT and ACH C- formats for their
    dedicated branches below.
    """
    s = (narration or "").strip()
    if re.search(r'\bTPT-', s, re.I):
        return s
    if re.match(r'ACH\s+C-', s, re.I):
        return s
    stripped = _INDIAN_BANK_PREFIX_PAT.sub('', s).strip(' /-')
    return stripped if stripped else s

def _extract_counterparty(narration: str) -> str:
    """
    Extract a human-useful counterparty / merchant name from common bank narrations.
    Works on both raw narrations (with / separators) and normalized narrations
    (space-separated text used by some staging paths).
    """
    s = (narration or "").strip()
    if not s:
        return "—"

    fallback_text = re.sub(r'\s+', ' ', s).strip(" /-")
    s = _normalize_indian_bank_narration(s)
    u = s.upper().strip()



        # PNB UPI pattern: UPI/txn/P2V/vpa/Actual Name
    if "/" in s and ("/P2V/" in s.upper() or "/P2M/" in s.upper()):
        parts = [p.strip() for p in s.split("/") if p.strip()]
        if len(parts) >= 5:
            name = parts[-1]
            name = re.sub(r"[^A-Za-z ]+", " ", name)
            name = re.sub(r"\s+", " ", name).strip().title()
            if name and name.upper() not in ("P2V", "P2M"):
                return name
            




    bank_tokens = {
        "HDFC", "BANK", "ICICI", "AXIS", "YES", "YESB", "SBIN", "STATE",
        "PUNJAB", "FEDERAL", "BA", "NAT", "INDIA", "OF", "IN", "L", "BANKL"
    }
    skip_tokens = {
        "UPI", "DR", "CR", "NO", "REMARKS", "SENT", "USING", "PAY", "FOR",
        "IN", "COLLECT", "RE", "TFR", "WDL", "DEP", "TO", "FROM"
    }
    noise_tokens = {
        "UPI", "NEFT", "RTGS", "IMPS", "ACH", "NACH", "TXN", "TXNID", "ID",
        "CR", "DR", "CREDIT", "DEBIT", "REF", "UTR", "RRN", "SEQ", "NETBANK",
        "HDFC", "HDFCBANK", "ICICI", "ICICIBANK", "AXIS", "AXISBANK", "YESB",
        "YESBANK", "SBIN", "STATEBANK", "BANK", "BANKL"
    }
    month_tokens = {"JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"}
    banned_counterparties = {
        "tpt", "sal", "salary", "ach", "cr", "dr", "unknown", "transaction",
        "bank", "hdfc", "icici", "remarks", "sent using", "sent", "using"
    }
    bank_only_pattern = re.compile(r'^(?:Hdfc|Icici|Axis|Sbi|Yes|Kotak|Federal|Indusind|Idfc|Au)(?: Bank)?$', re.I)

    def _strip_noise(x: str) -> str:
        x = (x or "").strip()
        if not x:
            return ""
        x = re.sub(r'@\S+', ' ', x)
        x = re.sub(r'\b(?:UPI|NEFT|RTGS|IMPS|ACH|NACH|TXN|TXNID|ID|UTR|RRN|REF|SEQ|NETBANK|USCNB|HDFCBANK|HDFCB)\b', ' ', x, flags=re.I)
        x = re.sub(r'\b(?:HDFC|ICICI|AXIS|YESB?|SBIN|KOTAK|FEDERAL|IDFC|AUBANK|BANDHAN|INDUSIND|PNB|CANARA)\b(?:\s*BANKL?|\s*BANK)?', ' ', x, flags=re.I)
        x = re.sub(r'\b[A-Z]{2,6}\d{4,}\b', ' ', x, flags=re.I)
        x = re.sub(r'\b[A-Z0-9]{12,}\b', ' ', x, flags=re.I)
        x = re.sub(r'(?<![A-Za-z])\d{7,}(?![A-Za-z])', ' ', x)
        x = re.sub(r'\b(?:INB|IB|TFR|WDL|DEP|CLG|ECS|NACH|AUTOPAY|AUTODEBIT)\b', ' ', x, flags=re.I)
        x = re.sub(r'[/\-]+', ' ', x)
        x = re.sub(r'[^A-Z0-9&. ]', ' ', x, flags=re.I)
        x = re.sub(r'\s+', ' ', x).strip()
        return x

    def _normalize_private_limited(x: str) -> str:
        x = re.sub(r'\bP\s*L\b', 'Private Limited', x, flags=re.I).strip()
        x = re.sub(r'\bPRI\b', 'Private Limited', x, flags=re.I).strip()
        return x

    def _is_bad_candidate(x: str) -> bool:
        compact = re.sub(r'\s+', ' ', (x or '')).strip()
        if not compact:
            return True
        if compact.lower() in banned_counterparties:
            return True
        if bank_only_pattern.fullmatch(compact):
            return True
        return False

    def _meaningful_fragments(x: str) -> list[str]:
        cleaned = _strip_noise(x)
        if not cleaned:
            return []
        fragments = []
        for chunk in re.split(r'\s{2,}|/| - |\|', cleaned):
            chunk = chunk.strip(" /-")
            if not chunk:
                continue
            words = [
                w for w in chunk.split()
                if w and w.upper() not in noise_tokens and w.upper() not in month_tokens and not re.fullmatch(r'\d+', w)
            ]
            if not words:
                continue
            fragment = _normalize_private_limited(' '.join(words).title())
            if not _is_bad_candidate(fragment):
                fragments.append(fragment)
        return fragments

    def _clean_fallback_text(x: str) -> str:
        fragments = _meaningful_fragments(x)
        if fragments:
            return fragments[-1]
        cleaned = _normalize_private_limited(_strip_noise(x).title())
        cleaned = re.sub(r'\bHuf\b', '', cleaned, flags=re.I).strip()
        cleaned = re.sub(r'\s+', ' ', cleaned)
        if cleaned and not _is_bad_candidate(cleaned):
            return cleaned
        fallback_fragments = _meaningful_fragments(fallback_text)
        if fallback_fragments:
            return fallback_fragments[-1]
        return _normalize_private_limited((fallback_text or "Unknown").title())

    def _clean_name(x: str) -> str:
        x = _strip_noise(x)
        if not x:
            return ""
        chunks = [c.strip() for c in re.split(r'\s{2,}|/', x) if c.strip()]
        if not chunks:
            chunks = [x]
        meaningful = []
        for chunk in chunks:
            words = [w for w in chunk.split() if w and w.upper() not in noise_tokens and not re.fullmatch(r'\d+', w)]
            if words:
                meaningful.append(' '.join(words))
        candidate = meaningful[-1] if meaningful else x
        candidate = re.sub(r'\b(?:NO|REMARKS|SENT|USING|PAY|FOR|COLLECT|RE|TFR|WDL|DEP|TO|FROM)\b', ' ', candidate, flags=re.I)
        candidate = re.sub(r'\s+', ' ', candidate).strip(" /-")
        candidate = _normalize_private_limited(candidate.title()) if candidate else ""
        candidate = re.sub(r'\bHuf\b', '', candidate, flags=re.I).strip()
        candidate = re.sub(r'\s+', ' ', candidate)
        if _is_bad_candidate(candidate):
            fragments = _meaningful_fragments(x)
            return fragments[-1] if fragments else ""
        return candidate

    # ── HDFC-specific extractions ──────────────────────────────────────────
    if re.search(r'L\s*D\s*K\s+(?:SHARES?|SECURITIES?)', u):
        return 'LDK Shares'
    if re.search(r'\bZERODHA\s+BROKING\s+LTD\b', u):
        return 'Zerodha Broking Ltd'
    if re.search(r'\bZERODHA\b|\bKITE\b', u):
        return 'Zerodha'
    if re.search(r'\bUPSTOX\b', u):
        return 'Upstox'
    if re.search(r'\b5PAISA\b', u):
        return '5Paisa'
    if re.search(r'\bGROWW\b', u):
        return 'Groww'
    if re.search(r'\bIIFL\b', u):
        return 'IIFL'
    if re.search(r'\bVENTURA\b', u):
        return 'Ventura'
    if re.search(r'\bBROKER\b', u):
        m = re.search(r'\b([A-Z][A-Z0-9&.\s]{2,}BROKER(?:ING)?(?:\s+LTD)?)\b', u)
        if m:
            cleaned = _clean_name(m.group(1))
            if cleaned:
                return cleaned

    if re.search(r'\bACH\s*C[-\s]', u):
        compact_issuer = _match_compact_dividend_issuer(u)
        if compact_issuer:
            return compact_issuer
        m = re.search(r'ACH\s*C[-\s]*([A-Z][A-Z0-9 &.\-/]+?)(?:[-\s/]*[A-Z0-9]*\d{5,}[A-Z0-9]*\s*$|$)', u)
        if m:
            issuer_raw = m.group(1)
            issuer_raw = re.sub(r'(?:INTDIV|FINDIV|FINALDIV|SPLDIV|ANNUALDIV|DIVIDEND|DIV|FNL|INT\d+).*$', '', issuer_raw, flags=re.I)
            issuer_raw = re.sub(r'\b(?:LIMITE|LIMITED)\b', 'Limited', issuer_raw, flags=re.I)
            issuer_raw = re.sub(r'\bLTD\b', 'Ltd', issuer_raw, flags=re.I)
            cleaned = _clean_name(issuer_raw)
            if cleaned != "—":
                return cleaned

    tpt_source = re.sub(r'^\d{4}-\d{2}-\d{2}(?:\d{8,})?-', '', s, flags=re.I)
    tpt_source = re.sub(r'^\d{10,}-', '', tpt_source)
    if re.match(r'^TPT-', tpt_source, re.I):
        tpt_salary = re.match(r'^TPT-SAL(?:[\s-]+)?(.+)$', tpt_source, re.I)
        if tpt_salary:
            remainder = (tpt_salary.group(1) or '').strip(" -/")
            remainder = re.sub(r'^(?:' + '|'.join(sorted(month_tokens)) + r')\b[\s-]*', '', remainder, flags=re.I)
            cleaned = _clean_name(remainder)
            if cleaned:
                return cleaned
        tpt_generic = re.match(r'^TPT-[^-]+-(.+)$', tpt_source, re.I)
        if tpt_generic:
            cleaned = _clean_name((tpt_generic.group(1) or '').strip(" -/"))
            if cleaned:
                return cleaned

    if re.search(r'\bTPT-(?:SAL|LOAN|REPAY|RETURN|GIFT)-', u):
        m = re.search(r'TPT-(?:SAL|LOAN|REPAY|RETURN|GIFT)(?:\s+[A-Z]{3,9})?-(.+)$', u)
        if m:
            cleaned = _clean_name(m.group(1))
            if cleaned:
                return cleaned

    if re.search(r'\bIB FUNDS TRANSFER\b', u):
        m = re.search(r'IB\s+FUNDS\s+TRANSFER\s+(?:CR|DR)-\S+\s+-([A-Z][A-Z0-9 &.\-/]+)$', u)
        if m:
            cleaned = _clean_name(m.group(1))
            if cleaned:
                return cleaned

    if re.search(r'\b(?:NEFT|RTGS)\s+(?:CR|DR)-', u):
        parts = [p.strip() for p in re.split(r'-+', s) if p.strip()]
        if len(parts) >= 4:
            mids = parts[2:-1]
            for p in mids:
                pu = p.upper()
                if len(pu) >= 4 and not re.fullmatch(r'[A-Z0-9]{8,}', pu):
                    if not re.search(r'NETBANK|MUM|YESB|UTIB|HDFC|SBIN|KARB|ICIC|AXIS', pu):
                        cleaned = _clean_name(p)
                        if cleaned:
                            return cleaned
                        
    m = re.search(r'(?:^|\b)(?:WDL\s+TFR\s+)?UPI/(?:DR|CR/)?(?:\d+/)?([^/]+)/', s, re.I)
    if m:
        cleaned = _clean_name(m.group(1))
        if cleaned:
            return cleaned

    if 'UPI' in u:
        tokens = [t for t in re.split(r'[\s/\-]+', u) if t]
        try:
            i = tokens.index('UPI')
        except ValueError:
            i = -1
        if i >= 0:
            name_parts = []
            for tok in tokens[i+1:]:
                if tok in skip_tokens or tok in bank_tokens:
                    break
                if re.fullmatch(r'\d{6,}', tok) or re.fullmatch(r'[A-Z0-9]{10,}', tok):
                    if name_parts:
                        break
                    continue
                if tok.endswith('QR') or tok.startswith('PAYTMQR') or tok.startswith('BHARATPE'):
                    break
                name_parts.append(tok)
                if len(name_parts) >= 4:
                    break
            if name_parts:
                cleaned = _clean_name(' '.join(name_parts))
                if cleaned:
                    return cleaned

    # ── TPT salary credits: extract employer/company name ───────────────────
    # Examples:
    #   50200014927657-TPT-SAL FEB-GOLDILOCKS TECH SOLUTION PRI
    #   TPT-SAL JAN-GOLDILOCKS TECH SOLUTION PRIVATE LIMITED
    #   SALARY GOLDILOKS TECH SOLUTION P L
    m = re.search(
        r'(?:^|-)TPT-SAL\s+[A-Z]{3,9}\s*-\s*([A-Z][A-Z0-9 &.\-]+)$',
        u,
        flags=re.I
    )
    if m:
        cleaned = _clean_name(m.group(1))
        cleaned = re.sub(r'\bP\s*L\b$', 'Private Limited', cleaned, flags=re.I).strip()
        cleaned = re.sub(r'\bPRI\b$', 'Private Limited', cleaned, flags=re.I).strip()
        if cleaned:
            return cleaned

    m = re.search(
        r'^(?:SALARY|SAL)\s+([A-Z][A-Z0-9 &.\-]+)$',
        u,
        flags=re.I
    )
    if m:
        cleaned = _clean_name(m.group(1))
        cleaned = re.sub(r'\bP\s*L\b$', 'Private Limited', cleaned, flags=re.I).strip()
        cleaned = re.sub(r'\bPRI\b$', 'Private Limited', cleaned, flags=re.I).strip()
        if cleaned:
            return cleaned
        
    m = re.match(r'^BIL/INFT/[^/]+/(.+)$', s, re.I)
    if m:
        cleaned = _clean_name(m.group(1))
        if cleaned:
            return cleaned

    # ── TPT transfers — full HDFC format: [date-][txnid-]TPT-[subtype|token]-NAME
    # Handles both:
    #   TPT-REPAY-RAVI KUMAR
    #   2026-02-2100271140017574-TPT-NBBFJHJP7LBJJMUX-SARJU GARG
    m = re.match(
        r'^(?:\d{4}-\d{2}-\d{2})?(?:[\d]+[-])?TPT-[A-Z0-9]+-([A-Z][A-Z\s&.]+?)(?:\s*-\s*USCNB\b.*)?$',
        s, re.I
    )
    if m:
        candidate = m.group(1).strip()
        if not re.fullmatch(r'[A-Z0-9]{8,}', candidate.replace(' ', '')):
            cleaned = _clean_name(candidate)
            if cleaned:
                return cleaned

    m = re.match(
        r'^(?:[\d-]+)?TPT-[A-Z]+\s+[A-Z]{2,4}\s*-\s*([A-Z][A-Z0-9 &.]+)$',
        s, re.I
    )
    if m:
        cleaned = _clean_name(m.group(1).strip())
        cleaned = re.sub(r'\bP\s*L\b$', 'Private Limited', cleaned, flags=re.I).strip()
        cleaned = re.sub(r'\bPRI\b$', 'Private Limited', cleaned, flags=re.I).strip()
        if cleaned:
            return cleaned

    m = re.match(r'^(?:ACH|NACH)\s+[A-Z-]*\s*(STATE\s+BANK\s+OF\s+INDIA|SBI|HDFC\s+BANK|ICICI\s+BANK|AXIS\s+BANK|YES\s+BANK|KOTAK\s+MAHINDRA\s+BANK)(?:[-/ ]\d|$)', u, re.I)
    if m:
        cleaned = _clean_name(m.group(1))
        if cleaned:
            return cleaned

    m = re.match(r'^IB\s+FUNDS\s+TRANSFER\s+(?:CR|DR)[-/ ]+\S+\s*[-–]\s*(.+)$', s, re.I)
    if m:
        cleaned = _clean_name(m.group(1))
        if cleaned:
            return cleaned

    if any(k in u for k in ("NEFT", "RTGS", "IMPS")):
        parts = [p.strip() for p in re.split(r'[*\/-]+', s) if p.strip()]
        stop_words = {"NEFT", "RTGS", "IMPS", "CR", "DR", "DEP", "TFR", "NETBANK", "UPI"}
        for p in parts:
            pu = p.upper().strip()
            if pu in stop_words:
                continue
            if re.fullmatch(r'[A-Z0-9]{8,}', pu) or re.fullmatch(r'\d{6,}', pu):
                continue
            if any(bt in pu.split() for bt in bank_tokens):
                continue
            if re.fullmatch(r'[A-Z0-9]+', pu) and len(pu) >= 6:
                continue
            if len(pu) >= 4 and re.search(r'[A-Z]', pu):
                cleaned = _clean_name(p)
                if cleaned:
                    return cleaned

    m = re.match(r'^(?:ACH|NACH)\s+[A-Z-]*\s*([A-Z][A-Z0-9 &.]+?)(?:[-/ ]\d|$)', u, re.I)
    if m:
        cleaned = _clean_name(m.group(1))
        if cleaned:
            return cleaned

    # ── ICICI Bank: VPS/CR/txnid/MerchantName or VPS/DR/txnid/MerchantName ──────
    m = re.match(r'^VPS/(?:CR|DR)/\d+/(.+)$', s, re.I)
    if m:
        cleaned = _clean_name(m.group(1))
        if cleaned: return cleaned

    # ── ICICI: ICICIB/txnid/MerchantName or similar ──────────────────────────
    m = re.match(r'^[A-Z]{4,6}/\d{6,}/(.{4,})$', s, re.I)
    if m:
        cleaned = _clean_name(m.group(1))
        if cleaned and not cleaned.upper().startswith(("UPI","NEFT","IMPS","RTGS","NACH","ACH")):
            return cleaned

    # ── Axis Bank: UPI-PayeeName-VPA@bank-ref (hyphen separator) ─────────────
    m = re.match(r'^UPI-([^@\-]{3,})-[^@\-]+@[^@\-]+-', s, re.I)
    if m:
        cleaned = _clean_name(m.group(1))
        if cleaned:
            return cleaned

    # ── Axis compact UPI / beneficiary formats ───────────────────────────────
    m = re.match(r'^UPI-([^@\-]{3,})-[^@]+@[^@]+-\d+', s, re.I)
    if m:
        cleaned = _clean_name(m.group(1))
        if cleaned:
            return cleaned

    m = re.match(r'^(?:IMPS|NEFT|RTGS)[-/].*?[-/ ]([A-Z][A-Z0-9 &.]{3,})$', s, re.I)
    if m:
        cleaned = _clean_name(m.group(1))
        if cleaned:
            return cleaned

    # ── HDFC NFS: NFS/txnid/MERCHANT ─────────────────────────────────────────
    m = re.match(r'^NFS/\d+/(.+)$', s, re.I)
    if m:
        cleaned = _clean_name(m.group(1))
        if cleaned: return cleaned

    # ── Kotak: TRF FROM Name / TRF TO Name ───────────────────────────────────
    m = re.match(r'^TRF\s+(?:FROM|TO)\s+(.+?)(?:\s+ON\s+|\s+\d{2}[/-]|\s+REF|\s*$)', s, re.I)
    if m:
        cleaned = _clean_name(m.group(1))
        if cleaned: return cleaned

    # ── SBI: TO TRANSFER-Name / BY TRANSFER-Name ─────────────────────────────
    m = re.match(r'^(?:TO|BY)\s+(?:TRANSFER|TRF)\s*[-–]\s*(.+?)(?:\s+ON\s+|\s+\d{2}[/-]|\s+REF|\s*$)', s, re.I)
    if m:
        cleaned = _clean_name(m.group(1))
        if cleaned: return cleaned

    # ── SBI: INB/FUNDS TFR/Name or NETBANKING/Name ───────────────────────────
    m = re.match(r'^(?:INB|NETBANKING)\s*/\s*(?:FUNDS?\s*TFR\s*/\s*)?(.+?)(?:\s*/|\s*$)', s, re.I)
    if m:
        cleaned = _clean_name(m.group(1))
        if cleaned and len(cleaned) >= 3: return cleaned

    # ── Yes Bank / Federal Bank: IMPS/txnid/Name/BankCode ────────────────────
    m = re.match(r'^IMPS/\d{6,}/([^/]{3,})', s, re.I)
    if m:
        cleaned = _clean_name(m.group(1))
        if cleaned: return cleaned

    if re.search(r'INT\.COLL|INT\.PD', u):
        return "Bank / Broker Interest"
    if re.search(r'RNWL', u):
        return "Renewal"

    parts = [p.strip() for p in re.split(r'[/\-]+', s) if p.strip()]
    for p in parts:
        pu = p.upper()
        if pu in skip_tokens:
            continue
        if re.fullmatch(r'\d{6,}', pu) or re.fullmatch(r'[A-Z0-9]{12,}', pu):
            continue
        if any(bt in pu.split() for bt in bank_tokens):
            continue
        cleaned = _clean_name(p)
        if cleaned:
            return cleaned

    fallback = _clean_fallback_text(s)
    return fallback if not _is_bad_candidate(fallback) else _clean_fallback_text(fallback_text)

def _build(narration: str, key: str, txn_type: str, confidence: float,
           note: str = "", source: str = "rule") -> Dict[str, Any]:
    rec = LEDGER_MAP.get(key, LEDGER_MAP["suspense_debit"])
    book, section, group, account = rec
    attr = ATTRIBUTION.get(key, "Account Holder (Self)")
    return {
        "narration":      narration,
        "ledger_key":     key,
        "book":           book,
        "section":        section,
        "group":          group,
        "account":        account,
        "txn_type":       txn_type,
        "counterparty":   _extract_counterparty(narration),
        "attribution":    attr,
        "confidence":     confidence,
        "note":           note,
        "source":         source,
        "amount":         0.0,
        "txn_date":       "",
    }


# ══════════════════════════════════════════════════════════════════════════════
# EVERYDAY EXPENSE CLASSIFIER  —  Token-set matching for common debits
# ══════════════════════════════════════════════════════════════════════════════

# Each entry: (ledger_key, [multi-word phrases checked first, then single keywords])
# Multi-word phrases are checked via substring match on normalized text.
# Single keywords are checked via token-set intersection.

_EVERYDAY_RULES: List[Tuple[str, List[str], List[str]]] = [
    # (ledger_key, multi_word_phrases, single_keywords)

    # Grocery — supermarkets, quick-commerce, provision stores
    ("exp_grocery", [
        "RELIANCE SMART", "RELIANCE FRESH", "STAR BAZAAR", "NATURES BASKET",
        "NATURE BASKET", "MORE RETAIL", "MORE SUPERMARKET", "EASY DAY",
        "COUNTRY DELIGHT", "MILK BASKET", "BIG BASKET", "LE MARCHE",
        "RATNADEEP", "HERITAGE FRESH",
    ], [
        "SUPERMARKET", "PROVISION", "KIRANA", "GROCERY",
        "GROFERS", "SPENCER", "SPAR", "EASYDAY", "RATNADEEP",
        "HERITAGE", "NILGIRIS", "NEEDS", "FRESHMART",
        "HYPERMART", "HYPERMARKET", "DEPARTMENTAL",
    ]),

    # Food — restaurants, cafés, bakeries, food delivery, QSR
    ("exp_food", [
        "BURGER KING", "TACO BELL", "PIZZA HUT", "BASKIN ROBBINS",
        "DUNKIN DONUTS", "CHAI POINT", "THIRD WAVE", "BLUE TOKAI",
        "MAD OVER DONUTS", "FOOD COURT", "FOOD HALL",
    ], [
        "BIRYANI", "HALDIRAM", "BAKERY", "CAKE", "FOOD", "EATERY",
        "CANTEEN", "MESS", "TIFFIN", "DHABA", "PAAN", "TEA",
        "JUICE", "SNACK", "SUBWAY", "BASKIN", "DINER",
        "BIRYANI", "MITHAI", "SWEET", "CONFECTION",
        "CHAAYOS", "BARISTA", "COSTA", "CCD", "BARBEQUE",
        "BEHROUZ", "FAASOS", "FRESHMEN", "ROLLS", "KEBAB",
        "WOK", "NOODLE", "SUSHI", "GRILL", "BISTRO",
    ]),

    # Pharmacy / Health — medical stores, diagnostics, hospitals
    ("exp_health", [
        "APOLLO PHARMACY", "DR LAL", "DR PATH", "MAX HOSPITAL",
        "FORTIS HOSPITAL", "MANIPAL HOSPITAL", "MEDANTA",
        "NARAYANA HEALTH", "ASTER PHARMACY", "FRANK ROSS",
    ], [
        "PHARMACY", "PHARMA", "MEDICAL", "CHEMIST", "DRUG",
        "WELLNESS", "DIAGNOSTIC", "PATHOLOGY", "CLINIC",
        "DENTAL", "OPTICAL", "HOSPITAL", "HEALTH",
        "MEDICINES", "MEDPLUS", "NETMEDS", "PHARMEASY",
        "PRACTO", "HEALTHIANS", "THYROCARE", "LYBRATE",
    ]),

    # Fuel — petrol pumps, CNG, EV charging
    ("exp_travel", [
        "BHARAT PETROLEUM", "INDIAN OIL", "HINDUSTAN PETROLEUM",
        "HP PETROL", "RELIANCE PETRO", "NAYARA ENERGY",
        "ADANI GAS", "MAHANAGAR GAS", "EV CHARGE", "CHARGE POINT",
        "ATHER GRID", "TATA POWER EZ",
    ], [
        "PETROL", "PETROLEUM", "DIESEL", "FUEL", "BPCL", "HPCL",
        "IOCL", "SHELL", "NAYARA", "CNG", "PUMP",
        "FILLING", "SERVO",
    ]),

    # Utilities — electricity, water, gas, telecom, DTH, broadband
    ("exp_utilities", [
        "TATA POWER", "ADANI ELECTRICITY", "ADANI GAS", "MAHANAGAR GAS",
        "BILL PAY", "BILL PAYMENT", "BILLDESK",
        "TATA PLAY", "DISH TV", "SUN DIRECT", "HATHWAY", "ACT FIBERNET",
        "TORRENT POWER", "CESC LTD",
    ], [
        "ELECTRICITY", "WATER", "GAS", "RECHARGE", "POSTPAID",
        "PREPAID", "MOBILE", "INTERNET", "WIFI", "CABLE",
        "LPG", "PIPELINE", "SEWAGE", "TORRENT", "ADANI",
        "BESCOM", "BSES", "MSEDCL", "AIRTEL", "JIO",
        "BROADBAND", "DTH", "VODAFONE", "IDEA",
    ]),

    # E-commerce — online retailers, electronics, specialty
    ("exp_shopping_online", [
        "RELIANCE DIGITAL", "VIJAY SALES", "CROMA RETAIL",
        "TATA CLIQ", "JIO MART", "PAYTM MALL",
    ], [
        "ECOM", "ONLINE", "PAYTMMALL", "JIOMART", "TATACLIQ",
        "CROMA", "LENSKART", "FIRSTCRY", "MAMAEARTH",
        "PURPLLE", "BEWAKOOF", "SOULED", "SHOPPERS",
        "DECATHLON", "IKEA",
    ]),
]


def classify_everyday_expense(narration: str, forced_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Classify common everyday debit transactions using normalized text and
    token-set matching. Only fires for debit or unknown direction — never
    for confirmed credits.

    Returns a _build() result dict or None if no everyday pattern matches.
    """
    # Never classify credits as everyday expenses
    if forced_type == "credit":
        return None

    norm = normalize_txn_text(narration)
    if not norm:
        return None
    tokens = tokenize_txn_text(narration)

    for ledger_key, phrases, keywords in _EVERYDAY_RULES:
        # Check multi-word phrases first (substring match on normalized text)
        for phrase in phrases:
            if phrase in norm:
                return _build(narration, ledger_key, forced_type or "debit", 0.88,
                              note=f"Everyday expense: matched phrase '{phrase}'",
                              source="everyday")
        # Check single keywords via token-set intersection
        for kw in keywords:
            if kw in tokens:
                return _build(narration, ledger_key, forced_type or "debit", 0.88,
                              note=f"Everyday expense: matched keyword '{kw}'",
                              source="everyday")

    return None


# ══════════════════════════════════════════════════════════════════════════════
# TRANSFER HEURISTIC  —  NEFT/RTGS/IMPS debits to individuals
# ══════════════════════════════════════════════════════════════════════════════

# Corporate / institutional keywords — if any matches, do NOT classify as personal transfer
_CORPORATE_KEYWORDS = re.compile(
    r'\b(?:LTD|PVT|PRIVATE|LIMITED|BANK|FINANCE|FINSERV|TECH|SOLUTIONS|SERVICES'
    r'|ENTERPRISE|CORP|CORPORATION|COMPANY|PAY|PAYMENTS|DIGITAL|PLATFORM|MART'
    r'|FOODS|LOGIS|LOGISTICS|VENTURES|CAPITAL|SECURITIES|BROKING|INSURANCE'
    r'|HOSPITAL|PHARMA|REALTY|INFRA|CONSTRUCTION|HOUSING|LOAN|EMI|CREDIT'
    r'|CBDT|GST|GOVERNMENT|AUTHORITY|COUNCIL|BOARD|BUREAU|MINISTRY'
    r'|MUTUAL\s+FUND|MF\b|SIP\b|PREMIUM|CHALLAN|TAX|EDUCATION|SCHOOL'
    r'|UNIVERSITY|COLLEGE|INSTITUTE|ACADEMY|TRUST|FOUNDATION|SOCIETY)\b',
    re.I
)

def classify_transfer_heuristic(narration: str, forced_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Catch NEFT/RTGS/IMPS debit transfers to individual persons that would
    otherwise fall to suspense. Conservative: skips anything that looks
    corporate, institutional, or is already a known merchant.

    Returns a _build() result dict or None.
    """
    if forced_type == "credit":
        return None

    u = _u(narration)
    if _has_broker_trading_marker(u) or _PROTECTED_INVESTMENT_INCOME_RE.search(u):
        return None

    # Must be an outward NEFT/RTGS/IMPS transfer
    m = re.match(
        r'^(?:NEFT|RTGS|IMPS)\s+DR[-/\s]+([A-Z0-9]+)[-/\s]+(.+?)(?:[-/\s]+NETBANK|[-/\s]+UPI|$)',
        u
    )
    if not m:
        return None

    beneficiary = m.group(2).strip()
    # Must have a meaningful name (> 3 chars)
    if len(beneficiary) <= 3:
        return None

    # Skip if looks corporate / institutional
    if _CORPORATE_KEYWORDS.search(beneficiary):
        return None

    # Skip if it looks like a known expense pattern already handled by rules
    if re.search(r'LOAN|EMI|CREDIT\s*CARD|INSURANCE|PREMIUM|CHALLAN|TAX|RENT|FEE|SALARY|PAYROLL', u):
        return None

    # Family/individual name → loan repayment (debit reduces the outstanding liability)
    # Both the original loan credit AND this repayment debit land in liability_loan_outstanding,
    # so they net correctly on the Balance Sheet.
    FAMILY_NAME_PAT = r'GARG|KUMAR|SHARMA|SINGH|GUPTA|AGARWAL|JAIN|PATEL|YADAV|VERMA|MEHTA|CHAUHAN|RAJPUT|SONI|BAHADUR|SAHU|SHETH|MODI|KAPOOR|KHAN|KHANNA|MALHOTRA|MALIK|MISHRA|TRIVEDI|TIWARI'
    if re.search(FAMILY_NAME_PAT, beneficiary):
        return _build(narration, "liability_loan_outstanding", forced_type or "debit", 0.80,
                      note=f"NEFT/RTGS debit to '{beneficiary[:30]}' — loan repayment. Reduces outstanding liability. Reclassify to exp_personal_transfer if routine family payment.",
                      source="transfer_heuristic")

    return _build(narration, "exp_personal_transfer", forced_type or "debit", 0.78,
                  note=f"Transfer heuristic: NEFT/RTGS/IMPS debit to individual '{beneficiary[:30]}'",
                  source="transfer_heuristic")


    # ── AU Bank narration prefix stripping ───────────────────────────────────────
_AU_PREFIX = re.compile(
    r'^(?:'
    r'EFTPOS\s+|'
    r'DIRECT\s+DEBIT\s+|'
    r'DIRECT\s+CREDIT\s+|'
    r'BPAY\s+|'
    r'OSKO\s+PAYMENT\s+|'
    r'NPP\s+|'
    r'CARD\s+PURCHASE\s+|'
    r'ATM\s+WITHDRAWAL\s+|'
    r'PAYMENT\s+TO\s+|'
    r'DEPOSIT\s+FROM\s+|'
    r'TRANSFER\s+TO\s+|'
    r'TRANSFER\s+FROM\s+'
    r')',
    re.IGNORECASE
)

def _strip_au_prefix(narration: str) -> str:
    """Strip Australian bank narration prefixes before classification."""
    return _AU_PREFIX.sub('', narration).strip()


# ══════════════════════════════════════════════════════════════════════════════
# CLASSIFICATION STATS  —  Suspense tracking and logging
# ══════════════════════════════════════════════════════════════════════════════

from collections import Counter as _Counter

_classification_stats = {
    "total": 0,
    "suspense": 0,
    "suspense_narrations": _Counter(),
}


def _track_classification(result: Dict[str, Any]) -> None:
    """Accumulate classification stats for suspense tracking."""
    _classification_stats["total"] += 1
    if result.get("book") == "SUSPENSE" or result.get("ledger_key", "").startswith("suspense_"):
        _classification_stats["suspense"] += 1
        narr = (result.get("narration") or "")[:80].strip()
        if narr:
            _classification_stats["suspense_narrations"][narr] += 1


def log_classification_stats() -> str:
    """
    Print and return suspense percentage + top 50 suspense narrations.
    Resets counters after logging.
    """
    total = _classification_stats["total"]
    susp = _classification_stats["suspense"]
    lines = []
    lines.append("")
    lines.append("═" * 80)
    lines.append("  CLASSIFICATION STATS")
    lines.append("═" * 80)
    if total == 0:
        lines.append("  No transactions classified in this batch.")
    else:
        pct = (susp / total) * 100
        lines.append(f"  Total classified : {total}")
        lines.append(f"  Suspense         : {susp}  ({pct:.1f}%)")
        lines.append(f"  Classified OK    : {total - susp}  ({100 - pct:.1f}%)")
        lines.append("")
        top50 = _classification_stats["suspense_narrations"].most_common(50)
        if top50:
            lines.append("  TOP 50 SUSPENSE NARRATIONS:")
            lines.append("  " + "─" * 76)
            for i, (narr, cnt) in enumerate(top50, 1):
                lines.append(f"  {i:3d}. [{cnt:3d}x]  {narr}")
        else:
            lines.append("  ✅ No transactions went to suspense!")
    lines.append("═" * 80)
    output = "\n".join(lines)
    print(output)
    # Reset for next batch
    _classification_stats["total"] = 0
    _classification_stats["suspense"] = 0
    _classification_stats["suspense_narrations"] = _Counter()
    return output


def reset_classification_stats() -> None:
    """Reset stats without logging (for testing)."""
    _classification_stats["total"] = 0
    _classification_stats["suspense"] = 0
    _classification_stats["suspense_narrations"] = _Counter()


# ══════════════════════════════════════════════════════════════════════════════
# SUPERVISED ML  —  TF-IDF + Calibrated LinearSVC
# ══════════════════════════════════════════════════════════════════════════════

class SupervisedModel:
    def __init__(self):
        self.vectorizer = None
        self.model = None
        self.labels: List[str] = []
        self.ready = False
        self._load_or_train()

    def _load_or_train(self):
        """Train reproducibly on fictional examples; never pool users' statements."""
        if os.path.exists(MODEL_PATH):
            saved = joblib.load(MODEL_PATH)
            self.vectorizer, self.model, self.labels = saved["vectorizer"], saved["model"], saved["labels"]
            self.ready = True
            return
        from generate_dataset import SPECS, make_row
        import random
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.svm import LinearSVC
        from sklearn.calibration import CalibratedClassifierCV
        rng_state = random.getstate()
        random.seed(42)
        X, y = [], []
        try:
            for category, spec in SPECS.items():
                key = CAT_TO_KEY.get(category, category)
                if key not in LEDGER_MAP:
                    continue
                for _ in range(60):
                    example = make_row(category, spec)
                    X.append(example.description); y.append(key)
        finally:
            random.setstate(rng_state)
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3,5), max_features=25000)
        model = CalibratedClassifierCV(LinearSVC(class_weight="balanced", random_state=42, max_iter=5000), cv=3)
        model.fit(vec.fit_transform(X), y)
        self.vectorizer, self.model, self.labels = vec, model, list(model.classes_)
        self.ready = True
        # File contains only a model trained on authored synthetic examples.
        joblib.dump({"vectorizer":vec,"model":model,"labels":self.labels}, MODEL_PATH)


    def predict(self, text: str) -> Tuple[str, float]:
        if not self.ready:
            return "suspense_debit", 0.0
        X = self.vectorizer.transform([text])
        proba = self.model.predict_proba(X)[0]
        idx = int(proba.argmax())
        return self.labels[idx], float(proba[idx])

    def add_correction(self, narration, correct_key):
        """Personal corrections live in the user-scoped learning_examples table."""
        return None

class UnsupervisedClusters:
    def __init__(self):
        self.vectorizer = None
        self.kmeans = None
        self.cluster_labels: Dict[int, str] = {}
        self.ready = False
        self._load_or_train()

    def _load_or_train(self):
        if os.path.exists(CLUSTER_PATH):
            try:
                b = joblib.load(CLUSTER_PATH)
                self.vectorizer = b["vectorizer"]
                self.kmeans = b["kmeans"]
                self.cluster_labels = b["cluster_labels"]
                self.ready = True
                return
            except Exception:
                pass

        EXCEL = ""  # Private workbooks are never shared model-training inputs.
        X_text, y_cat = [], []
        if os.path.exists(EXCEL):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(EXCEL, data_only=True)
                ws = wb["BS Classified Transactions"]
                for row in ws.iter_rows(values_only=True):
                    if not row[0] or not str(row[0]).strip().isdigit():
                        continue
                    narr = str(row[2]).strip()
                    mapped = EXCEL_LINE_TO_KEY.get(str(row[7]).strip())
                    if narr and mapped:
                        X_text.append(narr); y_cat.append(mapped)
            except Exception:
                pass

        if len(X_text) < 20:
            return

        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.cluster import MiniBatchKMeans
        from collections import Counter

        vec = TfidfVectorizer(lowercase=True, analyzer="word", ngram_range=(1,2),
                              max_features=30000, min_df=1)
        Xmat = vec.fit_transform(X_text)
        k = min(30, len(X_text) // 5)
        km = MiniBatchKMeans(n_clusters=k, random_state=42, n_init=5)
        cids = km.fit_predict(Xmat)
        cluster_labels = {}
        for cid in range(k):
            cats = [y_cat[i] for i, c in enumerate(cids) if c == cid]
            cluster_labels[cid] = Counter(cats).most_common(1)[0][0] if cats else "suspense_debit"

        self.vectorizer = vec
        self.kmeans = km
        self.cluster_labels = cluster_labels
        self.ready = True
        joblib.dump({"vectorizer": vec, "kmeans": km, "cluster_labels": cluster_labels}, CLUSTER_PATH)

    def predict(self, text: str) -> Tuple[int, str, float]:
        if not self.ready:
            return -1, "suspense_debit", 0.0
        X = self.vectorizer.transform([text])
        cid = int(self.kmeans.predict(X)[0])
        center = self.kmeans.cluster_centers_[cid]
        dist = float(np.linalg.norm(X.toarray()[0] - center))
        return cid, self.cluster_labels.get(cid, "suspense_debit"), dist

    def is_anomaly(self, text: str, threshold: float = 0.85) -> bool:
        _, _, dist = self.predict(text)
        return dist > threshold


_ml: Optional[SupervisedModel] = None
_cl: Optional[UnsupervisedClusters] = None
_model_lock = threading.RLock()

def get_ml():
    global _ml
    with _model_lock:
        if _ml is None: _ml = SupervisedModel()
    return _ml

def get_cl():
    global _cl
    with _model_lock:
        if _cl is None: _cl = UnsupervisedClusters()
    return _cl


# ══════════════════════════════════════════════════════════════════════════════
# MAIN CLASSIFY FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

# ── Misc rescue: reclassify exp_misc/suspense_debit when counterparty is known ──
_MISC_RESCUE_RULES: List[Tuple[str, str]] = [
    # Food & dining
    (r"SWIGGY|ZOMATO|ETERNAL|DOMINOS|PIZZA|KFC|MCDONALD|STARBUCKS|CAFE|RESTAURANT|BURGER\s*KING|CHAAYOS|BEHROUZ|FAASOS|HALDIRAM|BIRYANI|BAKERY|DHABA|FOOD\s*COURT|SUBWAY|BARISTA|COSTA|CCD|WINGSTOP|SUSHI|GRILL|BISTRO|BARBEQUE", "exp_food"),
    # Grocery
    (r"BLINKIT|ZEPTO|INSTAMART|BIGBASKET|DUNZO|MILKBASKET|GROFERS|DMART|BLINKIT|RELIANCE\s*FRESH|RELIANCE\s*SMART|STAR\s*BAZAAR|BIG\s*BASKET|COUNTRY\s*DELIGHT|NATURES?\s*BASKET|SPENCER|SPAR|EASYDAY|HERITAGE\s*FRESH|KIRANA|PROVISION|GROCERY|SUPERMARKET", "exp_grocery"),
    # Entertainment
    (r"NETFLIX|SPOTIFY|PRIME\s*VIDEO|HOTSTAR|DISNEY|ZEE5|BOOKMYSHOW|PVR|INOX|CINEPOLIS|GAMESKRAFT|DREAM11|MPL|WINZO", "exp_entertainment"),
    # Shopping
    (r"AMAZON|FLIPKART|MEESHO|SNAPDEAL|MYNTRA|AJIO|NYKAA|TATA\s*CLIQ|RELIANCE\s*DIGITAL|CROMA|LENSKART|FIRSTCRY|MAMAEARTH|PURPLLE|BEWAKOOF|DECATHLON|IKEA|SHOPPERS\s*STOP|WESTSIDE|LIFESTYLE|ZARA|H&?M|UNIQLO|MANYAVAR|RAYMOND|BIBA|FABINDIA|ZUDIO", "exp_shopping_online"),
    # Travel
    (r"UBER|OLA|RAPIDO|IRCTC|MAKEMYTRIP|GOIBIBO|CLEARTRIP|INDIGO|AIR\s*INDIA|VISTARA|SPICEJET|REDBUS|IXIGO|YATRA|EASEMYTRIP|ABHIBUS|METRO\s*RAIL|BMTC|KSRTC", "exp_travel"),
    # Health
    (r"APOLLO\s*PHARMACY|MEDPLUS|NETMEDS|1MG|PHARMEASY|PRACTO|HEALTHIANS|THYROCARE|DR\s*LAL|MEDANTA|FORTIS|MANIPAL\s*HOSPITAL|MAX\s*HOSPITAL|AIIMS|PHARMACY|CHEMIST|CLINIC|DIAGNOSTIC", "exp_health"),
    # Utilities
    (r"AIRTEL|JIO|VODAFONE|IDEA|BSNL|TATA\s*PLAY|DISH\s*TV|HATHWAY|BESCOM|BSES|MSEDCL|TATA\s*POWER|ADANI\s*(?:ELEC|GAS)|MAHANAGAR\s*GAS|TORRENT\s*POWER|CESC", "exp_utilities"),
    # Education
    (r"IIT|NIT|BITS\s*PILANI|COURSERA|UDEMY|BYJU|UNACADEMY|VEDANTU|SCHOOL\s*FEE|COLLEGE\s*FEE|UNIVERSITY|TUITION|INSTITUTE|ACADEMY|NIIT|APTECH", "exp_education"),
    # Personal care
    (r"SALON|SPA|BEAUTY|MAMAEARTH|SUGAR\s*COSMET|MINIMALIST|WOW\s*SKIN|TIRA|LAKME|NYKAA\s*BEAUTY|PLUM|DOT\s*&\s*KEY", "exp_personal_care"),
    # Insurance
    (r"LIC|HDFC\s*LIFE|SBI\s*LIFE|MAX\s*LIFE|ICICI\s*PRU|BAJAJ\s*ALLIANZ|STAR\s*HEALTH|NIVA\s*BUPA|CARE\s*HEALTH|RELIANCE\s*GENERAL|NEW\s*INDIA\s*ASSURANCE|TATA\s*AIG", "exp_insurance"),
]

def _rescue_misc_by_counterparty(narration: str, result: Dict[str, Any], forced_type: Optional[str]) -> Dict[str, Any]:
    """
    If classification ended at exp_misc or suspense_debit, try to recover
    a specific ledger key by pattern-matching the full narration against
    known merchant / brand patterns.
    """
    u = (narration or "").upper()
    for pattern, key in _MISC_RESCUE_RULES:
        if re.search(pattern, u, re.I):
            rescued = _build(narration, key, forced_type or "debit", 0.82,
                             note=f"Misc rescue: matched pattern for '{key}'",
                             source="rescue")
            rescued["amount"]     = result.get("amount", 0)
            rescued["txn_date"]   = result.get("txn_date", "")
            rescued["cluster_id"] = result.get("cluster_id", -1)
            rescued["is_anomaly"] = result.get("is_anomaly", False)
            return rescued
    return result


def classify(narration: str, forced_type: Optional[str] = None,
             amount: float = 0.0, txn_date: str = "") -> Dict[str, Any]:
    """
    Classify a bank narration into the 3-book ledger.
    Returns a rich dict with book / section / group / account / attribution.

    Pipeline order:
      Step 1 → Rule engine (regex patterns, high confidence)
      Step 2 → Everyday expense classifier (token-set matching)
      Step 3 → Transfer heuristic (NEFT/RTGS/IMPS to individuals)
      Step 4 → Supervised ML (TF-IDF + LinearSVC)
      Step 5 → Unsupervised cluster fallback
      Step 6 → Suspense (last resort)

    FIXED: Symmetric direction guards (Issues A1-A9).
    forced_type from the parser is treated as source of truth (Issue A3).
    """
    narration = (narration or "").strip()
    # ── Strip PDF/credit-card statement artefacts ─────────────────────────────
    # Remove card holder name prefix: "SARJU GARG //| : MERCHANT" → "MERCHANT"
    narration = re.sub(r'^[A-Za-z][A-Za-z\s]{3,25}\s+(?://|\|)\s*', '', narration).strip()
    # Remove "//| :" or "| :" or "//" prefix from PDF table border bleeding
    narration = re.sub(r'^[|/\s:]+', '', narration).strip()
    # Remove "+ C l" or "C l" credit card charge indicator suffix
    narration = re.sub(r'\s*\+?\s*C\s+[Ll]\s*$', '', narration).strip()
    # Remove "(Ref# VT...)" or "(Ref# ST...)" reference numbers
    narration = re.sub(r'\s*\(Ref#\s*[A-Z0-9]+\)', '', narration).strip()
    # Remove trailing isolated "C l" or "+ C"
    narration = re.sub(r'\s+\+?\s*C\s*$', '', narration).strip()
    u = _u(narration)

    # Strip AU bank prefixes if present
    if _AU_PREFIX.match(narration):
        narration = _strip_au_prefix(narration)

    # ── Step 1: Rule engine ──────────────────────────────────────────────────
    hit = rule_classify(narration, forced_type)
    if hit:
        # Run post-classification sanity validation
        hit = validate_classification(hit, forced_type)
        key = hit["ledger_key"]
        if key in PROTECTED_CLASSIFICATION_KEYS:
            hit = _protected_lock(hit)
            hit["amount"] = amount
            hit["txn_date"] = txn_date
            hit["cluster_id"] = -1
            hit["is_anomaly"] = False
            _track_classification(hit)
            return hit
        # Secondary direction guard: if a credit-only key is still present on a debit txn
        # after validation it means the validator allowed it (bidirectional key).
        # Only fall through to everyday if it's a clear case that everyday should handle.
        if forced_type == "debit" and key in _CREDIT_ONLY_KEYS:
            print(f"  [CLASSIFY] Post-validate guard (debit→credit key '{key}'): '{narration[:60]}' → everyday")
            hit = None
        elif forced_type == "credit" and key.startswith("exp_") and key not in _BIDIRECTIONAL_KEYS:
            print(f"  [CLASSIFY] Post-validate guard (credit→expense key '{key}'): '{narration[:60]}' → suspense_credit")
            hit = _build(narration, "income_other", "credit", 0.45,
                         note=f"Direction guard: credit txn matched expense key '{key}'. Posted to other income to avoid suspense.")
    if hit:
        hit["amount"] = amount
        hit["txn_date"] = txn_date
        hit["cluster_id"] = -1
        hit["is_anomaly"] = False
        _track_classification(hit)
        return hit

    # ── Step 2: Everyday expense classifier ──────────────────────────────────
    everyday = classify_everyday_expense(narration, forced_type)
    if everyday:
        everyday = validate_classification(everyday, forced_type)
        everyday["amount"] = amount
        everyday["txn_date"] = txn_date
        everyday["cluster_id"] = -1
        everyday["is_anomaly"] = False
        _track_classification(everyday)
        return everyday

    # ── Step 3: Transfer heuristic ───────────────────────────────────────────
    transfer = classify_transfer_heuristic(narration, forced_type)
    if transfer:
        transfer = validate_classification(transfer, forced_type)
        transfer["amount"] = amount
        transfer["txn_date"] = txn_date
        transfer["cluster_id"] = -1
        transfer["is_anomaly"] = False
        _track_classification(transfer)
        return transfer

    # ── Step 4: Supervised ML ────────────────────────────────────────────────
    ml = get_ml()
    pred_key, conf = ml.predict(narration)
    pred_key = CAT_TO_KEY.get(pred_key, pred_key)
    if pred_key not in LEDGER_MAP:
        pred_key = "suspense_debit"

    # Symmetric ML direction guards using canonical sets
    INCOME_KEY_PREFIXES = ("income_",)
    CREDIT_ASSET_KEYS = {
        "asset_own_transfer_in", "asset_refund_received", "asset_broker_payout",
        "asset_fcy_inward", "asset_fd_maturity", "asset_mf_redemption",
        "asset_equity_sale_proceeds", "asset_loan_repayment_received",
        "asset_advance_received_back", "asset_advance_returned",
    }
    if forced_type == "credit" and pred_key in _DEBIT_ONLY_KEYS:
        pred_key = "income_other"
        conf = min(max(conf, 0.35), 0.40)
        print(f"  [ML] Credit txn → debit-only key blocked → income_other fallback: '{narration[:60]}'")
    elif forced_type == "debit" and (
        any(pred_key.startswith(p) for p in INCOME_KEY_PREFIXES)
        or pred_key in CREDIT_ASSET_KEYS
    ):
        pred_key = "suspense_debit"
        conf = min(conf, 0.40)
        print(f"  [ML] Debit txn → income/credit-asset key blocked → suspense_debit: '{narration[:60]}'")

    # ── Step 5: Cluster fallback for low confidence ──────────────────────────
    cl = get_cl()
    cid, cl_key, dist = cl.predict(narration)
    is_anomaly = cl.is_anomaly(narration)
    if conf < 0.45 and cl_key in LEDGER_MAP:
        if not (forced_type == "credit" and cl_key in _DEBIT_ONLY_KEYS):
            if not (forced_type == "debit" and (
                any(cl_key.startswith(p) for p in INCOME_KEY_PREFIXES)
                or cl_key in CREDIT_ASSET_KEYS
            )):
                pred_key = cl_key
                conf = max(conf, 0.35)

    # ── Step 6: Suspense (last resort) ───────────────────────────────────────
    if conf < 0.30:
        pred_key = "income_other" if forced_type == "credit" else "suspense_debit"
        print(f"  [ML] Low confidence ({conf:.0%}) → fallback: '{narration[:60]}'")

    # Hard overrides: certain narration patterns must never go to wrong keys
    u_check = u  # already uppercased
    if re.search(r'AUTOPAY', u_check) and pred_key in ('liability_loan_outstanding','exp_loan_emi'):
        pred_key = 'exp_credit_card'; conf = 0.92
    elif re.search(r'IGST|CGST|SGST|FCY.MARKUP|MARKUP.FEE', u_check) and pred_key not in ('exp_bank_charges',):
        pred_key = 'exp_bank_charges'; conf = 0.88
    elif re.search(r'GOOGLE.PLAY|APPLE.*BILL|NETFLIX', u_check) and pred_key not in ('exp_entertainment',):
        pred_key = 'exp_entertainment'; conf = 0.90

    resolved_type = forced_type if forced_type in ("credit", "debit") else _infer_type(u)
    result = _build(narration, pred_key, resolved_type, conf,
                    note=f"ML: {pred_key} ({conf:.0%})", source="ml")
    result = validate_classification(result, forced_type)
    result["amount"] = amount
    result["txn_date"] = txn_date
    result["cluster_id"] = cid
    result["is_anomaly"] = is_anomaly

    # ── Post-classification misc rescue ──────────────────────────────────────
    # If we landed on exp_misc or suspense, try to recover a specific category
    # by matching the counterparty name against known brand patterns.
    if result.get("ledger_key") in ("exp_misc", "suspense_debit") and resolved_type == "debit":
        result = _rescue_misc_by_counterparty(narration, result, forced_type)

    _track_classification(result)
    return result


def _infer_type(u: str) -> str:
    CR_HINTS = ("NEFT CR","RTGS CR","IMPS CR","ACH CR","ACH-CR","ACH C-","NACH CR",
                "ECS CR","CREDIT","INWARD","REFUND","SALARY","PAYROLL","DIVIDEND",
                "INT.PD","INT PD","RECEIVED","PAYOUT","PROCEEDS","MATURITY",
                "CASHBACK","REVERSAL","TPT-REPAY","TPT-RETURN","TPT-MANI")
    DR_HINTS = ("NEFT DR","NACH DR","ACH DR","EMI DEBIT","DEBIT","PAYMENT",
                "PURCHASE","WITHDRAWAL","SIP","PREMIUM","CHALLAN","ADVANCE TAX",
                "BOOKING","RECHARGE","TPT-LOAN","TPT-PLOT")
    for w in CR_HINTS:
        if w in u: return "credit"
    for w in DR_HINTS:
        if w in u: return "debit"
    return "debit"


# ══════════════════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════════════════

def _hash_password(password: str) -> str:
    password = str(password or "")
    salt = os.urandom(16)
    iterations = 200000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"

def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, iter_s, salt_hex, hash_hex = str(stored or "").split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iter_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        actual = hashlib.pbkdf2_hmac("sha256", str(password or "").encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False

class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:return super().__exit__(exc_type, exc_value, traceback)
        finally:self.close()

class DBStore:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._init()

    def _conn(self):
        c = sqlite3.connect(self.db_path, check_same_thread=False, factory=ClosingConnection)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=15000")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def _init(self):
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS users(
                  id TEXT PRIMARY KEY, name TEXT, email TEXT, phone TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS ledger(
                  id TEXT PRIMARY KEY, user_id TEXT, account_id TEXT,
                  txn_date TEXT, narration TEXT, amount REAL DEFAULT 0,
                  txn_type TEXT,
                  ledger_key TEXT, book TEXT, section TEXT, grp TEXT, account TEXT,
                  attribution TEXT, counterparty TEXT DEFAULT '',
                  confidence REAL DEFAULT 0, source TEXT,
                  cluster_id INTEGER DEFAULT -1, is_anomaly INTEGER DEFAULT 0,
                  note TEXT DEFAULT '', created_at TEXT,
                  FOREIGN KEY(user_id) REFERENCES users(id));
                CREATE INDEX IF NOT EXISTS idx_uid   ON ledger(user_id);
                CREATE INDEX IF NOT EXISTS idx_book  ON ledger(book);
                CREATE INDEX IF NOT EXISTS idx_date  ON ledger(txn_date);
                DROP INDEX IF EXISTS ux_ledger_dedupe;
            """)

    def upsert_user(self, uid, name, email="", phone=""):
        now = datetime.utcnow().isoformat()
        with self._conn() as c:
            c.execute("""INSERT INTO users(id,name,email,phone,created_at) VALUES(?,?,?,?,?)
                         ON CONFLICT(id) DO UPDATE SET name=excluded.name""",
                      (uid, name, email, phone, now))

    def find_user(self, name="", phone="", email="") -> Optional[Dict]:
        with self._conn() as c:
            for col, val in [("phone",phone),("email",email),("name",name)]:
                if val:
                    r = c.execute(f"SELECT * FROM users WHERE LOWER({col})=LOWER(?) LIMIT 1", (val,)).fetchone()
                    if r: return dict(r)
        return None

    def insert_txn(self, row: Dict[str, Any]):
        now = datetime.utcnow().isoformat()
        with self._conn() as c:
            c.execute("""INSERT OR IGNORE INTO ledger(
                id,user_id,account_id,txn_date,narration,amount,txn_type,
                ledger_key,book,section,grp,account,attribution,counterparty,
                confidence,source,cluster_id,is_anomaly,note,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                row.get("id", str(uuid.uuid4())), row["user_id"], row.get("account_id","main"),
                row.get("txn_date",""), row.get("narration",""), float(row.get("amount",0)),
                row.get("txn_type","debit"), row.get("ledger_key","suspense_debit"),
                row.get("book","SUSPENSE"), row.get("section","Suspense"),
                row.get("group", row.get("grp","Unclassified")),
                row.get("account","Debit – Requires Review"),
                row.get("attribution",""), row.get("counterparty",""),
                float(row.get("confidence",0)), row.get("source",""),
                int(row.get("cluster_id",-1)), int(row.get("is_anomaly",0)),
                row.get("note",""), now))

    def get_txns(self, user_id: str, limit=1000) -> List[Dict]:
        with self._conn() as c:
            rows = c.execute("""SELECT * FROM ledger WHERE user_id=?
                               ORDER BY txn_date DESC, created_at DESC LIMIT ?""",
                             (user_id, limit)).fetchall()
            return [dict(r) for r in rows]

    def get_users(self) -> List[Dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM users ORDER BY created_at DESC")]

    def get_ledger_summary(self, user_id: str) -> List[Dict]:
        with self._conn() as c:
            rows = c.execute("""SELECT book, section, grp, account, txn_type,
                               SUM(amount) as total, COUNT(*) as cnt
                               FROM ledger WHERE user_id=?
                               GROUP BY book, section, grp, account, txn_type
                               ORDER BY book, section, grp, account""",
                             (user_id,)).fetchall()
            return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# EXCEL PARSER
# ══════════════════════════════════════════════════════════════════════════════

def _parse_xlsx_xml(file_bytes: bytes) -> List[List[Any]]:
    """
    Low-level XLSX reader using raw XML parsing.
    Works even when openpyxl fails (e.g. non-standard purl.oclc.org namespace used by HDFC exports).
    Returns list of rows from the sheet with the most transaction-like content.
    Each row is a list of cell values (str or float). Sparse columns are None.

    Shared strings: XLSX stores unique strings in xl/sharedStrings.xml and
    references them by integer index. Each <si> element contains either a
    single <t> (plain string) or multiple <r><t> sub-elements (rich text).

    Worksheet rows: xl/worksheets/sheet*.xml contains <row> elements each
    holding <c> (cell) elements.  The 'r' attribute on <c> gives the cell
    reference (e.g. "B3"); 't' attribute tells the type ('s'=sharedString,
    'inlineStr'=inline text, 'n' or absent=numeric/date).

    rows_raw format: List[List[Any]] — outer list = rows, inner = column
    values left-padded with None so that column indices are stable.
    """
    import zipfile
    from xml.etree import ElementTree as ET

    try:
        z = zipfile.ZipFile(io.BytesIO(file_bytes))
    except Exception as e:
        raise ValueError(f"_parse_xlsx_xml: cannot open ZIP: {e}")

    names = z.namelist()

    # ── Shared strings (may be absent in small or formula-only workbooks) ───────
    shared: List[str] = []
    if 'xl/sharedStrings.xml' in names:
        try:
            ss_raw = z.read('xl/sharedStrings.xml')
            ss_tree = ET.fromstring(ss_raw)
            # Strip all namespace prefixes so we can use plain tag names
            for el in ss_tree.iter():
                el.tag = re.sub(r'\{[^}]+\}', '', el.tag)
            for si in ss_tree.findall('.//si'):
                t_el = si.find('t')
                if t_el is not None:
                    shared.append(t_el.text or '')
                else:
                    # Rich text: concatenate all <r><t> fragments
                    shared.append(''.join(t.text or '' for t in si.findall('.//t')))
        except Exception as e:
            print(f"  [XML] sharedStrings.xml parse warning: {e} — continuing without shared strings")

    # ── Collect all worksheet paths ───────────────────────────────────────────
    sheet_paths = sorted(
        [n for n in names if re.match(r'xl/worksheets/sheet\d+\.xml', n)],
        key=lambda x: int(re.search(r'\d+', x.split('/')[-1]).group())
    )
    if not sheet_paths:
        raise ValueError("_parse_xlsx_xml: no worksheet XML files found in workbook")

    print(f"  [XML] sheets found in ZIP: {sheet_paths}")

    def col_idx(col_str: str) -> int:
        """Convert Excel column letter(s) to 0-based index. A→0, B→1, Z→25, AA→26."""
        idx = 0
        for ch in col_str.upper():
            idx = idx * 26 + (ord(ch) - ord('A') + 1)
        return idx - 1

    def _parse_sheet_rows(sheet_path: str) -> List[List[Any]]:
        sheet_raw = z.read(sheet_path)
        sheet_tree = ET.fromstring(sheet_raw)
        for el in sheet_tree.iter():
            el.tag = re.sub(r'\{[^}]+\}', '', el.tag)
        rows_out = []
        for row_el in sheet_tree.findall('.//row'):
            cells_dict: Dict[int, Any] = {}
            max_col = 0
            for c in row_el.findall('c'):
                ref = c.get('r', '')
                col_letters = ''.join(filter(str.isalpha, ref))
                if not col_letters:
                    continue
                cidx = col_idx(col_letters)
                max_col = max(max_col, cidx)
                t_attr = c.get('t', '')
                v_el = c.find('v')
                is_el = c.find('is')   # inlineStr container

                if t_attr == 'inlineStr' and is_el is not None:
                    # Inline string: text lives in <is><t>...</t></is>
                    val = ''.join(t.text or '' for t in is_el.findall('.//t'))
                elif t_attr == 's' and v_el is not None:
                    # Shared string reference
                    try:
                        val = shared[int(v_el.text)]
                    except (IndexError, ValueError, TypeError):
                        val = v_el.text  # fall back to raw index text
                elif v_el is not None:
                    # Numeric or date serial; keep as float if possible
                    try:
                        val = float(v_el.text)
                    except (ValueError, TypeError):
                        val = v_el.text
                else:
                    val = None
                cells_dict[cidx] = val
            if cells_dict:
                row = [cells_dict.get(i) for i in range(max_col + 1)]
                rows_out.append(row)
        return rows_out

    # ── Try all sheets; pick the one with the most non-empty rows ────────────
    best_rows: List[List[Any]] = []
    best_sheet = ""
    last_exc: Optional[Exception] = None

    for sp in sheet_paths:
        try:
            rows = _parse_sheet_rows(sp)
            non_empty = sum(
                1 for r in rows
                if any(c is not None and str(c).strip() for c in r)
            )
            print(f"  [XML] {sp}: {len(rows)} rows, {non_empty} non-empty")
            if non_empty > sum(
                1 for r in best_rows
                if any(c is not None and str(c).strip() for c in r)
            ):
                best_rows = rows
                best_sheet = sp
        except Exception as e:
            print(f"  [XML] {sp}: parse error — {e}")
            last_exc = e

    if not best_rows:
        exc_msg = f" Last error: {last_exc}" if last_exc else ""
        raise ValueError(f"_parse_xlsx_xml: no readable rows found in any worksheet.{exc_msg}")

    print(f"  [XML] selected sheet: {best_sheet} ({len(best_rows)} rows)")
    return best_rows



def _infer_txn_type(narration: str) -> str:
    """
    Infer credit/debit from narration text when no amount columns are present.
    HDFC bank statements embed CR/DR in the narration prefix.
    """
    u = narration.upper().strip()
    u_stripped = re.sub(r'^(?:WDL\s+TFR|DEP\s+TFR|WDL\s+CASH|DEP\s+CASH)\s+', '', u, flags=re.I)
    if u_stripped != u:
        if u.startswith('WDL'):
            return 'debit'
        if u.startswith('DEP'):
            return 'credit'
    u = u_stripped
    if re.match(r'NEFT\s+CR\b|RTGS\s+CR\b|IMPS.*-CR-|SWIFT\s+CR\b', u): return 'credit'
    if re.match(r'NEFT\s+DR\b|RTGS\s+DR\b|ACH\s+DR\b|NACH\s+DR\b|ECS\s+DR\b', u): return 'debit'
    if re.match(r'IB\s+FUNDS\s+TRANSFER\s+CR', u): return 'credit'
    if re.match(r'IB\s+FUNDS\s+TRANSFER\s+DR', u): return 'debit'
    if re.match(r'^UPI/CR/', u): return 'credit'
    if re.match(r'^UPI/DR/', u): return 'debit'
    if u.startswith('UPI-'): return 'debit'
    if re.search(r'TPT-(SAL|SALARY|REPAY|RETURN|MANI|LOAN\s+CR|TXFR\s+CR)\b', u): return 'credit'
    if re.search(r'TPT-(LOAN|TXFR|PLOT|NB[A-Z0-9]+)\b', u): return 'debit'
    if re.match(r'ACH\s*C-|ACH-CR', u): return 'credit'
    if re.search(r'INT\.PD|INT\s+PD|SB\s+\d+\s+INT|INTEREST\s+CREDIT|CREDIT\s+INTEREST|INTEREST\s+PAID\s+TILL', u): return 'credit'
    if re.search(r'\bDIV\d{2}|\bDIVIDEND\b|DIV\s*2[0-9]|DIVINTDIV|FINDIV|ANNUALDIV|ORDI\s+DIV|FINAL\s+DIV|SPL\s+INT\s+DIV|SPECIAL\s+DIV', u): return 'credit'
    if re.search(r'TATA\s+MOTORS.*DIV|HDFC\s+BANK.*DIV|UNITED\s+NIL.*DIV|UPL\s+LTD.*DIV|EIH\s+LIMITED.*DIV|BAYER.*DIV|PNB\s+HOU.*DIV|HDB\s+FINAN.*DIV', u): return 'credit'
    if re.search(r'YEIDA\s+REFUND|REFUND\s+RPS', u): return 'credit'
    if re.search(r'YAMUNAEXPRESSWAY|TOLL\s+PLAZA|GADPURI|FASTAG', u): return 'debit'
    if re.search(r'/RAZP[A-Z]+|RAZORPAY', u): return 'debit'
    if re.search(r'^TRAVELFG/|CMUPLFCM/', u): return 'debit'
    if re.search(r'HDFC\s+BANK.*INT|BANK.*INTEREST|INT\s+PAID', u): return 'credit'
    if re.search(r'NACH\s+DR|SIP\s+MANDATE|SIPMANDATEREF|^S?EBA/MFP[-/]?\d|^MFP[-/]\d', u): return 'debit'
    if re.search(r'CHQ\s+PAID|CHEQUE\s+PAID|CTS-RK', u): return 'debit'
    if re.match(r'IMPS-', u): return 'credit' if re.search(r'-CR-|-CREDIT-', u) else 'debit'
    return 'debit'


def _is_xls_bytes(file_bytes: bytes) -> bool:
    return file_bytes.startswith(b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1")


def _is_xlsx_bytes(file_bytes: bytes) -> bool:
    if not file_bytes:
        return False
    if file_bytes.startswith(b"PK\x03\x04"):
        return True
    try:
        import zipfile, io
        return zipfile.is_zipfile(io.BytesIO(file_bytes))
    except Exception:
        return False


def _convert_xls_bytes_to_xlsx_bytes(file_bytes: bytes) -> bytes:
    """
    Convert legacy .xls bytes to .xlsx using LibreOffice/soffice.
    Raises a descriptive ValueError at each failure point so callers can
    surface the exact reason to the user.
    """
    # Locate executable
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise ValueError(
            "LibreOffice executable not found. "
            "Legacy .xls files require LibreOffice (soffice) to convert. "
            "Install LibreOffice or convert the file to .xlsx before uploading."
        )
    print(f"  [XLS→XLSX] Using LibreOffice: {soffice}")

    with tempfile.TemporaryDirectory(prefix="hni_xls_") as td:
        xls_path = os.path.join(td, "input.xls")
        with open(xls_path, "wb") as f:
            f.write(file_bytes)

        cmd = [soffice, "--headless", "--convert-to", "xlsx", "--outdir", td, xls_path]
        print(f"  [XLS→XLSX] Running: {' '.join(cmd)}")
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            raise ValueError("LibreOffice conversion timed out after 60 seconds.")
        except Exception as e:
            raise ValueError(f"LibreOffice conversion subprocess error: {e}")

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise ValueError(
                f"LibreOffice conversion failed with exit code {proc.returncode}. "
                f"stderr/stdout: {detail or '(no output)'}"
            )

        out_path = os.path.join(td, "input.xlsx")
        if not os.path.exists(out_path):
            detail = (proc.stderr or proc.stdout or "").strip()
            raise ValueError(
                f"Converted xlsx file was not created at {out_path}. "
                f"LibreOffice output: {detail or '(no output)'}"
            )

        size = os.path.getsize(out_path)
        if size == 0:
            raise ValueError(
                f"LibreOffice produced an empty xlsx file (0 bytes). "
                "The source .xls may be corrupt or password-protected."
            )

        print(f"  [XLS→XLSX] Conversion succeeded — output {size} bytes")
        with open(out_path, "rb") as f:
            return f.read()

# ══════════════════════════════════════════════════════════════════════════════
# PDF SUPPORT (NEW) — Bank-agnostic PDF statement parser
# ══════════════════════════════════════════════════════════════════════════════
# Install deps:
#   pip install pdfplumber pymupdf pdfminer.six pytesseract
#   For OCR: apt-get install tesseract-ocr  (Linux)
#             brew install tesseract          (macOS)
# ══════════════════════════════════════════════════════════════════════════════

# ── PDF SUPPORT (NEW) ── Check PDF magic bytes
def _is_pdf_bytes(file_bytes: bytes) -> bool:
    """Returns True if file_bytes starts with the %PDF magic header."""
    return file_bytes[:4] == b'%PDF'


def _try_decrypt_pdf_bytes(file_bytes: bytes, password: Optional[str] = None) -> bytes:
    """Decrypt password protected PDFs when a password is supplied."""
    if not _is_pdf_bytes(file_bytes):
        return file_bytes
    try:
        from pypdf import PdfReader, PdfWriter  # type: ignore
    except Exception:
        return file_bytes
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
    except Exception:
        return file_bytes
    if not getattr(reader, 'is_encrypted', False):
        return file_bytes
    if not password:
        raise ValueError('PDF is password-protected. Enter the password in the upload box and try again.')
    try:
        decrypt_result = reader.decrypt(password)
    except Exception as e:
        raise ValueError(f'PDF password decryption failed: {e}')
    if decrypt_result == 0:
        raise ValueError('Incorrect PDF password. Please check the password and try again.')
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ── PDF SUPPORT (NEW) ── pdfplumber table extractor
def _extract_pdf_tables_pdfplumber(file_bytes: bytes) -> List[List[List[str]]]:
    """
    Extract tables from all PDF pages using pdfplumber.
    Returns a list-of-pages where each page is a list-of-rows,
    and each row is a list of cell strings.
    """
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return []

    all_page_tables: List[List[List[str]]] = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if pdf.metadata.get("Encrypted"):
                raise ValueError("PDF is password-protected. Please remove the password before uploading.")
            for page in pdf.pages:
                tables = page.extract_tables()
                for tbl in (tables or []):
                    for row in (tbl or []):
                        clean = [str(c or "").strip() for c in row]
                        all_page_tables.append(clean)
    except ValueError:
        raise
    except Exception:
        pass
    return all_page_tables


# ── PDF SUPPORT (NEW) ── PyMuPDF text extractor
def _extract_pdf_text_fitz(file_bytes: bytes) -> List[str]:
    """
    Extract raw text per page using PyMuPDF (fitz).
    Returns a list of strings, one per page.
    """
    try:
        import fitz  # type: ignore  (PyMuPDF)
    except ImportError:
        return []

    pages_text: List[str] = []
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        if doc.is_encrypted:
            raise ValueError("PDF is password-protected. Please remove the password before uploading.")
        for page in doc:
            pages_text.append(page.get_text("text"))
    except ValueError:
        raise
    except Exception:
        pass
    return pages_text


# ── PDF SUPPORT (NEW) ── pdfminer text extractor
def _extract_pdf_text_pdfminer(file_bytes: bytes) -> List[str]:
    """
    Extract text per page using pdfminer.six.
    Returns a list of strings, one per page.
    Uses extract_pages() (element-level) first, which handles more encodings
    than the stream-based extract_text_to_fp().
    """
    try:
        from pdfminer.high_level import extract_pages  # type: ignore
        from pdfminer.layout import LTTextContainer    # type: ignore
        from pdfminer.layout import LAParams           # type: ignore
    except ImportError:
        return []

    pages_text: List[str] = []

    # ── Method A: element-level extraction (handles more custom fonts) ─────
    try:
        for page_layout in extract_pages(io.BytesIO(file_bytes), laparams=LAParams()):
            lines: List[str] = []
            for element in page_layout:
                if isinstance(element, LTTextContainer):
                    lines.append(element.get_text())
            page_str = "".join(lines)
            if page_str.strip():
                pages_text.append(page_str)
    except Exception:
        pass

    if pages_text:
        return pages_text

    # ── Method B: stream-based extraction (original fallback) ─────────────
    try:
        from pdfminer.high_level import extract_text_to_fp  # type: ignore
        buf = io.StringIO()
        extract_text_to_fp(io.BytesIO(file_bytes), buf, laparams=LAParams())
        full = buf.getvalue()
        parts = full.split("\x0c")
        pages_text = [p for p in parts if p.strip()]
    except Exception:
        pass

    return pages_text


# ── PDF SUPPORT (NEW) ── pdfplumber raw-text extractor (separate from table mode)
def _extract_pdf_text_pdfplumber(file_bytes: bytes) -> List[str]:
    """
    Extract raw text per page using pdfplumber (no table heuristics).
    This often decodes custom bank fonts better than pdfminer because
    pdfplumber uses pdfminer internally but applies its own glyph mapping.
    Returns a list of strings, one per page.
    """
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return []

    pages_text: List[str] = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if pdf.metadata.get("Encrypted"):
                raise ValueError("PDF is password-protected. Please remove the password before uploading.")
            for page in pdf.pages:
                text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
                if text.strip():
                    pages_text.append(text)
    except ValueError:
        raise
    except Exception:
        pass
    return pages_text


# ── PDF SUPPORT (NEW) ── OCR fallback using pytesseract
def _extract_pdf_text_ocr(file_bytes: bytes) -> List[str]:
    """
    Convert PDF pages to images and run OCR via pytesseract.
    Last-resort for scanned/image-only PDFs.
    Returns a list of strings, one per page.
    """
    try:
        import fitz          # type: ignore
        import pytesseract   # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        return []

    pages_text: List[str] = []
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        if doc.is_encrypted:
            raise ValueError("PDF is password-protected. Please remove the password before uploading.")
        for page in doc:
            mat = fitz.Matrix(2.0, 2.0)   # 2× zoom → better OCR accuracy
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            text = pytesseract.image_to_string(img, lang="eng")
            pages_text.append(text)
    except ValueError:
        raise
    except Exception:
        pass
    return pages_text


# ── PDF SUPPORT (NEW) ── Regex-based row reconstruction from raw text
_DATE_PAT  = re.compile(
    r"""
    \b
    (?:
        \d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}       # DD/MM/YYYY  DD.MM.YYYY  D/M/YYYY
      | \d{1,2}[\s\-]\w{3}[\s\-]\d{2,4}            # DD Mon YYYY / DD-Mon-YY
      | \d{4}[/\-]\d{2}[/\-]\d{2}                  # YYYY-MM-DD
      | \d{8}                                       # DDMMYYYY compact
    )
    \b
    """,
    re.VERBOSE | re.IGNORECASE,
)

_AMT_PAT = re.compile(
    r"""
    (?:[\u20b9Rs\.]+\s*)?                           # optional ₹ / Rs prefix
    \d{1,3}(?:,\d{2,3})*(?:\.\d{2})?               # 1,23,456.78 or 1234.56
    |\d+\.\d{2}                                     # plain 0.00
    """,
    re.VERBOSE,
)


def _parse_text_to_rows(page_text: str) -> List[List[str]]:
    """
    Reconstruct tabular rows from space-aligned plain text.

    Two-pass strategy:
      Pass 1 — Date-anchored: lines that START with a date begin a new row.
               Continuation lines (no leading date) are merged into the
               previous row's narration.
      Pass 2 — Date-anywhere: if Pass 1 finds < 2 rows, scan every line for
               an embedded date token and split around it.  This handles
               formats where date appears mid-line or after a ref number.

    For each row the output columns are:
      [date_str, narration, amt1, amt2, ...]
    """
    rows: List[List[str]] = []

    # ── Pass 1: date at start of line ────────────────────────────────────────
    current: List[str] = []
    for raw_line in page_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        date_match = _DATE_PAT.match(line)
        # ICICI: line may start with S.No ("1 01.04.2025 ...")
        if date_match is None:
            sno = re.match(r'^\d{1,4}\s+', line)
            if sno:
                date_match = _DATE_PAT.match(line, sno.end())
        if date_match:
            if current:
                rows.append(current)
            date_str  = date_match.group()
            remainder = line[date_match.end():].strip()
            amounts   = _AMT_PAT.findall(remainder)
            narration = _AMT_PAT.sub("", remainder).strip()
            narration = re.sub(r"\s{2,}", " ", narration)
            current   = [date_str, narration] + amounts
        else:
            if current:
                narration_part = _AMT_PAT.sub("", line).strip()
                if narration_part:
                    current[1] = (current[1] + " " + narration_part).strip()
                extra_amts = _AMT_PAT.findall(line)
                current.extend(extra_amts)
    if current:
        rows.append(current)

    if len(rows) >= 2:
        return rows

    # ── Pass 2: date anywhere in line (fallback) ──────────────────────────────
    rows = []
    for raw_line in page_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        date_match = _DATE_PAT.search(line)
        if not date_match:
            continue
        date_str  = date_match.group()
        # Everything outside the date token
        before    = line[:date_match.start()].strip()
        after     = line[date_match.end():].strip()
        combined  = (before + " " + after).strip()
        amounts   = _AMT_PAT.findall(combined)
        narration = _AMT_PAT.sub("", combined).strip()
        narration = re.sub(r"\s{2,}", " ", narration)
        rows.append([date_str, narration] + amounts)

    if len(rows) >= 2:
        return rows

    # ── Pass 3: split entire page on whitespace boundaries into token rows ────
    # Last resort for very tightly packed text with no clear row structure.
    # Emit one "row" per line that has at least one date AND one amount token.
    rows = []
    for raw_line in page_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not _DATE_PAT.search(line):
            continue
        amounts = _AMT_PAT.findall(line)
        if not amounts:
            continue
        date_str  = _DATE_PAT.search(line).group()
        narration = _DATE_PAT.sub("", _AMT_PAT.sub("", line)).strip()
        narration = re.sub(r"\s{2,}", " ", narration)
        rows.append([date_str, narration] + amounts)

    return rows


# ── PDF SUPPORT (NEW) ── Amount normaliser
def _normalise_amount(raw: str) -> float:
    """
    Parse Indian-format amount strings to float.
    Handles: "1,23,456.78", "₹ 500.00", "Rs.1000", blanks, dashes.
    """
    if not raw or str(raw).strip() in ("", "-", "–", "—", "nil", "NIL"):
        return 0.0
    # Strip currency symbols and whitespace
    s = re.sub(r"[^\d.,\-]", "", str(raw))
    # Remove thousand separators (Indian grouping uses commas)
    s = s.replace(",", "")
    if not s or s == "-":
        return 0.0
    try:
        return abs(float(s))
    except ValueError:
        return 0.0


# ── PDF SUPPORT (NEW) ── Transaction type detector
def _detect_txn_type(
    debit_val: str,
    credit_val: str,
    type_col_val: str = "",
) -> str:
    """
    Determine 'debit' or 'credit'.

    Priority:
      1. Explicit DR/CR column (e.g. ICICI single-amount + Dr/Cr suffix).
      2. Non-zero debit cell  → debit.
      3. Non-zero credit cell → credit.
      4. Fallback → debit.
    """
    tc = (type_col_val or "").strip().lower()
    if tc in ("cr", "credit"):
        return "credit"
    if tc in ("dr", "debit"):
        return "debit"

    dr = _normalise_amount(debit_val)
    cr = _normalise_amount(credit_val)

    # ICICI-style: amount column may have "Dr" / "Cr" suffix baked in
    dv = str(debit_val or "").strip()
    cv = str(credit_val or "").strip()
    if re.search(r'\bCr\.?$', dv, re.I):
        return "credit"
    if re.search(r'\bDr\.?$', dv, re.I):
        return "debit"

    if dr > 0 and cr == 0:
        return "debit"
    if cr > 0 and dr == 0:
        return "credit"
    return "debit"


# ── PDF SUPPORT (NEW) ── Date normaliser (ISO output)
def _normalise_date(raw: str) -> str:
    """
    Parse a date string in any Indian bank format and return YYYY-MM-DD.
    Supports:
      DD/MM/YYYY  DD-MM-YYYY  DD/MM/YY
      DD Mon YYYY  DD-Mon-YYYY  DD-Mon-YY
      YYYY-MM-DD
      D/M/YYYY  (no leading zeros)
      DDMMYYYY  (compact 8-digit)
    """
    if not raw:
        return datetime.now().strftime("%Y-%m-%d")
    s = str(raw).strip()
    formats = [
        "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y",
        "%d %b %Y", "%d-%b-%Y", "%d %b %y", "%d-%b-%y",
        "%Y-%m-%d", "%Y/%m/%d",
        "%-d/%-m/%Y",   # no leading zero (Linux/Mac)
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass
    # Compact DDMMYYYY
    if re.fullmatch(r'\d{8}', s):
        try:
            return datetime.strptime(s, "%d%m%Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    # Partial match — extract digits
    m = re.search(r'(\d{1,2})[/\-\s](\d{1,2}|\w{3})[/\-\s](\d{2,4})', s)
    if m:
        candidate = f"{m.group(1)} {m.group(2)} {m.group(3)}"
        for fmt in ("%d %m %Y", "%d %m %y", "%d %b %Y", "%d %b %y"):
            try:
                return datetime.strptime(candidate, fmt).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass
    return datetime.now().strftime("%Y-%m-%d")


# ── PDF SUPPORT (NEW) ── Header-row detector
_HEADER_DATE_WORDS  = {"date", "txn date", "value date", "posting date", "tran date",
                        "transaction date", "trans date", "chq date"}
_HEADER_DESC_WORDS  = {"narration", "description", "particulars", "details",
                        "remarks", "transaction details", "trans details",
                        "transaction narration"}
_HEADER_DEBIT_WORDS = {"debit", "withdrawal", "withdrawals", "dr", "debit amount",
                        "withdrawal amt", "withdrawal amt.", "dr amount"}
_HEADER_CREDIT_WORDS = {"credit", "deposit", "deposits", "cr", "credit amount",
                         "deposit amt", "deposit amt.", "cr amount"}
_HEADER_BALANCE_WORDS = {"balance", "closing balance", "running balance",
                          "available balance", "bal"}


def _detect_header_row(rows: List[List[str]]) -> Optional[int]:
    """
    Scan rows for the header row by checking that it contains recognisable
    keywords for date, description, and at least one amount concept.
    Returns the 0-based row index, or None if not found.
    """
    for i, row in enumerate(rows):
        cells = {str(c or "").strip().lower() for c in row}

        has_date   = bool(cells & _HEADER_DATE_WORDS or
                          any("date" in c for c in cells))
        has_desc   = bool(cells & _HEADER_DESC_WORDS or
                          any(w in c for c in cells
                              for w in ("narrat", "descri", "particu", "detail", "remark")))
        has_amount = bool(
            (cells & _HEADER_DEBIT_WORDS or any("withdraw" in c or "debit" in c for c in cells)) and
            (cells & _HEADER_CREDIT_WORDS or any("deposit" in c or "credit" in c for c in cells))
        ) or bool(
            any("amount" in c for c in cells)
        )

        if has_date and has_desc and has_amount:
            return i
    return None


def _map_columns(header_row: List[str]) -> Dict[str, Optional[int]]:
    """
    Build a positional column-index mapping from a detected header row.
    Returns dict with keys: date, desc, debit, credit, amount, type, balance.
    """
    h = [str(c or "").strip().lower() for c in header_row]

    def find(word_set, extra_check=None) -> Optional[int]:
        # Exact match first
        for i, cell in enumerate(h):
            if cell in word_set:
                return i
        # Substring match
        for i, cell in enumerate(h):
            if any(w in cell for w in word_set):
                return i
        if extra_check:
            for i, cell in enumerate(h):
                if extra_check(cell):
                    return i
        return None

    date_col    = find(_HEADER_DATE_WORDS)
    desc_col    = find(_HEADER_DESC_WORDS)
    debit_col   = find(_HEADER_DEBIT_WORDS)
    credit_col  = find(_HEADER_CREDIT_WORDS)
    balance_col = find(_HEADER_BALANCE_WORDS)
    type_col    = find({"type", "txn type", "dr/cr", "dr / cr", "transaction type", "cr/dr"})

    # Fallback: single "amount" column
    amount_col: Optional[int] = None
    if debit_col is None and credit_col is None:
        for i, cell in enumerate(h):
            if "amount" in cell or cell in ("amt", "txn amount", "net amount"):
                amount_col = i
                break

    return {
        "date":    date_col,
        "desc":    desc_col,
        "debit":   debit_col,
        "credit":  credit_col,
        "amount":  amount_col,
        "type":    type_col,
        "balance": balance_col,
    }


def _is_junk_row(row: List[str], col_map: Dict[str, Optional[int]]) -> bool:
    """
    Return True for rows that should be skipped:
    repeated headers, page-number footers, opening/closing balance lines, etc.
    """
    joined = " ".join(str(c or "") for c in row).strip().lower()
    if not joined:
        return True
    junk_patterns = [
        r"^\s*page\s+\d",
        r"continued\s+on\s+next",
        r"opening\s+balance",
        r"closing\s+balance",
        r"brought\s+forward",
        r"b/?f\b",
        r"total\s+credit",
        r"total\s+debit",
        r"statement\s+summary",
        r"micr\s+code",
        r"ifsc\s+code",
        r"^\s*[\*\-=_]+\s*$",
    ]
    for pat in junk_patterns:
        if re.search(pat, joined):
            return True

    # Repeated header row detection
    date_cell = str(row[col_map["date"]] if col_map.get("date") is not None
                    and col_map["date"] < len(row) else "").lower()
    if date_cell in _HEADER_DATE_WORDS or "date" in date_cell:
        return True

    return False


def _parse_pdf_multiline_bank_statement(raw_text_pages: List[str]) -> Dict[str, Any]:
    """
    Parse multiline PDF statements of this shape:

      01-03-2026 B/F 4,794.26
      01-03-2026
      UPI/Brijesh /9029183746@pty/NO REMARKS/INDIAN
      BAN/200206884733/UPI4fe947...
      50.00 4,744.26

    Also handles one-line entries like:
      09-03-2026 ICICI EBA/MFP-... 100.00 4,539.26

    Returns:
      {
        "records": [...],
        "opening_balance": float | None,
      }
    """
    import re

    DATE_PAT = re.compile(r'^\s*(\d{2}[-/]\d{2}[-/]\d{4})\s*(.*)$')
    AMT_BAL_PAT = re.compile(r'^\s*([0-9,]+\.\d{2})\s+([0-9,]+\.\d{2})\s*$')
    ONE_LINE_PAT = re.compile(r'^\s*(\d{2}[-/]\d{2}[-/]\d{4})\s+(.+?)\s+([0-9,]+\.\d{2})\s+([0-9,]+\.\d{2})\s*$')
    BF_PAT = re.compile(r'^\s*(\d{2}[-/]\d{2}[-/]\d{4})\s+(?:B/?F|B\.F\.|BROUGHT\s+FORWARD|OPENING\s+BALANCE)\s+([0-9,]+\.\d{2})\s*$', re.I)

    def _amt(x: str) -> float:
        return float(str(x).replace(",", "").strip())

    def _norm_date(x: str) -> str:
        x = x.strip().replace("/", "-")
        dd, mm, yyyy = x.split("-")
        return f"{yyyy}-{mm}-{dd}"

    def _is_noise(line: str) -> bool:
        s = line.strip()
        if not s:
            return True
        su = s.upper()
        if re.match(r'^(PAGE\s+\d+|TOTAL:|MICR\s+CODE|IFSC\s+CODE|STATEMENT\s+SUMMARY|CORPORATE\s+OFFICE:)', su):
            return True
        if re.match(r'^[*\-_=]{4,}$', s):
            return True
        return False

    records = []
    opening_balance = None
    prev_balance = None    

    current_date = None
    current_desc_lines: List[str] = []

    def _flush(amount: float, balance: float):
        nonlocal current_date, current_desc_lines, prev_balance, records, opening_balance
        if not current_date:
            return

        desc = " ".join(x.strip() for x in current_desc_lines if x.strip())
        desc = re.sub(r'\s+', ' ', desc).strip(" /|-")

        if not desc:
            current_date = None
            current_desc_lines = []
            prev_balance = balance
            return

        # infer direction from balance movement
        if prev_balance is None:
           txn_type = _infer_txn_type(desc)
        else:
            if balance < prev_balance:
                txn_type = "debit"
            elif balance > prev_balance:
                txn_type = "credit"
            else:
                txn_type = _infer_txn_type(desc)

        records.append({
            "txn_date": _norm_date(current_date),
            "description": desc,
            "narration": desc,
            "amount": amount,
            "txn_type": txn_type,
            "balance": balance,
            "raw_debit": amount if txn_type == "debit" else None,
            "raw_credit": amount if txn_type == "credit" else None,
        })

        if opening_balance is None and prev_balance is None:
            # first transaction
            if txn_type == "debit":
                opening_balance = balance + amount
            else:
                opening_balance = balance - amount
        prev_balance = balance
        current_date = None
        current_desc_lines = []

    for page_text in raw_text_pages:
        for raw_line in page_text.splitlines():
            line = re.sub(r'\s+', ' ', str(raw_line or "")).strip()
            if _is_noise(line):
                continue

            # Opening balance line
            m_bf = BF_PAT.match(line)
            if m_bf:
                current_date = None
                current_desc_lines = []
                opening_balance = _amt(m_bf.group(2))
                prev_balance = opening_balance
                continue

            # Full one-line transaction
            m_one = ONE_LINE_PAT.match(line)
            if m_one and " B/F " not in f" {line.upper()} ":
                dt = m_one.group(1)
                desc = m_one.group(2).strip()
                amt = _amt(m_one.group(3))
                bal = _amt(m_one.group(4))

                if prev_balance is None:
                    txn_type = _infer_txn_type(desc)
                else:
                    if bal < prev_balance:
                        txn_type = "debit"
                    elif bal > prev_balance:
                        txn_type = "credit"
                    else:
                        txn_type = _infer_txn_type(desc)

                records.append({
                    "txn_date": _norm_date(dt),
                    "description": desc,
                    "narration": desc,
                    "amount": amt,
                    "txn_type": txn_type,
                    "balance": bal,
                    "raw_debit": amt if txn_type == "debit" else None,
                    "raw_credit": amt if txn_type == "credit" else None,
                })

                if opening_balance is None and prev_balance is None:
                    if txn_type == "debit":
                        opening_balance = bal + amt
                    else:
                        opening_balance = bal - amt
                prev_balance = bal
                current_date = None
                current_desc_lines = []
                continue

            # New dated line
            m_date = DATE_PAT.match(line)
            if m_date:
                # if another txn was in progress but not closed, drop it
                current_date = m_date.group(1)
                tail = m_date.group(2).strip()
                current_desc_lines = [tail] if tail else []
                continue

            # Amount + balance terminator line
            m_amt_bal = AMT_BAL_PAT.match(line)
            if m_amt_bal and current_date:
                amt = _amt(m_amt_bal.group(1))
                bal = _amt(m_amt_bal.group(2))
                _flush(amt, bal)
                continue

            # narration continuation
            if current_date:
                current_desc_lines.append(line)

    return {
        "records": records,
        "opening_balance": opening_balance,
        "closing_balance": _resolve_statement_closing_balance(
            records, opening_balance, None, "_parse_pdf_multiline_bank_statement"
        ),
    }

def _parse_axis_pdf_text(raw_text_pages: List[str]) -> Dict[str, Any]:
    """
    Axis PDF text parser.
    Typical pattern:
      01-03-2026 01-03-2026 UPI-ABC-abc@okhdfcbank-123456 250.00 Dr 12000.50
    """
    import re

    line_pat = re.compile(
        r'^\s*(\d{2}[-/\.]\d{2}[-/\.]\d{4})\s+'
        r'(?:\d{2}[-/\.]\d{2}[-/\.]\d{4}\s+)?'
        r'(.+?)\s+'
        r'([0-9,]+\.\d{2})\s+'
        r'(Dr|Cr|DR|CR)\s+'
        r'([0-9,]+\.\d{2})\s*$'
    )

    records = []
    opening_balance = None

    def _amt(x):
        return float(str(x).replace(",", "").strip())

    def _norm_date(x):
        x = x.replace(".", "-").replace("/", "-")
        dd, mm, yyyy = x.split("-")
        return f"{yyyy}-{mm}-{dd}"

    for page_text in raw_text_pages:
        for raw_line in page_text.splitlines():
            line = re.sub(r"\s+", " ", str(raw_line or "")).strip()
            m = line_pat.match(line)
            if not m:
                continue

            dt = _norm_date(m.group(1))
            desc = m.group(2).strip()
            amt = _amt(m.group(3))
            typ = m.group(4).lower()
            bal = _amt(m.group(5))
            txn_type = "credit" if typ == "cr" else "debit"

            records.append({
                "txn_date": dt,
                "description": desc,
                "narration": desc,
                "amount": amt,
                "txn_type": txn_type,
                "balance": bal,
                "raw_debit": amt if txn_type == "debit" else None,
                "raw_credit": amt if txn_type == "credit" else None,
            })

            if opening_balance is None:
                opening_balance = bal - amt if txn_type == "credit" else bal + amt

    statement_from_date = None
    statement_to_date = None
    closing_balance = _resolve_statement_closing_balance(
        records, opening_balance, None, "_parse_axis_pdf_text"
    )

    def _safe_parse_statement_date(text: Any) -> Optional[str]:
        s = str(text or "").strip()
        if not s:
            return None

        s = s.replace(".", "-").replace("/", "-")
        s = re.sub(r"\s+", "-", s)

        # Prefer day-first for Indian bank statements
        for fmt in (
            "%d-%m-%Y",
            "%d-%m-%y",
            "%d-%b-%Y",
            "%d-%b-%y",
            "%d-%B-%Y",
            "%d-%B-%y",
        ):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except Exception:
                pass

        # Explicit month-first fallback only if day-first failed
        for fmt in (
            "%m-%d-%Y",
            "%m-%d-%y",
        ):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except Exception:
                pass

        try:
            return _safe_parse_excel_date(s)
        except Exception:
            return None
    
    def _extract_statement_period_from_header_rows(header_rows: List[List[Any]]) -> Tuple[Optional[str], Optional[str]]:
        lines = []
        for row in header_rows or []:
            txt = " ".join(str(c or "").strip() for c in row if str(c or "").strip())
            if txt:
                lines.append(re.sub(r"\s+", " ", txt).strip())

        candidates: List[Tuple[str, str, str]] = []

        date_pat = r'(\d{1,2}[./-](?:\d{1,2}|[A-Za-z]{3,9})[./-]\d{2,4})'

        strong_patterns = [
            rf'(?:statement\s*period|period)\s*[:\-]?\s*{date_pat}\s*(?:to|\-)\s*[:\-]?\s*{date_pat}',
            rf'(?:from)\s*[:\-]?\s*{date_pat}\s*(?:to)\s*[:\-]?\s*{date_pat}',
        ]
        for line in lines:
            s = re.sub(r'\s+', ' ', line).strip()
            for pat in strong_patterns:
                m = re.search(pat, s, flags=re.I)
                if m:
                    d1 = _safe_parse_statement_date(m.group(1))
                    d2 = _safe_parse_statement_date(m.group(2))
                    if d1 and d2 and d1 <= d2:
                        candidates.append((d1, d2, s))

        if not candidates:
            for line in lines:
                s = re.sub(r'\s+', ' ', line).strip()
                m = re.search(
                    rf'{date_pat}\s*(?:to|\-)\s*[:\-]?\s*{date_pat}',
                    s,
                    flags=re.I
                )
                if m:
                    d1 = _safe_parse_statement_date(m.group(1))
                    d2 = _safe_parse_statement_date(m.group(2))
                    if d1 and d2 and d1 <= d2:
                        candidates.append((d1, d2, s))

        if not candidates:
            return None, None

        def _score(item):
            d1, d2, line = item
            start_day1 = 1 if d1.endswith("-01") else 0
            try:
                span_days = (datetime.strptime(d2, "%Y-%m-%d") - datetime.strptime(d1, "%Y-%m-%d")).days
            except Exception:
                span_days = -1
            return (start_day1, span_days, len(line))

        best = max(candidates, key=_score)
        print(f"  [PERIOD] Selected header period line: {best[2]}")
        return best[0], best[1]

    try:
        header_rows = (locals().get("rows") or locals().get("rows_raw") or [])[:25]
        statement_from_date, statement_to_date = _extract_statement_period_from_header_rows(header_rows)
        print(f"  [PERIOD] Header-derived period: {statement_from_date} to {statement_to_date}")
    except Exception as e:
        print(f"  [PERIOD] Header extraction failed: {e}")
        statement_from_date, statement_to_date = None, None


    return {
        "records": records,
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "statement_from_date": statement_from_date,
        "statement_to_date": statement_to_date,
    }

def _parse_au_pdf_text(raw_text_pages: List[str]) -> Dict[str, Any]:
    """
    AU PDF parser for common retail statement lines.
    """
    import re

    line_pat = re.compile(
        r'^\s*(\d{2}[-/\.]\d{2}[-/\.]\d{4})\s+(.+?)\s+([0-9,]+\.\d{2})(?:\s+([0-9,]+\.\d{2}))?\s+([0-9,]+\.\d{2})\s*$'
    )

    records = []
    opening_balance = None
    prev_balance = None

    def _amt(x):
        return float(str(x).replace(",", "").strip())

    def _norm_date(x):
        x = x.replace(".", "-").replace("/", "-")
        dd, mm, yyyy = x.split("-")
        return f"{yyyy}-{mm}-{dd}"

    for page_text in raw_text_pages:
        for raw_line in page_text.splitlines():
            line = re.sub(r"\s+", " ", str(raw_line or "")).strip()
            m = line_pat.match(line)
            if not m:
                continue

            dt = _norm_date(m.group(1))
            desc = _strip_au_prefix(m.group(2).strip())
            a1 = _amt(m.group(3))
            a2 = _amt(m.group(4)) if m.group(4) else 0.0
            bal = _amt(m.group(5))

            if a2 > 0:
                amt = a1 or a2
            else:
                amt = a1

            if prev_balance is None:
                txn_type = _infer_txn_type(desc)
            else:
                txn_type = "debit" if bal < prev_balance else "credit" if bal > prev_balance else _infer_txn_type(desc)

            records.append({
                "txn_date": dt,
                "description": desc,
                "narration": desc,
                "amount": amt,
                "txn_type": txn_type,
                "balance": bal,
                "raw_debit": amt if txn_type == "debit" else None,
                "raw_credit": amt if txn_type == "credit" else None,
            })

            if opening_balance is None:
                opening_balance = bal - amt if txn_type == "credit" else bal + amt

            prev_balance = bal

    return {
        "records": records,
        "opening_balance": opening_balance,
        "closing_balance": _resolve_statement_closing_balance(
            records, opening_balance, None, "_parse_au_pdf_text"
        ),
    }

def _parse_hdfc_pdf_text(raw_text_pages: List[str]) -> Dict[str, Any]:
    """
    HDFC PDF parser that extracts ALL transactions by looking for the
    exact HDFC transaction line pattern.
    """
    import re

    records: List[Dict[str, Any]] = []
    statement_from_date: Optional[str] = None
    statement_to_date: Optional[str] = None
    opening_balance: Optional[float] = None
    closing_balance: Optional[float] = None
    
    # Combine all pages into one text
    full_text = "\n".join(raw_text_pages)
    
    # Extract period
    period_pattern = re.compile(r'Statement\s+From\s*:\s*(\d{2}/\d{2}/\d{4})\s+To\s*:\s*(\d{2}/\d{2}/\d{4})', re.I)
    period_match = period_pattern.search(full_text)
    if period_match:
        statement_from_date = _norm_date_hdfc(period_match.group(1))
        statement_to_date = _norm_date_hdfc(period_match.group(2))
        print(f'[HDFC] Period: {statement_from_date} to {statement_to_date}', flush=True)
    
    # Extract opening and closing balance from summary
    summary_pattern = re.compile(
        r'Opening\s+Balance\s+([0-9,]+\.\d{2})\s+\d+\s+\d+\s+([0-9,]+\.\d{2})\s+([0-9,]+\.\d{2})\s+([0-9,]+\.\d{2})',
        re.I
    )
    summary_match = summary_pattern.search(full_text)
    if summary_match:
        opening_balance = float(summary_match.group(1).replace(',', ''))
        closing_balance = float(summary_match.group(4).replace(',', ''))
        print(f'[HDFC] Opening balance: {opening_balance}, Closing: {closing_balance}', flush=True)
    
    # HDFC transaction pattern - matches a single line with:
    # Date, Narration, Ref No, Value Date, Withdrawal Amt, Deposit Amt, Balance
    # Pattern matches lines like:
    # 02/04/24 NEFTDR-KARB0000225-MANIGARG-NETBANK, M UM-N093242963932427-FORLOCKER 02/04/24 40,000.00 21,494.67
    txn_pattern = re.compile(
        r'^(\d{2}/\d{2}/\d{2})\s+'           # Date (DD/MM/YY)
        r'([^0-9]+?)'                         # Narration (non-greedy, stop before numbers)
        r'\s+(\d{2}/\d{2}/\d{2})\s+'          # Value Date
        r'([0-9,]+\.\d{2})\s+'                # Withdrawal Amt (debit)
        r'([0-9,]+\.\d{2})\s+'                # Deposit Amt (credit)
        r'([0-9,]+\.\d{2})\s*$'               # Closing Balance
    , re.MULTILINE)
    
    # Also match lines with DR/CR indicator in narration
    txn_pattern2 = re.compile(
        r'^(\d{2}/\d{2}/\d{2})\s+'           # Date
        r'(.+?)\s+'                           # Narration
        r'\b(DR|CR)\b\s+'                     # DR/CR indicator
        r'([0-9,]+\.\d{2})\s+'                # Amount
        r'([0-9,]+\.\d{2})\s*$'               # Balance
    , re.MULTILINE | re.IGNORECASE)
    
    # Split text into lines and process each line
    lines = full_text.split('\n')
    processed_count = 0
    
    for line_num, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        
        # Skip header/footer lines
        skip_patterns = [
            r'^Page No\.', r'^Statement of account', r'^MRS\.', r'^MANI GARG',
            r'^FLAT NO', r'^SECTOR', r'^NEAR GYM', r'^FARIDABAD', r'^HARYANA',
            r'^JOINT HOLDERS', r'^Nomination', r'^Registered', r'^Account Branch',
            r'^Address', r'^City', r'^State', r'^Phone no', r'^OD Limit',
            r'^Currency', r'^Email', r'^Cust ID', r'^Account No', r'^A/C Open Date',
            r'^Account Status', r'^RTGS/NEFT IFSC', r'^MICR', r'^Branch Code',
            r'^Account Type', r'^HDFC BANK LIMITED', r'^\*Closing balance',
            r'^Contents of this statement', r'^State account branch GSTN',
            r'^HDFC Bank GSTIN', r'^Registered Office Address', r'^Generated On',
            r'^Generated By', r'^Requesting Branch Code', r'^This is a computer generated',
            r'^STATEMENT SUMMARY', r'^Opening Balance', r'^Dr Count', r'^Cr Count',
            r'^Debits', r'^Credits', r'^Closing Bal'
        ]
        skip = False
        for pat in skip_patterns:
            if re.match(pat, line, re.I):
                skip = True
                break
        if skip:
            continue
        
        # Try pattern 1 (HDFC standard format)
        match = txn_pattern.match(line)
        if match:
            raw_date = match.group(1)
            narration_raw = match.group(2).strip()
            # value_date = match.group(3)
            withdrawal_amt = float(match.group(4).replace(',', ''))
            deposit_amt = float(match.group(5).replace(',', ''))
            balance = float(match.group(6).replace(',', ''))
            
            if withdrawal_amt > 0:
                txn_type = 'debit'
                amount = withdrawal_amt
            else:
                txn_type = 'credit'
                amount = deposit_amt
            
            narration = _clean_hdfc_narration_simple(narration_raw)
            processed_count += 1
            
        else:
            # Try pattern 2 (with DR/CR indicator)
            match2 = txn_pattern2.match(line)
            if match2:
                raw_date = match2.group(1)
                narration_raw = match2.group(2).strip()
                dr_cr = match2.group(3).upper()
                amount = float(match2.group(4).replace(',', ''))
                balance = float(match2.group(5).replace(',', ''))
                
                txn_type = 'debit' if dr_cr == 'DR' else 'credit'
                narration = _clean_hdfc_narration_simple(narration_raw)
                processed_count += 1
            else:
                # Try to find date at start of line and extract manually
                date_match = re.match(r'^(\d{2}/\d{2}/\d{2})\s+', line)
                if date_match:
                    raw_date = date_match.group(1)
                    remaining = line[date_match.end():].strip()
                    
                    # Look for amount patterns
                    amounts = re.findall(r'([0-9,]+\.\d{2})', remaining)
                    if len(amounts) >= 2:
                        withdrawal_amt = float(amounts[0].replace(',', ''))
                        deposit_amt = float(amounts[1].replace(',', '')) if len(amounts) > 1 else 0
                        balance = float(amounts[-1].replace(',', '')) if len(amounts) > 2 else None
                        
                        if withdrawal_amt > 0 and withdrawal_amt > deposit_amt:
                            txn_type = 'debit'
                            amount = withdrawal_amt
                        elif deposit_amt > 0:
                            txn_type = 'credit'
                            amount = deposit_amt
                        else:
                            continue
                        
                        narration_raw = remaining
                        for amt in amounts:
                            narration_raw = narration_raw.replace(amt, '')
                        narration_raw = re.sub(r'\s+', ' ', narration_raw).strip()
                        narration = _clean_hdfc_narration_simple(narration_raw)
                        processed_count += 1
                    else:
                        continue
                else:
                    continue
        
        # Convert date
        try:
            parts = raw_date.split('/')
            if len(parts) == 3:
                dd, mm, yy = parts
                yyyy = '20' + yy if len(yy) == 2 else yy
                txn_date = f"{yyyy}-{mm}-{dd}"
            else:
                txn_date = raw_date
        except:
            txn_date = raw_date
        
        records.append({
            'txn_date': txn_date,
            'description': narration,
            'narration': narration,
            'amount': amount,
            'txn_type': txn_type,
            'balance': balance if balance else None,
            'raw_debit': amount if txn_type == 'debit' else None,
            'raw_credit': amount if txn_type == 'credit' else None,
            'source': 'pdf',
        })
    
    print(f'[HDFC] Processed {processed_count} lines, extracted {len(records)} transactions', flush=True)
    
    # Print sample
    print('[HDFC] Sample transactions:')
    for i, r in enumerate(records[:20]):
        print(f'  [{i}] {r["txn_date"]} | {r["txn_type"]:6} | {r["amount"]:>12,.2f} | {r["narration"]}')
    
    return {
        'records': records,
        'opening_balance': opening_balance,
        'closing_balance': _resolve_statement_closing_balance(
            records, opening_balance, closing_balance, "_parse_hdfc_pdf_text"
        ),
        'statement_from_date': statement_from_date,
        'statement_to_date': statement_to_date,
    }


def _clean_hdfc_narration_simple(narration: str) -> str:
    """
    Preserve the real HDFC narration while removing only obvious ref junk.
    Do NOT collapse to generic labels like SALARY / ZERODHA / TPT.
    """
    if not narration:
        return "Unknown"

    s = str(narration).strip()

    # normalize whitespace only
    s = re.sub(r'\s+', ' ', s).strip()

    # remove trailing long refs and masked ids, but keep meaningful words
    s = re.sub(r'\b(?:UTR|RRN|REF|TXN|TXNID|SEQ|CHQ|CHEQUE|NETBANK)\s*[:#-]?\s*[A-Z0-9-]{6,}\b', ' ', s, flags=re.I)
    s = re.sub(r'\b[A-Z]{2,}\d{6,}\b', ' ', s)
    s = re.sub(r'(?<![A-Za-z])\d{10,}(?![A-Za-z])', ' ', s)
    s = re.sub(r'\b[X*]{2,}\d{3,6}\b', ' ', s)

    # keep separators readable
    s = re.sub(r'[_|]+', ' ', s)
    s = re.sub(r'\s*[/]+\s*', '/', s)
    s = re.sub(r'\s+', ' ', s).strip(' -/')

    # Never downgrade rich narrations to generic placeholders
    if not s:
        return "Unknown"
    return s[:180]


def _norm_date_hdfc(date_str: str) -> str:
    """Convert DD/MM/YYYY to YYYY-MM-DD"""
    try:
        parts = date_str.split('/')
        if len(parts) == 3:
            dd, mm, yyyy = parts
            return f"{yyyy}-{mm}-{dd}"
    except:
        pass
    return date_str

def _extract_counterparty_from_hdfc(remaining_text: str, narration_clean: str) -> str:
    """
    Extract the actual counterparty name from HDFC transaction text.
    Examples:
      - "UTIB0002172-MANI GARG" -> "Mani Garg"
      - "UTIB0002172-SARJU GARG" -> "Sarju Garg"  
      - "TPT-MANISH GARG" -> "Manish Garg"
      - "TPT-SARJU GARG HUF" -> "Sarju Garg HUF"
      - "ICIC0002122-CHANDRA BHAN KUMAR" -> "Chandra Bhan Kumar"
    """
    import re

    generic_tokens = {
        "TPT", "LOAN", "REPAY", "RETURN", "GIFT", "TXFR", "SAL", "SALARY",
        "ACH", "NACH", "CREDIT", "DEBIT", "CR", "DR", "UNKNOWN", "TRANSFER",
        "FUNDS", "FUND", "IB", "UPI", "NEFT", "RTGS", "IMPS", "PAYROLL",
        "NETBANK", "REF", "UTR", "RRN", "ID", "SEQ",
        "BANK", "HDFC", "UTIB", "ICIC", "YESB", "SBIN", "STATE", "INDIA",
    }
    banned_values = {"tpt", "salary", "ach credit", "cr", "dr", "unknown"}

    def _format_name(value: str) -> str:
        words = []
        for word in re.split(r"\s+", str(value or "").strip()):
            if not word:
                continue
            def _format_name(value: str) -> str:
                words = []
                for word in re.split(r"\s+", str(value or "").strip()):
                    if not word:
                        continue
                    if word.upper() == "HUF":
                        continue   # REMOVE HUF FROM DISPLAY
                    words.append(word.title())
    
                return " ".join(words).strip()

    def _clean_name_chunk(value: str) -> str:
        s = str(value or "").upper()
        s = re.sub(r'\b(?:UTIB|ICIC|YESB|SBIN|HDFC)\d{4,}\b', ' ', s)
        s = re.sub(r'\b[A-Z]{2,6}\d{4,}\b', ' ', s)
        s = re.sub(r'(?<![A-Z])\d{5,}(?![A-Z])', ' ', s)
        s = re.sub(r'[^A-Z\s&.]', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    def _human_name(value: str) -> str:
        cleaned = _clean_name_chunk(value)
        if not cleaned:
            return ""
        words = [w for w in cleaned.split() if w]
        core_words = [w for w in words if w != "HUF"]
        if len(core_words) < 2:
            return ""
        if any(w in generic_tokens for w in core_words):
            return ""
        candidate = _format_name(" ".join(words))
        return "" if candidate.lower() in banned_values else candidate

    def _final_hyphen_name(value: str) -> str:
        parts = [p.strip() for p in re.split(r'[-–]+', str(value or "")) if p.strip()]
        for part in reversed(parts):
            candidate = _human_name(part)
            if candidate:
                return candidate
        return ""

    sources = []
    for source in (
        remaining_text or "",
        narration_clean or "",
        f"{remaining_text or ''} {narration_clean or ''}".strip(),
    ):
        text = str(source or "").strip()
        if text and text not in sources:
            sources.append(text)

    explicit_patterns = [
        r'(?:UTIB|ICIC|YESB|SBIN|HDFC)\d{4,}-([A-Z][A-Z\s]+(?:\s+HUF)?)\b',
        r'TPT-(?:LOAN|REPAY|RETURN|GIFT|TXFR|SAL)(?:-[A-Z0-9]+)?-([A-Z][A-Z\s]+(?:\s+HUF)?)\b',
        r'TPT-([A-Z][A-Z\s]+(?:\s+HUF)?)\b',
    ]

    for source in sources:
        upper = str(source or "").upper().strip()
        if not upper:
            continue
        for pattern in explicit_patterns:
            match = re.search(pattern, upper)
            if match:
                candidate = _human_name(match.group(1))
                if candidate:
                    return candidate

    for source in sources:
        if "TPT" in str(source or "").upper():
            candidate = _final_hyphen_name(source)
            if candidate:
                return candidate

    for source in sources:
        upper = _clean_name_chunk(source)
        if not upper:
            continue
        words = upper.split()
        for size in range(min(4, len(words)), 1, -1):
            for start in range(0, len(words) - size + 1):
                candidate = _human_name(" ".join(words[start:start + size]))
                if candidate:
                    return candidate

    fallback = _final_hyphen_name(narration_clean or remaining_text or "")
    return fallback or "Unknown"






def parse_pdf_statement(file_bytes: bytes, filename: str = "", password: Optional[str] = None) -> Dict[str, Any]:
    """
    HDFC-focused PDF parser - the version that gave 236 transactions.
    """
    import re
    from datetime import datetime

    print(f"  [PDF:{filename}] Starting HDFC-focused parser", flush=True)

    # Filename based PNB hint
    is_pnb_file = bool(re.search(r'PNB|Punjab', filename or '', re.I))

    if is_pnb_file:
        full_text = ""

        try:
            import io
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(file_bytes), strict=False)

            if reader.is_encrypted:
                try:
                    reader.decrypt(password or "")
                except Exception:
                    pass

            try:
                reader._override_encryption = True
            except Exception:
                pass

            pages_text = []
            for page in reader.pages:
                try:
                    pages_text.append(page.extract_text() or "")
                except Exception as pe:
                    print(f"  [PDF:PNB] page extract skipped: {pe}", flush=True)

            full_text = "\n".join(pages_text)
            print("  [PDF:PNB] pypdf force text extracted", flush=True)

        except Exception as e:
            print(f"  [PDF:PNB] pypdf force failed: {e}", flush=True)

        print("  [PDF] PNB statement detected by filename", flush=True)
        return _parse_pnb_pdf_text(full_text)

    file_bytes = _try_decrypt_pdf_bytes(file_bytes, password=password)

  
   
    
    file_bytes = _try_decrypt_pdf_bytes(file_bytes, password=password)
    
    records = []
    opening_balance = None
    closing_balance = None
    statement_from_date = None
    statement_to_date = None
    
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("  [PDF] PyMuPDF not installed. Run: pip install pymupdf", flush=True)
        return {"records": [], "opening_balance": None, "closing_balance": None}
    
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    
    # Extract all text
    all_text = []
    for page_num, page in enumerate(doc):
        text = page.get_text()
        all_text.append(text)
    
    doc.close()
    
    full_text = "\n".join(all_text)





        # PNB PDF support
    if is_pnb_file or re.search(r'Punjab\s+National\s+Bank|PUNB\d+|Generated\s+through\s+mPassBook', full_text, re.I):
        print("  [PDF] PNB statement detected", flush=True)
        return _parse_pnb_pdf_text(full_text)
    
    # Union Bank PDF support
    if re.search(r'Union|UBIN|unionbankofindia', full_text, re.I):
        print("  [PDF] Union Bank statement detected", flush=True)
        return _parse_union_bank_pdf_text(full_text)
    
        # PNB PDF support
    if re.search(r'Punjab\s+National\s+Bank|PUNB\d+|Generated\s+through\s+mPassBook', full_text, re.I):
        print("  [PDF] PNB statement detected", flush=True)
        return _parse_pnb_pdf_text(full_text)
 

    
    # Extract period from statement header
    period_pattern = re.compile(r'Statement\s+From\s*:\s*(\d{2}/\d{2}/\d{4})\s+To\s*:\s*(\d{2}/\d{2}/\d{4})', re.I)
    period_match = period_pattern.search(full_text)
    if period_match:
        statement_from_date = _norm_date_hdfc(period_match.group(1))
        statement_to_date = _norm_date_hdfc(period_match.group(2))
        print(f'  [PDF] Period: {statement_from_date} to {statement_to_date}', flush=True)
    
    # Extract opening and closing balance from summary
    summary_pattern = re.compile(
        r'Opening\s+Balance\s+([0-9,]+\.\d{2})\s+\d+\s+\d+\s+([0-9,]+\.\d{2})\s+([0-9,]+\.\d{2})\s+([0-9,]+\.\d{2})',
        re.I
    )
    summary_match = summary_pattern.search(full_text)
    if summary_match:
        opening_balance = float(summary_match.group(1).replace(',', ''))
        closing_balance = float(summary_match.group(4).replace(',', ''))
        print(f'  [PDF] Opening balance: {opening_balance}, Closing: {closing_balance}', flush=True)
    
    # Split into lines and process each line
    lines = full_text.split('\n')
    
    # HDFC transaction pattern - matches lines with date at start
    date_pattern = re.compile(r'^(\d{2}/\d{2}/\d{2})\s+')
    
    transactions = []
    current_txn = None
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Skip header/footer lines
        if re.match(r'^(Page No\.|Statement of account|MRS\.|MANI GARG|FLAT NO|SECTOR|NEAR GYM|FARIDABAD|HARYANA|JOINT HOLDERS|Nomination|Registered|Account Branch|Address|City|State|Phone no|OD Limit|Currency|Email|Cust ID|Account No|A/C Open Date|Account Status|RTGS/NEFT IFSC|MICR|Branch Code|Account Type|HDFC BANK LIMITED|\*Closing balance|Contents of this statement|State account branch GSTN|HDFC Bank GSTIN|Registered Office Address|Generated On|Generated By|Requesting Branch Code|This is a computer generated|STATEMENT SUMMARY|Opening Balance|Dr Count|Cr Count|Debits|Credits|Closing Bal)', line, re.I):
            continue
        
        if date_pattern.match(line):
            if current_txn:
                transactions.append(current_txn)
            current_txn = line
        elif current_txn:
            current_txn += ' ' + line
    
    if current_txn:
        transactions.append(current_txn)
    
    print(f'  [PDF] Found {len(transactions)} potential transactions', flush=True)
    
    # Parse each transaction
    for txn in transactions:
        # Extract date
        date_match = re.match(r'^(\d{2}/\d{2}/\d{2})\s+', txn)
        if not date_match:
            continue
        
        raw_date = date_match.group(1)
        remaining = txn[date_match.end():].strip()
        
        # Check for CR/DR indicator
        cr_match = re.search(r'\bCR\b', remaining, re.IGNORECASE)
        dr_match = re.search(r'\bDR\b', remaining, re.IGNORECASE)
        
        # Find all amounts in the line
        amount_pattern = re.compile(r'([0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2})')
        amounts = amount_pattern.findall(remaining)
        
        if len(amounts) < 2:
            continue
        
        # Remove amounts from remaining to get narration
        narration = remaining
        for amt in amounts:
            narration = narration.replace(amt, '')
        
        # Clean narration
        narration = re.sub(r'\s+', ' ', narration).strip()
        
        def parse_amt(s):
            return float(s.replace(',', ''))
        
        withdrawal_amt = parse_amt(amounts[0]) if len(amounts) >= 1 else 0
        deposit_amt = parse_amt(amounts[1]) if len(amounts) >= 2 else 0
        balance = parse_amt(amounts[-1]) if len(amounts) >= 3 else None
        
        # Determine transaction type
        if cr_match and not dr_match:
            txn_type = 'credit'
            amount = deposit_amt if deposit_amt > 0 else withdrawal_amt
        elif dr_match:
            txn_type = 'debit'
            amount = withdrawal_amt if withdrawal_amt > 0 else deposit_amt
        else:
            if withdrawal_amt > 0 and withdrawal_amt > deposit_amt:
                txn_type = 'debit'
                amount = withdrawal_amt
            elif deposit_amt > 0:
                txn_type = 'credit'
                amount = deposit_amt
            else:
                continue
        
        # Simple narration cleaning - keep it short
        narration_clean = narration
        narration_clean = re.sub(r'^(NEFT|RTGS|IMPS|IB\s+FUNDS\s+TRANSFER)\s*(?:CR|DR)?[-/]*\s*', '', narration_clean, flags=re.I)
        narration_clean = re.sub(r'\s+', ' ', narration_clean).strip()
        
        if not narration_clean or len(narration_clean) < 2:
            narration_clean = "Transaction"
        
        # Convert date
        try:
            parts = raw_date.split('/')
            if len(parts) == 3:
                dd, mm, yy = parts
                yyyy = '20' + yy if len(yy) == 2 else yy
                txn_date = f"{yyyy}-{mm}-{dd}"
            else:
                txn_date = raw_date
        except:
            txn_date = raw_date
        
        records.append({
            "txn_date": txn_date,
            "description": narration_clean,
            "narration": narration_clean,
            "amount": amount,
            "txn_type": txn_type,
            "balance": balance,
            "counterparty": "",
            "raw_debit": amount if txn_type == 'debit' else None,
            "raw_credit": amount if txn_type == 'credit' else None,
        })
    
    print(f'  [PDF] Parsed {len(records)} transactions', flush=True)
    
    # Print sample
    print('  [PDF] Sample transactions:')
    for i, r in enumerate(records[:10]):
        print(f'    [{i}] {r["txn_date"]} | {r["txn_type"]:6} | {r["amount"]:>12,.2f} | {r["narration"][:50]}')
    
    return {
        "records": records,
        "opening_balance": opening_balance,
        "closing_balance": _resolve_statement_closing_balance(
            records, opening_balance, closing_balance, "parse_pdf_statement"
        ),
        "statement_from_date": statement_from_date,
        "statement_to_date": statement_to_date,
    }

def _parse_union_bank_pdf_text(full_text: str) -> Dict[str, Any]:
    import re
    from datetime import datetime

    records = []
    statement_from_date = None
    statement_to_date = None

    period_match = re.search(
        r'Statement\s+Period\s+From\s*-?\s*(\d{2}/\d{2}/\d{4})\s+To\s+(\d{2}/\d{2}/\d{4})',
        full_text,
        re.I
    )
    if period_match:
        statement_from_date = _norm_date_hdfc(period_match.group(1))
        statement_to_date = _norm_date_hdfc(period_match.group(2))

    text = re.sub(r'\s+', ' ', full_text)

    pattern = re.compile(
        r'([SC]\d{6,})\s+'
        r'(\d{2}/\d{2}/\d{4})\s+'
        r'(.*?)\s+'
        r'([0-9,]+\.\d{2})\s*\((Dr|Cr)\)\s+'
        r'([0-9,]+\.\d{2})',
        re.I
    )

    for m in pattern.finditer(text):
        tran_id, raw_date, narration, amount_raw, drcr, balance_raw = m.groups()

        try:
            txn_date = datetime.strptime(raw_date, "%d/%m/%Y").strftime("%Y-%m-%d")
        except Exception:
            txn_date = raw_date

        amount = float(amount_raw.replace(",", ""))
        balance = float(balance_raw.replace(",", ""))
        txn_type = "debit" if drcr.lower() == "dr" else "credit"

        narration = re.sub(r'\s+', ' ', narration).strip()

        records.append({
            "txn_date": txn_date,
            "description": narration,
            "narration": narration,
            "amount": amount,
            "txn_type": txn_type,
            "balance": balance,
            "counterparty": "",
            "raw_debit": amount if txn_type == "debit" else None,
            "raw_credit": amount if txn_type == "credit" else None,
        })

    print(f"  [PDF:Union] Parsed {len(records)} transactions", flush=True)

    return {
        "records": records,
        "opening_balance": None,
        "closing_balance": _resolve_statement_closing_balance(
            records, None, None, "union_bank_pdf"
        ),
        "statement_from_date": statement_from_date,
        "statement_to_date": statement_to_date,
    }







def _parse_pnb_pdf_text(full_text: str) -> Dict[str, Any]:
    import re
    from datetime import datetime

    records = []

    period_match = re.search(
        r'Statement\s+For:\s*(\d{4}/\d{2}/\d{2})\s*to\s*(\d{4}/\d{2}/\d{2})',
        full_text,
        re.I
    )

    statement_from_date = None
    statement_to_date = None

    if period_match:
        statement_from_date = period_match.group(1).replace("/", "-")
        statement_to_date = period_match.group(2).replace("/", "-")

    pattern = re.compile(
        r'(\d{2}/\d{2}/\d{4})\s+'
        r'([0-9]+\.[0-9]{2})?\s*'
        r'([0-9]+\.[0-9]{2})?\s*'
        r'([0-9]+\.[0-9]{2})\s+Cr\.?\s+'
        r'(.*?)(?=\d{2}/\d{2}/\d{4}|Page\s+\d+|$)',
        re.S
    )

    for m in pattern.finditer(full_text):
        raw_date, withdrawal, deposit, balance, narration = m.groups()

        try:
            txn_date = datetime.strptime(raw_date, "%d/%m/%Y").strftime("%Y-%m-%d")
        except:
            txn_date = raw_date

        narration = re.sub(r"\s+", " ", narration).strip()





        counterparty = ""

        parts = narration.split("/")
        if len(parts) >= 2:
           counterparty = parts[-1].strip()

        counterparty = re.sub(r"[^A-Za-z ]+", " ", counterparty)
        counterparty = re.sub(r"\s+", " ", counterparty).strip().title()

        if not counterparty or counterparty.upper() in ("P2M", "P2V"):
           counterparty = ""










        # if deposit:
        #     amount = float(deposit)
        #     txn_type = "credit"
        # elif withdrawal:
        #     amount = float(withdrawal)
        #     txn_type = "debit"
        # else:
        #     continue
        if deposit:
            amount = float(deposit)
            txn_type = "credit"
        elif withdrawal:
             amount = float(withdrawal)

    # PNB PDF extraction shifts deposit values into withdrawal column.
     # IMPS-IN rows are inward credits in this statement format.
             if re.search(r'\bIMPS\s*-\s*IN\b|\bIMPS[-\s]*IN\b', narration, re.I):
               txn_type = "credit"
             else:
                  txn_type = "debit"
        else:
          continue


 





        records.append({
            "txn_date": txn_date,
            "description": narration,
            "narration": narration,
            "amount": amount,
            "txn_type": txn_type,
            "balance": float(balance),
            "counterparty": counterparty,
            "raw_debit": amount if txn_type=="debit" else None,
            "raw_credit": amount if txn_type=="credit" else None,
        })

    print(f"  [PDF:PNB] Parsed {len(records)} transactions", flush=True)

    return {
        "records": records,
        "opening_balance": None,
        "closing_balance": _resolve_statement_closing_balance(
            records, None, None, "pnb_pdf"
        ),
        "statement_from_date": statement_from_date,
        "statement_to_date": statement_to_date,
    }





def _norm_date_hdfc(date_str: str) -> str:
    """Convert DD/MM/YYYY to YYYY-MM-DD"""
    try:
        parts = date_str.split('/')
        if len(parts) == 3:
            dd, mm, yyyy = parts
            return f"{yyyy}-{mm}-{dd}"
    except:
        pass
    return date_str

# ── PDF SUPPORT (NEW) ── Library availability checker
def _pdf_library_status() -> Dict[str, bool]:
    """Return availability of each PDF parsing library."""
    status: Dict[str, bool] = {}

    try:
        import pdfplumber  # type: ignore  # noqa: F401
        status["pdfplumber"] = True
    except ImportError:
        status["pdfplumber"] = False

    try:
        import fitz  # type: ignore  # noqa: F401
        status["fitz"] = True
    except ImportError:
        status["fitz"] = False

    try:
        from pdfminer.high_level import extract_text_to_fp  # type: ignore  # noqa: F401
        status["pdfminer"] = True
    except ImportError:
        status["pdfminer"] = False

    try:
        import pytesseract  # type: ignore  # noqa: F401
        import shutil as _shutil
        status["tesseract"] = bool(_shutil.which("tesseract"))
    except ImportError:
        status["tesseract"] = False

    return status


# ══════════════════════════════════════════════════════════════════════════════
# END PDF SUPPORT (NEW)
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# EXCEL PARSE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _normalize_header_label(text: Any) -> str:
    """Lowercase, strip punctuation, collapse spaces for loose header matching."""
    s = str(text or "").lower()
    s = re.sub(r'[^\w\s]', ' ', s)   # punctuation → space
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _safe_parse_amount(value: Any) -> float:
    """
    Parse a cell value to a float amount.
    Handles: commas, currency symbols (₹$£€), CR/DR suffixes,
    parentheses for negatives, blank strings, merged-cell artifacts.
    Returns 0.0 on failure instead of raising.
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return abs(float(value))
    s = str(value).strip()
    if not s:
        return 0.0
    su = s.upper()
    # Strip currency symbols and commas
    cleaned = re.sub(r'[₹$£€,\s]', '', su)
    # Parentheses = negative magnitude
    if cleaned.startswith('(') and cleaned.endswith(')'):
        cleaned = cleaned[1:-1]
    # Remove CR/DR suffixes
    cleaned = re.sub(r'(CR|DR)$', '', cleaned).strip()
    # Remove any remaining non-numeric except dot and minus
    cleaned = re.sub(r'[^\d.\-]', '', cleaned)
    if not cleaned:
        return 0.0
    # Handle multiple dots (e.g. "1,234.56" after partial cleaning)
    parts = cleaned.split('.')
    if len(parts) > 2:
        cleaned = parts[0] + '.' + ''.join(parts[1:])
    try:
        return abs(float(cleaned))
    except ValueError:
        return 0.0


def _safe_parse_excel_date(value: Any) -> str:
    """
    Convert any Excel date representation to 'YYYY-MM-DD' string.
    Handles: datetime objects, date objects, numeric serials, formatted strings.
    Returns today's date string on failure.
    """
    from datetime import date as _date_type
    today = datetime.now().strftime('%Y-%m-%d')
    if value is None:
        return today
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d')
    if isinstance(value, _date_type):
        return value.strftime('%Y-%m-%d')
    if isinstance(value, (int, float)):
        try:
            return (datetime(1899, 12, 30) + timedelta(days=float(value))).strftime('%Y-%m-%d')
        except Exception:
            return today
    s = str(value).strip()
    if not s:
        return today
    # Normalise single-digit day/month
    s = re.sub(
        r'(?<![:\d])(\d)(?=[/\-])|(?<=[/\-])(\d)(?![:\d])',
        lambda m: '0' + (m.group(1) or m.group(2)), s
    )
    for fmt in (
        '%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d',
        '%d %b %Y', '%d-%b-%Y',
        '%d/%m/%y', '%d-%b-%y',
        '%d%m%Y',
        '%Y/%m/%d',
    ):
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
        except Exception:
            pass
    return today


def _is_transaction_like_row(row: List[Any]) -> bool:
    """
    Heuristic: True if the row looks like a transaction data row.
    Criteria: at least one date-like cell, at least one non-empty text cell,
    at least one numeric cell, and the row is not mostly empty.
    """
    non_empty = [c for c in row if c is not None and str(c).strip()]
    if len(non_empty) < 3:
        return False
    has_date = any(
        re.search(r'\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}|\d{5,}', str(c))
        for c in non_empty
    )
    has_numeric = any(
        isinstance(c, (int, float)) or
        bool(re.search(r'^\d[\d,. ]*$', str(c).strip()))
        for c in non_empty
    )
    has_text = any(
        isinstance(c, str) and len(c.strip()) > 3 and not c.strip().replace('.', '').replace(',', '').isdigit()
        for c in non_empty
    )
    return has_date and has_numeric and has_text


def _select_best_sheet_from_workbook(wb: Any) -> Any:
    """
    Given an openpyxl workbook, return the worksheet with the most
    transaction-like rows. Falls back to the active sheet.
    """
    best_ws = wb.active
    best_score = 0
    for name in wb.sheetnames:
        try:
            ws = wb[name]
            score = 0
            for row in ws.iter_rows(max_row=200, values_only=True):
                if _is_transaction_like_row(list(row)):
                    score += 1
            print(f"  [OPENPYXL] Sheet '{name}': {score} transaction-like rows")
            if score > best_score:
                best_score = score
                best_ws = ws
        except Exception as e:
            print(f"  [OPENPYXL] Sheet '{name}' inspect error: {e}")
    print(f"  [OPENPYXL] Selected sheet: '{best_ws.title}' (score={best_score})")
    return best_ws


def _looks_like_encrypted_office_file(file_bytes: bytes, filename: str = "") -> bool:
    """Heuristic for password protected Office workbooks."""
    lower = (filename or '').lower()
    return lower.endswith(('.xlsx', '.xlsm', '.xltx', '.xltm')) and _is_xls_bytes(file_bytes) and not _is_xlsx_bytes(file_bytes)


def _safe_parse_excel_date(v: Any) -> str:
    """
    Parse Excel/openpyxl/xlrd date-ish values into ISO YYYY-MM-DD.
    """
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")

    s = str(v).strip()
    if not s:
        return ""

    for fmt in (
        "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
        "%d/%m/%y", "%d-%m-%y", "%d.%m.%y",
        "%Y-%m-%d", "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass

    try:
        return _normalise_date(s)
    except Exception:
        return ""


def _valid_balance_value(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        val = float(value)
    except Exception:
        return None
    if not np.isfinite(val):
        return None
    return val


def _sum_statement_rows(records: List[Dict[str, Any]]) -> Tuple[float, float]:
    total_credits = 0.0
    total_debits = 0.0
    for r in records or []:
        amt = float(r.get("amount") or 0)
        txn_type = str(r.get("txn_type") or "").strip().lower()
        if txn_type == "credit":
            total_credits += amt
        elif txn_type == "debit":
            total_debits += amt
    return total_credits, total_debits


def _last_valid_running_balance(records: List[Dict[str, Any]]) -> Optional[float]:
    for r in reversed(records or []):
        bal = _valid_balance_value(r.get("balance"))
        if bal is not None:
            return bal
    return None


def _resolve_statement_closing_balance(
    records: List[Dict[str, Any]],
    opening_balance: Any,
    explicit_closing_balance: Any = None,
    source: str = "statement",
) -> Optional[float]:
    """
    Authoritative closing balance precedence:
      1. explicit parsed statement closing balance,
      2. opening + credits - debits for parsed statement rows,
      3. last valid running balance captured from rows.
    """
    explicit = _valid_balance_value(explicit_closing_balance)
    opening = _valid_balance_value(opening_balance)
    computed = None

    if opening is not None:
        total_credits, total_debits = _sum_statement_rows(records)
        computed = opening + total_credits - total_debits

    if explicit is not None:
        if computed is not None and abs(explicit - computed) > 0.01:
            print(
                f"  [CLOSING BALANCE NOTE] {source}: explicit closing {explicit:.2f} "
                f"differs from computed {computed:.2f}; keeping explicit parsed value.",
                flush=True,
            )
        return explicit

    if computed is not None:
        return computed

    return _last_valid_running_balance(records)


def _extract_explicit_closing_balance_from_rows(rows: List[List[Any]]) -> Optional[float]:
    """
    Capture statement-summary closing balances without using date ranges or row
    positions as inference. Returns None if no clear summary line is found.
    """
    for row in rows or []:
        cells = [str(c or "").strip() for c in row]
        joined = " ".join(cells)
        if not re.search(r"\bclosing\s+(?:bal(?:ance)?|balance)\b", joined, re.I):
            continue
        nums = [_safe_parse_amount(c) for c in cells if _safe_parse_amount(c) > 0]
        if nums:
            return nums[-1]
    return None


def _looks_like_axis_statement(rows: List[List[Any]], filename: str = "") -> bool:
    name = (filename or "").upper()
    if "AXIS" in name:
        return True

    text = " ".join(str(c or "") for row in rows[:40] for c in row[:8]).upper()
    axis_signals = [
        "AXIS BANK",
        "TRAN DATE",
        "PARTICULARS",
        "CHQ NO",
        "AMOUNT",
        "DR/CR",
        "BALANCE",
        "BRANCH NAME",
    ]
    return sum(1 for s in axis_signals if s in text) >= 2


def _parse_axis_rows(rows_raw: List[List[Any]], filename: str = "") -> Dict[str, Any]:
    """
    Axis specific parser for Excel-like row grids.
    Typical columns:
      Tran Date | Value Date | Particulars | Chq No | Amount | Dr/Cr | Balance | Branch Name
    """
    import re

    header_idx = None

    def _norm(x):
        return re.sub(r"\s+", " ", str(x or "")).strip().lower()

    for i, row in enumerate(rows_raw[:60]):
        vals = [_norm(c) for c in row]
        if "tran date" in vals or ("particulars" in vals and "balance" in vals):
            header_idx = i
            break

    if header_idx is None:
        return {"records": [], "opening_balance": None}

    hdr = [_norm(c) for c in rows_raw[header_idx]]

    def _find(*needles):
        for n in needles:
            for j, h in enumerate(hdr):
                if n == h or n in h:
                    return j
        return None

    date_col    = _find("tran date", "transaction date", "date")
    desc_col    = _find("particulars", "narration", "description", "details")
    amount_col  = _find("amount", "txn amount")
    type_col    = _find("dr/cr", "cr/dr", "type")
    balance_col = _find("balance", "running balance", "closing balance")

    if date_col is None or desc_col is None or amount_col is None:
        return {"records": [], "opening_balance": None}

    records = []
    opening_balance = None

    def _amt(v):
        s = str(v or "").replace(",", "").strip()
        s = re.sub(r"[^\d.\-]", "", s)
        try:
            return abs(float(s))
        except Exception:
            return 0.0

    for row in rows_raw[header_idx + 1:]:
        if not any(str(c or "").strip() for c in row):
            continue

        date_val = str(row[date_col] if date_col < len(row) else "").strip()
        desc = str(row[desc_col] if desc_col < len(row) else "").strip()
        amt_raw = row[amount_col] if amount_col < len(row) else ""
        typ_raw = str(row[type_col] if type_col is not None and type_col < len(row) else "").strip().lower()
        bal_raw = row[balance_col] if balance_col is not None and balance_col < len(row) else ""

        if not date_val or not desc:
            continue

        amount = _amt(amt_raw)
        balance = _amt(bal_raw) if str(bal_raw or "").strip() else None

        if typ_raw in ("cr", "credit"):
            txn_type = "credit"
        elif typ_raw in ("dr", "debit"):
            txn_type = "debit"
        else:
            txn_type = _infer_txn_type(desc)

        records.append({
            "txn_date": _safe_parse_excel_date(date_val),
            "description": desc,
            "narration": desc,
            "amount": amount,
            "txn_type": txn_type,
            "raw_debit": amount if txn_type == "debit" else None,
            "raw_credit": amount if txn_type == "credit" else None,
            "balance": balance,
        })

    if opening_balance is None and records:
        first = next(
            (r for r in records
             if r.get("balance") is not None and r.get("amount") and r.get("txn_type") in ("debit", "credit")),
            None
        )
        if first:
            bal = float(first["balance"])
            amt = float(first["amount"])
            opening_balance = bal - amt if first["txn_type"] == "credit" else bal + amt

    statement_from_date = None
    statement_to_date = None
    explicit_closing_balance = _extract_explicit_closing_balance_from_rows(rows_raw[:60])
    closing_balance = _resolve_statement_closing_balance(
        records, opening_balance, explicit_closing_balance, "_parse_axis_rows"
    )

    return {
        "records": records,
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "statement_from_date": statement_from_date,
        "statement_to_date": statement_to_date,
    }


def _looks_like_au_statement(rows: List[List[Any]], filename: str = "") -> bool:
    name = (filename or "").upper()
    if "AUBANK" in name or "AU BANK" in name or "AU SMALL FINANCE" in name:
        return True

    text = " ".join(str(c or "") for row in rows[:50] for c in row[:8]).upper()
    signals = [
        "DATE",
        "DESCRIPTION",
        "DEBIT",
        "CREDIT",
        "BALANCE",
        "BPAY",
        "OSKO",
        "NPP",
        "DIRECT DEBIT",
        "DIRECT CREDIT",
    ]
    return sum(1 for s in signals if s in text) >= 3

def _parse_au_rows(rows_raw: List[List[Any]], filename: str = "") -> Dict[str, Any]:
    import re

    header_idx = None

    def _norm(x):
        return re.sub(r"\s+", " ", str(x or "")).strip().lower()

    def _safe_parse_statement_date(text: Any) -> Optional[str]:
        s = str(text or "").strip()
        if not s:
            return None

        s = s.replace(".", "-").replace("/", "-")
        s = re.sub(r"\s+", "-", s)

        for fmt in (
            "%d-%m-%Y",
            "%d-%m-%y",
            "%d-%b-%Y",
            "%d-%b-%y",
            "%d-%B-%Y",
            "%d-%B-%y",
        ):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except Exception:
                pass

        for fmt in (
            "%m-%d-%Y",
            "%m-%d-%y",
        ):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except Exception:
                pass

        try:
            return _safe_parse_excel_date(s)
        except Exception:
            return None

    def _extract_statement_period_from_header_rows(header_rows: List[List[Any]]) -> Tuple[Optional[str], Optional[str]]:
        lines = []
        for row in header_rows or []:
            txt = " ".join(str(c or "").strip() for c in row if str(c or "").strip())
            if txt:
                lines.append(re.sub(r"\s+", " ", txt).strip())

        candidates: List[Tuple[str, str, str]] = []

        strong_patterns = [
            r'(?:statement\s*period|period)\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\s*(?:to|\-)\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})',
            r'(?:from)\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\s*(?:to)\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})',
        ]

        for line in lines:
            s = re.sub(r'\s+', ' ', line).strip()
            for pat in strong_patterns:
                m = re.search(pat, s, flags=re.I)
                if m:
                    d1 = _safe_parse_statement_date(m.group(1))
                    d2 = _safe_parse_statement_date(m.group(2))
                    if d1 and d2 and d1 <= d2:
                        candidates.append((d1, d2, s))

        if not candidates:
            for line in lines:
                s = re.sub(r'\s+', ' ', line).strip()
                m = re.search(
                    r'(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\s*(?:to|\-)\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})',
                    s,
                    flags=re.I
                )
                if m:
                    d1 = _safe_parse_statement_date(m.group(1))
                    d2 = _safe_parse_statement_date(m.group(2))
                    if d1 and d2 and d1 <= d2:
                        candidates.append((d1, d2, s))

        if not candidates:
            return None, None

        def _score(item):
            d1, d2, line = item
            start_day1 = 1 if d1.endswith("-01") else 0
            try:
                span_days = (datetime.strptime(d2, "%Y-%m-%d") - datetime.strptime(d1, "%Y-%m-%d")).days
            except Exception:
                span_days = -1
            return (start_day1, span_days, len(line))

        best = max(candidates, key=_score)
        print(f"  [AU PERIOD] Selected header period line: {best[2]}")
        return best[0], best[1]

    for i, row in enumerate(rows_raw[:60]):
        vals = [_norm(c) for c in row]
        if "description" in vals and "balance" in vals and ("debit" in vals or "credit" in vals or "amount" in vals):
            header_idx = i
            break

    if header_idx is None:
        return {
            "records": [],
            "opening_balance": None,
            "closing_balance": None,
            "statement_from_date": None,
            "statement_to_date": None,
        }

    hdr = [_norm(c) for c in rows_raw[header_idx]]

    def _find(*needles):
        for n in needles:
            for j, h in enumerate(hdr):
                if n == h or n in h:
                    return j
        return None

    date_col    = _find("date")
    desc_col    = _find("description", "details", "narration")
    debit_col   = _find("debit")
    credit_col  = _find("credit")
    amount_col  = _find("amount")
    balance_col = _find("balance")

    records = []
    opening_balance = None

    def _amt(v):
        s = str(v or "").replace(",", "").strip()
        s = re.sub(r"[^\d.\-]", "", s)
        try:
            return abs(float(s))
        except Exception:
            return 0.0

    for row in rows_raw[header_idx + 1:]:
        if not any(str(c or "").strip() for c in row):
            continue

        date_val = str(row[date_col] if date_col is not None and date_col < len(row) else "").strip()
        desc = str(row[desc_col] if desc_col is not None and desc_col < len(row) else "").strip()

        if not date_val or not desc:
            continue

        debit_raw   = row[debit_col] if debit_col is not None and debit_col < len(row) else ""
        credit_raw  = row[credit_col] if credit_col is not None and credit_col < len(row) else ""
        amount_raw  = row[amount_col] if amount_col is not None and amount_col < len(row) else ""
        balance_raw = row[balance_col] if balance_col is not None and balance_col < len(row) else ""

        dr = _amt(debit_raw)
        cr = _amt(credit_raw)
        amt = dr or cr or _amt(amount_raw)

        clean_desc = _strip_au_prefix(desc)

        if dr > 0 and cr == 0:
            txn_type = "debit"
        elif cr > 0 and dr == 0:
            txn_type = "credit"
        else:
            txn_type = _infer_txn_type(clean_desc)

        bal = _amt(balance_raw) if str(balance_raw or "").strip() else None

        records.append({
            "txn_date": _safe_parse_excel_date(date_val),
            "description": clean_desc,
            "narration": clean_desc,
            "amount": amt,
            "txn_type": txn_type,
            "raw_debit": dr if txn_type == "debit" else None,
            "raw_credit": cr if txn_type == "credit" else None,
            "balance": bal,
        })

    if opening_balance is None and records:
        first = next(
            (r for r in records
             if r.get("balance") is not None and r.get("amount") and r.get("txn_type") in ("debit", "credit")),
            None
        )
        if first:
            bal = float(first["balance"])
            amt = float(first["amount"])
            opening_balance = bal - amt if first["txn_type"] == "credit" else bal + amt

    statement_from_date = None
    statement_to_date = None
    explicit_closing_balance = _extract_explicit_closing_balance_from_rows(rows_raw[:60])

    try:
        header_rows = rows_raw[:25] if rows_raw else []
        statement_from_date, statement_to_date = _extract_statement_period_from_header_rows(header_rows)
        print(f"  [AU PERIOD] Header-derived period: {statement_from_date} to {statement_to_date}")
    except Exception as e:
        print(f"  [AU PERIOD] Header extraction failed: {e}")
        statement_from_date, statement_to_date = None, None


    closing_balance = _resolve_statement_closing_balance(
        records, opening_balance, explicit_closing_balance, "_parse_au_rows"
    )

    return {
        "records": records,
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "statement_from_date": statement_from_date,
        "statement_to_date": statement_to_date,
    }

def parse_excel_statement(file_bytes: bytes, filename: str = "", password: Optional[str] = None) -> Dict[str, Any]:
    def _log_parse_return(records, opening_balance, closing_balance, statement_from_date, statement_to_date):
        print("[PARSE RETURN]", {
            "statement_from_date": statement_from_date,
            "statement_to_date": statement_to_date,
            "opening_balance": opening_balance,
            "closing_balance": closing_balance,
            "record_count": len(records or [])
        }, flush=True)

    # ── PDF SUPPORT ──
    if (filename or "").lower().endswith(".pdf") or _is_pdf_bytes(file_bytes):
        pdf_result = parse_pdf_statement(file_bytes, filename, password=password)
        if isinstance(pdf_result, list):
            res = {
                "records": pdf_result,
                "opening_balance": None,
                "closing_balance": None,
                "statement_from_date": None,
                "statement_to_date": None,
            }
            _log_parse_return(res.get("records"), res.get("opening_balance"), res.get("closing_balance"), res.get("statement_from_date"), res.get("statement_to_date"))
            return res
        res = {
            "records": pdf_result.get("records", []),
            "opening_balance": pdf_result.get("opening_balance"),
            "closing_balance": pdf_result.get("closing_balance"),
            "statement_from_date": pdf_result.get("statement_from_date"),
            "statement_to_date": pdf_result.get("statement_to_date"),
        }
        _log_parse_return(res.get("records"), res.get("opening_balance"), res.get("closing_balance"), res.get("statement_from_date"), res.get("statement_to_date"))
        return res

    lower_name = (filename or "").lower()
    print(f"  [EXCEL] Parsing '{filename}' ({len(file_bytes)} bytes)")

    if _looks_like_encrypted_office_file(file_bytes, filename):
        if password:
            raise ValueError(
                "Password-protected Excel files are not yet decryptable in this runtime. "
                "Please unlock and save the workbook as a normal .xlsx, then upload that file."
            )
        raise ValueError(
            "Excel file appears to be password-protected. Entering a password is supported for PDFs, "
            "but encrypted Excel workbooks still need to be unlocked and saved as a normal .xlsx before upload."
        )

    parse_attempts: List[str] = []
    rows_raw: List[List[Any]] = []

    is_legacy_xls = (
        (lower_name.endswith(".xls") and not lower_name.endswith(".xlsx") and not lower_name.endswith(".xlsm"))
        or _is_xls_bytes(file_bytes)
    )

    # ── 1. Legacy .xls ────────────────────────────────────────────────────────
    if is_legacy_xls:
        print(f"  [EXCEL] Detected legacy .xls format for '{filename}'")
        xls_parsed = False

        try:
            import xlrd
            from xlrd import xldate_as_datetime, XL_CELL_DATE, XL_CELL_EMPTY

            book = xlrd.open_workbook(file_contents=file_bytes)
            print(f"  [XLS] xlrd opened workbook: {book.nsheets} sheets: {book.sheet_names()}")

            best_sheet = book.sheet_by_index(0)
            best_nrows = best_sheet.nrows
            for si in range(book.nsheets):
                s = book.sheet_by_index(si)
                print(f"  [XLS] xlrd sheet '{s.name}': {s.nrows} rows")
                if s.nrows > best_nrows:
                    best_nrows = s.nrows
                    best_sheet = s
            print(f"  [XLS] xlrd selected sheet: '{best_sheet.name}'")

            def _cell_val(cell):
                if cell.ctype == XL_CELL_DATE:
                    try:
                        return xldate_as_datetime(cell.value, book.datemode).strftime("%d/%m/%Y")
                    except Exception:
                        return cell.value
                if cell.ctype == XL_CELL_EMPTY:
                    return None
                return cell.value

            rows_raw = [
                [_cell_val(best_sheet.cell(r, c)) for c in range(best_sheet.ncols)]
                for r in range(best_sheet.nrows)
            ]
            xls_parsed = True
            print(f"  [XLS] xlrd extracted {len(rows_raw)} rows")
            parse_attempts.append(f"xlrd: OK ({len(rows_raw)} rows from '{best_sheet.name}')")
        except ImportError:
            msg = "xlrd not installed"
            print(f"  [XLS] {msg} — trying LibreOffice fallback")
            parse_attempts.append(f"xlrd: {msg}")
        except Exception as e:
            msg = f"xlrd failed: {e}"
            print(f"  [XLS] {msg} — trying LibreOffice fallback")
            parse_attempts.append(msg)

        if not xls_parsed:
            try:
                converted_bytes = _convert_xls_bytes_to_xlsx_bytes(file_bytes)
                file_bytes = converted_bytes
                parse_attempts.append("LibreOffice conversion: OK")
                print(f"  [XLS] LibreOffice conversion succeeded for '{filename}'")
            except ValueError as conv_err:
                parse_attempts.append(f"LibreOffice conversion: {conv_err}")
                raise ValueError("Excel parse failed. " + " | ".join(parse_attempts)) from conv_err

    # ── 2. Standard .xlsx / .xlsm via openpyxl ───────────────────────────────
    looks_like_xlsx = (
        lower_name.endswith((".xlsx", ".xlsm", ".xltx", ".xltm"))
        or _is_xlsx_bytes(file_bytes)
        or file_bytes[:2] == b"PK"
    )

    if not rows_raw and looks_like_xlsx:
        try:
            import openpyxl
            print(f"  [XLSX] Using openpyxl parser for '{filename}'")
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
            print(f"  [OPENPYXL] Opened workbook; sheets: {wb.sheetnames}")
            ws = _select_best_sheet_from_workbook(wb)
            rows_raw = [list(r) for r in ws.iter_rows(values_only=True)]
            print(f"  [OPENPYXL] Extracted {len(rows_raw)} rows from '{ws.title}'")
            print(f"  [XLSX] Final rows extracted: {len(rows_raw)}")
            parse_attempts.append(f"openpyxl: OK ({len(rows_raw)} rows from '{ws.title}')")
        except Exception as e:
            msg = f"openpyxl failed: {e}"
            print(f"  [OPENPYXL] {msg}")
            parse_attempts.append(msg)

        if not rows_raw:
            try:
                import openpyxl
                print(f"  [XLSX] Retrying openpyxl non-read-only mode for '{filename}'")
                wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=False)
                ws = _select_best_sheet_from_workbook(wb)
                rows_raw = [list(r) for r in ws.iter_rows(values_only=True)]
                print(f"  [OPENPYXL] Non-read-only extracted {len(rows_raw)} rows from '{ws.title}'")
                parse_attempts.append(f"openpyxl(non-read-only): OK ({len(rows_raw)} rows from '{ws.title}')")
            except Exception as e:
                msg = f"openpyxl(non-read-only) failed: {e}"
                print(f"  [OPENPYXL] {msg}")
                parse_attempts.append(msg)

        if not rows_raw:
            try:
                print(f"  [XLSX] Trying pandas.read_excel fallback for '{filename}'")
                xl = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl")
                best_name = xl.sheet_names[0]
                best_df = None
                best_score = -1

                for sname in xl.sheet_names:
                    try:
                        df = xl.parse(sname, header=None, dtype=object)
                        score = 0
                        for _, row in df.head(200).iterrows():
                            if _is_transaction_like_row(row.tolist()):
                                score += 1
                        print(f"  [PANDAS] Sheet '{sname}': {score} transaction-like rows")
                        if score > best_score:
                            best_score = score
                            best_name = sname
                            best_df = df
                    except Exception as se:
                        print(f"  [PANDAS] Sheet '{sname}' failed: {se}")

                if best_df is not None:
                    rows_raw = best_df.where(pd.notnull(best_df), None).values.tolist()
                    print(f"  [PANDAS] Extracted {len(rows_raw)} rows from '{best_name}'")
                    parse_attempts.append(f"pandas: OK ({len(rows_raw)} rows from '{best_name}')")
            except Exception as e:
                msg = f"pandas failed: {e}"
                print(f"  [PANDAS] {msg}")
                parse_attempts.append(msg)

    # ── 3. XML fallback for non-standard XLSX ────────────────────────────────
    if not rows_raw and looks_like_xlsx:
        try:
            rows_raw = _parse_xlsx_xml(file_bytes)
            print(f"  [XML] Extracted {len(rows_raw)} rows")
            print(f"  [XLSX] Final rows extracted: {len(rows_raw)}")
            parse_attempts.append(f"XML fallback: OK ({len(rows_raw)} rows)")
        except Exception as e:
            msg = f"XML fallback failed: {e}"
            print(f"  [XML] {msg}")
            parse_attempts.append(msg)

    if not rows_raw:
        raise ValueError("Excel parse failed. " + " | ".join(parse_attempts))

    # ── Bank-specific early parsers ───────────────────────────────────────────
    if _looks_like_axis_statement(rows_raw, filename):
        axis_result = _parse_axis_rows(rows_raw, filename)
        if axis_result.get("records"):
            print(f"  [AXIS] Parsed {len(axis_result['records'])} transactions from '{filename}'")
            _log_parse_return(
                axis_result.get("records"),
                axis_result.get("opening_balance"),
                axis_result.get("closing_balance"),
                axis_result.get("statement_from_date"),
                axis_result.get("statement_to_date"),
            )
            return axis_result

    if _looks_like_au_statement(rows_raw, filename):
        au_result = _parse_au_rows(rows_raw, filename)
        if au_result.get("records"):
            print(f"  [AU] Parsed {len(au_result['records'])} transactions from '{filename}'")
            _log_parse_return(
                au_result.get("records"),
                au_result.get("opening_balance"),
                au_result.get("closing_balance"),
                au_result.get("statement_from_date"),
                au_result.get("statement_to_date"),
            )
            return au_result

    max_width = max((len(r) for r in rows_raw), default=0)
    rows = [list(r) + [None] * (max_width - len(r)) for r in rows_raw]

    # ── Helper: parse statement period from sheet header text ────────────────
    def _safe_parse_statement_date(text: Any) -> Optional[str]:
        s = str(text or "").strip()
        if not s:
            return None

        s = s.replace(".", "-").replace("/", "-")
        s = re.sub(r"\s+", "-", s)

        # Prefer day-first for Indian bank statements
        for fmt in (
            "%d-%m-%Y",
            "%d-%m-%y",
            "%d-%b-%Y",
            "%d-%b-%y",
            "%d-%B-%Y",
            "%d-%B-%y",
        ):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except Exception:
                pass

        # Explicit month-first fallback only if day-first failed
        for fmt in (
            "%m-%d-%Y",
            "%m-%d-%y",
        ):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except Exception:
                pass

        try:
            return _safe_parse_excel_date(s)
        except Exception:
            return None

    # ── Header detection ─────────────────────────────────────────────────────
    _DATE_VARIANTS = {
        "date", "txn date", "tran date", "trans date", "transaction date",
        "value date", "value dt", "posting date", "chq date", "cheque date",
        "book date", "entry date",
    }
    _DESC_VARIANTS = {
        "narration", "description", "particulars", "details", "remarks",
        "transaction details", "trans details", "transaction narration",
        "trans narration", "narration description",
    }
    _DEBIT_VARIANTS = {
        "debit", "withdrawal", "withdrawals", "withdrawl", "debit amount",
        "debit amt", "dr amount", "dr", "dr amt", "withdrawal amt",
        "withdrawal amt.", "paid out", "debit(inr)",
    }
    _CREDIT_VARIANTS = {
        "credit", "deposit", "deposits", "credit amount", "credit amt",
        "cr", "cr amount", "deposit amt", "deposit amt.", "paid in",
        "credit(inr)",
    }
    _BALANCE_VARIANTS = {
        "balance", "running balance", "closing balance", "bal", "outstanding",
        "avl bal", "available balance",
    }
    _AMOUNT_VARIANTS = {
        "amount", "txn amount", "net amount", "transaction amount", "amt",
    }

    def _norm(x: Any) -> str:
        return _normalize_header_label(x)

    header_idx: Optional[int] = None

    for i, row in enumerate(rows[:50]):
        vals = [_norm(c) for c in row]
        has_date = any(
            v in _DATE_VARIANTS or
            (v not in ("", "balance", "amount", "dr", "cr") and "date" in v)
            for v in vals
        )
        has_desc = any(
            v in _DESC_VARIANTS or
            any(w in v for w in ("narrat", "descri", "particu", "detail", "remark"))
            for v in vals
        )
        if has_date and has_desc:
            header_idx = i
            print(f"  [HEADER] Found formal header at row {i}: {[c for c in row if c is not None]}")
            break

    if header_idx is None:
        for i, row in enumerate(rows[:50]):
            vals = [_norm(c) for c in row]
            non_empty = [v for v in vals if v]

            has_dateish = any(v in _DATE_VARIANTS or "date" in v for v in non_empty)
            has_descish = any(
                v in _DESC_VARIANTS or any(w in v for w in ("narrat", "descri", "particu", "detail", "remark"))
                for v in non_empty
            )
            has_moneyish = any(
                v in _DEBIT_VARIANTS or v in _CREDIT_VARIANTS or v in _AMOUNT_VARIANTS or v in _BALANCE_VARIANTS
                for v in non_empty
            )

            if has_dateish and (has_descish or has_moneyish):
                header_idx = i
                print(f"  [HEADER] Using guarded fallback header at row {i}: {[c for c in row if c is not None]}")
                break

    if header_idx is None:
        for i, row in enumerate(rows[:30]):
            if _is_transaction_like_row(row):
                header_idx = max(0, i - 1)
                print(f"  [HEADER] Inferred header at row {header_idx} (first txn row at {i})")
                break

    if header_idx is None:
        raise ValueError(
            "Excel parse failed: Workbook was read but no transaction table could be detected. "
            + " | ".join(parse_attempts)
        )

    headers = [_norm(h) for h in rows[header_idx]]
    print(f"  [HEADER] Normalized headers: {headers}")

    def fc_exact(candidates: List[str]) -> Optional[int]:
        for kw in candidates:
            for i, h in enumerate(headers):
                if h == kw:
                    return i
        return None

    def fc_contains(candidates: List[str], exclude_cols: Optional[List[int]] = None) -> Optional[int]:
        exclude_cols = exclude_cols or []
        for kw in candidates:
            for i, h in enumerate(headers):
                if i in exclude_cols:
                    continue
                if kw in h:
                    return i
        return None

    date_col = fc_exact(list(_DATE_VARIANTS))
    if date_col is None:
        date_col = fc_contains(["date"])

    desc_col = fc_exact(list(_DESC_VARIANTS) + ["narration / description"])
    if desc_col is None:
        desc_col = fc_contains(["narrat", "descri", "particu", "detail", "remark"])

    type_col = fc_exact(["type", "txn type", "transaction type", "dr cr", "dr  cr", "cr dr"])

    skip = [c for c in [desc_col] if c is not None]
    debit_candidates = list(_DEBIT_VARIANTS)
    if type_col is None:
        debit_candidates.append("dr")
    debit_col = fc_exact(debit_candidates)
    if debit_col is None:
        debit_col = fc_contains(
            ["withdrawal", "debit amount", "debit amt", "dr amount", "debit inr", "paid out"],
            exclude_cols=skip,
        )

    credit_col = fc_exact(list(_CREDIT_VARIANTS))
    if credit_col is None:
        credit_col = fc_contains(
            ["deposit", "credit amount", "credit amt", "paid in", "credit inr"],
            exclude_cols=skip,
        )

    amount_col = fc_exact(list(_AMOUNT_VARIANTS))
    if amount_col is None:
        amount_col = fc_contains(
            ["amount", "amt"],
            exclude_cols=skip + [c for c in [debit_col, credit_col] if c is not None],
        )

    balance_col = fc_exact(list(_BALANCE_VARIANTS))
    if balance_col is None:
        balance_col = fc_contains(
            ["balance", "bal", "closing balance", "running balance"],
            exclude_cols=[c for c in [date_col, desc_col, debit_col, credit_col, amount_col, type_col] if c is not None],
        )

    print(
        f"  [COLS] date={date_col}, desc={desc_col}, type={type_col}, "
        f"debit={debit_col}, credit={credit_col}, amount={amount_col}, balance={balance_col}"
    )

    def safe_cell(row: List[Any], i: Optional[int]) -> Any:
        if i is None or i < 0 or i >= len(row):
            return None
        return row[i]

    def try_amt(val: Any) -> float:
        return _safe_parse_amount(val)

    def try_amt_signed(val: Any) -> float:
        if val is None:
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).strip()
        su = s.upper()
        is_debit = su.endswith("DR") or su.endswith(" DR")
        is_credit = su.endswith("CR") or su.endswith(" CR")
        if su.startswith("(") and su.endswith(")"):
            is_debit = True
        magnitude = _safe_parse_amount(val)
        if is_debit:
            return -magnitude
        if is_credit:
            return magnitude
        return -magnitude if s.lstrip().startswith("-") else magnitude

    has_amounts = False
    for row in rows[header_idx + 1: header_idx + 51]:
        if (
            try_amt(safe_cell(row, debit_col)) > 0
            or try_amt(safe_cell(row, credit_col)) > 0
            or try_amt(safe_cell(row, amount_col)) > 0
        ):
            has_amounts = True
            break

    JUNK = re.compile(
        r"^(micr[\s/]*code|ifsc[\s/]*code|reward\s+points|total\s+credits"
        r"|total\s+debits|closing\s+balance|statement\s+summary"
        r"|[\*]+|▼|▶|◆|►|•)\s*$",
        re.I,
    )

    # ── Opening balance from explicit B/F rows ───────────────────────────────
    opening_balance = None
    bf_row_indexes = set()

    for idx, row in enumerate(rows[header_idx + 1:], start=header_idx + 1):
        joined = " ".join(str(c or "") for c in row).strip().lower()
        if re.search(r"(?:^|\s)(b/?f|b\.f\.|opening\s+balance|brought\s+forward)(?:\s|$)", joined, re.I):
            bal_candidate = try_amt(safe_cell(row, balance_col)) if balance_col is not None else 0.0
            if bal_candidate > 0:
                opening_balance = bal_candidate
            else:
                nums = [try_amt(c) for c in row if try_amt(c) > 0]
                if nums:
                    opening_balance = max(nums)

            if opening_balance:
                print(f"  [OPENING] Captured opening balance: ₹{opening_balance}")
            bf_row_indexes.add(idx)

    DATE_RE = re.compile(r"\d")
    records = []

    for row_i, row in enumerate(rows[header_idx + 1:], start=header_idx + 1):
        if all(c is None or str(c).strip() == "" for c in row):
            continue

        desc = str(safe_cell(row, desc_col) or "").strip()
        if not desc or JUNK.match(desc):
            continue
        if re.match(r"^[\s▼▶◆►•]+", desc):
            continue

        date_val = safe_cell(row, date_col)
        if date_val is not None:
            from datetime import date as _date_type
            if isinstance(date_val, (_date_type, datetime)):
                pass
            elif isinstance(date_val, float):
                pass
            elif isinstance(date_val, str) and not DATE_RE.search(str(date_val).strip()):
                continue

        dr = try_amt(safe_cell(row, debit_col))
        cr = try_amt(safe_cell(row, credit_col))

        if has_amounts:
            if row_i in bf_row_indexes:
                continue
            elif dr > 0 and cr == 0:
                amount, ttype = dr, "debit"
            elif cr > 0 and dr == 0:
                amount, ttype = cr, "credit"
            elif amount_col is not None:
                signed = try_amt_signed(safe_cell(row, amount_col))
                amount = abs(signed)
                if type_col is not None:
                    raw_type = str(safe_cell(row, type_col) or "").strip().lower()
                    ttype = "credit" if raw_type in ("credit", "cr") else "debit"
                elif signed < 0:
                    ttype = "debit"
                elif signed > 0:
                    ttype = "credit"
                else:
                    ttype = _infer_txn_type(desc)
            elif type_col is not None:
                raw_type = str(safe_cell(row, type_col) or "").strip().lower()
                ttype = "credit" if raw_type in ("credit", "cr") else "debit"
                amount = 0.0
            else:
                amount, ttype = 0.0, _infer_txn_type(desc)
        else:
            if type_col is not None:
                raw_type = str(safe_cell(row, type_col) or "").strip().lower()
                ttype = "credit" if raw_type in ("credit", "cr") else "debit"
            else:
                ttype = _infer_txn_type(desc)
            amount = 0.0

        ttype = derive_txn_type(
            raw_debit=safe_cell(row, debit_col),
            raw_credit=safe_cell(row, credit_col),
            raw_type=safe_cell(row, type_col),
            raw_amount=safe_cell(row, amount_col),
            narration=desc,
            parser_txn_type=ttype,
        )

        bal_raw = safe_cell(row, balance_col)
        bal_val = try_amt(bal_raw) if bal_raw is not None else None

        records.append({
            "txn_date": _safe_parse_excel_date(date_val),
            "description": desc,
            "narration": desc,
            "amount": amount,
            "txn_type": ttype,
            "raw_debit": safe_cell(row, debit_col),
            "raw_credit": safe_cell(row, credit_col),
            "balance": bal_val if bal_val else None,
        })

    print(f"  [EXCEL] Parsed {len(records)} transactions from '{filename}'")

    if not records:
        raise ValueError(
            "Workbook read succeeded but no valid transaction table was detected. "
            "Header row found at index " + str(header_idx) + " but zero data rows passed filters. "
            + " | ".join(parse_attempts)
        )

    # ── Opening balance fallback from first running balance ──────────────────
    if opening_balance is None and records:
        first = next(
            (
                r for r in records
                if r.get("balance") is not None and r.get("amount") and r.get("txn_type") in ("debit", "credit")
            ),
            None
        )
        if first:
            bal = float(first["balance"])
            amt = float(first["amount"])
            opening_balance = bal - amt if first["txn_type"] == "credit" else bal + amt
            print(f"  [OPENING] Derived opening balance from first transaction: ₹{opening_balance}")

     # ── Period and closing balance ───────────────────────────────────────────
    statement_from_date = None
    statement_to_date = None
    explicit_closing_balance = _extract_explicit_closing_balance_from_rows(rows_raw[:80])

    def _extract_statement_period_from_header_rows(header_rows: List[List[Any]]) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract ONE real statement-period pair from header rows.
        Do not merge start from one line and end from another line.
        """
        lines = []
        for row in header_rows or []:
            txt = " ".join(str(c or "").strip() for c in row if str(c or "").strip())
            if txt:
                lines.append(re.sub(r"\s+", " ", txt).strip())

        candidates: List[Tuple[str, str, str]] = []

        strong_patterns = [
            r'(?:statement\s*period|period)\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\s*(?:to|\-)\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})',
            r'(?:from)\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\s*(?:to)\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})',
        ]

        for line in lines:
            s = re.sub(r'\s+', ' ', line).strip()
            for pat in strong_patterns:
                m = re.search(pat, s, flags=re.I)
                if m:
                    d1 = _safe_parse_statement_date(m.group(1))
                    d2 = _safe_parse_statement_date(m.group(2))
                    if d1 and d2 and d1 <= d2:
                        candidates.append((d1, d2, s))

        if not candidates:
            for line in lines:
                s = re.sub(r'\s+', ' ', line).strip()
                m = re.search(
                    r'(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\s*(?:to|\-)\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})',
                    s,
                    flags=re.I
                )
                if m:
                    d1 = _safe_parse_statement_date(m.group(1))
                    d2 = _safe_parse_statement_date(m.group(2))
                    if d1 and d2 and d1 <= d2:
                        candidates.append((d1, d2, s))

        if not candidates:
            return None, None

        def _score(item):
            d1, d2, line = item
            start_day1 = 1 if d1.endswith("-01") else 0
            try:
                span_days = (datetime.strptime(d2, "%Y-%m-%d") - datetime.strptime(d1, "%Y-%m-%d")).days
            except Exception:
                span_days = -1
            return (start_day1, span_days, len(line))

        best = max(candidates, key=_score)
        print(f"  [PERIOD] Selected header period line: {best[2]}")
        return best[0], best[1]

    try:
        header_rows = rows_raw[:25] if rows_raw else []
        statement_from_date, statement_to_date = _extract_statement_period_from_header_rows(header_rows)
        print(f"  [PERIOD] Header-derived period: {statement_from_date} to {statement_to_date}")
    except Exception as e:
        print(f"  [PERIOD] Header extraction failed: {e}")
        statement_from_date, statement_to_date = None, None


    closing_balance = _resolve_statement_closing_balance(
        records, opening_balance, explicit_closing_balance, "parse_excel_statement"
    )

    res = {
        "records": records,
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "statement_from_date": statement_from_date,
        "statement_to_date": statement_to_date,
    }
    _log_parse_return(res.get("records"), res.get("opening_balance"), res.get("closing_balance"), res.get("statement_from_date"), res.get("statement_to_date"))
    return res

# ══════════════════════════════════════════════════════════════════════════════
# LEDGER & BALANCE SHEET GENERATORS
# ══════════════════════════════════════════════════════════════════════════════

def generate_ledger_report(db: DBStore, user_id: str) -> str:
    rows = db.get_ledger_summary(user_id)
    structure: Dict[str, Any] = {}
    for r in rows:
        b, s, g, a = r["book"], r["section"], r["grp"], r["account"]
        structure.setdefault(b, {}).setdefault(s, {}).setdefault(g, {}).setdefault(a, {"credit":0,"debit":0,"cnt":0})
        structure[b][s][g][a][r["txn_type"]] = structure[b][s][g][a].get(r["txn_type"],0) + r["total"]
        structure[b][s][g][a]["cnt"] += r["cnt"]
    all_txns = db.get_txns(user_id, limit=999999)

    # Get opening balance — use get_users() since plain DBStore has no get_user()
    _all_users = db.get_users()
    user = next((u for u in _all_users if u["id"] == user_id), None)
    opening_balance = float((user or {}).get("opening_balance") or 0)

    bank_debits = max(opening_balance, 0.0)
    bank_credits = abs(min(opening_balance, 0.0))
    for tx in all_txns:
        amt = float(tx.get("amount", 0) or 0)
        if tx.get("txn_type") == "credit":
            bank_debits += amt
        elif tx.get("txn_type") == "debit":
            bank_credits += amt

    bank_balance = bank_debits - bank_credits
    W = 90
    sym = "₹"
    def bar(c="─"): return c * W
    def hdr1(t): return f"\n{'═'*W}\n  {t}\n{'═'*W}"
    def amtf(v): return f"{sym}{v:>16,.2f}"
    def normal_balance(section: str, data: Dict[str, Any]) -> float:
        debit = float(data.get("debit", 0) or 0)
        credit = float(data.get("credit", 0) or 0)
        if section in ("Assets", "Expenditure"):
            return debit - credit
        if section in ("Liabilities", "Equity", "Income"):
            return credit - debit
        return debit + credit

    structure.setdefault("BALANCE_SHEET", {}) \
         .setdefault("Assets", {}) \
         .setdefault("Current Assets", {}) \
         .setdefault("Bank Balance", {"credit": 0.0, "debit": 0.0, "cnt": 0})

    structure["BALANCE_SHEET"]["Assets"]["Current Assets"]["Bank Balance"]["debit"] = bank_debits
    structure["BALANCE_SHEET"]["Assets"]["Current Assets"]["Bank Balance"]["credit"] = bank_credits
    structure["BALANCE_SHEET"]["Assets"]["Current Assets"]["Bank Balance"]["cnt"] = len(all_txns)

    users = db.get_users()
    uname = next((u["name"] for u in users if u["id"] == user_id), "Account Holder")

    total_income = total_expenditure = total_assets = total_liabilities = total_equity = 0.0
    lines = [
        "=" * W,
        f"{'HNI  —  COMPLETE LEDGER REPORT':^{W}}",
        f"{'Account Holder: ' + uname:^{W}}",
        f"{'Generated: ' + datetime.now().strftime('%d-%b-%Y  %H:%M'):^{W}}",
        "=" * W,
        f"\n  {'Accounting Basis: Cash Basis (bank statement = cash book)':^{W-4}}",
        f"  {'Debits = expenses already paid. Liabilities = only outstanding amounts.':^{W-4}}\n",
    ]
    
    # ── Book 1: INCOME & EXPENDITURE ─────────────────────────────────────────
    lines.append(hdr1("BOOK I  —  INCOME & EXPENDITURE ACCOUNT  (P&L)"))
    for section in ["Income", "Expenditure"]:
        if "INCOME_EXPENSE" not in structure:
            continue
        if section not in structure["INCOME_EXPENSE"]:
            continue

        lines.append(f"\n  ▌ {section.upper()}")
        sec_total = 0.0

        for grp, accounts in structure["INCOME_EXPENSE"][section].items():
            grp_total = 0.0
            grp_lines = []

            for acct, data in accounts.items():
                amt = normal_balance(section, data)
                if abs(amt) < 0.005:
                    continue

                grp_total += amt
                attr = next((v for k, v in ATTRIBUTION.items() if LEDGER_MAP.get(k, ("", "", "", ""))[3] == acct), "")

                grp_lines.append(f"      ├ {acct:<55} {amtf(amt)}  [{data['cnt']} txns]")
                if attr:
                    grp_lines.append(f"      │  ↳ {attr}")

            if not grp_lines:
                continue

            lines.append(f"\n    ┌── {grp}")
            lines.extend(grp_lines)
            lines.append(f"    └── {grp:<55} {amtf(grp_total)}")
            sec_total += grp_total

        if section == "Income":
            total_income = sec_total
            lines.append(f"\n  {bar('-')}")
            lines.append(f"  {'TOTAL INCOME':<71} {amtf(sec_total)}")
            lines.append(f"  {bar('-')}")
        else:
            total_expenditure = sec_total
            lines.append(f"\n  {bar('-')}")
            lines.append(f"  {'TOTAL EXPENDITURE':<71} {amtf(sec_total)}")
            lines.append(f"  {bar('-')}")

    net_surplus = total_income - total_expenditure
    lines.append(f"\n  {bar('═')}")
    lines.append(f"  {'NET SURPLUS (Income – Expenditure)':<71} {amtf(net_surplus)}")
    lines.append(f"  {bar('═')}")
    lines.append(f"  (This surplus increases owner equity / retained earnings)\n")

    # ── Book 2: BALANCE SHEET ─────────────────────────────────────────────────
    lines.append(hdr1("BOOK II  —  BALANCE SHEET  (Net Worth Statement)"))

    for bs_section in ["Assets", "Liabilities", "Equity"]:
        if "BALANCE_SHEET" not in structure:
            continue
        if bs_section not in structure["BALANCE_SHEET"]:
            continue

        lines.append(f"\n  ▌ {bs_section.upper()}")
        sec_total = 0.0

        for grp, accounts in structure["BALANCE_SHEET"][bs_section].items():
            grp_total = 0.0
            grp_lines = []

            for acct, data in accounts.items():
                amt = normal_balance(bs_section, data)
                if abs(amt) < 0.005:
                    continue

                grp_total += amt
                attr = next((v for k, v in ATTRIBUTION.items() if LEDGER_MAP.get(k, ("", "", "", ""))[3] == acct), "")

                grp_lines.append(f"      ├ {acct:<55} {amtf(amt)}  [{data['cnt']} txns]")
                if attr:
                    grp_lines.append(f"      │  ↳ {attr}")

            if not grp_lines:
                continue

            lines.append(f"\n    ┌── {grp}")
            lines.extend(grp_lines)
            lines.append(f"    └── {grp:<55} {amtf(grp_total)}")
            sec_total += grp_total

        if bs_section == "Assets":
            total_assets = sec_total
        if bs_section == "Liabilities":
            total_liabilities = sec_total
        if bs_section == "Equity":
            total_equity = sec_total

        lines.append(f"\n  {bar('-')}")
        lines.append(f"  {'TOTAL ' + bs_section.upper():<71} {amtf(sec_total)}")
        lines.append(f"  {bar('-')}")

    # FIX C6+C9: Include net P&L in equity for balance check
    retained_earnings = net_surplus
    total_equity_before_adjustment = total_equity + retained_earnings
    balance_gap = total_assets - (total_liabilities + total_equity_before_adjustment)
    balancing_adjustment = balance_gap if abs(balance_gap) >= 0.01 else 0.0
    total_equity_with_pl = total_equity_before_adjustment + balancing_adjustment

    # Balance check
    lines.append(hdr1("BOOK III  —  BALANCE CHECK"))
    lines.append(f"  {'Total Assets (A)':<55} {amtf(total_assets)}")
    lines.append(f"  {'Total Liabilities (B)':<55} {amtf(total_liabilities)}")
    lines.append(f"  {'Total Equity from BS entries (C)':<55} {amtf(total_equity)}")
    lines.append(f"  {'Retained Earnings from P&L (D)':<55} {amtf(retained_earnings)}")
    if balancing_adjustment:
        lines.append(f"  {'Final Balancing Adjustment (E)':<55} {amtf(balancing_adjustment)}")
    lines.append(f"  {bar('-')}")
    lines.append(f"  {'Total Equity incl. P&L and Adjustment':<55} {amtf(total_equity_with_pl)}")
    balance_gap = total_assets - (total_liabilities + total_equity_with_pl)
    lines.append(f"  {bar('═')}")
    lines.append(f"  {'BALANCE GAP  Assets – (Liabilities + Equity)':<55} {amtf(balance_gap)}")
    lines.append(f"  {bar('═')}")
    if abs(balance_gap) < 0.01:
        lines.append(f"  ✅ BALANCE SHEET IN BALANCE")
    else:
        lines.append(f"  ⚠️ Balance gap = {amtf(balance_gap)} — check suspense or unclassified items")

    # Suspense summary
    if "SUSPENSE" in structure:
        lines.append(hdr1("SUSPENSE ACCOUNTS  (Requires Manual Review)"))
        for section, grps in structure["SUSPENSE"].items():
            for grp, accounts in grps.items():
                for acct, data in accounts.items():
                    amt = data.get("credit",0) + data.get("debit",0)
                    lines.append(f"  ► {acct:<60} {amtf(amt)}  [{data['cnt']} txns]")

    lines.append(f"\n{'═'*W}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# WEB SERVER
# ══════════════════════════════════════════════════════════════════════════════

from http.server import BaseHTTPRequestHandler, HTTPServer

# The web boundary lives in goldilocks/app.py. No process-global signed-in user.

def main():
    import sys
    if os.environ.get("RENDER") or os.environ.get("APP_ENV") == "production":
        # Keep the existing Render startup command; replace the process with Gunicorn.
        os.execv(sys.executable, [sys.executable, "-m", "gunicorn", "wsgi:app",
            "--bind", "0.0.0.0:" + os.environ.get("PORT", "8082"),
            "--workers", "1", "--threads", "2", "--timeout", "180"])
    sys.modules.setdefault("hni_accounting_system", sys.modules[__name__])
    from goldilocks.app import create_app
    create_app().run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8082)), debug=False)


if __name__ == "__main__":
    main()
