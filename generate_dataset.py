#!/usr/bin/env python3
"""
generate_dataset_clean.py

Cleaner synthetic dataset generator for HNI ledger classification.

Outputs:
  1. dataset_high_precision.csv
  2. dataset_augmentation.csv
  3. dataset_combined.csv

Design goals:
  - synthetic data should support training, not dominate truth
  - ambiguous classes should be rare and low weight
  - each row carries metadata for downstream weighting
  - easy to extend category by category
"""

from __future__ import annotations

import csv
import random
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

random.seed(2026)


# ──────────────────────────────────────────────────────────────────────────────
# Shared pools
# ──────────────────────────────────────────────────────────────────────────────

FIRST = [
    "ARINJAY", "SARJU", "MANI", "NEHA", "ANJALI", "VIKAS", "SNEHA", "KARAN",
    "RAVI", "POOJA", "DEEPAK", "PRIYA", "AMIT", "SUNITA", "RAJESH", "KAVITA",
    "SURESH", "MEENA", "VIKRAM", "ANITA", "RAHUL", "NIDHI", "MANISH", "PREETI",
]

LAST = [
    "GARG", "SHARMA", "PATEL", "MEHTA", "JOSHI", "GUPTA", "KUMAR", "SINGH",
    "VERMA", "MISHRA", "AGARWAL", "BANSAL", "GOYAL", "MITTAL", "KAPOOR",
    "MALHOTRA", "KHANNA", "ARORA", "NAIR", "REDDY", "RAO", "YADAV",
]

COMPANIES = [
    "RELIANCE INDUSTRIES", "TATA CONSULTANCY SERVICES", "HDFC BANK LTD",
    "INFOSYS", "ICICI BANK LTD", "STATE BANK OF INDIA", "AXIS BANK",
    "KOTAK MAHINDRA BANK", "SUN PHARMACEUTICAL", "MAHARASHTRA SCOOTERS",
    "SWARAJ ENGINES LIMITED", "LIFE INSURANCE CORPORATION",
    "VCSS TECHNOLOGIES", "ALPHA TECH PVT LTD", "NEXUS CONSULTING",
]

BANKS = [
    "HDFC BANK", "ICICI BANK", "AXIS BANK", "KOTAK MAHINDRA BANK",
    "YES BANK", "STATE BANK OF INDIA", "BANK OF BARODA", "PUNJAB NATIONAL BANK",
]

BROKERS = [
    "ZERODHA BROKING LIMITED", "5PAISA CAPITAL LIMITED", "IIFL SECURITIES",
    "UPSTOX", "GROWW SECURITIES", "NSDL", "CDSL",
]

MF = [
    "HDFC MUTUAL FUND", "SBI MUTUAL FUND", "ICICI PRUDENTIAL MF",
    "AXIS MUTUAL FUND", "MIRAE ASSET MF", "NIPPON INDIA MF",
]

UPI_VPA = [
    "okaxis", "okicici", "oksbi", "okhdfcbank", "ybl", "ibl",
    "paytm", "phonepe", "gpay", "bhim", "upi",
]

DIV_KW = ["DIV", "DIVIDEND", "INTERIMDIV", "FINALDIV", "1STINTDIV", "2NDINTDIV"]


def rfirst() -> str:
    return random.choice(FIRST)


def rlast() -> str:
    return random.choice(LAST)


def rname() -> str:
    return f"{rfirst()} {rlast()}"


def rcomp() -> str:
    return random.choice(COMPANIES)


def rbank() -> str:
    return random.choice(BANKS)


def rbroker() -> str:
    return random.choice(BROKERS)


def rmf() -> str:
    return random.choice(MF)


def rvpa() -> str:
    return random.choice(UPI_VPA)


def ref(n: int = 8) -> str:
    return uuid.uuid4().hex[:n].upper()


def num(a: int, b: int) -> str:
    return str(random.randint(a, b))


def trunc(s: str, n: int | None = None) -> str:
    if n is None:
        return s
    return s[:n]


def tok(s: str, limit: int = 16) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())[:limit]


# ──────────────────────────────────────────────────────────────────────────────
# Row model
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Row:
    description: str
    category: str
    txn_type: str
    statement_section: str
    accounting_class: str
    accounting_subclass: str
    entry_nature: str
    source: str
    label_quality: str
    weight: float
    channel: str
    counterparty_type: str


# ──────────────────────────────────────────────────────────────────────────────
# Accounting map
# ──────────────────────────────────────────────────────────────────────────────

ACCOUNTING: Dict[str, Tuple[str, str, str]] = {
    "income_dividend": ("profit_and_loss", "income", "dividend_income"),
    "banking_interest": ("profit_and_loss", "income", "interest_income"),
    "income_salary": ("profit_and_loss", "income", "salary_income"),
    "professional_income": ("profit_and_loss", "income", "professional_fees"),
    "income_gift_family": ("balance_sheet", "equity", "capital_introduced"),
    "income_other": ("profit_and_loss", "income", "other_income"),

    "investment_mutual_fund_purchase": ("balance_sheet", "asset", "mutual_fund_investment"),
    "investment_mutual_fund_redemption_principal": ("balance_sheet", "asset", "mutual_fund_recovery"),
    "investment_equity_purchase": ("balance_sheet", "asset", "equity_investment"),
    "investment_equity_sale_principal": ("balance_sheet", "asset", "equity_recovery"),
    "investment_fd_creation": ("balance_sheet", "asset", "fixed_deposit"),
    "investment_fd_maturity_principal": ("balance_sheet", "asset", "fd_recovery"),
    "ppf_investment": ("balance_sheet", "asset", "ppf_investment"),
    "nps_investment": ("balance_sheet", "asset", "nps_investment"),

    "asset_broker_payout": ("balance_sheet", "asset", "broker_payout"),
    "asset_broker_balance": ("balance_sheet", "asset", "broker_balance"),
    "asset_own_transfer_in": ("balance_sheet", "asset", "own_transfer"),
    "asset_loan_repayment_received": ("balance_sheet", "asset", "loan_repayment_received"),
    "asset_refund_received": ("balance_sheet", "asset", "refund_received"),
    "asset_loans_advances_given": ("balance_sheet", "asset", "loans_advances_given"),

    "exp_food": ("profit_and_loss", "expense", "food"),
    "exp_grocery": ("profit_and_loss", "expense", "groceries"),
    "exp_shopping_online": ("profit_and_loss", "expense", "shopping"),
    "exp_utilities": ("profit_and_loss", "expense", "utilities"),
    "exp_entertainment": ("profit_and_loss", "expense", "entertainment"),
    "exp_health": ("profit_and_loss", "expense", "healthcare"),
    "exp_travel": ("profit_and_loss", "expense", "travel"),
    "exp_education": ("profit_and_loss", "expense", "education"),
    "exp_bank_charges": ("profit_and_loss", "expense", "bank_charges"),
    "exp_broker_dp": ("profit_and_loss", "expense", "broker_dp_charges"),
    "exp_broker_charges": ("profit_and_loss", "expense", "broker_transaction_costs"),
    "exp_broker_interest": ("profit_and_loss", "expense", "broker_interest"),
    "exp_tax": ("profit_and_loss", "expense", "tax"),
    "exp_insurance": ("profit_and_loss", "expense", "insurance"),
    "exp_loan_emi": ("profit_and_loss", "expense", "loan_repayment"),
    "exp_personal_transfer": ("profit_and_loss", "expense", "personal_transfer"),
    "exp_staff_wages": ("profit_and_loss", "expense", "salary_expense"),
    "exp_consultant": ("profit_and_loss", "expense", "consultant_expense"),

    "suspense_credit": ("balance_sheet", "asset", "suspense_credit"),
    "suspense_debit": ("balance_sheet", "asset", "suspense_debit"),
}


# ──────────────────────────────────────────────────────────────────────────────
# Category spec
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class CategorySpec:
    generator: Callable[[], Tuple[str, str, str, str]]
    target: int
    dataset: str          # "high_precision" or "augmentation"
    label_quality: str    # "high_precision" / "augmentation" / "weak_label"
    weight: float


# ──────────────────────────────────────────────────────────────────────────────
# Generators
# Each returns: (description, txn_type, channel, counterparty_type)
# ──────────────────────────────────────────────────────────────────────────────

def gen_income_dividend() -> Tuple[str, str, str, str]:
    comp = rcomp()
    return (
        trunc(f"ACH C- {comp}-{num(1000, 9999999)} {random.choice(DIV_KW)}"),
        "credit",
        "ACH",
        "corporate",
    )


def gen_banking_interest() -> Tuple[str, str, str, str]:
    return (
        f"SB:{num(100000000000, 999999999999)}:Int.Pd:{int(num(1,28)):02d}-01-2026 to {int(num(1,28)):02d}-03-2026",
        "credit",
        "BANK_SYSTEM",
        "bank",
    )


def gen_income_salary() -> Tuple[str, str, str, str]:
    comp = rcomp()
    return (
        trunc(f"DEP TFR NEFT*HDFC0000001*{comp}*SALARY*{ref(10)}"),
        "credit",
        "NEFT",
        "corporate",
    )


def gen_professional_income() -> Tuple[str, str, str, str]:
    comp = rcomp()
    return (
        trunc(f"NEFT CR-{tok(rbank(), 4)}{num(1000000,9999999)}-{comp}-CONSULTING FEE-{ref(10)}"),
        "credit",
        "NEFT",
        "corporate",
    )


def gen_income_gift_family() -> Tuple[str, str, str, str]:
    return (
        f"{num(10000000000000, 99999999999999)}-TPT-GIFT-{rname()}",
        "credit",
        "TRANSFER",
        "family",
    )


def gen_income_other() -> Tuple[str, str, str, str]:
    person = rname()
    return (
        trunc(f"UPI/{person}/{rvpa()}/PAYMENT RECEIVED/{rbank()}/{ref()}/{ref()}"),
        "credit",
        "UPI",
        "individual",
    )


def gen_mf_purchase() -> Tuple[str, str, str, str]:
    mf = rmf()
    return (
        trunc(f"NACH DR-{tok(mf)} SIP-{num(100000,9999999)}"),
        "debit",
        "NACH",
        "fund_house",
    )


def gen_mf_redemption() -> Tuple[str, str, str, str]:
    mf = rmf()
    return (
        trunc(f"MF REDEMPTION {mf} FOLIO {num(10000000, 99999999)}"),
        "credit",
        "MF",
        "fund_house",
    )


def gen_equity_purchase() -> Tuple[str, str, str, str]:
    return (
        f"{num(1000000000000000, 9999999999999999)}/ZERODHA",
        "debit",
        "BROKER",
        "broker",
    )


def gen_equity_sale() -> Tuple[str, str, str, str]:
    return (
        trunc(f"NEFT CR-{tok(rbank(),4)}{num(1000000,9999999)}-ZERODHA BROKING LTD-SETTLEMENT-{ref(12)}"),
        "credit",
        "NEFT",
        "broker",
    )


def gen_fd_creation() -> Tuple[str, str, str, str]:
    return (
        f"FD CREATION {num(100000000000, 999999999999)} {num(50000, 5000000)}",
        "debit",
        "BANK_SYSTEM",
        "bank",
    )


def gen_fd_maturity() -> Tuple[str, str, str, str]:
    return (
        f"FD MATURITY CREDIT {num(100000000000, 999999999999)} {num(50000, 5000000)}",
        "credit",
        "BANK_SYSTEM",
        "bank",
    )


def gen_ppf_investment() -> Tuple[str, str, str, str]:
    return (
        trunc(f"NEFT DR-SBIN0000734-{rname()}-NETBANK MUM-{ref(15)}-PPF"),
        "debit",
        "NEFT",
        "government",
    )


def gen_nps_investment() -> Tuple[str, str, str, str]:
    return (
        trunc(f"NACH DR-NSDL CRA NPS-{num(100000,9999999)}"),
        "debit",
        "NACH",
        "government",
    )


def gen_broker_payout() -> Tuple[str, str, str, str]:
    return (
        f"BEING PAYOUT RELEASED BY IIFL [{num(200000000, 299999999)}]",
        "credit",
        "BROKER",
        "broker",
    )


def gen_broker_balance_move() -> Tuple[str, str, str, str]:
    return (
        trunc(f"NBSM/{num(100000000,999999999)}/{rbroker()}/"),
        "debit",
        "BROKER",
        "broker",
    )


def gen_refund_received() -> Tuple[str, str, str, str]:
    merchant = random.choice(["SWIGGY", "AMAZON INDIA", "ZOMATO", "BLINKIT", "FLIPKART"])
    return (
        trunc(f"UPI/{merchant}/{rvpa()}/REFUND/{rbank()}/{ref()}/{ref()}"),
        "credit",
        "UPI",
        "merchant",
    )


def gen_loan_repayment_received() -> Tuple[str, str, str, str]:
    return (
        f"{num(10000000000000, 99999999999999)}-TPT-REPAY-{rname()}",
        "credit",
        "TRANSFER",
        "individual",
    )


def gen_loans_advances_given() -> Tuple[str, str, str, str]:
    return (
        f"{num(10000000000000, 99999999999999)}-TPT-LOAN-{rname()}",
        "debit",
        "TRANSFER",
        "individual",
    )


def gen_food() -> Tuple[str, str, str, str]:
    vendor = random.choice(["SWIGGY", "ZOMATO", "DOMINOS PIZZA", "KFC INDIA"])
    return (
        trunc(f"UPI/{vendor}/{rvpa()}/FOOD/{rbank()}/{ref()}/{ref()}"),
        "debit",
        "UPI",
        "merchant",
    )


def gen_grocery() -> Tuple[str, str, str, str]:
    vendor = random.choice(["BLINKIT", "ZEPTO", "BIGBASKET", "COUNTRYDELIGHT"])
    return (
        trunc(f"UPI/{vendor}/{rvpa()}/GROCERY/{rbank()}/{ref()}/{ref()}"),
        "debit",
        "UPI",
        "merchant",
    )


def gen_shopping() -> Tuple[str, str, str, str]:
    vendor = random.choice(["AMAZON INDIA", "FLIPKART", "MYNTRA", "NYKAA"])
    return (
        trunc(f"ECOM PUR {vendor} {ref(8)}"),
        "debit",
        "ECOM",
        "merchant",
    )


def gen_utilities() -> Tuple[str, str, str, str]:
    vendor = random.choice(["BESCOM", "AIRTEL BILL", "JIO RECHARGE", "TATA POWER"])
    return (
        trunc(f"UPI/{vendor}/{rvpa()}/BILL/{rbank()}/{ref()}/{ref()}"),
        "debit",
        "UPI",
        "utility",
    )


def gen_entertainment() -> Tuple[str, str, str, str]:
    vendor = random.choice(["NETFLIX", "SPOTIFY", "BOOKMYSHOW", "PVR INOX"])
    return (
        trunc(f"SUBSCRIPTION {vendor} {ref(6)}"),
        "debit",
        "CARD",
        "merchant",
    )


def gen_health() -> Tuple[str, str, str, str]:
    vendor = random.choice(["APOLLO PHARMACY", "MEDPLUS MART", "1MG TECHNOLOGIES"])
    return (
        trunc(f"PHARMACY {vendor} {ref(8)}"),
        "debit",
        "CARD",
        "merchant",
    )


def gen_travel() -> Tuple[str, str, str, str]:
    vendor = random.choice(["UBER INDIA", "OLA CABS", "IRCTC RAILWAY", "MAKEMYTRIP"])
    return (
        trunc(f"TRAVEL BOOKING {vendor} {ref(8)}"),
        "debit",
        "CARD",
        "merchant",
    )


def gen_education() -> Tuple[str, str, str, str]:
    vendor = random.choice(["COURSERA INC", "UDEMY ONLINE", "SCHOOL FEE PAYMENT"])
    return (
        trunc(f"EDUCATION FEE {vendor} {ref(6)}"),
        "debit",
        "UPI",
        "institution",
    )


def gen_bank_charges() -> Tuple[str, str, str, str]:
    return (
        random.choice([
            "INSTAALERTCHG",
            "SMS ALERT CHARGES",
            f"ATM FEE {ref(4)} {num(10,200)}",
            f"BANK CHARGES {ref(6)}",
        ]),
        "debit",
        "BANK_SYSTEM",
        "bank",
    )


def gen_broker_dp() -> Tuple[str, str, str, str]:
    return (
        f"CDSL DP Bill for demat account no. {num(1000000000000000, 9999999999999999)}",
        "debit",
        "BROKER",
        "broker",
    )


def gen_broker_charges() -> Tuple[str, str, str, str]:
    code = random.choice(["NM", "NB", "BB", "NZ"]) + str(random.randint(2024001, 2025999))
    return (
        f"Being contract note bill {code}",
        "debit",
        "BROKER",
        "broker",
    )


def gen_broker_interest() -> Tuple[str, str, str, str]:
    return (
        "BEING INTEREST ON DELAYED PAYMENT FOR PERIOD 01 APRIL 2025 - 30 APRIL 2025",
        "debit",
        "BROKER",
        "broker",
    )


def gen_tax() -> Tuple[str, str, str, str]:
    return (
        f"ADVANCE TAX PAYMENT BSR {num(1000000,9999999)}",
        "debit",
        "TAX",
        "government",
    )


def gen_insurance() -> Tuple[str, str, str, str]:
    return (
        f"LIC PREMIUM {num(100000000, 999999999)} {ref()}",
        "debit",
        "NACH",
        "insurance",
    )


def gen_loan_emi() -> Tuple[str, str, str, str]:
    return (
        f"EMI DEBIT {ref(6)} LOAN A/C {num(100000000,999999999)}",
        "debit",
        "NACH",
        "bank",
    )


def gen_personal_transfer() -> Tuple[str, str, str, str]:
    return (
        trunc(f"NEFT DR-{tok(rbank(),4)}{num(1000000,9999999)}-{rname()}-NETBANK, MUM-{ref(16)}"),
        "debit",
        "NEFT",
        "individual",
    )


def gen_staff_wages() -> Tuple[str, str, str, str]:
    return (
        trunc(f"NEFT DR-{tok(rbank(),4)}{num(1000000,9999999)}-{rname()}-NETBANK, MUM-{ref(20)}-SAL APR"),
        "debit",
        "NEFT",
        "individual",
    )


def gen_consultant() -> Tuple[str, str, str, str]:
    return (
        trunc(f"NEFT DR-{tok(rbank(),4)}{num(1000000,9999999)}-{rname()}-NETBANK, MUM-{ref(20)}-CON APR"),
        "debit",
        "NEFT",
        "individual",
    )


# Weak label / augmentation only
def gen_suspense_credit() -> Tuple[str, str, str, str]:
    return (
        trunc(f"UPI/{rname()}/{rvpa()}/PAYMENT/{rbank()}/{ref()}/{ref()}"),
        "credit",
        "UPI",
        "individual",
    )


def gen_suspense_debit() -> Tuple[str, str, str, str]:
    return (
        trunc(f"IMPS/{num(100000000000,999999999999)}/{rname()}/{tok(rbank(),4)}{num(1000000,9999999)}/{ref()}"),
        "debit",
        "IMPS",
        "individual",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Specs
# ──────────────────────────────────────────────────────────────────────────────

SPECS: Dict[str, CategorySpec] = {
    "income_dividend": CategorySpec(gen_income_dividend, 350, "high_precision", "high_precision", 1.00),
    "banking_interest": CategorySpec(gen_banking_interest, 300, "high_precision", "high_precision", 1.00),
    "income_salary": CategorySpec(gen_income_salary, 350, "high_precision", "high_precision", 1.00),
    "professional_income": CategorySpec(gen_professional_income, 220, "high_precision", "high_precision", 1.00),
    "income_gift_family": CategorySpec(gen_income_gift_family, 220, "high_precision", "high_precision", 1.00),
    "income_other": CategorySpec(gen_income_other, 120, "augmentation", "augmentation", 0.30),

    "investment_mutual_fund_purchase": CategorySpec(gen_mf_purchase, 320, "high_precision", "high_precision", 1.00),
    "investment_mutual_fund_redemption_principal": CategorySpec(gen_mf_redemption, 220, "high_precision", "high_precision", 1.00),
    "investment_equity_purchase": CategorySpec(gen_equity_purchase, 260, "high_precision", "high_precision", 1.00),
    "investment_equity_sale_principal": CategorySpec(gen_equity_sale, 220, "high_precision", "high_precision", 1.00),
    "investment_fd_creation": CategorySpec(gen_fd_creation, 180, "high_precision", "high_precision", 1.00),
    "investment_fd_maturity_principal": CategorySpec(gen_fd_maturity, 160, "high_precision", "high_precision", 1.00),
    "ppf_investment": CategorySpec(gen_ppf_investment, 180, "high_precision", "high_precision", 1.00),
    "nps_investment": CategorySpec(gen_nps_investment, 150, "high_precision", "high_precision", 1.00),

    "asset_broker_payout": CategorySpec(gen_broker_payout, 180, "high_precision", "high_precision", 1.00),
    "asset_broker_balance": CategorySpec(gen_broker_balance_move, 180, "augmentation", "augmentation", 0.40),
    "asset_refund_received": CategorySpec(gen_refund_received, 220, "high_precision", "high_precision", 1.00),
    "asset_loan_repayment_received": CategorySpec(gen_loan_repayment_received, 180, "high_precision", "high_precision", 1.00),
    "asset_loans_advances_given": CategorySpec(gen_loans_advances_given, 180, "high_precision", "high_precision", 1.00),

    "exp_food": CategorySpec(gen_food, 280, "high_precision", "high_precision", 1.00),
    "exp_grocery": CategorySpec(gen_grocery, 260, "high_precision", "high_precision", 1.00),
    "exp_shopping_online": CategorySpec(gen_shopping, 240, "high_precision", "high_precision", 1.00),
    "exp_utilities": CategorySpec(gen_utilities, 220, "high_precision", "high_precision", 1.00),
    "exp_entertainment": CategorySpec(gen_entertainment, 180, "high_precision", "high_precision", 1.00),
    "exp_health": CategorySpec(gen_health, 180, "high_precision", "high_precision", 1.00),
    "exp_travel": CategorySpec(gen_travel, 220, "high_precision", "high_precision", 1.00),
    "exp_education": CategorySpec(gen_education, 160, "high_precision", "high_precision", 1.00),
    "exp_bank_charges": CategorySpec(gen_bank_charges, 180, "high_precision", "high_precision", 1.00),
    "exp_broker_dp": CategorySpec(gen_broker_dp, 140, "high_precision", "high_precision", 1.00),
    "exp_broker_charges": CategorySpec(gen_broker_charges, 140, "high_precision", "high_precision", 1.00),
    "exp_broker_interest": CategorySpec(gen_broker_interest, 120, "high_precision", "high_precision", 1.00),
    "exp_tax": CategorySpec(gen_tax, 150, "high_precision", "high_precision", 1.00),
    "exp_insurance": CategorySpec(gen_insurance, 140, "high_precision", "high_precision", 1.00),
    "exp_loan_emi": CategorySpec(gen_loan_emi, 180, "high_precision", "high_precision", 1.00),
    "exp_personal_transfer": CategorySpec(gen_personal_transfer, 160, "high_precision", "high_precision", 1.00),
    "exp_staff_wages": CategorySpec(gen_staff_wages, 150, "high_precision", "high_precision", 1.00),
    "exp_consultant": CategorySpec(gen_consultant, 130, "high_precision", "high_precision", 1.00),

    # Weak / ambiguous only as augmentation
    "suspense_credit": CategorySpec(gen_suspense_credit, 60, "augmentation", "weak_label", 0.10),
    "suspense_debit": CategorySpec(gen_suspense_debit, 60, "augmentation", "weak_label", 0.10),
}


# ──────────────────────────────────────────────────────────────────────────────
# Optional noise augmentation
# ──────────────────────────────────────────────────────────────────────────────

def add_noise(description: str, channel: str) -> str:
    variants = [description]

    # spacing noise
    variants.append(re.sub(r"\s+", "  ", description))
    variants.append(description.replace("-", " - "))
    variants.append(description.replace("/", " / "))

    # truncation noise
    if len(description) > 28:
        variants.append(description[: random.randint(28, min(45, len(description)))])

    # bank wrapper noise
    if channel == "UPI":
        variants.append(f"WDL TFR {description}")
        variants.append(f"DEP TFR {description}")
    if channel == "NEFT":
        variants.append(description.replace("NEFT", "DEP TFR NEFT", 1))
    if channel == "ACH":
        variants.append(description.replace("ACH C-", "ACH-CR-", 1))
    if channel == "BROKER":
        variants.append(description.replace(" ", ""))

    return random.choice(variants)


# ──────────────────────────────────────────────────────────────────────────────
# Build rows
# ──────────────────────────────────────────────────────────────────────────────

def make_row(category: str, spec: CategorySpec) -> Row:
    description, txn_type, channel, counterparty_type = spec.generator()
    statement_section, accounting_class, accounting_subclass = ACCOUNTING[category]

    if spec.dataset == "augmentation":
        description = add_noise(description, channel)

    return Row(
        description=description,
        category=category,
        txn_type=txn_type,
        statement_section=statement_section,
        accounting_class=accounting_class,
        accounting_subclass=accounting_subclass,
        entry_nature="normal",
        source="synthetic",
        label_quality=spec.label_quality,
        weight=spec.weight,
        channel=channel,
        counterparty_type=counterparty_type,
    )


def write_csv(path: str, rows: List[Row]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "description",
            "category",
            "txn_type",
            "statement_section",
            "accounting_class",
            "accounting_subclass",
            "entry_nature",
            "source",
            "label_quality",
            "weight",
            "channel",
            "counterparty_type",
        ])
        for r in rows:
            w.writerow([
                r.description,
                r.category,
                r.txn_type,
                r.statement_section,
                r.accounting_class,
                r.accounting_subclass,
                r.entry_nature,
                r.source,
                r.label_quality,
                f"{r.weight:.2f}",
                r.channel,
                r.counterparty_type,
            ])


def main() -> None:
    high_precision_rows: List[Row] = []
    augmentation_rows: List[Row] = []

    for category, spec in SPECS.items():
        for _ in range(spec.target):
            row = make_row(category, spec)
            if spec.dataset == "high_precision":
                high_precision_rows.append(row)
            else:
                augmentation_rows.append(row)

    random.shuffle(high_precision_rows)
    random.shuffle(augmentation_rows)

    combined = high_precision_rows + augmentation_rows
    random.shuffle(combined)

    write_csv("dataset_high_precision.csv", high_precision_rows)
    write_csv("dataset_augmentation.csv", augmentation_rows)
    write_csv("dataset_combined.csv", combined)

    cat_counts = Counter(r.category for r in combined)
    type_counts = Counter(r.txn_type for r in combined)
    quality_counts = Counter(r.label_quality for r in combined)

    print(f"\nGenerated:")
    print(f"  dataset_high_precision.csv : {len(high_precision_rows):,} rows")
    print(f"  dataset_augmentation.csv   : {len(augmentation_rows):,} rows")
    print(f"  dataset_combined.csv       : {len(combined):,} rows")
    print(f"\nTxn types: {dict(type_counts)}")
    print(f"Label quality: {dict(quality_counts)}")
    print("\nTop categories:")
    for cat, cnt in cat_counts.most_common(20):
        print(f"  {cat:<40} {cnt:>6}")


if __name__ == "__main__":
    main()