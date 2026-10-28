"""User-scoped import, review, atomic posting, provenance and reconciliation."""

import hashlib
import json
import re
import uuid
from collections import Counter
from datetime import datetime
from .accounting import paise, post


def initialize(db):
    with db._conn() as c:
        c.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS source_documents(batch_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,content BLOB NOT NULL,bank TEXT,parser TEXT,warnings TEXT);
        CREATE TABLE IF NOT EXISTS learning_examples(user_id TEXT,narration TEXT,txn_type TEXT,ledger_key TEXT,reviewed_at TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(user_id,narration,txn_type));
        """)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(source_documents)")}
        for name in ("opening_source", "closing_source"):
            if name not in cols:
                c.execute(f"ALTER TABLE source_documents ADD COLUMN {name} TEXT")


def event(c, uid, action, rid, before=None, after=None):
    c.execute(
        "INSERT INTO review_events(id,user_id,action,record_id,before_json,after_json) VALUES(?,?,?,?,?,?)",
        (str(uuid.uuid4()), uid, action, rid, json.dumps(before), json.dumps(after)),
    )


def classification(edb, uid, narration, direction, amount, day):
    import hni_accounting_system as core
    import hni_ledger_extension as extension

    with edb._conn() as c:
        learned = c.execute(
            "SELECT ledger_key FROM learning_examples WHERE user_id=? AND narration=? AND txn_type=?",
            (uid, narration.casefold().strip(), direction),
        ).fetchone()
    if learned:
        result = core._build(
            narration,
            learned["ledger_key"],
            direction,
            1.0,
            source="reviewed example",
            note="Your previously reviewed classification.",
        )
    else:
        result = None
        for rule in edb.get_custom_rules(uid):
            if rule["txn_type"] and rule["txn_type"] != direction:
                continue
            text = narration.casefold()
            pattern = rule["pattern"].casefold()
            if (rule["match_mode"] == "exact" and text == pattern) or (
                rule["match_mode"] == "contains" and pattern in text
            ):
                result = core._build(
                    narration,
                    rule["ledger_key"],
                    direction,
                    1.0,
                    source="personal rule",
                    note="Personal rule " + rule["id"],
                )
                break
        if result is None:
            if direction == "debit" and re.search(
                r"\b(?:RENT PAYMENT|RENT PAID|RENT RESIDENCE|HOUSE RENT)\b",
                narration,
                re.I,
            ):
                result = core._build(
                    narration, "exp_rent", direction, 0.97, source="rule"
                )
            elif direction == "credit" and re.search(
                r"\b(?:BANK INTEREST|SAVINGS INTEREST|SB INT|FD INTEREST)\b",
                narration,
                re.I,
            ):
                result = core._build(
                    narration, "income_interest", direction, 0.98, source="rule"
                )
            elif (
                re.search(r"\bEMI\b|LOAN INSTAL+MENT", narration, re.I)
                and direction == "debit"
            ):
                result = core._build(
                    narration,
                    "suspense_debit",
                    direction,
                    0,
                    source="review required",
                    note="Split principal and interest using the loan schedule before approval.",
                )
            else:
                result = core.classify(
                    narration, forced_type=direction, amount=amount, txn_date=day
                )
            result = extension._apply_profile_classification_hints(
                edb, uid, narration, result, direction
            )
            if result.get("source") == "transfer_heuristic":
                result = core._build(
                    narration,
                    "suspense_" + direction,
                    direction,
                    0,
                    source="review required",
                    note="A transfer name alone cannot establish whether this is a gift, loan or expense.",
                )
            if result.get("ledger_key") == "exp_loan_emi":
                result = core._build(
                    narration,
                    "suspense_debit",
                    direction,
                    0,
                    source="review required",
                    note="An EMI can contain principal and interest. Provide the split before treating it as expense.",
                )
            elif result.get("ledger_key") == "exp_credit_card":
                result = core._build(
                    narration,
                    "liability_credit_card",
                    direction,
                    0.6,
                    source="rule",
                    note="Settlement reduces card liability; spending belongs in the card statement. Declare opening debt to avoid incomplete balances.",
                )
            elif result.get("source") == "ml" and result.get("confidence", 0) < 0.6:
                result = core._build(
                    narration,
                    "suspense_" + direction,
                    direction,
                    result.get("confidence", 0),
                    source="ml",
                    note="Model suggestion below the review threshold.",
                )
    return extension._canonicalize_result(result, direction)


def stage(db, edb, uid, account_id, filename, data, parsed):
    if not re.fullmatch(r"[A-Za-z0-9 _.-]{1,60}", account_id):
        raise ValueError(
            "Use a short account label with letters, numbers, spaces or dashes."
        )
    filehash = hashlib.sha256(data).hexdigest()
    batchid = str(uuid.uuid4())
    with db._conn() as c:
        existing = c.execute(
            "SELECT * FROM raw_import_batches WHERE user_id=? AND account_id=? AND source_file_hash=?",
            (uid, account_id, filehash),
        ).fetchone()
        if existing:
            return {
                "batch_id": existing["id"],
                "duplicate": True,
                "staged": 0,
                "message": "This exact statement is already imported.",
            }
        storage = c.execute(
            "SELECT coalesce(sum(length(content)),0) FROM source_documents WHERE user_id=?",
            (uid,),
        ).fetchone()[0]
    if storage + len(data) > 150_000_000:
        raise ValueError("This workspace has reached its 150 MB source-file allowance.")
    prepared = []
    occurrences = Counter()
    for rec in parsed["records"]:
        narration = str(rec.get("narration") or rec.get("description") or "").strip()
        direction = rec["txn_type"]
        day = rec["txn_date"]
        amount = paise(rec["amount"]) / 100
        if amount <= 0 or not narration:
            raise ValueError(
                "Every transaction must have a positive amount and narration."
            )
        base = f"{uid}|{account_id}|{day}|{narration.casefold()}|{paise(amount)}|{direction}"
        occurrences[base] += 1
        fingerprint = hashlib.sha256(
            (base + f"|{occurrences[base]}").encode()
        ).hexdigest()
        predicted = classification(edb, uid, narration, direction, amount, day)
        prepared.append(
            (rec, narration, direction, day, amount, fingerprint, predicted)
        )
    now = datetime.now().isoformat()
    staged = duplicates = 0
    with db._conn() as c:
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute(
            "SELECT id FROM raw_import_batches WHERE user_id=? AND account_id=? AND source_file_hash=?",
            (uid, account_id, filehash),
        ).fetchone()
        if existing:
            return {"batch_id": existing["id"], "duplicate": True, "staged": 0}
        if (
            c.execute(
                "SELECT coalesce(sum(length(content)),0) FROM source_documents WHERE user_id=?",
                (uid,),
            ).fetchone()[0]
            + len(data)
            > 150_000_000
        ):
            raise ValueError("Workspace source storage limit reached.")
        c.execute(
            """INSERT INTO raw_import_batches(id,user_id,account_id,statement_type,source_file_name,source_file_hash,file_size_bytes,total_rows,parsed_rows,valid_rows,invalid_rows,duplicate_rows,staged_rows,approved_rows,opening_balance,closing_balance,statement_from_date,statement_to_date,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                batchid,
                uid,
                account_id,
                "BANK",
                filename,
                filehash,
                len(data),
                len(prepared),
                len(prepared),
                len(prepared),
                0,
                0,
                0,
                0,
                parsed.get("opening_balance"),
                parsed.get("closing_balance"),
                parsed.get("statement_from_date"),
                parsed.get("statement_to_date"),
                now,
                now,
            ),
        )
        c.execute(
            "INSERT INTO source_documents(batch_id,user_id,content,bank,parser,warnings,opening_source,closing_source) VALUES(?,?,?,?,?,?,?,?)",
            (
                batchid,
                uid,
                data,
                parsed.get("bank"),
                parsed.get("parser"),
                json.dumps(parsed.get("warnings", [])),
                parsed.get("opening_balance_source"),
                parsed.get("closing_balance_source"),
            ),
        )
        for i, (rec, narration, direction, day, amount, fp, result) in enumerate(
            prepared, 1
        ):
            rid = str(uuid.uuid4())
            pid = str(uuid.uuid4())
            duplicate = c.execute(
                "SELECT id FROM raw_import_rows WHERE user_id=? AND account_id=? AND fingerprint=? LIMIT 1",
                (uid, account_id, fp),
            ).fetchone()
            if duplicate:
                duplicates += 1
                pid = None
            else:
                staged += 1
                c.execute(
                    """INSERT INTO pending_ledger(id,user_id,account_id,txn_date,narration,amount,txn_type,predicted_ledger_key,confidence,book,section,grp,account,counterparty,attribution,source,note,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        pid,
                        uid,
                        account_id,
                        day,
                        narration,
                        amount,
                        direction,
                        result["ledger_key"],
                        float(result.get("confidence", 0)),
                        result["book"],
                        result["section"],
                        result.get("grp", result.get("group", "")),
                        result["account"],
                        result.get("counterparty", ""),
                        result.get("attribution", ""),
                        result.get("source", "rule"),
                        result.get("note", ""),
                        "PENDING",
                        now,
                    ),
                )
            c.execute(
                """INSERT INTO raw_import_rows(id,batch_id,user_id,account_id,statement_type,row_number,source_file_name,source_file_hash,raw_date,raw_description,raw_debit,raw_credit,raw_amount,raw_balance,raw_txn_type,raw_json,normalized_date,normalized_narration,normalized_amount,normalized_balance,normalized_txn_type,parse_status,validation_status,validation_errors,fingerprint,duplicate_status,duplicate_of_row_id,moved_to_pending_flag,moved_to_pending_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rid,
                    batchid,
                    uid,
                    account_id,
                    "BANK",
                    i,
                    filename,
                    filehash,
                    day,
                    narration,
                    str(rec.get("raw_debit", "")),
                    str(rec.get("raw_credit", "")),
                    str(amount),
                    str(rec.get("balance", "")),
                    direction,
                    json.dumps(rec),
                    day,
                    narration,
                    amount,
                    rec.get("balance"),
                    direction,
                    "PARSED",
                    "VALID",
                    "[]",
                    fp,
                    "EXACT_DUPLICATE" if duplicate else "NEW",
                    duplicate["id"] if duplicate else None,
                    int(not duplicate),
                    pid,
                    now,
                ),
            )
        c.execute(
            "UPDATE raw_import_batches SET staged_rows=?,duplicate_rows=? WHERE id=?",
            (staged, duplicates, batchid),
        )
        # A statement-derived opening is recorded explicitly, never plugged to force balance.
        opening = parsed.get("opening_balance")
        if opening is not None:
            current = c.execute(
                "SELECT as_of FROM opening_positions WHERE user_id=? AND account_id=?",
                (uid, account_id),
            ).fetchone()
            if not current or parsed["statement_from_date"] < current["as_of"]:
                c.execute(
                    "INSERT INTO opening_positions VALUES(?,?,?,?) ON CONFLICT(user_id,account_id) DO UPDATE SET amount=excluded.amount,as_of=excluded.as_of",
                    (uid, account_id, paise(opening), parsed["statement_from_date"]),
                )
        event(
            c,
            uid,
            "statement_imported",
            batchid,
            after={
                "staged": staged,
                "duplicates": duplicates,
                "bank": parsed.get("bank"),
            },
        )
    return {
        "batch_id": batchid,
        "duplicate": False,
        "staged": staged,
        "duplicates": duplicates,
        "warnings": parsed.get("warnings", []),
        "bank": parsed.get("bank"),
    }


def reclassify(db, uid, rid, key, note="", posted=False):
    import hni_accounting_system as core

    if key in ("exp_loan_emi", "exp_credit_card"):
        raise ValueError(
            "Use a principal/interest split for an EMI, or the credit-card liability category for settlement."
        )
    if key not in core.LEDGER_MAP:
        raise ValueError("Choose a known ledger category.")
    table = "ledger" if posted else "pending_ledger"
    with db._conn() as c:
        c.execute("BEGIN IMMEDIATE")
        before = c.execute(
            f"SELECT * FROM {table} WHERE id=? AND user_id=?", (rid, uid)
        ).fetchone()
        if not before:
            raise LookupError("Transaction not found.")
        before = dict(before)
        if not posted and before["status"] not in ("PENDING", "RECLASSIFIED"):
            raise ValueError("This transaction is already posted.")
        c.execute(
            "DELETE FROM transaction_splits WHERE transaction_id=? AND user_id=?",
            (rid, uid),
        )
        book, section, group, label = core.LEDGER_MAP[key]
        if section == "Income" and before["txn_type"] == "debit":
            raise ValueError(
                "A debit cannot be classified as income. Review the source direction."
            )
        if posted and (section == "Suspense" or key.startswith("suspense")):
            raise ValueError("Posted entries need a resolved category.")
        if posted:
            c.execute(
                "UPDATE ledger SET ledger_key=?,book=?,section=?,grp=?,account=?,note=? WHERE id=? AND user_id=?",
                (key, book, section, group, label, note, rid, uid),
            )
            c.execute(
                "DELETE FROM journal_entries WHERE ledger_id=? AND user_id=?",
                (rid, uid),
            )
        else:
            c.execute(
                "UPDATE pending_ledger SET reclassified_key=?,book=?,section=?,grp=?,account=?,note=?,status='RECLASSIFIED' WHERE id=? AND user_id=?",
                (key, book, section, group, label, note, rid, uid),
            )
        after = dict(
            c.execute(
                f"SELECT * FROM {table} WHERE id=? AND user_id=?", (rid, uid)
            ).fetchone()
        )
        if posted:
            post(c, after)
        c.execute(
            "INSERT INTO learning_examples(user_id,narration,txn_type,ledger_key) VALUES(?,?,?,?) ON CONFLICT(user_id,narration,txn_type) DO UPDATE SET ledger_key=excluded.ledger_key,reviewed_at=CURRENT_TIMESTAMP",
            (uid, before["narration"].casefold().strip(), before["txn_type"], key),
        )
        event(
            c,
            uid,
            "posted_classification_corrected" if posted else "classification_reviewed",
            rid,
            before,
            after,
        )
    return after


def approve(db, uid, ids):
    if (
        not isinstance(ids, list)
        or not all(isinstance(r, str) for r in ids)
        or not ids
        or len(ids) > 1000
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("Select 1–1,000 distinct transactions.")
    with db._conn() as c:
        c.execute("BEGIN IMMEDIATE")
        rows = []
        for rid in ids:
            row = c.execute(
                "SELECT * FROM pending_ledger WHERE id=? AND user_id=?", (rid, uid)
            ).fetchone()
            if not row:
                raise LookupError("A selected transaction is unavailable.")
            rows.append(dict(row))
        # Validate the whole selection before posting anything.
        for r in rows:
            if r["status"] == "APPROVED":
                continue
            has_split = c.execute(
                "SELECT 1 FROM transaction_splits WHERE user_id=? AND transaction_id=?",
                (uid, r["id"]),
            ).fetchone()
            if not has_split and (
                r["section"] == "Suspense"
                or (r.get("reclassified_key") or r["predicted_ledger_key"]).startswith(
                    "suspense"
                )
            ):
                raise ValueError(
                    "Resolve the selected unclassified items before posting."
                )
        moved = 0
        for r in rows:
            if r["status"] == "APPROVED":
                continue
            r["ledger_key"] = r.get("reclassified_key") or r["predicted_ledger_key"]
            cols = [
                "id",
                "user_id",
                "account_id",
                "txn_date",
                "narration",
                "amount",
                "txn_type",
                "ledger_key",
                "book",
                "section",
                "grp",
                "account",
                "counterparty",
                "attribution",
                "confidence",
                "source",
                "note",
                "created_at",
            ]
            c.execute(
                f"INSERT INTO ledger({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",
                [r.get(k, "") for k in cols],
            )
            post(c, r)
            c.execute(
                "UPDATE pending_ledger SET status='APPROVED' WHERE id=? AND user_id=?",
                (r["id"], uid),
            )
            event(
                c,
                uid,
                "transaction_posted",
                r["id"],
                after={"category": r["ledger_key"], "amount": r["amount"]},
            )
            moved += 1
        c.execute(
            """UPDATE raw_import_batches SET approved_rows=(SELECT count(*) FROM raw_import_rows r JOIN pending_ledger p ON p.id=r.moved_to_pending_id WHERE r.batch_id=raw_import_batches.id AND p.status='APPROVED') WHERE user_id=?""",
            (uid,),
        )
    return moved


def reconcile(db, uid):
    output = []
    with db._conn() as c:
        for batch in c.execute(
            "SELECT b.*,s.opening_source,s.closing_source FROM raw_import_batches b LEFT JOIN source_documents s ON s.batch_id=b.id WHERE b.user_id=? ORDER BY b.created_at DESC",
            (uid,),
        ).fetchall():
            rows = c.execute(
                "SELECT * FROM raw_import_rows WHERE batch_id=? AND user_id=? ORDER BY row_number",
                (batch["id"], uid),
            ).fetchall()
            net = sum(
                paise(r["normalized_amount"])
                * (1 if r["normalized_txn_type"].lower() == "credit" else -1)
                for r in rows
            )
            opening = batch["opening_balance"]
            closing = batch["closing_balance"]
            gap = None
            expected = None
            breaks = []
            if opening is not None:
                balance = paise(opening)
                for r in rows:
                    balance += paise(r["normalized_amount"]) * (
                        1 if r["normalized_txn_type"].lower() == "credit" else -1
                    )
                    if (
                        r["normalized_balance"] is not None
                        and paise(r["normalized_balance"]) != balance
                    ):
                        breaks.append(r["row_number"])
                expected = (paise(opening) + net) / 100
                if closing is not None:
                    gap = round(expected - closing, 2)
            output.append(
                {
                    "id": batch["id"],
                    "file_name": batch["source_file_name"],
                    "account_id": batch["account_id"],
                    "opening": opening,
                    "closing": closing,
                    "computed_closing": expected,
                    "difference": gap,
                    "row_breaks": breaks,
                    "opening_source": batch["opening_source"],
                    "closing_source": batch["closing_source"],
                    "status": "needs_input"
                    if gap is None
                    else (
                        "consistent_inferred"
                        if str(batch["opening_source"]).startswith("inferred")
                        or not batch["opening_source"]
                        else "matched"
                    )
                    if gap == 0 and not breaks
                    else "gap",
                    "rows": len(rows),
                    "pending": max(
                        0, (batch["staged_rows"] or 0) - (batch["approved_rows"] or 0)
                    ),
                    "duplicates": batch["duplicate_rows"] or 0,
                }
            )
    return output


def split_transaction(db, uid, rid, allocations, note="", posted=False):
    import hni_accounting_system as core

    if not isinstance(allocations, list) or not 2 <= len(allocations) <= 12:
        raise ValueError("Add 2–12 allocation lines.")
    prepared = []
    for a in allocations:
        if not isinstance(a, dict) or a.get("key") not in core.LEDGER_MAP:
            raise ValueError("Choose a known category for every line.")
        if a["key"] in ("exp_loan_emi", "exp_credit_card"):
            raise ValueError(
                "Allocate principal to its liability and interest to loan interest; card settlements reduce card liability."
            )
        if core.LEDGER_MAP[a["key"]][1] == "Suspense":
            raise ValueError("Resolve every split category before saving.")
        amount = paise(a.get("amount"))
        if amount <= 0:
            raise ValueError("Every split amount must be positive.")
        prepared.append((a["key"], amount))
    table = "ledger" if posted else "pending_ledger"
    with db._conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            f"SELECT * FROM {table} WHERE id=? AND user_id=?", (rid, uid)
        ).fetchone()
        if not row:
            raise LookupError("Transaction not found.")
        if not posted and row["status"] == "APPROVED":
            raise ValueError("This transaction is already posted.")
        if sum(a for _, a in prepared) != paise(row["amount"]):
            raise ValueError("Split amounts must exactly equal the original amount.")
        if row["txn_type"] == "debit" and any(
            core.LEDGER_MAP[k][1] == "Income" for k, _ in prepared
        ):
            raise ValueError("Debit allocations cannot be income.")
        c.execute(
            "DELETE FROM transaction_splits WHERE user_id=? AND transaction_id=?",
            (uid, rid),
        )
        c.executemany(
            "INSERT INTO transaction_splits VALUES(?,?,?,?,?)",
            [(str(uuid.uuid4()), uid, rid, k, a) for k, a in prepared],
        )
        c.execute(
            f"UPDATE {table} SET book='MIXED',section='Mixed',account=?,note=? WHERE id=? AND user_id=?",
            (f"Split across {len(prepared)} categories", note, rid, uid),
        )
        if posted:
            c.execute(
                "DELETE FROM journal_entries WHERE ledger_id=? AND user_id=?",
                (rid, uid),
            )
            post(
                c,
                dict(
                    c.execute(
                        "SELECT * FROM ledger WHERE id=? AND user_id=?", (rid, uid)
                    ).fetchone()
                ),
            )
        else:
            c.execute(
                "UPDATE pending_ledger SET status='RECLASSIFIED' WHERE id=? AND user_id=?",
                (rid, uid),
            )
        event(
            c,
            uid,
            "allocation_saved",
            rid,
            after={"allocations": allocations, "note": note},
        )
    return {"ok": True}
