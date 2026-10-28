"""Integer-paise journals and independent reconciliation, not a balancing plug."""

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import uuid


def paise(value):
    d = Decimal(str(value or 0))
    if not d.is_finite() or abs(d) > Decimal("1000000000000"):
        raise ValueError("Amount is outside the supported range.")
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def initialize(db):
    with db._conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS journal_entries (
          id TEXT PRIMARY KEY,user_id TEXT NOT NULL,ledger_id TEXT UNIQUE,txn_date TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS journal_lines (
          id INTEGER PRIMARY KEY, entry_id TEXT NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
          code TEXT NOT NULL,label TEXT NOT NULL,section TEXT NOT NULL,
          debit INTEGER NOT NULL DEFAULT 0 CHECK(debit>=0),credit INTEGER NOT NULL DEFAULT 0 CHECK(credit>=0),
          CHECK((debit=0 OR credit=0) AND debit+credit>0));
        CREATE TABLE IF NOT EXISTS opening_positions (
          user_id TEXT,account_id TEXT,amount INTEGER NOT NULL,as_of TEXT NOT NULL,
          PRIMARY KEY(user_id,account_id));
        CREATE TABLE IF NOT EXISTS review_events (
          id TEXT PRIMARY KEY,user_id TEXT NOT NULL,action TEXT NOT NULL,record_id TEXT,
          before_json TEXT,after_json TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS transaction_splits (
          id TEXT PRIMARY KEY,user_id TEXT NOT NULL,transaction_id TEXT NOT NULL,
          ledger_key TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>0));
        CREATE INDEX IF NOT EXISTS split_transaction ON transaction_splits(user_id,transaction_id);
        CREATE TABLE IF NOT EXISTS opening_holdings (
          user_id TEXT NOT NULL,account_id TEXT NOT NULL,ledger_key TEXT NOT NULL,
          amount INTEGER NOT NULL,as_of TEXT NOT NULL,PRIMARY KEY(user_id,account_id,ledger_key));
        CREATE INDEX IF NOT EXISTS journal_user ON journal_entries(user_id,txn_date);
        """)


def counterpart(row):
    key = row["ledger_key"]
    section = row["section"]
    label = row["account"]
    code = key
    if key in (
        "trading_funds_added",
        "trading_payout",
        "asset_broker_payout",
        "asset_broker_balance",
    ):
        code = "asset_broker_balance"
        label = "Broker cash balance"
        section = "Assets"
    elif key in ("asset_investment_fd", "asset_fd_maturity"):
        code = "asset_investment_fd"
        label = "Fixed deposits at recorded cost"
        section = "Assets"
    elif key in ("asset_investment_mf", "asset_mf_redemption"):
        code = "asset_investment_mf"
        label = "Mutual funds at recorded cost"
        section = "Assets"
    elif key in ("asset_investment_equity", "asset_equity_sale_proceeds"):
        code = "asset_investment_equity"
        label = "Equity investments at recorded cost"
        section = "Assets"
    elif key in (
        "asset_loans_advances_given",
        "asset_loan_repayment_received",
        "asset_advance_received_back",
        "asset_advance_returned",
    ):
        code = "asset_loans_advances_given"
        label = "Loans and advances"
        section = "Assets"
    elif "own_transfer" in key or key.startswith("contra_"):
        code = "transfer_clearing"
        label = "Internal transfer clearing"
        section = "Suspense"
    return code, label, section


def post(c, row):
    """Caller holds the posting transaction; no independent commit can split a journal."""
    if c.execute(
        "SELECT 1 FROM journal_entries WHERE ledger_id=?", (row["id"],)
    ).fetchone():
        return
    amount = paise(row["amount"])
    if amount <= 0:
        raise ValueError("Only positive transaction amounts can be posted.")
    eid = str(uuid.uuid4())
    c.execute(
        "INSERT INTO journal_entries(id,user_id,ledger_id,txn_date) VALUES(?,?,?,?)",
        (eid, row["user_id"], row["id"], row["txn_date"]),
    )
    incoming = row["txn_type"] == "credit"
    code, label, section = counterpart(row)
    c.execute(
        "INSERT INTO journal_lines(entry_id,code,label,section,debit,credit) VALUES(?,?,?,?,?,?)",
        (
            eid,
            "bank:" + row["account_id"],
            row["account_id"],
            "Assets",
            amount if incoming else 0,
            0 if incoming else amount,
        ),
    )
    splits = c.execute(
        "SELECT * FROM transaction_splits WHERE transaction_id=? AND user_id=?",
        (row["id"], row["user_id"]),
    ).fetchall()
    if splits:
        import hni_accounting_system as core

        if sum(s["amount"] for s in splits) != amount:
            raise ValueError("Split amounts must equal the original transaction.")
        allocations = []
        for s in splits:
            book, section, group, label = core.LEDGER_MAP[s["ledger_key"]]
            part = counterpart(
                {"ledger_key": s["ledger_key"], "section": section, "account": label}
            )
            allocations.append((*part, s["amount"]))
    else:
        allocations = [(code, label, section, amount)]
    for code, label, section, value in allocations:
        c.execute(
            "INSERT INTO journal_lines(entry_id,code,label,section,debit,credit) VALUES(?,?,?,?,?,?)",
            (
                eid,
                code,
                label,
                section,
                0 if incoming else value,
                value if incoming else 0,
            ),
        )


def report(db, uid, date_from=None, date_to=None, account_id=None):
    params = [uid]
    where = "j.user_id=?"
    if date_to:
        where += " AND j.txn_date<=?"
        params.append(date_to)
    if account_id:
        where += " AND t.account_id=?"
        params.append(account_id)
    with db._conn() as c:
        entries = [
            dict(x)
            for x in c.execute(
                f"""SELECT l.*,j.txn_date,j.ledger_id,t.account_id FROM journal_lines l
           JOIN journal_entries j ON j.id=l.entry_id JOIN ledger t ON t.id=j.ledger_id WHERE {where}""",
                params,
            )
        ]
        openings = [
            dict(x)
            for x in c.execute(
                "SELECT * FROM opening_positions WHERE user_id=?", (uid,)
            )
            if (not account_id or x["account_id"] == account_id)
            and (not date_to or x["as_of"] <= date_to)
        ]
        holdings = [
            dict(x)
            for x in c.execute("SELECT * FROM opening_holdings WHERE user_id=?", (uid,))
            if (not account_id or x["account_id"] == account_id)
            and (not date_to or x["as_of"] <= date_to)
        ]
        missing = c.execute(
            "SELECT count(*) FROM ledger t WHERE user_id=? AND NOT EXISTS(SELECT 1 FROM journal_entries j WHERE j.ledger_id=t.id)",
            (uid,),
        ).fetchone()[0]
        unresolved = c.execute(
            "SELECT count(*) FROM pending_ledger WHERE user_id=? AND status IN ('PENDING','RECLASSIFIED')",
            (uid,),
        ).fetchone()[0]
    balances = defaultdict(int)
    info = {}
    income = defaultdict(int)
    expense = defaultdict(int)
    entry_totals = defaultdict(int)
    for x in entries:
        net = x["debit"] - x["credit"]
        balances[x["code"]] += net
        info[x["code"]] = (x["label"], x["section"])
        entry_totals[x["entry_id"]] += net
        if not date_from or x["txn_date"] >= date_from:
            if x["section"] == "Income":
                income[x["label"]] -= net
            elif x["section"] == "Expenditure":
                expense[x["label"]] += net
    capital = 0
    for o in openings:
        code = "bank:" + o["account_id"]
        balances[code] += o["amount"]
        info[code] = (o["account_id"], "Assets")
        capital += o["amount"]
    import hni_accounting_system as core

    for h in holdings:
        book, section, group, label = core.LEDGER_MAP[h["ledger_key"]]
        code, label, section = counterpart(
            {"ledger_key": h["ledger_key"], "section": section, "account": label}
        )
        value = h["amount"] * (1 if section == "Assets" else -1)
        balances[code] += value
        info[code] = (label, section)
        capital += value
    sections = {"Assets": [], "Liabilities": [], "Equity": [], "Suspense": []}
    retained = 0
    for code, net in balances.items():
        label, section = info[code]
        if section in ("Income", "Expenditure"):
            retained -= net
            continue
        if section not in sections:
            section = "Suspense"
        amount = net if section in ("Assets", "Suspense") else -net
        if section == "Assets" and amount < 0:
            section = "Liabilities"
            amount = -amount
            label = (
                "Broker cash payable"
                if code == "asset_broker_balance"
                else label + " (credit balance)"
            )
        if amount:
            sections[section].append(
                {"code": code, "label": label, "amount": amount / 100}
            )
    sections["Equity"].extend(
        [
            {
                "code": "opening_equity",
                "label": "Opening net assets (recorded / inferred)",
                "amount": capital / 100,
            },
            {
                "code": "retained",
                "label": "Accumulated surplus / deficit",
                "amount": retained / 100,
            },
        ]
    )
    totals = {
        s: round(sum(r["amount"] for r in rows), 2) for s, rows in sections.items()
    }
    difference = round(totals["Assets"] - totals["Liabilities"] - totals["Equity"], 2)
    months = defaultdict(lambda: {"income": 0, "expense": 0})
    for x in entries:
        if date_from and x["txn_date"] < date_from:
            continue
        if x["section"] == "Income":
            months[x["txn_date"][:7]]["income"] += (x["credit"] - x["debit"]) / 100
        if x["section"] == "Expenditure":
            months[x["txn_date"][:7]]["expense"] += (x["debit"] - x["credit"]) / 100
    return {
        "monthly_performance": dict(months),
        "sections": sections,
        "totals": totals,
        "income": [{"label": k, "amount": v / 100} for k, v in income.items()],
        "expenses": [{"label": k, "amount": v / 100} for k, v in expense.items()],
        "income_total": sum(income.values()) / 100,
        "expense_total": sum(expense.values()) / 100,
        "surplus": (sum(income.values()) - sum(expense.values())) / 100,
        "net_worth": round(totals["Assets"] - totals["Liabilities"], 2),
        "difference": difference,
        "journal_balanced": all(v == 0 for v in entry_totals.values()),
        "missing_journals": missing,
        "pending": unresolved,
        "scope": "Recorded transactions and opening balances only. Inferred bank openings must be confirmed. Not a market valuation or certified financial statement.",
    }
