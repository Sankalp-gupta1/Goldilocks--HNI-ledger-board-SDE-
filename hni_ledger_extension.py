#!/usr/bin/env python3
"""
HNI Ledger Extension Module
════════════════════════════════════════════════════════════════════════════════
Drop-in extension for hni_accounting_system.py

New Features:
  1. user_type column (INDIVIDUAL / ORGANISATION)
  2. User-type-specific financial statements
  3. Transaction review & reclassification workflow
  4. pending_ledger table with PENDING / APPROVED / RECLASSIFIED states
  5. Approval gate before final ledger write

INTEGRATION:
  In hni_accounting_system.py, add at the very bottom (before main()):

      from hni_ledger_extension import (
          extend_db, ExtendedDBStore, ReviewWorkflow,
          generate_income_statement, generate_balance_sheet_individual,
          generate_profit_loss, generate_reports_for_user,
          patch_handler_routes,
      )
      extend_db(_db)                     # migrate schema once
      _edb  = ExtendedDBStore(_db)       # wrap existing DBStore
      _wflow = ReviewWorkflow(_edb)       # review workflow engine

  Then in Handler.do_POST, replace direct _db.insert_txn() calls inside
  /upload and /classify with _wflow.store_pending_transactions().

  Add the new route handler by calling patch_handler_routes(Handler, _wflow, _edb)
  after class definition.

══════ompt ══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs
from flask import jsonify

_EXT_HERE = os.path.dirname(os.path.abspath(__file__))
_UPLOAD_STORE_DIR = os.path.join(_EXT_HERE, "uploaded_files")


def _safe_upload_ext(file_name: str) -> str:
    ext = os.path.splitext(os.path.basename(file_name or ""))[1].lower()
    if not re.match(r"^\.[a-z0-9]{1,12}$", ext or ""):
        return ".bin"
    return ext


def _uploaded_file_path(batch_id: str, file_name: str = "") -> str:
    return os.path.join(_UPLOAD_STORE_DIR, f"{os.path.basename(batch_id)}{_safe_upload_ext(file_name)}")


def _persist_uploaded_file(batch_id: str, file_name: str, file_bytes: bytes) -> Optional[str]:
    if not batch_id or not file_bytes:
        return None
    try:
        os.makedirs(_UPLOAD_STORE_DIR, exist_ok=True)
        fpath = _uploaded_file_path(batch_id, file_name)
        if not os.path.exists(fpath):
            with open(fpath, "wb") as f:
                f.write(file_bytes)
        return fpath
    except Exception as e:
        print(f"  [UPLOAD STORE] Could not persist uploaded file for batch={batch_id}: {e}", flush=True)
        return None


def _find_uploaded_file(batch_id: str, file_name: str = "") -> Optional[str]:
    preferred = _uploaded_file_path(batch_id, file_name)
    if os.path.exists(preferred):
        return preferred
    try:
        prefix = f"{os.path.basename(batch_id)}."
        for name in os.listdir(_UPLOAD_STORE_DIR):
            if name.startswith(prefix):
                candidate = os.path.join(_UPLOAD_STORE_DIR, name)
                if os.path.isfile(candidate):
                    return candidate
    except Exception:
        pass
    return None
from numpy import rint
import io

print("LIVE MARKER EXTENSION 2026-04-12 08:30", flush=True)

# ─── LEDGER_MAP and ATTRIBUTION are imported lazily at call time ─────────────
# Do NOT import them at module level — hni_accounting_system imports this
# module at module level too, creating a circular import deadlock.
# The _get_ledger_map() helper below handles the lazy fetch correctly.
LEDGER_MAP = {}   # local fallback for standalone testing only
ATTRIBUTION = {}  # local fallback for standalone testing only


def _get_ledger_map() -> Dict[str, Tuple[str, str, str, str]]:
    """
    Return the live LEDGER_MAP from the running main app.
    Works whether the server is running as hni_accounting_system or as __main__.
    Falls back to the local placeholder only for standalone testing.
    """
    try:
        from hni_accounting_system import LEDGER_MAP as live_map
        if isinstance(live_map, dict) and live_map:
            return live_map
    except Exception:
        pass

    try:
        main_mod = sys.modules.get("__main__")
        live_map = getattr(main_mod, "LEDGER_MAP", None)
        if isinstance(live_map, dict) and live_map:
            return live_map
    except Exception:
        pass

    return LEDGER_MAP


def _get_live_attribution() -> Dict[str, str]:
    """
    Return the live ATTRIBUTION map from the running main app.
    Works whether the server is running as hni_accounting_system or as __main__.
    """
    try:
        from hni_accounting_system import ATTRIBUTION as live_attr
        if isinstance(live_attr, dict) and live_attr:
            return live_attr
    except Exception:
        pass

    try:
        main_mod = sys.modules.get("__main__")
        live_attr = getattr(main_mod, "ATTRIBUTION", None)
        if isinstance(live_attr, dict) and live_attr:
            return live_attr
    except Exception:
        pass

    return ATTRIBUTION


def _get_cat_to_key() -> Dict[str, str]:
    """
    Return legacy alias mapping from hni_accounting_system if available.
    This lets the review dropdown / API accept both canonical keys and older
    alias values without throwing a validation error.
    """
    try:
        from hni_accounting_system import CAT_TO_KEY as live_map
        if isinstance(live_map, dict):
            return live_map
    except Exception:
        pass
    return {}


def _get_classify():
    """
    Lazy import of `classify` to avoid circular-import / not-yet-defined issues.
    hni_accounting_system imports this module at module level, so `classify`
    may not exist yet when the top-level `from … import classify` runs.
    Always call this helper instead of using a module-level `classify` variable.
    """
    try:
        from hni_accounting_system import classify as _c
        if _c is None:
            raise ImportError("classify is None")
        return _c
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            "classify() could not be imported from hni_accounting_system. "
            "Ensure the server is started via hni_accounting_system.py, not standalone."
        ) from exc


def _get_normalize_parsed_record():
    """Lazy import of normalize_parsed_record with a safe fallback."""
    try:
        from hni_accounting_system import normalize_parsed_record as _n
        return _n
    except (ImportError, AttributeError):
        def _fallback(raw: Dict[str, Any], source: str = "excel") -> Dict[str, Any]:
            narration = str(raw.get("description") or raw.get("narration") or "").strip()
            txn_type = str(raw.get("txn_type") or "debit").strip().lower()
            if txn_type not in ("debit", "credit"):
                txn_type = "debit"
            return {
                "txn_date": str(raw.get("txn_date") or "").strip(),
                "narration": narration,
                "description": narration,
                "amount": float(raw.get("amount", 0) or 0),
                "txn_type": txn_type,
                "source": source,
                "parser_notes": "normalize_parsed_record fallback used",
                "raw_debit": raw.get("raw_debit"),
                "raw_credit": raw.get("raw_credit"),
            }
        return _fallback


def _get_validate_classification():
    """Lazy import of validate_classification with a permissive fallback."""
    try:
        from hni_accounting_system import validate_classification as _v
        return _v
    except (ImportError, AttributeError):
        def _fallback(result: Dict[str, Any], forced_type: Optional[str]) -> Dict[str, Any]:
            out = dict(result)
            if forced_type in ("debit", "credit"):
                out["txn_type"] = forced_type
                if out.get("ledger_key") == "suspense_credit" and forced_type == "debit":
                    out["ledger_key"] = "suspense_debit"
                elif out.get("ledger_key") == "suspense_debit" and forced_type == "credit":
                    out["ledger_key"] = "suspense_credit"
            return out
        return _fallback


def _get_family_loan_checker():
    try:
        from hni_accounting_system import _is_family_loan_style_narration as _fn
        return _fn
    except (ImportError, AttributeError):
        def _fallback(_narration: str) -> bool:
            return False
        return _fallback


def _get_directional_key_sets() -> Tuple[set, set]:
    try:
        from hni_accounting_system import _DEBIT_ONLY_KEYS as _debit_only, _CREDIT_ONLY_KEYS as _credit_only
        return set(_debit_only), set(_credit_only)
    except (ImportError, AttributeError):
        return set(), set()


def _get_extract_counterparty():
    """Lazy import of the main counterparty extractor with a safe fallback."""
    try:
        from hni_accounting_system import _extract_counterparty as _cp
        return _cp
    except (ImportError, AttributeError):
        def _fallback(narration: str) -> str:
            text = str(narration or "").strip()
            if not text:
                return ""
            parts = [p.strip() for p in re.split(r'[/-]+', text) if p.strip()]
            for part in parts:
                if len(part) > 3 and not re.search(r'\d{6,}', part):
                    return part.title()
            return text[:48].title()
        return _fallback


_PROFILE_PROTECTED_KEYS = {
    "income_salary", "income_dividend", "income_interest", "income_capital_gains",
    "exp_tax", "exp_utilities", "trading_funds_added", "trading_payout",
    "exp_broker_charges", "exp_broker_dp", "exp_broker_interest",
    "asset_investment_mf", "asset_investment_equity", "asset_investment_fd",
    "asset_investment_ppf", "asset_investment_nps", "asset_land_cwip",
    "asset_mf_redemption", "asset_fd_maturity", "asset_equity_sale_proceeds",
}
_PROFILE_OVERRIDABLE_KEYS = {
    "income_other", "income_professional", "income_inward_payment",
    "exp_misc", "exp_personal_transfer", "suspense_credit", "suspense_debit",
    "liability_loan_outstanding", "asset_own_transfer_in",
}

_PROFILE_APPROVED_SAFE_KEYS = {
    "suspense_credit", "suspense_debit", "income_other", "exp_misc",
    "exp_personal_transfer", "liability_loan_outstanding", "asset_own_transfer_in",
}


def _is_directionally_compatible_key(key: str, txn_type: str) -> bool:
    debit_only, credit_only = _get_directional_key_sets()
    if txn_type == "debit" and key in credit_only:
        return False
    if txn_type == "credit" and key in debit_only:
        return False
    return True


def _allow_profile_override(current_key: str, new_key: str) -> bool:
    if not new_key:
        return False
    if current_key == new_key:
        return True
    if new_key in _PROFILE_PROTECTED_KEYS:
        return True
    if current_key in _PROFILE_PROTECTED_KEYS:
        return False
    return current_key in _PROFILE_OVERRIDABLE_KEYS or not current_key


_PROFILE_BROKER_MARKER_RE = re.compile(
    r'\b(?:'
    r'ZERODHA|KITE|UPSTOX|5PAISA|GROWW|IIFL|VENTURA|LDK|'
    r'SHARES?|SECURITIES|NSE|BSE|CLEARING|SETTLEMENT|BROKER|'
    r'DEMAT|DP|TRADING\s+ACCOUNT|HOLDING\s+ACCOUNT'
    r')\b|L\s*D\s*K\s+SHARES?',
    re.I,
)

_PROFILE_PROTECTED_RULE_CONFLICT_RE = re.compile(
    r'\b(?:'
    r'SALARY|PAYROLL|TPT-SAL|DIV|DIVIDEND|INTDIV|FINDIV|FINALDIV|SPLDIV|ANNUALDIV|'
    r'INTEREST|INT\.?PD|PPF|PUBLIC\s+PROVIDENT\s+FUND|NPS|NATIONAL\s+PENSION|'
    r'NSDL|CRA|POP|POP[-\s]*SP|CBDT|ADVANCE\s+TAX|TDS\s+PAYMENT|GST\s+CHALLAN|'
    r'SELF\s+ASSESSMENT\s+TAX|PLOT|LAND|YEIDA'
    r')\b',
    re.I,
)


def _has_profile_broker_marker(text: str) -> bool:
    return bool(_PROFILE_BROKER_MARKER_RE.search(str(text or "")))


def _append_profile_note(result: Dict[str, Any], marker: str) -> Dict[str, Any]:
    if not marker:
        return result
    note = result.get("note", "") or ""
    if marker not in note:
        result = dict(result)
        result["note"] = (note + " | " + marker).strip(" | ")
    return result


def _apply_ledger_metadata(result: Dict[str, Any]) -> Dict[str, Any]:
    """Refresh book/section/grp/account/attribution from the current ledger key."""
    out = dict(result)
    ledger_map = _get_ledger_map()
    key = out.get("ledger_key") or "suspense_debit"
    if key not in ledger_map:
        key = "suspense_debit"
        out["ledger_key"] = key
    book, section, grp, account = ledger_map[key]
    out["book"] = book
    out["section"] = section
    out["grp"] = grp
    out["group"] = grp
    out["account"] = account
    try:
        from hni_accounting_system import ATTRIBUTION as _ATTR
        out["attribution"] = _ATTR.get(key, out.get("attribution", ""))
    except (ImportError, AttributeError):
        out["attribution"] = ATTRIBUTION.get(key, out.get("attribution", ""))
    return out


def _canonicalize_result(
    result: Dict[str, Any],
    forced_type: Optional[str],
    *,
    parser_txn_type: str = "",
    derived_txn_type: str = "",
    parser_notes: str = "",
) -> Dict[str, Any]:
    """
    Run the final classification validator and add compact audit diagnostics.
    This is intentionally lightweight so it does not affect the UI.
    """
    validate_classification = _get_validate_classification()
    original_key = result.get("ledger_key")
    original_type = result.get("txn_type")
    fixed = validate_classification(dict(result), forced_type if forced_type in ("debit", "credit") else None)
    fixed = _apply_ledger_metadata(fixed)

    diagnostics = []
    if parser_txn_type:
        diagnostics.append(f"parser_txn_type={parser_txn_type}")
    if derived_txn_type:
        diagnostics.append(f"derived_txn_type={derived_txn_type}")
    if result.get("source"):
        diagnostics.append(f"classifier_source={result.get('source')}")
    if result.get("confidence") is not None:
        try:
            diagnostics.append(f"confidence={float(result.get('confidence', 0)):.2f}")
        except Exception:
            pass
    if original_key:
        diagnostics.append(f"initial_ledger_key={original_key}")
    if fixed.get("ledger_key"):
        diagnostics.append(f"final_ledger_key={fixed.get('ledger_key')}")
    if original_key != fixed.get("ledger_key") or (forced_type in ("debit", "credit") and original_type != fixed.get("txn_type")):
        diagnostics.append("conflict_resolved=1")
    else:
        diagnostics.append("conflict_resolved=0")
    if parser_notes:
        diagnostics.append(f"parser_notes={parser_notes}")

    existing_note = (fixed.get("note") or "").strip()
    diag_note = " | ".join(diagnostics)
    fixed["note"] = (existing_note + " | " + diag_note).strip(" | ")
    if forced_type in ("debit", "credit"):
        fixed["txn_type"] = forced_type if fixed.get("txn_type") not in ("debit", "credit") else fixed.get("txn_type")
    return fixed


def _get_current_user_id():
    try:
        from hni_accounting_system import _current_user_id as uid
        return uid
    except Exception:
        return None


def _get_live_extended_db():
    try:
        from hni_accounting_system import _edb as live_edb
        return live_edb
    except Exception:
        main_mod = sys.modules.get("__main__")
        return getattr(main_mod, "_edb", None)


def validate_min_profile_payload(payload: Dict[str, Any]) -> Tuple[bool, str]:
    payload = payload or {}
    entity_type = str(payload.get("entity_type") or "").strip().upper()
    legal_name = str(payload.get("legal_name") or "").strip()
    huf_name = str(payload.get("huf_name") or "").strip()
    employer_name = str(payload.get("employer_name") or "").strip()
    is_salaried = bool(payload.get("is_salaried"))

    if not entity_type:
        return False, "Entity type is required"
    if entity_type not in {"INDIVIDUAL", "NRI", "HUF"}:
        return False, "Entity type must be one of INDIVIDUAL, NRI, HUF"
    if not legal_name:
        return False, "Legal name is required"
    if entity_type == "HUF" and not huf_name:
        return False, "HUF name is required when Entity Type is HUF"
    if is_salaried and not employer_name:
        return False, "Employer name is required when Salaried is selected"
    return True, ""


def is_min_profile_complete(user_id: str) -> Tuple[bool, str]:
    if not str(user_id or "").strip():
        return False, "No active user."
    edb = _get_live_extended_db()
    if edb is None:
        return False, "Profile store is unavailable"
    profile = edb.get_user_profile(user_id) or {}
    return validate_min_profile_payload(profile)


def _get_custom_rule_match():
    try:
        from hni_accounting_system import match_custom_rule as fn
        return fn
    except Exception:
        return None


def _learn_from_reclassification(narration: str, ledger_key: str) -> None:
    """
    Persist a user correction into the ML correction store.
    Best effort only — never break the reclassify flow if ML is unavailable.
    """
    try:
        from hni_accounting_system import get_ml
        ml = get_ml()
        if ml and hasattr(ml, "add_correction"):
            narr = str(narration or "").strip()
            key = str(ledger_key or "").strip()
            if narr and key:
                ml.add_correction(narr, key)
    except Exception as e:
        print(f"  [LEARN] skipped: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# 1.  SCHEMA MIGRATION
# ══════════════════════════════════════════════════════════════════════════════

def extend_db(db_store) -> None:
    """
    Run migrations on the existing DBStore instance.
    Safe to call multiple times (fully idempotent).

    Each statement is executed individually so that:
      • A "duplicate column" error on ALTER TABLE is silently skipped.
      • CREATE TABLE / CREATE INDEX use IF NOT EXISTS — never raise.
      • No string-splitting on ";" — avoids bugs with multi-line DDL
        that contains inline comments or semicolons inside string literals.
    """
    conn: sqlite3.Connection = sqlite3.connect(db_store.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    def _try(sql: str) -> None:
        """Execute one DDL statement; swallow expected idempotency errors."""
        try:
            cur.execute(sql)
            conn.commit()
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "duplicate column" in msg or "already exists" in msg:
                return  # idempotent — column or object already there
            raise

    # ── 1. Extend users table ─────────────────────────────────────────────
    _try("ALTER TABLE users ADD COLUMN user_type TEXT DEFAULT 'INDIVIDUAL'")
    _try("ALTER TABLE users ADD COLUMN phone TEXT")
    _try("ALTER TABLE users ADD COLUMN opening_balance REAL DEFAULT 0")
    _try("ALTER TABLE users ADD COLUMN password_hash TEXT")
    _try("ALTER TABLE users ADD COLUMN reset_token TEXT")
    _try("ALTER TABLE users ADD COLUMN reset_token_expires_at TEXT")
    # ── 2. Create pending_ledger (no inline comments — pure DDL) ─────────
    _try(
        """
        CREATE TABLE IF NOT EXISTS pending_ledger (
            id                   TEXT PRIMARY KEY,
            user_id              TEXT NOT NULL,
            account_id           TEXT    DEFAULT 'main',
            txn_date             TEXT,
            narration            TEXT,
            amount               REAL    DEFAULT 0,
            txn_type             TEXT,
            predicted_ledger_key TEXT,
            confidence           REAL    DEFAULT 0,
            book                 TEXT,
            section              TEXT,
            grp                  TEXT,
            account              TEXT,
            counterparty         TEXT    DEFAULT '',
            attribution          TEXT    DEFAULT '',
            source               TEXT    DEFAULT '',
            cluster_id           INTEGER DEFAULT -1,
            is_anomaly           INTEGER DEFAULT 0,
            note                 TEXT    DEFAULT '',
            status               TEXT    DEFAULT 'PENDING',
            reclassified_key     TEXT,
            created_at           TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    _try("ALTER TABLE pending_ledger ADD COLUMN account_id TEXT DEFAULT 'main'")
    _try("ALTER TABLE ledger ADD COLUMN stock_name TEXT")
    _try("ALTER TABLE ledger ADD COLUMN trade_type TEXT")
    _try("ALTER TABLE ledger ADD COLUMN trade_price REAL DEFAULT 0")
    _try("ALTER TABLE ledger ADD COLUMN trade_qty REAL DEFAULT 0")
    _try("ALTER TABLE ledger ADD COLUMN trade_tds REAL DEFAULT 0")

    # ── 3. Pipeline Tables (Raw, Manual, Notes, Audit) ───────────────────
    _try(
        """
        CREATE TABLE IF NOT EXISTS raw_import_batches (
            id                   TEXT PRIMARY KEY,
            user_id              TEXT NOT NULL,
            account_id           TEXT    DEFAULT 'main',
            statement_type       TEXT    DEFAULT 'BANK',
            source_file_name     TEXT,
            source_file_hash     TEXT,
            file_size_bytes      INTEGER DEFAULT 0,
            import_status        TEXT    DEFAULT 'PROCESSED',
            duplicate_batch_flag INTEGER DEFAULT 0,
            total_rows           INTEGER DEFAULT 0,
            parsed_rows          INTEGER DEFAULT 0,
            valid_rows           INTEGER DEFAULT 0,
            invalid_rows         INTEGER DEFAULT 0,
            duplicate_rows       INTEGER DEFAULT 0,
            staged_rows          INTEGER DEFAULT 0,
            approved_rows        INTEGER DEFAULT 0,
            opening_balance      REAL,
            closing_balance      REAL,
            statement_from_date  TEXT,
            statement_to_date    TEXT,
            created_at           TEXT,
            updated_at           TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    _try("CREATE UNIQUE INDEX IF NOT EXISTS ux_raw_batches ON raw_import_batches(user_id, account_id, source_file_hash)")

    _try(
        """
        CREATE TABLE IF NOT EXISTS raw_import_rows (
            id                   TEXT PRIMARY KEY,
            batch_id             TEXT NOT NULL,
            user_id              TEXT NOT NULL,
            account_id           TEXT    DEFAULT 'main',
            statement_type       TEXT    DEFAULT 'BANK',
            row_number           INTEGER,
            source_file_name     TEXT,
            source_file_hash     TEXT,
            raw_date             TEXT,
            raw_description      TEXT,
            raw_debit            TEXT,
            raw_credit           TEXT,
            raw_amount           TEXT,
            raw_balance          TEXT,
            raw_txn_type         TEXT,
            raw_json             TEXT,
            normalized_date      TEXT,
            normalized_narration TEXT,
            normalized_amount    REAL,
            normalized_balance   REAL,
            normalized_txn_type  TEXT,
            parse_status         TEXT,
            validation_status    TEXT,
            validation_errors    TEXT,
            junk_flag            INTEGER DEFAULT 0,
            review_required_flag INTEGER DEFAULT 0,
            fingerprint          TEXT,
            duplicate_status     TEXT    DEFAULT 'NEW',
            duplicate_of_row_id  TEXT,
            moved_to_pending_flag INTEGER DEFAULT 0,
            moved_to_pending_id  TEXT,
            classification_status TEXT,
            created_at           TEXT,
            updated_at           TEXT,
            FOREIGN KEY(batch_id) REFERENCES raw_import_batches(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    # Dedupe index for raw rows:
    _try("CREATE INDEX IF NOT EXISTS idx_raw_rows_fingerprint ON raw_import_rows(fingerprint)")
    _try("CREATE INDEX IF NOT EXISTS idx_raw_rows_batch ON raw_import_rows(batch_id)")
    _try("CREATE INDEX IF NOT EXISTS idx_raw_rows_val ON raw_import_rows(validation_status)")

    _try(
        """
        CREATE TABLE IF NOT EXISTS manual_entries (
            id                   TEXT PRIMARY KEY,
            user_id              TEXT NOT NULL,
            account_id           TEXT    DEFAULT 'main',
            txn_date             TEXT,
            narration            TEXT,
            amount               REAL    DEFAULT 0,
            txn_type             TEXT,
            balance              REAL,
            desired_ledger_key   TEXT,
            source               TEXT    DEFAULT 'MANUAL',
            entered_by           TEXT,
            note                 TEXT,
            fingerprint          TEXT,
            duplicate_status     TEXT    DEFAULT 'NEW',
            approval_status      TEXT    DEFAULT 'PENDING',
            linked_pending_id    TEXT,
            linked_ledger_id     TEXT,
            created_at           TEXT,
            updated_at           TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    _try("CREATE INDEX IF NOT EXISTS idx_manual_fp ON manual_entries(fingerprint)")

    _try(
        """
        CREATE TABLE IF NOT EXISTS review_notes (
            id                   TEXT PRIMARY KEY,
            user_id              TEXT NOT NULL,
            related_batch_id     TEXT,
            related_raw_row_id   TEXT,
            related_pending_id   TEXT,
            related_ledger_id    TEXT,
            message_text         TEXT,
            message_type         TEXT    DEFAULT 'USER',
            created_by           TEXT,
            status               TEXT    DEFAULT 'ACTIVE',
            created_at           TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    _try(
        """
        CREATE TABLE IF NOT EXISTS import_audit_log (
            id                   TEXT PRIMARY KEY,
            batch_id             TEXT,
            action               TEXT,
            actor                TEXT,
            details_json         TEXT,
            created_at           TEXT
        )
        """
    )

    # ── 4. Indexes ────────────────────────────────────────────────────────
    _try("CREATE INDEX IF NOT EXISTS idx_pending_uid    ON pending_ledger(user_id)")
    _try("CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_ledger(status)")
    # Keep old unique indexes for backwards compatibility
    _try("CREATE UNIQUE INDEX IF NOT EXISTS ux_ledger_dedupe ON ledger(user_id, account_id, txn_date, narration, amount, txn_type, counterparty, source)")
    _try("CREATE UNIQUE INDEX IF NOT EXISTS ux_pending_dedupe ON pending_ledger(user_id, txn_date, narration, amount, txn_type, counterparty, source)")

    # ── 4. Trading ledger (broker account — 5paisa / Zerodha etc.) ───────────
    _try(
        """
        CREATE TABLE IF NOT EXISTS trading_ledger (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            account_id  TEXT    DEFAULT '5paisa',
            txn_date    TEXT,
            segment     TEXT,
            particular  TEXT,
            description TEXT,
            debit       REAL    DEFAULT 0,
            credit      REAL    DEFAULT 0,
            balance     REAL    DEFAULT 0,
            created_at  TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    _try("CREATE INDEX IF NOT EXISTS idx_tl_user      ON trading_ledger(user_id)")
    _try("CREATE INDEX IF NOT EXISTS idx_tl_account   ON trading_ledger(user_id, account_id)")
    _try("CREATE INDEX IF NOT EXISTS idx_tl_date      ON trading_ledger(txn_date)")
    _try("CREATE UNIQUE INDEX IF NOT EXISTS ux_tl_dedupe ON trading_ledger(user_id, account_id, txn_date, particular, description, debit, credit)")

    _try(
        """
        CREATE TABLE IF NOT EXISTS custom_classifier_rules (
            id            TEXT PRIMARY KEY,
            user_id       TEXT NOT NULL,
            pattern       TEXT NOT NULL,
            match_mode    TEXT DEFAULT 'contains',   -- contains | regex | exact
            txn_type      TEXT DEFAULT '',           -- debit | credit | ''
            ledger_key    TEXT NOT NULL,
            priority      INTEGER DEFAULT 100,
            is_active     INTEGER DEFAULT 1,
            created_at    TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    _try("CREATE INDEX IF NOT EXISTS idx_ccr_user_active ON custom_classifier_rules(user_id, is_active, priority)")
    _try(
        """
        CREATE TABLE IF NOT EXISTS user_profile_facts (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            entity_type TEXT DEFAULT '',
            legal_name TEXT DEFAULT '',
            huf_name TEXT DEFAULT '',
            dob TEXT DEFAULT '',
            is_nri INTEGER DEFAULT 0,
            has_family_transactions INTEGER DEFAULT 0,
            is_salaried INTEGER DEFAULT 0,
            employer_name TEXT DEFAULT '',
            has_consultancy INTEGER DEFAULT 0,
            has_trading INTEGER DEFAULT 0,
            has_multiple_bank_accounts INTEGER DEFAULT 0,
            has_credit_cards INTEGER DEFAULT 0,
            has_rental_income INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    _try("CREATE INDEX IF NOT EXISTS idx_user_profile_facts_user_id ON user_profile_facts(user_id)")
    _try(
        """
        CREATE TABLE IF NOT EXISTS user_known_counterparties (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            normalized_name TEXT DEFAULT '',
            party_type TEXT DEFAULT '',
            relationship TEXT DEFAULT '',
            default_ledger_key TEXT DEFAULT '',
            txn_direction_hint TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    _try("CREATE INDEX IF NOT EXISTS idx_user_known_counterparties_user_id ON user_known_counterparties(user_id)")
    _try(
        """
        CREATE TABLE IF NOT EXISTS user_known_accounts (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            account_label TEXT DEFAULT '',
            institution_name TEXT DEFAULT '',
            account_mask TEXT DEFAULT '',
            account_type TEXT DEFAULT '',
            ownership_type TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    _try("CREATE INDEX IF NOT EXISTS idx_user_known_accounts_user_id ON user_known_accounts(user_id)")

    # ── 5. Self-heal old duplicate ledger rows created by duplicate-batch restaging ──
    try:
        dup_groups = cur.execute(
            """SELECT user_id, account_id, txn_date, narration, amount, txn_type, COUNT(*) AS cnt
                   FROM ledger
                  GROUP BY user_id, account_id, txn_date, narration, amount, txn_type
                 HAVING COUNT(*) > 1"""
        ).fetchall()
        removed = 0
        for g in dup_groups:
            rows = cur.execute(
                """SELECT id, source, created_at,
                          CASE WHEN LOWER(COALESCE(source,''))='restaged' THEN 1 ELSE 0 END AS restaged_rank,
                          CASE WHEN LOWER(COALESCE(source,'')) IN ('excel','upload','manual','review') THEN 0 ELSE 1 END AS source_rank
                     FROM ledger
                    WHERE user_id=? AND account_id=? AND txn_date=? AND narration=? AND amount=? AND txn_type=?
                 ORDER BY source_rank, restaged_rank, created_at, id""",
                (g['user_id'], g['account_id'], g['txn_date'], g['narration'], g['amount'], g['txn_type'])
            ).fetchall()
            keep_id = rows[0]['id']
            delete_ids = [r['id'] for r in rows[1:]]
            if delete_ids:
                cur.executemany("DELETE FROM ledger WHERE id=?", [(x,) for x in delete_ids])
                removed += len(delete_ids)
        if removed:
            conn.commit()
            print(f"  ✅ HNI Extension: removed {removed} duplicate ledger row(s).")
    except Exception as e:
        print(f"  ⚠️ HNI Extension duplicate cleanup skipped: {e}")

    conn.close()
    print("  ✅ HNI Extension: schema migration complete.")


# ══════════════════════════════════════════════════════════════════════════════
# 2.  EXTENDED DB STORE
# ══════════════════════════════════════════════════════════════════════════════

class ExtendedDBStore:
    """
    Wraps the existing DBStore and adds:
      • user_type-aware create/get
      • pending_ledger CRUD
      • approval + promotion to ledger
    """

    def __init__(self, db_store):
        self._db = db_store          # original DBStore instance

    # ── Delegate original API unchanged ─────────────────────────────────────

    def insert_txn(self, row: Dict[str, Any]) -> None:
        self._db.insert_txn(row)

    def ledger_id_exists(self, txn_id: str) -> bool:
        with self._conn() as c:
            r = c.execute("SELECT 1 FROM ledger WHERE id=? LIMIT 1", (txn_id,)).fetchone()
            return bool(r)

    def ledger_business_duplicate_exists(
        self,
        user_id: str,
        account_id: str,
        txn_date: str,
        narration: str,
        amount: float,
        txn_type: str,
    ) -> bool:
        """
        Detect already-posted ledger rows for the same real-world transaction.
        This intentionally ignores source / counterparty / confidence because
        duplicate-batch restaging can recreate the same transaction with a
        different source label (e.g. upload vs restaged).
        """
        with self._conn() as c:
            r = c.execute(
                """SELECT 1 FROM ledger
                   WHERE user_id=? AND account_id=? AND txn_date=?
                     AND narration=? AND ABS(amount - ?) < 0.0001
                     AND LOWER(COALESCE(txn_type,'')) = LOWER(COALESCE(?,''))
                   LIMIT 1""",
                (user_id, account_id or 'main', txn_date or '', narration or '', float(amount or 0), txn_type or ''),
            ).fetchone()
            return bool(r)

    def get_txns(self, user_id: str, limit: int = 1000) -> List[Dict]:
        return self._db.get_txns(user_id, limit)

    def get_ledger_summary(self, user_id: str) -> List[Dict]:
        return self._db.get_ledger_summary(user_id)

    def get_users(self) -> List[Dict]:
        return self._db.get_users()

    def find_user(self, **kwargs) -> Optional[Dict]:
        return self._db.find_user(**kwargs)

    def set_reset_token(self, user_id: str, token: str, expires_at: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE users SET reset_token=?, reset_token_expires_at=? WHERE id=?",
                (token, expires_at, user_id)
            )

    def find_user_for_reset(self, email: str = "", phone: str = "") -> Optional[Dict]:
        with self._conn() as c:
            if email:
                row = c.execute(
                    "SELECT * FROM users WHERE LOWER(email)=LOWER(?) ORDER BY created_at DESC LIMIT 1",
                    (email,)
                ).fetchone()
                if row:
                    return dict(row)
            if phone:
                row = c.execute(
                    "SELECT * FROM users WHERE phone=? ORDER BY created_at DESC LIMIT 1",
                    (phone,)
                ).fetchone()
                if row:
                    return dict(row)
        return None

    def reset_password_with_token(self, token: str, new_password_hash: str) -> bool:
        now = datetime.utcnow().isoformat()
        with self._conn() as c:
            row = c.execute(
                """SELECT id FROM users
                   WHERE reset_token=? AND reset_token_expires_at IS NOT NULL
                     AND reset_token_expires_at >= ?
                   LIMIT 1""",
                (token, now)
            ).fetchone()
            if not row:
                return False

            c.execute(
                """UPDATE users
                   SET password_hash=?,
                       reset_token=NULL,
                       reset_token_expires_at=NULL
                   WHERE id=?""",
                (new_password_hash, row["id"])
            )
            return True

    # ── Extended user management ─────────────────────────────────────────────

    def create_user(
        self,
        uid: str,
        name: str,
        email: str = "",
        phone: str = "",
        user_type: str = "INDIVIDUAL",
        password_hash: str = "",
    ) -> Dict[str, Any]:
        """
        Create a new user with user_type and password hash.
        """
        if user_type not in ("INDIVIDUAL", "ORGANISATION"):
            raise ValueError(f"Invalid user_type '{user_type}'. Must be INDIVIDUAL or ORGANISATION.")
        now = datetime.utcnow().isoformat()
        with self._conn() as c:
            c.execute(
                """INSERT INTO users(id, name, email, phone, user_type, password_hash, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name,
                     user_type=excluded.user_type""",
                (uid, name, email, phone, user_type, password_hash, now),
            )
        return self.get_user(uid)

    def get_user(self, user_id: str) -> Optional[Dict]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM users WHERE id=? LIMIT 1", (user_id,)
            ).fetchone()
            return dict(row) if row else None

    def set_opening_balance(self, user_id: str, amount: float) -> None:
        """Store the opening/brought-forward balance for a user.
        Uses an explicit commit so the write is durable regardless of
        the connection's isolation_level setting.
        """
        c = self._conn()
        try:
            c.execute(
                "UPDATE users SET opening_balance=? WHERE id=?",
                (float(amount), user_id)
            )
            c.commit()
        finally:
            c.close()

    def get_user_profile(self, user_id: str) -> Optional[Dict]:
        with self._conn() as c:
            row = c.execute(
                """SELECT * FROM user_profile_facts
                   WHERE user_id=?
                   ORDER BY updated_at DESC, created_at DESC, id DESC
                   LIMIT 1""",
                (user_id,),
            ).fetchone()
            return dict(row) if row else None

    def ensure_user_profile_exists(self, user_id: str) -> Dict:
        existing = self.get_user_profile(user_id)
        if existing:
            return existing
        now = datetime.utcnow().isoformat()
        row = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "entity_type": "",
            "legal_name": "",
            "huf_name": "",
            "dob": "",
            "is_nri": 0,
            "has_family_transactions": 0,
            "is_salaried": 0,
            "employer_name": "",
            "has_consultancy": 0,
            "has_trading": 0,
            "has_multiple_bank_accounts": 0,
            "has_credit_cards": 0,
            "has_rental_income": 0,
            "created_at": now,
            "updated_at": now,
        }
        with self._conn() as c:
            c.execute(
                """INSERT INTO user_profile_facts(
                       id, user_id, entity_type, legal_name, huf_name, dob, is_nri,
                       has_family_transactions, is_salaried, employer_name, has_consultancy,
                       has_trading, has_multiple_bank_accounts, has_credit_cards,
                       has_rental_income, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["id"], row["user_id"], row["entity_type"], row["legal_name"], row["huf_name"], row["dob"], row["is_nri"],
                    row["has_family_transactions"], row["is_salaried"], row["employer_name"], row["has_consultancy"],
                    row["has_trading"], row["has_multiple_bank_accounts"], row["has_credit_cards"],
                    row["has_rental_income"], row["created_at"], row["updated_at"],
                ),
            )
        return self.get_user_profile(user_id) or row

    def upsert_user_profile(self, user_id: str, payload: Dict[str, Any]) -> Dict:
        now = datetime.utcnow().isoformat()
        existing = self.get_user_profile(user_id) or {}
        profile_id = existing.get("id") or str(uuid.uuid4())
        row = {
            "entity_type": str(payload.get("entity_type", existing.get("entity_type", "")) or ""),
            "legal_name": str(payload.get("legal_name", existing.get("legal_name", "")) or ""),
            "huf_name": str(payload.get("huf_name", existing.get("huf_name", "")) or ""),
            "dob": str(payload.get("dob", existing.get("dob", "")) or ""),
            "is_nri": int(bool(payload.get("is_nri", existing.get("is_nri", 0)))),
            "has_family_transactions": int(bool(payload.get("has_family_transactions", existing.get("has_family_transactions", 0)))),
            "is_salaried": int(bool(payload.get("is_salaried", existing.get("is_salaried", 0)))),
            "employer_name": str(payload.get("employer_name", existing.get("employer_name", "")) or ""),
            "has_consultancy": int(bool(payload.get("has_consultancy", existing.get("has_consultancy", 0)))),
            "has_trading": int(bool(payload.get("has_trading", existing.get("has_trading", 0)))),
            "has_multiple_bank_accounts": int(bool(payload.get("has_multiple_bank_accounts", existing.get("has_multiple_bank_accounts", 0)))),
            "has_credit_cards": int(bool(payload.get("has_credit_cards", existing.get("has_credit_cards", 0)))),
            "has_rental_income": int(bool(payload.get("has_rental_income", existing.get("has_rental_income", 0)))),
        }
        ok, reason = validate_min_profile_payload(row)
        if not ok:
            raise ValueError(reason)
        with self._conn() as c:
            c.execute("DELETE FROM user_profile_facts WHERE user_id=?", (user_id,))
            c.execute(
                """INSERT INTO user_profile_facts(
                       id, user_id, entity_type, legal_name, huf_name, dob, is_nri,
                       has_family_transactions, is_salaried, employer_name, has_consultancy,
                       has_trading, has_multiple_bank_accounts, has_credit_cards,
                       has_rental_income, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    profile_id, user_id, row["entity_type"], row["legal_name"], row["huf_name"], row["dob"], row["is_nri"],
                    row["has_family_transactions"], row["is_salaried"], row["employer_name"], row["has_consultancy"],
                    row["has_trading"], row["has_multiple_bank_accounts"], row["has_credit_cards"],
                    row["has_rental_income"], existing.get("created_at") or now, now,
                ),
            )
        return self.get_user_profile(user_id) or {}

    def get_known_counterparties(self, user_id: str, active_only: bool = True) -> List[Dict]:
        sql = """SELECT * FROM user_known_counterparties
                 WHERE user_id=?"""
        params: List[Any] = [user_id]
        if active_only:
            sql += " AND COALESCE(is_active, 1)=1"
        sql += " ORDER BY display_name COLLATE NOCASE, created_at, id"
        with self._conn() as c:
            rows = c.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def upsert_known_counterparties(self, user_id: str, rows: List[Dict[str, Any]]) -> List[Dict]:
        now = datetime.utcnow().isoformat()
        with self._conn() as c:
            c.execute("DELETE FROM user_known_counterparties WHERE user_id=?", (user_id,))
            for row in rows or []:
                display_name = str(row.get("display_name", "") or "").strip()
                if not display_name:
                    continue
                row_id = str(row.get("id") or uuid.uuid4())
                normalized_name = _normalize_known_counterparty_name(
                    row.get("normalized_name") or display_name
                )
                c.execute(
                    """INSERT INTO user_known_counterparties(
                           id, user_id, display_name, normalized_name, party_type, relationship,
                           default_ledger_key, txn_direction_hint, notes, is_active, created_at, updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row_id,
                        user_id,
                        display_name,
                        normalized_name,
                        str(row.get("party_type", "") or ""),
                        str(row.get("relationship", "") or ""),
                        str(row.get("default_ledger_key", "") or ""),
                        str(row.get("txn_direction_hint", "") or ""),
                        str(row.get("notes", "") or ""),
                        int(bool(row.get("is_active", 1))),
                        now,
                        now,
                    ),
                )
        return self.get_known_counterparties(user_id, active_only=False)

    def get_known_accounts(self, user_id: str, active_only: bool = True) -> List[Dict]:
        sql = """SELECT * FROM user_known_accounts
                 WHERE user_id=?"""
        params: List[Any] = [user_id]
        if active_only:
            sql += " AND COALESCE(is_active, 1)=1"
        sql += " ORDER BY account_label COLLATE NOCASE, institution_name COLLATE NOCASE, created_at, id"
        with self._conn() as c:
            rows = c.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def upsert_known_accounts(self, user_id: str, rows: List[Dict[str, Any]]) -> List[Dict]:
        now = datetime.utcnow().isoformat()
        with self._conn() as c:
            c.execute("DELETE FROM user_known_accounts WHERE user_id=?", (user_id,))
            for row in rows or []:
                row_id = str(row.get("id") or uuid.uuid4())
                account_label = str(row.get("account_label", "") or "")
                institution_name = str(row.get("institution_name", "") or "")
                account_mask = str(row.get("account_mask", "") or "")
                if not any([account_label.strip(), institution_name.strip(), account_mask.strip()]):
                    continue
                c.execute(
                    """INSERT INTO user_known_accounts(
                           id, user_id, account_label, institution_name, account_mask,
                           account_type, ownership_type, is_active, created_at, updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row_id,
                        user_id,
                        account_label,
                        institution_name,
                        account_mask,
                        str(row.get("account_type", "") or ""),
                        str(row.get("ownership_type", "") or ""),
                        int(bool(row.get("is_active", 1))),
                        now,
                        now,
                    ),
                )
        return self.get_known_accounts(user_id, active_only=False)

    # ── pending_ledger CRUD ──────────────────────────────────────────────────

    def store_pending_transaction(self, row: Dict[str, Any]) -> str:
        """Insert a classified transaction into pending_ledger. Returns id."""
        pid = row.get("id") or str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        with self._conn() as c:
            c.execute(
                """INSERT OR IGNORE INTO pending_ledger(
                       id, user_id, account_id, txn_date, narration, amount, txn_type,
                       predicted_ledger_key, confidence, book, section, grp, account,
                       counterparty, attribution, source, cluster_id, is_anomaly,
                       note, status, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    pid,
                    row["user_id"],
                    row.get("account_id", "main"),
                    row.get("txn_date", ""),
                    row.get("narration", ""),
                    float(row.get("amount", 0)),
                    row.get("txn_type", "debit"),
                    row.get("ledger_key", "suspense_debit"),
                    float(row.get("confidence", 0)),
                    row.get("book", "SUSPENSE"),
                    row.get("section", "Unclassified"),
                    row.get("group", row.get("grp", "Unclassified")),
                    row.get("account", "Requires Review"),
                    row.get("counterparty", ""),
                    row.get("attribution", ""),
                    row.get("source", ""),
                    int(row.get("cluster_id", -1)),
                    int(row.get("is_anomaly", False)),
                    row.get("note", ""),
                    "PENDING",
                    now,
                ),
            )
        return pid

    def get_pending_transactions(
        self, user_id: str, status: Optional[str] = None
    ) -> List[Dict]:
        """Return pending transactions for a user, optionally filtered by status."""
        with self._conn() as c:
            if status:
                rows = c.execute(
                    """SELECT * FROM pending_ledger
                       WHERE user_id=? AND status=?
                       ORDER BY txn_date, created_at""",
                    (user_id, status),
                ).fetchall()
            else:
                rows = c.execute(
                    """SELECT * FROM pending_ledger
                       WHERE user_id=? AND status IN ('PENDING','RECLASSIFIED')
                       ORDER BY txn_date, created_at""",
                    (user_id,),
                ).fetchall()
            return [dict(r) for r in rows]

    def reclassify_transaction(
        self,
        txn_id: str,
        new_ledger_key: str,
        new_book: Optional[str] = None,
        new_section: Optional[str] = None,
        new_account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Override the predicted classification for a pending transaction.
        Validates direction/category compatibility against the stored canonical
        txn_type so review actions cannot create impossible combinations.
        """
        ledger_map = _get_ledger_map()
        cat_to_key = _get_cat_to_key()

        raw_key = str(new_ledger_key or "").strip()
        if not raw_key:
            raise ValueError("new_ledger_key is required.")

        pending_row = self._get_pending_by_id(txn_id)
        if not pending_row or pending_row.get("status") not in ("PENDING", "RECLASSIFIED"):
            raise ValueError("Transaction not found in pending review queue.")

        normalized_key = raw_key
        if normalized_key not in ledger_map:
            normalized_key = cat_to_key.get(normalized_key, normalized_key)
        if normalized_key not in ledger_map:
            lowered = raw_key.lower()
            alias_lookup = {str(k).lower(): v for k, v in cat_to_key.items()}
            key_lookup = {str(k).lower(): k for k in ledger_map.keys()}
            normalized_key = alias_lookup.get(lowered, key_lookup.get(lowered, raw_key))
        if normalized_key not in ledger_map:
            raise ValueError(
                f"'{raw_key}' is not a valid ledger key. "
                f"Check LEDGER_MAP for valid keys."
            )

        book, section, grp, account = ledger_map[normalized_key]
        candidate = {
            "ledger_key": normalized_key,
            "book": new_book or book,
            "section": new_section or section,
            "grp": grp,
            "group": grp,
            "account": new_account or account,
            "txn_type": pending_row.get("txn_type") or "debit",
            "note": pending_row.get("note", ""),
            "source": "review",
            "confidence": pending_row.get("confidence", 0),
            "attribution": pending_row.get("attribution", ""),
        }
        fixed = _canonicalize_result(candidate, candidate["txn_type"])

        with self._conn() as c:
            c.execute(
                """UPDATE pending_ledger SET
                       reclassified_key = ?,
                       book             = ?,
                       section          = ?,
                       grp              = ?,
                       account          = ?,
                       attribution      = ?,
                       txn_type         = ?,
                       note             = ?,
                       status           = 'RECLASSIFIED'
                   WHERE id=? AND status IN ('PENDING','RECLASSIFIED')""",
                (
                    fixed["ledger_key"],
                    fixed["book"],
                    fixed["section"],
                    fixed["grp"],
                    fixed["account"],
                    fixed.get("attribution", ""),
                    fixed["txn_type"],
                    fixed.get("note", ""),
                    txn_id,
                ),
            )
            if c.total_changes == 0:
                raise ValueError("Transaction not found in pending review queue.")

        # Learn from user correction
        _learn_from_reclassification(
            pending_row.get("narration", ""),
            fixed["ledger_key"],
        )

        return self._get_pending_by_id(txn_id)

    def approve_transactions(self, user_id: str) -> int:
        """
        Promote all PENDING/RECLASSIFIED rows to the ledger table.
        Every row is revalidated at approval time so stale or mismatched
        pending data cannot pollute the final ledger.
        """
        pending = self.get_pending_transactions(user_id)
        if not pending:
            return 0

        pending_ids = [p["id"] for p in pending if p.get("id")]
        affected_batch_ids: set = set()
        if pending_ids:
            placeholders = ",".join("?" for _ in pending_ids)
            with self._conn() as c:
                affected_batch_ids = {
                    r[0] for r in c.execute(
                        f"""SELECT DISTINCT batch_id
                            FROM raw_import_rows
                            WHERE user_id=? AND moved_to_pending_id IN ({placeholders})""",
                        (user_id, *pending_ids),
                    ).fetchall()
                    if r[0]
                }

        moved = 0
        for p in pending:
            ledger_key = p.get("reclassified_key") or p["predicted_ledger_key"]
            candidate = {
                "ledger_key": ledger_key,
                "book": p.get("book", "SUSPENSE"),
                "section": p.get("section", "Suspense"),
                "grp": p.get("grp", "Unclassified"),
                "group": p.get("grp", "Unclassified"),
                "account": p.get("account", "Requires Review"),
                "txn_type": p.get("txn_type") or "debit",
                "note": p.get("note", ""),
                "source": p.get("source", ""),
                "confidence": p.get("confidence", 0),
                "attribution": p.get("attribution", ""),
            }
            fixed = _canonicalize_result(candidate, candidate["txn_type"])

            is_dup = self.ledger_business_duplicate_exists(
                p["user_id"],
                p.get("account_id") or "main",
                p["txn_date"],
                p["narration"],
                p["amount"],
                fixed["txn_type"],
            )
            if is_dup:
                dup_note = ((fixed.get("note", p.get("note", "")) or "") + " | approval skipped: duplicate ledger transaction detected").strip(" | ")
                with self._conn() as c:
                    c.execute(
                        "UPDATE pending_ledger SET status='APPROVED', note=? WHERE id=?",
                        (dup_note, p["id"]),
                    )
                continue

            self.insert_txn(
                {
                    "id":           p["id"],
                    "user_id":      p["user_id"],
                    "account_id":   p.get("account_id") or "main",
                    "txn_date":     p["txn_date"],
                    "narration":    p["narration"],
                    "amount":       p["amount"],
                    "txn_type":     fixed["txn_type"],
                    "ledger_key":   fixed["ledger_key"],
                    "book":         fixed["book"],
                    "section":      fixed["section"],
                    "grp":          fixed["grp"],
                    "account":      fixed["account"],
                    "attribution":  fixed.get("attribution", p.get("attribution", "")),
                    "counterparty": p.get("counterparty", ""),
                    "confidence":   p.get("confidence", 0),
                    "source":       p.get("source", ""),
                    "cluster_id":   p.get("cluster_id", -1),
                    "is_anomaly":   p.get("is_anomaly", 0),
                    "note":         fixed.get("note", p.get("note", "")),
                }
            )
            with self._conn() as c:
                c.execute(
                    "UPDATE pending_ledger SET status='APPROVED', note=? WHERE id=?",
                    (fixed.get("note", p.get("note", "")), p["id"]),
                )
            moved += 1
        if affected_batch_ids:
            with self._conn() as c:
                for batch_id in affected_batch_ids:
                    approved = c.execute(
                        """SELECT COUNT(*)
                           FROM raw_import_rows rr
                           JOIN pending_ledger pl ON pl.id = rr.moved_to_pending_id
                           WHERE rr.batch_id=? AND rr.user_id=? AND pl.status='APPROVED'""",
                        (batch_id, user_id),
                    ).fetchone()[0]
                    c.execute(
                        """UPDATE raw_import_batches
                           SET approved_rows=?,
                               import_status=CASE
                                   WHEN COALESCE(staged_rows,0) > 0 AND ? >= COALESCE(staged_rows,0)
                                   THEN 'APPROVED'
                                   ELSE import_status
                               END,
                               updated_at=?
                           WHERE id=? AND user_id=?""",
                        (approved, approved, datetime.utcnow().isoformat(), batch_id, user_id),
                    )
        return moved

    def get_accounts(self, user_id: str) -> List[Dict]:
        """Return distinct bank accounts for a user with txn counts and date ranges."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT account_id,
                          COUNT(*) as txn_count,
                          MIN(txn_date) as date_from,
                          MAX(txn_date) as date_to
                   FROM ledger WHERE user_id=?
                   GROUP BY account_id ORDER BY account_id""",
                (user_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_account_data(self, user_id: str, account_id: str) -> int:
        """Delete all ledger/import/review data for one bank account."""
        with self._conn() as c:
            total_deleted = 0

            batch_ids = [
                r[0] for r in c.execute(
                    "SELECT id FROM raw_import_batches WHERE user_id=? AND account_id=?",
                    (user_id, account_id)
                ).fetchall()
            ]

            for sql in [
                "DELETE FROM ledger WHERE user_id=? AND account_id=?",
                "DELETE FROM pending_ledger WHERE user_id=? AND account_id=?",
                "DELETE FROM raw_import_rows WHERE user_id=? AND account_id=?",
                "DELETE FROM raw_import_batches WHERE user_id=? AND account_id=?",
                "DELETE FROM manual_entries WHERE user_id=? AND account_id=?",
                "DELETE FROM trading_ledger WHERE user_id=? AND account_id=?",
            ]:
                cur = c.execute(sql, (user_id, account_id))
                total_deleted += max(cur.rowcount or 0, 0)

            if batch_ids:
                cur = c.executemany(
                    "DELETE FROM import_audit_log WHERE batch_id=?",
                    [(bid,) for bid in batch_ids]
                )
                total_deleted += max(cur.rowcount or 0, 0)

            return total_deleted

    def delete_all_data(self, user_id: str) -> int:
        """Delete all transaction/import/review data for a user across all accounts. User account preserved."""
        with self._conn() as c:
            total_deleted = 0

            batch_ids = [
                r[0] for r in c.execute(
                    "SELECT id FROM raw_import_batches WHERE user_id=?",
                    (user_id,)
                ).fetchall()
            ]

            for sql in [
                "DELETE FROM ledger WHERE user_id=?",
                "DELETE FROM pending_ledger WHERE user_id=?",
                "DELETE FROM raw_import_rows WHERE user_id=?",
                "DELETE FROM raw_import_batches WHERE user_id=?",
                "DELETE FROM manual_entries WHERE user_id=?",
                "DELETE FROM trading_ledger WHERE user_id=?",
                "DELETE FROM review_notes WHERE user_id=?",
            ]:
                cur = c.execute(sql, (user_id,))
                total_deleted += max(cur.rowcount or 0, 0)

            if batch_ids:
                cur = c.executemany(
                    "DELETE FROM import_audit_log WHERE batch_id=?",
                    [(bid,) for bid in batch_ids]
                )
                total_deleted += max(cur.rowcount or 0, 0)

            cur = c.execute(
                "UPDATE users SET opening_balance=0 WHERE id=?",
                (user_id,)
            )
            total_deleted += max(cur.rowcount or 0, 0)

            return total_deleted

    def delete_user(self, user_id: str) -> int:
        """Delete the full user record and all dependent data."""
        with self._conn() as c:
            total_deleted = 0

            batch_ids = [
                r[0] for r in c.execute(
                    "SELECT id FROM raw_import_batches WHERE user_id=?",
                    (user_id,)
                ).fetchall()
            ]

            for sql in [
                "DELETE FROM ledger WHERE user_id=?",
                "DELETE FROM pending_ledger WHERE user_id=?",
                "DELETE FROM raw_import_rows WHERE user_id=?",
                "DELETE FROM raw_import_batches WHERE user_id=?",
                "DELETE FROM manual_entries WHERE user_id=?",
                "DELETE FROM trading_ledger WHERE user_id=?",
                "DELETE FROM review_notes WHERE user_id=?",
                "DELETE FROM custom_classifier_rules WHERE user_id=?",
            ]:
                cur = c.execute(sql, (user_id,))
                total_deleted += max(cur.rowcount or 0, 0)

            if batch_ids:
                cur = c.executemany(
                    "DELETE FROM import_audit_log WHERE batch_id=?",
                    [(bid,) for bid in batch_ids]
                )
                total_deleted += max(cur.rowcount or 0, 0)

            cur = c.execute("DELETE FROM users WHERE id=?", (user_id,))
            total_deleted += max(cur.rowcount or 0, 0)
            return total_deleted

    # ── Trading ledger CRUD ──────────────────────────────────────────────────

    def insert_trading_rows(
        self,
        user_id: str,
        account_id: str,
        rows: List[Dict[str, Any]],
    ) -> Tuple[int, int]:
        """
        Bulk-insert parsed trading ledger rows.
        Returns (inserted, skipped) counts.
        """
        now = datetime.utcnow().isoformat()
        inserted = skipped = 0
        with self._conn() as c:
            for row in rows:
                rid = str(uuid.uuid4())
                try:
                    c.execute(
                        """INSERT OR IGNORE INTO trading_ledger
                           (id, user_id, account_id, txn_date, segment, particular,
                            description, debit, credit, balance, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            rid, user_id, account_id,
                            row.get("txn_date", ""),
                            row.get("segment", ""),
                            row.get("particular", ""),
                            row.get("description", ""),
                            float(row.get("debit") or 0),
                            float(row.get("credit") or 0),
                            float(row.get("balance") or 0),
                            now,
                        ),
                    )
                    if c.total_changes > 0:
                        inserted += 1
                    else:
                        skipped += 1
                except Exception:
                    skipped += 1
        return inserted, skipped

    def get_trading_rows(
        self,
        user_id: str,
        account_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch trading ledger rows for a user, optionally filtered."""
        sql = "SELECT * FROM trading_ledger WHERE user_id=?"
        params: List[Any] = [user_id]
        if account_id:
            sql += " AND account_id=?"
            params.append(account_id)
        if date_from:
            sql += " AND txn_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND txn_date <= ?"
            params.append(date_to)
        sql += " ORDER BY txn_date, id"
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def clear_pending(self, user_id: str) -> None:
        """Discard all pending transactions for a user (cancel upload)."""
        with self._conn() as c:
            c.execute(
                "DELETE FROM pending_ledger WHERE user_id=? AND status IN ('PENDING','RECLASSIFIED')",
                (user_id,),
            )

    def apply_profile_to_pending(self, user_id: str) -> Dict[str, int]:
        """Re-run current profile hints on active review rows."""
        rows = self.get_pending_transactions(user_id)
        counterparties = self.get_known_counterparties(user_id)
        accounts = self.get_known_accounts(user_id)
        profile = self.get_user_profile(user_id) or {}
        reviewed = 0
        updated = 0

        for row in rows:
            reviewed += 1
            status = str(row.get("status") or "PENDING").upper()
            current_key = str(row.get("reclassified_key") or row.get("predicted_ledger_key") or "")
            if status == "RECLASSIFIED" and current_key not in _PROFILE_OVERRIDABLE_KEYS:
                continue

            candidate = {
                "ledger_key": current_key,
                "book": row.get("book", "SUSPENSE"),
                "section": row.get("section", "Suspense"),
                "grp": row.get("grp", "Unclassified"),
                "group": row.get("grp", "Unclassified"),
                "account": row.get("account", "Requires Review"),
                "txn_type": row.get("txn_type") or "debit",
                "note": row.get("note", ""),
                "source": row.get("source", ""),
                "confidence": row.get("confidence", 0),
                "attribution": row.get("attribution", ""),
                "counterparty": row.get("counterparty", ""),
            }
            hinted = _apply_profile_classification_hints(
                self,
                user_id,
                row.get("narration", ""),
                candidate,
                candidate["txn_type"],
                known_counterparties=counterparties,
                known_accounts=accounts,
                profile=profile,
            )
            new_key = str(hinted.get("ledger_key") or current_key)
            if new_key == current_key:
                continue
            if not _is_directionally_compatible_key(new_key, candidate["txn_type"]):
                continue
            if not _allow_profile_override(current_key, new_key):
                continue

            fixed = _canonicalize_result(hinted, candidate["txn_type"])
            note = fixed.get("note", row.get("note", ""))
            if "profile_reapplied=1" not in note:
                note = (note + " | profile_reapplied=1").strip(" | ")

            with self._conn() as c:
                if status == "RECLASSIFIED":
                    c.execute(
                        """UPDATE pending_ledger
                           SET reclassified_key=?, book=?, section=?, grp=?, account=?,
                               attribution=?, txn_type=?, counterparty=?, confidence=?, note=?
                           WHERE id=? AND user_id=? AND status='RECLASSIFIED'""",
                        (
                            fixed["ledger_key"], fixed["book"], fixed["section"], fixed["grp"],
                            fixed["account"], fixed.get("attribution", ""),
                            fixed["txn_type"], fixed.get("counterparty", row.get("counterparty", "")),
                            float(fixed.get("confidence", row.get("confidence", 0)) or 0),
                            note, row["id"], user_id,
                        ),
                    )
                else:
                    c.execute(
                        """UPDATE pending_ledger
                           SET predicted_ledger_key=?, book=?, section=?, grp=?, account=?,
                               attribution=?, txn_type=?, counterparty=?, confidence=?, note=?
                           WHERE id=? AND user_id=? AND status='PENDING'""",
                        (
                            fixed["ledger_key"], fixed["book"], fixed["section"], fixed["grp"],
                            fixed["account"], fixed.get("attribution", ""),
                            fixed["txn_type"], fixed.get("counterparty", row.get("counterparty", "")),
                            float(fixed.get("confidence", row.get("confidence", 0)) or 0),
                            note, row["id"], user_id,
                        ),
                    )
                if c.total_changes:
                    updated += 1

        return {"reviewed": reviewed, "updated": updated}

    def apply_profile_to_approved_safe(self, user_id: str) -> Dict[str, Any]:
        """Conservatively re-run profile hints on approved ledger rows."""
        counterparties = self.get_known_counterparties(user_id)
        accounts = self.get_known_accounts(user_id)
        profile = self.get_user_profile(user_id) or {}
        reviewed = 0
        updated = 0

        with self._conn() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM ledger WHERE user_id=? ORDER BY txn_date, id",
                (user_id,),
            ).fetchall()]

        for row in rows:
            reviewed += 1
            current_key = str(row.get("ledger_key") or "")
            txn_type = str(row.get("txn_type") or "").lower()
            if txn_type not in ("debit", "credit"):
                continue
            if current_key not in _PROFILE_APPROVED_SAFE_KEYS:
                continue

            candidate = {
                "ledger_key": current_key,
                "book": row.get("book", "SUSPENSE"),
                "section": row.get("section", "Suspense"),
                "grp": row.get("grp", "Unclassified"),
                "group": row.get("grp", "Unclassified"),
                "account": row.get("account", "Requires Review"),
                "txn_type": txn_type,
                "note": row.get("note", ""),
                "source": row.get("source", ""),
                "confidence": row.get("confidence", 0),
                "attribution": row.get("attribution", ""),
                "counterparty": row.get("counterparty", ""),
            }
            hinted = _apply_profile_classification_hints(
                self,
                user_id,
                row.get("narration", ""),
                candidate,
                txn_type,
                known_counterparties=counterparties,
                known_accounts=accounts,
                profile=profile,
            )
            new_key = str(hinted.get("ledger_key") or current_key)
            if new_key == current_key:
                continue
            if current_key in _PROFILE_PROTECTED_KEYS or new_key in _PROFILE_PROTECTED_KEYS:
                continue
            if not _is_directionally_compatible_key(new_key, txn_type):
                continue
            if not _allow_profile_override(current_key, new_key):
                continue

            fixed = _canonicalize_result(hinted, txn_type)
            note = fixed.get("note", row.get("note", ""))
            if "profile_reapplied_approved_safe=1" not in note:
                note = (note + " | profile_reapplied_approved_safe=1").strip(" | ")

            with self._conn() as c:
                c.execute(
                    """UPDATE ledger
                       SET ledger_key=?, book=?, section=?, grp=?, account=?,
                           attribution=?, txn_type=?, counterparty=?, confidence=?, note=?
                       WHERE id=? AND user_id=?""",
                    (
                        fixed["ledger_key"], fixed["book"], fixed["section"], fixed["grp"],
                        fixed["account"], fixed.get("attribution", ""), fixed["txn_type"],
                        fixed.get("counterparty", row.get("counterparty", "")),
                        float(fixed.get("confidence", row.get("confidence", 0)) or 0),
                        note, row["id"], user_id,
                    ),
                )
                if c.total_changes:
                    updated += 1

        return {
            "reviewed": reviewed,
            "updated": updated,
            "msg": f"Reviewed {reviewed} approved transactions; safely updated {updated}.",
        }

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._db.db_path, check_same_thread=False, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    def _get_pending_by_id(self, txn_id: str) -> Optional[Dict]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM pending_ledger WHERE id=? LIMIT 1", (txn_id,)
            ).fetchone()
            return dict(row) if row else None

    def reclassify_approved_transaction(
        self,
        txn_id: str,
        new_ledger_key: str,
        stock_name: str = "",
        trade_type: str = "",
        trade_price: str = "",
        trade_qty: str = "",
        trade_tds: str = "",
        txn_type_override: str = "",
    ) -> Dict[str, Any]:
        ledger_map = _get_ledger_map()
        cat_to_key = _get_cat_to_key()

        raw_key = str(new_ledger_key or "").strip()
        if not raw_key:
            raise ValueError("new_ledger_key is required.")

        normalized_key = raw_key
        if normalized_key not in ledger_map:
            normalized_key = cat_to_key.get(normalized_key, normalized_key)
        if normalized_key not in ledger_map:
            lowered = raw_key.lower()
            alias_lookup = {str(k).lower(): v for k, v in cat_to_key.items()}
            key_lookup = {str(k).lower(): k for k in ledger_map.keys()}
            normalized_key = alias_lookup.get(lowered, key_lookup.get(lowered, raw_key))
        if normalized_key not in ledger_map:
            raise ValueError(f"'{raw_key}' is not a valid ledger key.")

        with self._conn() as c:
            existing = c.execute("SELECT * FROM ledger WHERE id=? LIMIT 1", (txn_id,)).fetchone()
            if not existing:
                raise ValueError("Approved ledger transaction not found.")
            existing = dict(existing)

            stock_name = str(stock_name or "").strip().upper()
            trade_type = str(trade_type or "").strip().upper()
            trade_price_val = float(trade_price or 0) if str(trade_price or "").strip() else 0.0
            trade_qty_val = float(trade_qty or 0) if str(trade_qty or "").strip() else 0.0
            trade_tds_val = float(trade_tds or 0) if str(trade_tds or "").strip() else 0.0

            effective_txn_type = (txn_type_override or existing.get("txn_type") or "debit").strip().lower()
            if effective_txn_type not in ("debit", "credit"):
                effective_txn_type = existing.get("txn_type") or "debit"

            candidate = {
                "ledger_key": normalized_key,
                "book": ledger_map[normalized_key][0],
                "section": ledger_map[normalized_key][1],
                "grp": ledger_map[normalized_key][2],
                "group": ledger_map[normalized_key][2],
                "account": ledger_map[normalized_key][3],
                "txn_type": effective_txn_type,
                "note": (existing.get("note") or "") + " | Reclassified after approval",
                "source": existing.get("source", "manual"),
                "confidence": existing.get("confidence", 0),
                "attribution": existing.get("attribution", ""),
            }
            fixed = _canonicalize_result(candidate, candidate["txn_type"])

            if normalized_key == "asset_investment_equity":
                fixed["book"] = "BALANCE_SHEET"
                fixed["section"] = "Assets"
                fixed["grp"] = "Non-Current Assets"

                if stock_name:
                    fixed["account"] = f"Investment – Equity Shares ({stock_name})"
                    fixed["counterparty"] = stock_name

                    stock_parts = [f"Share: {stock_name}"]
                    if trade_type in ("BUY", "PURCHASE"):
                        fixed["txn_type"] = "debit"
                    elif trade_type in ("SELL", "SALE"):
                        fixed["txn_type"] = "credit"
                    if trade_price:
                        stock_parts.append(f"Price: {trade_price}")
                    if trade_qty:
                        stock_parts.append(f"Qty: {trade_qty}")
                    if trade_tds:
                        stock_parts.append(f"TDS: {trade_tds}")

                    stock_note = " | ".join(stock_parts)
                    fixed["note"] = ((fixed.get("note") or "") + " | " + stock_note).strip(" | ")
                else:
                    fixed["account"] = "Investment – Equity Shares"
            # ✅ ALWAYS RUN UPDATE (outside if/else)
            c.execute(
                """
                UPDATE ledger
                SET ledger_key=?,
                    book=?,
                    section=?,
                    grp=?,
                    account=?,
                    attribution=?,
                    txn_type=?,
                    counterparty=?,
                    note=?,
                    stock_name=?,
                    trade_type=?,
                    trade_price=?,
                    trade_qty=?,
                    trade_tds=?
                WHERE id=?
                """,
                (
                    fixed["ledger_key"],
                    fixed["book"],
                    fixed["section"],
                    fixed["grp"],
                    fixed["account"],
                    fixed.get("attribution", ""),
                    fixed["txn_type"],
                    fixed.get("counterparty", existing.get("counterparty", "")),
                    fixed.get("note", existing.get("note", "")),
                    stock_name,
                    trade_type,
                    trade_price_val,
                    trade_qty_val,
                    trade_tds_val,
                    txn_id,
                ),
            )

            row = c.execute(
                "SELECT * FROM ledger WHERE id=? LIMIT 1",
                (txn_id,),
            ).fetchone()

        # Learn from user correction
        _learn_from_reclassification(
            existing.get("narration", ""),
            fixed["ledger_key"],
        )

        return dict(row) if row else {}

    def get_custom_rules(self, user_id: str) -> List[Dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT * FROM custom_classifier_rules
                   WHERE user_id=? AND is_active=1
                   ORDER BY priority ASC, created_at ASC""",
                (user_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def add_custom_rule(
        self,
        user_id: str,
        pattern: str,
        ledger_key: str,
        txn_type: str = "",
        match_mode: str = "contains",
        priority: int = 100,
    ) -> Dict[str, Any]:
        ledger_map = _get_ledger_map()
        cat_to_key = _get_cat_to_key()

        pattern = str(pattern or "").strip()
        ledger_key = str(ledger_key or "").strip()
        txn_type = str(txn_type or "").strip().lower()
        match_mode = str(match_mode or "contains").strip().lower()

        if not pattern:
            raise ValueError("pattern is required.")
        if txn_type not in ("", "debit", "credit"):
            raise ValueError("txn_type must be '', 'debit', or 'credit'.")
        if match_mode not in ("contains", "regex", "exact"):
            raise ValueError("match_mode must be contains, regex, or exact.")

        if ledger_key not in ledger_map:
            ledger_key = cat_to_key.get(ledger_key, ledger_key)
        if ledger_key not in ledger_map:
            raise ValueError(f"Invalid ledger key: {ledger_key}")

        rid = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        with self._conn() as c:
            c.execute(
                """INSERT INTO custom_classifier_rules
                   (id, user_id, pattern, match_mode, txn_type, ledger_key, priority, is_active, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (rid, user_id, pattern, match_mode, txn_type, ledger_key, int(priority), 1, now)
            )
            row = c.execute(
                "SELECT * FROM custom_classifier_rules WHERE id=? LIMIT 1",
                (rid,)
            ).fetchone()
            return dict(row)


# ══════════════════════════════════════════════════════════════════════════════
# 3.  REVIEW WORKFLOW ENGINE
# ══════════════════════════════════════════════════════════════════════════════

import hashlib

class ReviewWorkflow:
    """
    Orchestrates the classify → review → approve → store pipeline.
    """

    def __init__(self, edb: ExtendedDBStore):
        self._edb = edb

    def compute_file_hash(self, file_bytes: bytes) -> str:
        return hashlib.sha256(file_bytes).hexdigest()

    def _normalize_string(self, s: Any) -> str:
        if not s:
            return ""
        s = str(s).upper()
        # Collapse whitespace and remove basic punctuation for canonicalization
        import re
        s = re.sub(r'[^\w\s]', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    def compute_row_fingerprint(
        self, user_id: str, account_id: str,
        norm_date: str, norm_narration: str, norm_amount: float, norm_type: str
    ) -> str:
        # fingerprint rule: user_id | account_id | normalized_date | normalized_narration | normalized_amount | normalized_txn_type
        # norm_amount fixed precision string (2 decimals)
        f_amt = f"{norm_amount:.2f}"
        raw_str = f"{user_id}|{account_id}|{norm_date}|{norm_narration}|{f_amt}|{norm_type}"
        return hashlib.sha256(raw_str.encode('utf-8')).hexdigest()

    def process_import_batch(
        self,
        user_id: str,
        account_id: str,
        statement_type: str,
        file_name: str,
        file_bytes: bytes,
        raw_records: List[Dict[str, Any]],
        statement_from_date: Optional[str] = None,
        statement_to_date: Optional[str] = None,
        opening_balance: Optional[float] = None,
        closing_balance: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Full ingestion pipeline:
        1. File dedupe  2. Create batch  3. Store raw rows
        4. Normalize + Validate + Dedupe  5. Classify + Stage to pending
        6. Return detailed JSON summary with staged_rows key
        """
        print("[PROCESS_IMPORT_BATCH PERIOD]", statement_from_date, statement_to_date, flush=True)
        file_hash = self.compute_file_hash(file_bytes)
        file_size = len(file_bytes)
        now = datetime.utcnow().isoformat()

        # 1. Check duplicate batch
        # ── Stale-batch guard ────────────────────────────────────────────────
        # After delete_all_data / delete_account_data the raw_import_batches
        # row may still exist (file hash cached) even though all transaction
        # data was wiped.  In that case the file must be treated as a fresh
        # upload — NOT blocked as a duplicate.
        #
        # Rule: a batch is "stale" if its raw_import_rows are all gone.
        # The row-level fingerprint deduplication (further below) still
        # prevents individual row duplicates, so deleting the stale batch
        # record and re-importing is always safe.
        existing_batch = None
        with self._edb._conn() as c:
            batch_row = c.execute(
                """SELECT * FROM raw_import_batches
                WHERE user_id=? AND account_id=? AND source_file_hash=?
                ORDER BY created_at DESC, id DESC
                LIMIT 1""",
                (user_id, account_id, file_hash)
            ).fetchone()

            live_ledger_rows = c.execute(
                "SELECT COUNT(*) FROM ledger WHERE user_id=? AND account_id=?",
                (user_id, account_id)
            ).fetchone()[0]

            live_pending_rows = c.execute(
                "SELECT COUNT(*) FROM pending_ledger WHERE user_id=? AND account_id=? AND status IN ('PENDING','RECLASSIFIED')",
                (user_id, account_id)
            ).fetchone()[0]

            live_manual_rows = c.execute(
                "SELECT COUNT(*) FROM manual_entries WHERE user_id=? AND account_id=?",
                (user_id, account_id)
            ).fetchone()[0]

            no_live_account_data = (
                live_ledger_rows == 0 and
                live_pending_rows == 0 and
                live_manual_rows == 0
            )

            if batch_row:
                batch_row = dict(batch_row)

                raw_rows_for_hash = c.execute(
                    """SELECT COUNT(*)
                       FROM raw_import_rows
                       WHERE user_id=? AND account_id=? AND source_file_hash=?""",
                    (user_id, account_id, file_hash)
                ).fetchone()[0]

                is_stale_batch = (
                    raw_rows_for_hash == 0 or no_live_account_data
                )

                if is_stale_batch:
                    # Purge ALL stale import metadata for this user/account/file hash,
                    # not just one batch id, otherwise old fingerprints still mark
                    # every row as EXACT_DUPLICATE.
                    stale_batch_ids = [
                        r[0] for r in c.execute(
                            """SELECT id FROM raw_import_batches
                               WHERE user_id=? AND account_id=? AND source_file_hash=?""",
                            (user_id, account_id, file_hash)
                        ).fetchall()
                    ]

                    if stale_batch_ids:
                        c.executemany(
                            "DELETE FROM raw_import_rows WHERE batch_id=? AND user_id=? AND account_id=?",
                            [(bid, user_id, account_id) for bid in stale_batch_ids]
                        )
                        c.executemany(
                            "DELETE FROM import_audit_log WHERE batch_id=?",
                            [(bid,) for bid in stale_batch_ids]
                        )

                    c.execute(
                        """DELETE FROM raw_import_batches
                           WHERE user_id=? AND account_id=? AND source_file_hash=?""",
                        (user_id, account_id, file_hash)
                    )

                    print(f"  [IMPORT] Stale batches for '{file_name}' cleared — all raw rows + batch metadata removed. Re-importing fresh.")
                else:
                    existing_batch = batch_row

        if existing_batch is not None:
            print(f"  [DUPLICATE] Batch detected for file '{file_name}' — attempting safe recovery")
            _persist_uploaded_file(existing_batch["id"], file_name or existing_batch.get("source_file_name") or "", file_bytes)

            restaged_count = 0
            existing_pending = [
                r for r in self._edb.get_pending_transactions(user_id)
                if (r.get("account_id") or "main") == account_id
            ]

            if not existing_pending:
                print("  [DUPLICATE] No active pending rows for this account — attempting guarded restage from raw_import_rows")
                try:
                    classify = _get_classify()
                    extract_counterparty = _get_extract_counterparty()

                    with self._edb._conn() as c2:
                        raw_rows = c2.execute(
                            """SELECT * FROM raw_import_rows
                            WHERE user_id=? AND account_id=? AND batch_id=?
                            ORDER BY row_number, created_at""",
                            (user_id, account_id, existing_batch["id"])
                        ).fetchall()

                    already_posted = 0
                    known_counterparties = self._edb.get_known_counterparties(user_id)
                    known_accounts = self._edb.get_known_accounts(user_id)
                    user_profile = self._edb.get_user_profile(user_id) or {}

                    for r in raw_rows:
                        r = dict(r)

                        narration = r.get("raw_description") or r.get("normalized_narration") or ""
                        txn_type  = (r.get("normalized_txn_type") or "DEBIT").lower()
                        amount    = float(r.get("normalized_amount") or 0)
                        txn_date  = r.get("normalized_date") or ""

                        if not narration or amount <= 0 or not txn_date:
                            continue

                        if self._edb.ledger_business_duplicate_exists(
                            user_id, account_id, txn_date, narration, amount, txn_type
                        ):
                            already_posted += 1
                            continue

                        classified = classify(
                            narration,
                            forced_type=txn_type,
                            amount=amount,
                            txn_date=txn_date
                        )
                        classified = _apply_profile_classification_hints(
                            self._edb,
                            user_id,
                            narration,
                            classified,
                            txn_type,
                            known_counterparties=known_counterparties,
                            known_accounts=known_accounts,
                            profile=user_profile,
                        )
                        fixed = _canonicalize_result(classified, txn_type)

                        best_counterparty = _best_import_counterparty(
                            narration=narration,
                            classified_counterparty=fixed.get("counterparty", ""),
                            existing_counterparty=r.get("counterparty", ""),
                            user_id=user_id,
                            known_counterparties=known_counterparties,
                        )

                        self._edb.store_pending_transaction({
                            "user_id": user_id,
                            "account_id": account_id,
                            "txn_date": txn_date,
                            "narration": narration,
                            "amount": amount,
                            "txn_type": txn_type,
                            "ledger_key": fixed.get("ledger_key", "suspense_debit"),
                            "book": fixed.get("book", "SUSPENSE"),
                            "section": fixed.get("section", "Suspense"),
                            "group": fixed.get("group", fixed.get("grp", "Unclassified")),
                            "account": fixed.get("account", "Requires Review"),
                            "counterparty": best_counterparty,
                            "confidence": fixed.get("confidence", 0),
                            "attribution": fixed.get("attribution", ""),
                            "note": (fixed.get("note", "") + " | duplicate-batch guarded restage").strip(" | "),
                            "source": "restaged",
                        })
                        restaged_count += 1

                    print(f"  [DUPLICATE] Guarded restage: restaged={restaged_count}, already_posted={already_posted}")

                except Exception as e:
                    print(f"  [DUPLICATE] Restage failed: {e}")

                existing_pending = [
                    r for r in self._edb.get_pending_transactions(user_id)
                    if (r.get("account_id") or "main") == account_id
                ]

            effective_staged = len(existing_pending) if existing_pending else restaged_count

            return {
                "ok": True,
                "batch_id": existing_batch["id"],
                "account_id": account_id,
                "duplicate_batch": True,
                "staged_rows": effective_staged,
                "staged": effective_staged,
                "transactions": existing_pending if isinstance(existing_pending, list) else [],
                "warnings": ["Duplicate file detected. Showing existing pending transactions."],
            }

        batch_id = str(uuid.uuid4())
        _persist_uploaded_file(batch_id, file_name, file_bytes)
        normalize_parsed_record = _get_normalize_parsed_record()
        classify = _get_classify()

        total_rows = len(raw_records)
        valid_rows = invalid_rows = duplicate_rows = staged_rows = 0

        # 2. Create batch record
        with self._edb._conn() as c:
            print("[RAW_IMPORT_BATCH INSERT PERIOD]", statement_from_date, statement_to_date, flush=True)
            c.execute(
                """INSERT INTO raw_import_batches(
                    id, user_id, account_id, statement_type, source_file_name, source_file_hash,
                    file_size_bytes, import_status, statement_from_date, statement_to_date,
                    opening_balance, closing_balance, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    batch_id,
                    user_id,
                    account_id,
                    statement_type,
                    file_name,
                    file_hash,
                    file_size,
                    'PROCESSING',
                    statement_from_date,
                    statement_to_date,
                    opening_balance,
                    closing_balance,
                    now,
                    now,
                )
            )
            known_fingerprints = set()
        with self._edb._conn() as c:
            live_ledger_rows = c.execute(
                "SELECT COUNT(*) FROM ledger WHERE user_id=? AND account_id=?",
                (user_id, account_id)
            ).fetchone()[0]

            live_pending_rows = c.execute(
                "SELECT COUNT(*) FROM pending_ledger WHERE user_id=? AND account_id=? AND status IN ('PENDING','RECLASSIFIED')",
                (user_id, account_id)
            ).fetchone()[0]

            live_manual_rows = c.execute(
                "SELECT COUNT(*) FROM manual_entries WHERE user_id=? AND account_id=?",
                (user_id, account_id)
            ).fetchone()[0]

            # If account has been wiped, ignore leftover raw-import fingerprints entirely.
            if live_ledger_rows > 0 or live_pending_rows > 0 or live_manual_rows > 0:
                for r in c.execute(
                    "SELECT fingerprint FROM raw_import_rows WHERE user_id=? AND account_id=?",
                    (user_id, account_id)
                ).fetchall():
                    if r[0]:
                        known_fingerprints.add(r[0])

                for r in c.execute(
                    "SELECT fingerprint FROM manual_entries WHERE user_id=? AND account_id=?",
                    (user_id, account_id)
                ).fetchall():
                    if r[0]:
                        known_fingerprints.add(r[0])

        rows_to_insert = []
        pending_to_insert = []
        known_counterparties = self._edb.get_known_counterparties(user_id)
        known_accounts = self._edb.get_known_accounts(user_id)
        user_profile = self._edb.get_user_profile(user_id) or {}

        for i, rec in enumerate(raw_records):
            row_id = str(uuid.uuid4())
            raw_date_str = str(rec.get("txn_date") or "")
            raw_desc_str = str(rec.get("description") or rec.get("narration") or "")
            raw_amt_str  = str(rec.get("amount", 0))
            raw_type_str = str(rec.get("txn_type", ""))

            source = str(rec.get("source") or "excel")
            normed = normalize_parsed_record(rec, source=source)
            norm_date = normed.get("txn_date", "")
            norm_desc = self._normalize_string(normed.get("narration") or normed.get("description") or "")
            norm_amt  = float(normed.get("amount", 0) or 0)
            norm_type = str(normed.get("txn_type", "debit")).upper()
            if norm_type not in ("DEBIT", "CREDIT"):
                norm_type = "DEBIT"

            fp = self.compute_row_fingerprint(user_id, account_id, norm_date, norm_desc, norm_amt, norm_type)

            val_status = "VALID"
            val_errors = []

            raw_balance = rec.get("balance", rec.get("raw_balance"))
            try:
                bal_float = float(raw_balance) if raw_balance not in (None, "", "None") else None
            except Exception:
                bal_float = None

            weak_desc = len((norm_desc or "").strip()) < 4

            if not norm_date:
                val_status = "INVALID"; val_errors.append("Missing or invalid date")
            if not norm_desc:
                val_status = "INVALID"; val_errors.append("Empty narration")
            elif weak_desc:
                val_status = "INVALID"; val_errors.append("Narration too weak")
            if norm_amt <= 0:
                val_status = "INVALID"; val_errors.append("Amount must be > 0")

            # PDF style corruption guard: amount copied into balance column
            if bal_float is not None and norm_amt > 0 and abs(norm_amt - bal_float) < 0.0001:
                val_status = "INVALID"; val_errors.append("Amount equals balance; likely malformed parse row")

            # Basic malformed row guard
            if weak_desc and not norm_date:
                val_status = "INVALID"; val_errors.append("Malformed row")

            # Optional balance movement consistency check when prior row exists
            if i > 0 and bal_float is not None:
                prev_bal_raw = raw_records[i-1].get("balance", raw_records[i-1].get("raw_balance"))
                try:
                    prev_bal = float(prev_bal_raw) if prev_bal_raw not in (None, "", "None") else None
                except Exception:
                    prev_bal = None

                if prev_bal is not None:
                    delta = bal_float - prev_bal
                    implied = "credit" if delta > 0 else "debit" if delta < 0 else ""
                    if implied and abs(abs(delta) - norm_amt) < 1.0 and implied != norm_type.lower():
                        val_status = "INVALID"
                        val_errors.append("Direction inconsistent with balance movement")

            junk_flag = 1 if val_status == "INVALID" else 0
            if val_status == "VALID":
                valid_rows += 1
            else:
                invalid_rows += 1

            dup_status = "NEW"
            if fp in known_fingerprints:
                dup_status = "EXACT_DUPLICATE"; duplicate_rows += 1

            moved_to_pending = 0
            pending_id = None
            if val_status == "VALID" and dup_status == "NEW":
                classify_text = raw_desc_str or norm_desc
                classified = classify(classify_text, forced_type=norm_type.lower(), amount=norm_amt, txn_date=norm_date)
                classified = _apply_profile_classification_hints(
                    self._edb,
                    user_id,
                    classify_text,
                    classified,
                    norm_type.lower(),
                    known_counterparties=known_counterparties,
                    known_accounts=known_accounts,
                    profile=user_profile,
                )
                fixed = _canonicalize_result(
                    classified, norm_type.lower(),
                    parser_txn_type=raw_type_str.lower(),
                    derived_txn_type=norm_type.lower(),
                    parser_notes=normed.get("parser_notes", ""),
                )

                best_counterparty = _best_import_counterparty(
                    narration=classify_text,
                    classified_counterparty=fixed.get("counterparty", ""),
                    existing_counterparty=str(rec.get("counterparty") or ""),
                    user_id=user_id,
                    known_counterparties=known_counterparties,
                )

                pending_id = str(uuid.uuid4())
                moved_to_pending = 1
                staged_rows += 1
                known_fingerprints.add(fp)
                pending_to_insert.append((
                    pending_id, user_id, account_id, norm_date, classify_text, norm_amt, norm_type.lower(),
                    fixed.get("ledger_key", "suspense_debit"), float(fixed.get("confidence", 0)),
                    fixed.get("book", "SUSPENSE"), fixed.get("section", "Unclassified"),
                    fixed.get("group", fixed.get("grp", "Unclassified")), fixed.get("account", "Requires Review"),
                    best_counterparty,
                    fixed.get("attribution", ""), source,
                    int(fixed.get("cluster_id", -1)), int(fixed.get("is_anomaly", 0)),
                    fixed.get("note", f"Source Row ID: {row_id}"), "PENDING", now
                ))

            rows_to_insert.append((
                row_id, batch_id, user_id, account_id, statement_type, i+1,
                file_name, file_hash, raw_date_str, raw_desc_str,
                str(rec.get("raw_debit", "")), str(rec.get("raw_credit", "")), raw_amt_str, "", raw_type_str,
                json.dumps(rec), norm_date, norm_desc, norm_amt, 0.0, norm_type,
                "PARSED", val_status, json.dumps(val_errors), junk_flag, 0,
                fp, dup_status, None, moved_to_pending, pending_id, "CLASSIFIED" if moved_to_pending else "N/A",
                now, now
            ))

        with self._edb._conn() as c:
            c.executemany(
                """INSERT INTO raw_import_rows(
                       id, batch_id, user_id, account_id, statement_type, row_number,
                       source_file_name, source_file_hash, raw_date, raw_description,
                       raw_debit, raw_credit, raw_amount, raw_balance, raw_txn_type,
                       raw_json, normalized_date, normalized_narration, normalized_amount,
                       normalized_balance, normalized_txn_type, parse_status, validation_status,
                       validation_errors, junk_flag, review_required_flag, fingerprint,
                       duplicate_status, duplicate_of_row_id, moved_to_pending_flag, moved_to_pending_id,
                       classification_status, created_at, updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows_to_insert
            )
            if pending_to_insert:
                c.executemany(
                    """INSERT INTO pending_ledger(
                           id, user_id, account_id, txn_date, narration, amount, txn_type,
                           predicted_ledger_key, confidence, book, section, grp, account,
                           counterparty, attribution, source, cluster_id, is_anomaly,
                           note, status, created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    pending_to_insert
                )
            c.execute(
                """UPDATE raw_import_batches SET
                       import_status='PROCESSED', total_rows=?, parsed_rows=?, valid_rows=?,
                       invalid_rows=?, duplicate_rows=?, staged_rows=?, updated_at=?
                   WHERE id=?""",
                (total_rows, total_rows, valid_rows, invalid_rows, duplicate_rows, staged_rows, now, batch_id)
            )
            c.execute(
                "INSERT INTO import_audit_log(id, batch_id, action, actor, details_json, created_at) VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4()), batch_id, "IMPORT_BATCH_PROCESSED", user_id,
                 json.dumps({"staged": staged_rows, "duplicates": duplicate_rows}), now)
            )

        print(f"[IMPORT DEBUG] total={total_rows}, valid={valid_rows}, staged={staged_rows}, dup={duplicate_rows}, invalid={invalid_rows}")
        return {
            "ok": True,
            "batch_id": batch_id,
            "account_id": account_id,
            "statement_type": statement_type,
            "source_file_name": file_name,
            "duplicate_batch": False,
            "total_rows": total_rows,
            "valid_rows": valid_rows,
            "invalid_rows": invalid_rows,
            "duplicate_rows": duplicate_rows,
            "staged_rows": staged_rows,
            "staged": staged_rows,
            "warnings": [],
            "errors": [],
        }

    def add_manual_entry(
        self, user_id: str, account_id: str, txn_date: str, narration: str,
        amount: float, txn_type: str, desired_ledger_key: str = "", note: str = "", override_duplicate: bool = False
    ) -> Dict[str, Any]:
        """
        Add a manual entry and push it to pending_ledger (or reject if dupe).
        """
        now = datetime.utcnow().isoformat()
        norm_desc = self._normalize_string(narration)
        norm_type = txn_type.upper()
        if norm_type not in ("DEBIT", "CREDIT"):
            norm_type = "DEBIT"

        fp = self.compute_row_fingerprint(user_id, account_id, txn_date, norm_desc, amount, norm_type)

        with self._edb._conn() as c:
            if not override_duplicate:
                # Check dupe
                existing = c.execute("SELECT id FROM manual_entries WHERE fingerprint=?", (fp,)).fetchone()
                if existing:
                    raise ValueError(f"An exact duplicate manual entry already exists.")
                # Check ledger/pending too
                existing = c.execute("SELECT id FROM pending_ledger WHERE user_id=? AND txn_date=? AND amount=? AND txn_type=?", (user_id, txn_date, amount, norm_type.lower())).fetchone()
                if existing:
                    raise ValueError(f"An exact duplicate transaction is already pending.")

            entry_id = str(uuid.uuid4())
            # Stage to pending
            classify = _get_classify()
            classified = classify(norm_desc, forced_type=norm_type.lower(), amount=amount, txn_date=txn_date)
            classified = _apply_profile_classification_hints(
                self._edb,
                user_id,
                narration,
                classified,
                norm_type.lower(),
                known_counterparties=self._edb.get_known_counterparties(user_id),
                known_accounts=self._edb.get_known_accounts(user_id),
                profile=self._edb.get_user_profile(user_id) or {},
            )

            forced_key = desired_ledger_key or classified.get("ledger_key", "suspense_debit")
            if desired_ledger_key:
                classified["ledger_key"] = desired_ledger_key

            fixed = _canonicalize_result(classified, norm_type.lower())
            pending_id = str(uuid.uuid4())

            c.execute(
                """INSERT INTO manual_entries(
                       id, user_id, account_id, txn_date, narration, amount, txn_type,
                       desired_ledger_key, source, entered_by, note, fingerprint,
                       duplicate_status, approval_status, linked_pending_id, created_at, updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (entry_id, user_id, account_id, txn_date, narration, amount, norm_type.lower(),
                 desired_ledger_key, "MANUAL", user_id, note, fp,
                 "EXACT_DUPLICATE" if override_duplicate else "NEW", "PENDING", pending_id, now, now)
            )

            c.execute(
                """INSERT INTO pending_ledger(
                        id, user_id, account_id, txn_date, narration, amount, txn_type,
                        predicted_ledger_key, confidence, book, section, grp, account,
                        counterparty, attribution, source, cluster_id, is_anomaly,
                        note, status, created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    pending_id, user_id, account_id, txn_date, narration, amount, norm_type.lower(),
                    fixed.get("ledger_key", "suspense_debit"), float(fixed.get("confidence", 0)),
                    fixed.get("book", "SUSPENSE"), fixed.get("section", "Unclassified"),
                    fixed.get("group", fixed.get("grp", "Unclassified")), fixed.get("account", "Requires Review"),
                    _best_import_counterparty(
                        narration=narration,
                        classified_counterparty=fixed.get("counterparty", ""),
                        existing_counterparty="",
                        user_id=user_id,
                        known_counterparties=self._edb.get_known_counterparties(user_id),
                    ), fixed.get("attribution", ""), "MANUAL",
                    int(fixed.get("cluster_id", -1)), 0,
                    note + f" | Manual Entry ID: {entry_id}", "PENDING", now
                )
            )
            return {"ok": True, "manual_entry_id": entry_id, "pending_id": pending_id}

    def add_review_note(self, user_id: str, note_text: str, message_type: str = "USER",
                        batch_id: str = "", raw_row_id: str = "", pending_id: str = "", ledger_id: str = "") -> str:
        note_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        with self._edb._conn() as c:
            c.execute(
                """INSERT INTO review_notes(id, user_id, related_batch_id, related_raw_row_id,
                                            related_pending_id, related_ledger_id, message_text,
                                            message_type, created_by, status, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (note_id, user_id, batch_id, raw_row_id, pending_id, ledger_id, note_text, message_type, user_id, 'ACTIVE', now)
            )
        return note_id

    def store_pending_transactions(self, user_id: str, records: List[Dict[str, Any]], account_id: str = "main") -> List[str]:
        """Backward-compatible helper used by older /upload flows."""
        ids: List[str] = []
        normalize_parsed_record = _get_normalize_parsed_record()
        classify = _get_classify()
        known_counterparties = self._edb.get_known_counterparties(user_id)
        known_accounts = self._edb.get_known_accounts(user_id)
        user_profile = self._edb.get_user_profile(user_id) or {}

        for rec in records:
            normed = normalize_parsed_record(rec, source=str(rec.get("source") or "excel"))
            classify_text = str(rec.get("description") or rec.get("narration") or normed.get("narration") or "").strip()
            amount = float(normed.get("amount", 0) or 0)
            txn_type = str(normed.get("txn_type") or "debit").lower()
            txn_date = str(normed.get("txn_date") or "")

            result = classify(classify_text, forced_type=txn_type, amount=amount, txn_date=txn_date)
            result = _apply_profile_classification_hints(
                self._edb,
                user_id,
                classify_text,
                result,
                txn_type,
                known_counterparties=known_counterparties,
                known_accounts=known_accounts,
                profile=user_profile,
            )
            fixed = _canonicalize_result(
                result, txn_type,
                parser_txn_type=str(rec.get("txn_type") or "").lower(),
                derived_txn_type=txn_type,
                parser_notes=str(normed.get("parser_notes") or ""),
            )

            fixed["counterparty"] = _best_import_counterparty(
                narration=classify_text,
                classified_counterparty=fixed.get("counterparty", ""),
                existing_counterparty=str(rec.get("counterparty") or ""),
                user_id=user_id,
                known_counterparties=known_counterparties,
            )

            pid = self._edb.store_pending_transaction({
                "user_id": user_id,
                "account_id": account_id,
                "txn_date": txn_date,
                "narration": classify_text,
                "amount": amount,
                **fixed,
            })
            ids.append(pid)
        return ids
    
    def classify_and_stage(
        self,
        user_id: str,
        narration: str,
        amount: float = 0,
        txn_type: Optional[str] = None,
        txn_date: str = "",
    ) -> Dict[str, Any]:
        """
        Classify a single narration and stage it as PENDING.
        Applies the same canonical validation used by batch uploads.
        """
        classify = _get_classify()
        canonical_type = txn_type if txn_type in ("debit", "credit") else "debit"
        result = classify(narration, forced_type=canonical_type, amount=amount, txn_date=txn_date)
        result = _apply_profile_classification_hints(
            self._edb,
            user_id,
            narration,
            result,
            canonical_type,
            known_counterparties=self._edb.get_known_counterparties(user_id),
            known_accounts=self._edb.get_known_accounts(user_id),
            profile=self._edb.get_user_profile(user_id) or {},
        )
        fixed = _canonicalize_result(
            result,
            canonical_type,
            parser_txn_type=txn_type or "",
            derived_txn_type=canonical_type,
        )

        fixed["counterparty"] = _best_import_counterparty(
            narration=narration,
            classified_counterparty=fixed.get("counterparty", ""),
            existing_counterparty="",
            user_id=user_id,
            known_counterparties=self._edb.get_known_counterparties(user_id),
        )

        pid = self._edb.store_pending_transaction({
            "user_id": user_id,
            "txn_date": txn_date,
            "narration": narration,
            "amount": amount,
            **fixed,
        })
        row = self._edb._get_pending_by_id(pid)
        return row or {}
    
    def get_pending_transactions(self, user_id: str) -> List[Dict]:
        return self._edb.get_pending_transactions(user_id)

    def reclassify_transaction(
        self, txn_id: str, new_ledger_key: str
    ) -> Dict[str, Any]:
        return self._edb.reclassify_transaction(txn_id, new_ledger_key)

    def approve_transactions(self, user_id: str) -> int:
        return self._edb.approve_transactions(user_id)

    def discard_pending(self, user_id: str) -> None:
        self._edb.clear_pending(user_id)

    def pending_summary(self, user_id: str) -> Dict[str, Any]:
        """Quick count of pending transactions by status bucket."""
        rows = self._edb.get_pending_transactions(user_id)
        pending = sum(1 for r in rows if r["status"] == "PENDING")
        reclassified = sum(1 for r in rows if r["status"] == "RECLASSIFIED")
        return {
            "total": len(rows),
            "pending": pending,
            "reclassified": reclassified,
        }


# ══════════════════════════════════════════════════════════════════════════════
# 4.  REPORT GENERATORS
# ══════════════════════════════════════════════════════════════════════════════

def _fmt(amount: float, sym: str = "₹") -> str:
    """Format a rupee amount with Indian comma grouping."""
    return f"{sym}{amount:>16,.2f}"


def _bar(char: str = "─", width: int = 88) -> str:
    return char * width


# Canonical counterparty name map.
# Keys are regex patterns (case-insensitive) matched against the raw counterparty
# string produced by _extract_counterparty. First match wins.
# Add new entries here whenever a merchant appears under multiple names.
import re as _re
_COUNTERPARTY_NORMALISE = [
    (_re.compile(r"swiggy",        _re.I), "Swiggy"),
    (_re.compile(r"zomato|eternal",_re.I), "Zomato"),
    (_re.compile(r"amazon",        _re.I), "Amazon"),
    (_re.compile(r"flipkart",      _re.I), "Flipkart"),
    (_re.compile(r"netflix",       _re.I), "Netflix"),
    (_re.compile(r"pvr|inox",      _re.I), "PVR INOX"),
    (_re.compile(r"uber",          _re.I), "Uber"),
    (_re.compile(r"ola\b",         _re.I), "Ola"),
    (_re.compile(r"blinkit",       _re.I), "Blinkit"),
    (_re.compile(r"zepto",         _re.I), "Zepto"),
    (_re.compile(r"bigbasket",     _re.I), "BigBasket"),
    (_re.compile(r"airtel",        _re.I), "Airtel"),
    (_re.compile(r"jio\b",         _re.I), "Jio"),
    (_re.compile(r"irctc",         _re.I), "IRCTC"),
    (_re.compile(r"makemytrip",    _re.I), "MakeMyTrip"),
    (_re.compile(r"npci.?bhim|bhim.?cashback", _re.I), "NPCI BHIM Cashback"),
    (_re.compile(r"l\s*d\s*k\s+(?:shares?|securities?)", _re.I), "LDK Shares"),
    (_re.compile(r"zerodha",       _re.I), "Zerodha"),
    (_re.compile(r"hdfc.?bank|hdfcbank", _re.I), "HDFC Bank"),
    (_re.compile(r"icici.?bank",   _re.I), "ICICI Bank"),
    (_re.compile(r"google.?pay|gpay", _re.I), "Google Pay"),
    (_re.compile(r"paytm",         _re.I), "Paytm"),
    (_re.compile(r"phonepe",       _re.I), "PhonePe"),
    (_re.compile(r"mcdonalds|mcdonald", _re.I), "McDonald's"),
    (_re.compile(r"dominos|domino", _re.I), "Domino's"),
    (_re.compile(r"starbucks",     _re.I), "Starbucks"),
    (_re.compile(r"myntra",        _re.I), "Myntra"),
    (_re.compile(r"nykaa",         _re.I), "Nykaa"),
    (_re.compile(r"bookmyshow",    _re.I), "BookMyShow"),
    (_re.compile(r"spotify",       _re.I), "Spotify"),
    (_re.compile(r"hotstar|disney", _re.I), "Disney+ Hotstar"),
]

def _normalize_counterparty(raw: str) -> str:
    """
    Map raw counterparty strings to canonical names so that e.g.
    'Swiggy Ltd', 'Swiggy Lim', 'SWIGGY' all become 'Swiggy'.
    Falls back to the raw string if no rule matches.
    """
    if not raw or raw == "—":
        return raw
    for pattern, canonical in _COUNTERPARTY_NORMALISE:
        if pattern.search(raw):
            return canonical
    return raw

def _clean_counterparty_name(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return "Unknown"

    s = _normalize_counterparty(s)

    s = re.sub(r'^(?:ACH|NACH)\s+(?:C|D|CR|DR)\s*-\s*', '', s, flags=re.I)
    s = re.sub(r'^IB\s+FUNDS\s+TRANSFER\s+(?:CR|DR)?\s*[-\s]*', '', s, flags=re.I)
    s = re.sub(r'^(?:NEFT|RTGS|IMPS|UPI|BIL/INFT|TPT|WDL|DEP)\s*[-/\s]+', '', s, flags=re.I)

    s = re.sub(r'\b(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b', ' ', s, flags=re.I)
    s = re.sub(r'\b(?:SAL|SALARY|TPT|REPAY|RETURN|GIFT|LOAN|TXFR)\b', ' ', s, flags=re.I)
    s = re.sub(r'\b(?:REF|UTR|TXN|TXNID|RRN|CHQ|CHEQUE|NETBANK|NO|REMARKS|SENT|USING|PAY|FOR|IN)\b', ' ', s, flags=re.I)
    s = re.sub(r'\b(?:HDFC|ICICI|AXIS|YESB|SBIN|SBI|BANK|BANKL|FEDERAL|KOTAK|PUNB)\b', ' ', s, flags=re.I)

    s = re.sub(r'\b\d{6,}\b', ' ', s)
    s = re.sub(r'[@#/|*]+', ' ', s)
    s = re.sub(r'[-_]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip(' -/')
    s = re.sub(r'\bP\s*L\b', 'Private Limited', s, flags=re.I).strip()
    s = re.sub(r'\bPRI\b', 'Private Limited', s, flags=re.I).strip()

    bad = {"ach", "nach", "cr", "dr", "c", "d", "unknown", "unknown party", "tpt", "salary", "sal", "ach credit", "transaction", "bank", "hdfc", "icici", "remarks", "sent using"}
    if s.lower() in bad or len(s) < 3:
        return "Unknown"

    if re.fullmatch(r'(?:Tpt|Salary|Sal|Ach Credit|Unknown|Unknown Party)', s, flags=re.I):
        return "Unknown"

    titled = s.title()
    titled = re.sub(r'\bHuf\b', '', titled)
    titled = re.sub(r'\s+', ' ', titled).strip()
    return titled


def _normalize_known_counterparty_name(raw: str) -> str:
    cleaned = _clean_counterparty_name(raw)
    base = cleaned if cleaned and cleaned != "Unknown" else str(raw or "").strip()
    base = re.sub(r'\s+', ' ', base).strip()
    return base.upper()


def _normalize_known_account_text(raw: str) -> str:
    s = str(raw or "").upper()
    s = re.sub(r'[^A-Z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _match_known_counterparty(
    narration: str,
    *,
    known_counterparties: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    rows = known_counterparties or []
    raw_narr = str(narration or "").strip()
    clean_narr = _normalize_import_narration_for_counterparty(raw_narr)
    haystacks = [
        raw_narr.upper(),
        clean_narr.upper(),
        _normalize_known_counterparty_name(raw_narr),
        _normalize_known_counterparty_name(clean_narr),
    ]
    ranked_rows = sorted(
        rows,
        key=lambda r: len(str(r.get("display_name", "") or "")),
        reverse=True,
    )
    for row in ranked_rows:
        display_name = str(row.get("display_name", "") or "").strip()
        normalized_name = str(row.get("normalized_name", "") or "").strip()
        if not display_name:
            continue
        probes = [display_name.upper()]
        if normalized_name:
            probes.append(normalized_name.upper())
        else:
            probes.append(_normalize_known_counterparty_name(display_name))
        for probe in probes:
            if probe and any(probe in hay for hay in haystacks):
                return row
    return None


def _looks_like_own_account_transfer(
    narration: str,
    known_accounts: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    raw_narr = str(narration or "").strip()
    if not raw_narr or not (known_accounts or []):
        return False
    u = raw_narr.upper()
    if _has_profile_broker_marker(u):
        return False
    if _get_family_loan_checker()(u):
        return False
    if not re.search(r'\b(TRANSFER|TPT|NEFT|RTGS|IMPS|UPI|SELF|OWN\s+ACCOUNT|OWN\s+A/C|INFT|TXFR)\b', u):
        return False
    hay = _normalize_known_account_text(raw_narr)
    for row in known_accounts or []:
        label = _normalize_known_account_text(row.get("account_label", ""))
        institution = _normalize_known_account_text(row.get("institution_name", ""))
        mask = re.sub(r'[^A-Z0-9]+', '', str(row.get("account_mask", "") or "").upper())
        label_hit = bool(label and label in hay)
        inst_hit = bool(institution and institution in hay)
        mask_hit = bool(mask and len(mask) >= 4 and mask in re.sub(r'[^A-Z0-9]+', '', u))
        if (label_hit and inst_hit) or (label_hit and mask_hit) or (inst_hit and mask_hit):
            return True
    return False


def _apply_profile_classification_hints(
    edb: "ExtendedDBStore",
    user_id: str,
    narration: str,
    result: Dict[str, Any],
    forced_type: str,
    *,
    known_counterparties: Optional[List[Dict[str, Any]]] = None,
    known_accounts: Optional[List[Dict[str, Any]]] = None,
    profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out = dict(result or {})
    txn_type = forced_type if forced_type in ("debit", "credit") else str(out.get("txn_type") or "")
    if txn_type not in ("debit", "credit"):
        return out

    current_key = str(out.get("ledger_key") or "")
    raw_narr = str(narration or "").strip()
    clean_narr = _normalize_import_narration_for_counterparty(raw_narr)
    narr_for_rules = " ".join(filter(None, [raw_narr, clean_narr, out.get("counterparty", "")]))
    narr_u = narr_for_rules.upper()
    broker_marker = _has_profile_broker_marker(raw_narr)
    broker_evidence = broker_marker or _has_profile_broker_marker(clean_narr) or _has_profile_broker_marker(out.get("counterparty", ""))
    protected_rule_conflict = bool(_PROFILE_PROTECTED_RULE_CONFLICT_RE.search(narr_u))
    broker_locked_keys = {
        "trading_funds_added", "trading_payout",
        "exp_broker_charges", "exp_broker_dp", "exp_broker_interest",
    }

    counterparties = known_counterparties if known_counterparties is not None else edb.get_known_counterparties(user_id)
    accounts = known_accounts if known_accounts is not None else edb.get_known_accounts(user_id)
    profile_row = profile if profile is not None else (edb.get_user_profile(user_id) or {})
    matched_cp = _match_known_counterparty(
        narr_for_rules,
        known_counterparties=counterparties,
    )
    explicit_self_given_loan = bool(re.search(
        r'\b(LOAN\s+(GIVEN|ADVANCE|ADVANCED)|ADVANCE\s+(GIVEN|PAID)|LENT\s+TO|GIVEN\s+TO)\b',
        narr_u,
    ))
    party_type = str((matched_cp or {}).get("party_type", "") or "").strip().lower()
    relationship = str((matched_cp or {}).get("relationship", "") or "").strip().lower()
    matched_is_broker = bool(matched_cp and (party_type == "broker" or relationship == "broker"))
    broker_cost_line = bool(re.search(
        r'\b(?:CONTRACT\s+(?:BILL|COPY|NOTE)|CONTRACT\s+NOTE|STT|GST|BROKERAGE|'
        r'DP\s+CHARGES?|DEPOSITORY\s+AMC|DEMAT\s+AMC|AMC|'
        r'DELAYED\s+PAYMENT\s+INTEREST|BROKER\s+INTEREST)\b',
        narr_u,
        re.I,
    ))
    broker_cash_movement = bool(
        broker_evidence
        and re.search(
            r'\b(?:LDK|FUNDS?|PAYOUT|PAY\s*OUT|TRANSFER|TRADING\s+ACCOUNT|HOLDING\s+ACCOUNT|'
            r'BROKER|BROKING|NSE|BSE|CLEARING|SETTLEMENT|ZERODHA|KITE|UPSTOX|'
            r'5PAISA|GROWW|IIFL|VENTURA)\b|L\s*D\s*K\s+(?:SHARES?|SECURITIES?)',
            narr_u,
            re.I,
        )
        and not broker_cost_line
    )
    family_loan_evidence = bool(
        matched_cp
        and party_type == "family"
    )
    salary_profile_conflict = bool(re.search(r'\b(?:SALARY|PAYROLL|TPT-SAL|SAL\s+CR|WAGES|STIPEND)\b', narr_u, re.I))

    if broker_evidence:
        if txn_type in ("debit", "credit") and not broker_cost_line and (broker_cash_movement or matched_is_broker):
            out["ledger_key"] = "trading_funds_added" if txn_type == "debit" else "trading_payout"
            out["txn_type"] = txn_type
            out["confidence"] = max(float(out.get("confidence", 0.0) or 0.0), 0.995)
            if matched_cp and str(matched_cp.get("display_name", "") or "").strip():
                out["counterparty"] = str(matched_cp.get("display_name", "") or "").strip()
            out = _append_profile_note(out, "hard_broker_guard=1")
            out = _append_profile_note(out, "broker_priority_override=1")
            if matched_is_broker:
                out = _append_profile_note(out, "known_broker_match=1")
            return out
        if current_key in broker_locked_keys:
            return _append_profile_note(out, "broker_priority_override=1")
        if (
            current_key in _PROFILE_OVERRIDABLE_KEYS or not current_key
        ) and txn_type in ("debit", "credit") and (matched_is_broker or broker_cash_movement):
            out["ledger_key"] = "trading_funds_added" if txn_type == "debit" else "trading_payout"
            out["txn_type"] = txn_type
            out["confidence"] = max(float(out.get("confidence", 0.0) or 0.0), 0.90)
            if matched_cp and str(matched_cp.get("display_name", "") or "").strip():
                out["counterparty"] = str(matched_cp.get("display_name", "") or "").strip()
            out = _append_profile_note(out, "broker_priority_override=1")
            if matched_is_broker:
                out = _append_profile_note(out, "known_broker_match=1")
            return out

    if current_key in _PROFILE_PROTECTED_KEYS:
        locked = _append_profile_note(out, "protected_key_locked=1")
        if broker_evidence and current_key in broker_locked_keys:
            locked = _append_profile_note(locked, "broker_priority_override=1")
        return locked

    employer_name = str(profile_row.get("employer_name", "") or "").strip()
    employer_norm = _normalize_known_counterparty_name(employer_name) if employer_name else ""
    if (
        txn_type == "credit"
        and not broker_evidence
        and (not protected_rule_conflict or salary_profile_conflict)
        and int(profile_row.get("is_salaried", 0) or 0) == 1
        and employer_norm
        and employer_norm in _normalize_known_counterparty_name(" ".join(filter(None, [raw_narr, clean_narr, out.get("counterparty", "")])))
        and _allow_profile_override(current_key, "income_salary")
        and (current_key in _PROFILE_OVERRIDABLE_KEYS or not current_key)
    ):
        out["ledger_key"] = "income_salary"
        out["txn_type"] = "credit"
        out["confidence"] = max(float(out.get("confidence", 0.0) or 0.0), 0.90)
        out["counterparty"] = employer_name
        out["note"] = ((out.get("note", "") or "") + " | profile_hint=known_employer_salary | employer_profile_match=1").strip(" | ")
        return out

    if protected_rule_conflict:
        return out

    if matched_cp:
        display_name = str(matched_cp.get("display_name", "") or "").strip()
        default_key = str(matched_cp.get("default_ledger_key", "") or "").strip()
        if display_name:
            out["counterparty"] = display_name
        if (
            txn_type in ("debit", "credit")
            and (party_type == "broker" or relationship == "broker")
            and _allow_profile_override(current_key, "trading_funds_added" if txn_type == "debit" else "trading_payout")
            and current_key not in _PROFILE_PROTECTED_KEYS
        ):
            out["ledger_key"] = "trading_funds_added" if txn_type == "debit" else "trading_payout"
            out["txn_type"] = txn_type
            out["confidence"] = max(float(out.get("confidence", 0.0) or 0.0), 0.90)
            out["note"] = ((out.get("note", "") or "") + " | profile_hint=known_broker_counterparty | known_broker_match=1 | broker_priority_override=1 | protected_key_locked=1").strip(" | ")
            return out
        if (
            party_type == "family"
            and not explicit_self_given_loan
            and not broker_evidence
            and not matched_is_broker
            and not broker_cash_movement
            and not protected_rule_conflict
            and _is_directionally_compatible_key("liability_loan_outstanding", txn_type)
            and _allow_profile_override(current_key, "liability_loan_outstanding")
            and (current_key in _PROFILE_OVERRIDABLE_KEYS or not current_key)
        ):
            out["ledger_key"] = "liability_loan_outstanding"
            out["txn_type"] = txn_type
            out["confidence"] = max(float(out.get("confidence", 0.0) or 0.0), 0.91)
            out["note"] = ((out.get("note", "") or "") + " | profile_hint=known_family_loan | family_loan_override=1").strip(" | ")
            return out
        if (
            default_key
            and _is_directionally_compatible_key(default_key, txn_type)
            and _allow_profile_override(current_key, default_key)
            and not (default_key == "liability_loan_outstanding" and (broker_evidence or matched_is_broker or broker_cash_movement or protected_rule_conflict))
            and not (default_key == "asset_own_transfer_in" and (broker_evidence or family_loan_evidence or protected_rule_conflict))
            and (current_key in _PROFILE_OVERRIDABLE_KEYS or not current_key)
        ):
            out["ledger_key"] = default_key
            out["txn_type"] = txn_type
            out["confidence"] = max(float(out.get("confidence", 0.0) or 0.0), 0.89)
            out["note"] = ((out.get("note", "") or "") + " | profile_hint=known_counterparty_default").strip(" | ")
            return out

    if (
        _looks_like_own_account_transfer(raw_narr, accounts)
        and not broker_evidence
        and not family_loan_evidence
        and not protected_rule_conflict
        and not explicit_self_given_loan
        and _allow_profile_override(current_key, "asset_own_transfer_in")
        and (current_key in _PROFILE_OVERRIDABLE_KEYS or not current_key)
    ):
        out["ledger_key"] = "asset_own_transfer_in"
        out["txn_type"] = txn_type
        out["confidence"] = max(float(out.get("confidence", 0.0) or 0.0), 0.88)
        out["note"] = ((out.get("note", "") or "") + " | profile_hint=known_own_account_transfer | own_account_override=1").strip(" | ")
        return out
    return out

def _extract_display_party_from_narration(narr: str) -> str:
    s = str(narr or "").strip()
    if not s:
        return "Unknown"

    def _valid(name: str) -> str:
        cleaned = _clean_counterparty_name(name)
        if cleaned in ("Unknown", "—", "Ach", "Nach", "Cr", "Dr", "C", "D", "Tpt", "Salary", "Sal", "Ach Credit", "Unknown Party"):
            return ""
        return cleaned

    tpt_source = re.sub(r'^\d{4}-\d{2}-\d{2}(?:\d{8,})?-', '', s, flags=re.I)
    tpt_source = re.sub(r'^\d{10,}-', '', tpt_source)

    m = re.search(r'^TPT-SAL(?:[\s-]+)?(.+)$', tpt_source, flags=re.I)
    if m:
        remainder = re.sub(r'^(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b[\s-]*', '', (m.group(1) or '').strip(), flags=re.I)
        name = _valid(remainder)
        if name:
            return name

    m = re.search(r'^(?:SALARY|SAL)\s+([A-Z][A-Z0-9 &.\-]+)$', s, flags=re.I)
    if m:
        name = _valid(m.group(1))
        if name:
            return name

    m = re.search(r'^TPT-[^-]+-(.+)$', tpt_source, flags=re.I)
    if m:
        name = _valid(m.group(1))
        if name:
            return name

    m = re.search(r'^IB\s+FUNDS\s+TRANSFER\s+(?:CR|DR)\s*[-\s]*\S+\s*[-–]\s*(.+)$', s, flags=re.I)
    if m:
        name = _valid(m.group(1))
        if name:
            return name

    for pat in (r'^BIL/INFT/[^/]+/(.+)$', r'^VPS/(?:CR|DR)/\d+/(.+)$'):
        m = re.search(pat, s, flags=re.I)
        if m:
            name = _valid(m.group(1))
            if name:
                return name

    for pat in (r'^UPI/(?:DR|CR)/\d+/([^/]+)/', r'^UPI/([^/]+)/[^/]+/'):
        m = re.search(pat, s, flags=re.I)
        if m:
            name = _valid(m.group(1))
            if name:
                return name

    # NEFT / RTGS / IMPS special handling:
    # split tokens, remove bank codes / refs / UTR-like ids, keep only human names
    u = s.upper()
    if any(k in u for k in ("NEFT", "RTGS", "IMPS")):
        parts = [p.strip() for p in re.split(r'[*\/-]+', s) if p.strip()]
        stop_words = {
            "NEFT", "RTGS", "IMPS", "CR", "DR", "DEP", "TFR", "NETBANK", "UPI",
            "HDFC", "ICICI", "AXIS", "YESB", "SBIN", "SBI", "BANK", "BANKL",
            "UTIBR", "UTR", "REF", "TXN", "RRN"
        }
        for p in reversed(parts):
            pu = p.upper().strip()

            # skip ids / refs / bank codes
            if pu in stop_words:
                continue
            if re.fullmatch(r'[A-Z]{2,}\d{6,}', pu):
                continue
            if re.fullmatch(r'[A-Z0-9]{10,}', pu):
                continue
            if re.fullmatch(r'\d{6,}', pu):
                continue

            cleaned = _valid(p)
            if cleaned not in ("Unknown", "—", "Ach", "Nach", "Cr", "Dr", "C", "D"):
                # accept only human-ish names
                if re.search(r'[A-Za-z]', cleaned) and not re.fullmatch(r'[A-Z][a-z]?\d+', cleaned):
                    return cleaned

    derived = _valid(_get_extract_counterparty()(s))
    if derived not in ("Unknown", "—", "Ach", "Nach", "Cr", "Dr", "C", "D"):
        # reject UTR / transaction id style fake names
        if not re.fullmatch(r'[A-Za-z]{2,}\d{6,}', derived.replace(" ", "")):
            return derived

    return "Unknown"

def _looks_like_bad_counterparty(value: str) -> bool:
    s = str(value or "").strip()
    if not s:
        return True

    cleaned = _clean_counterparty_name(s)
    if cleaned in ("", "Unknown", "—", "Ach", "Nach", "Cr", "Dr", "C", "D", "Ach C"):
        return True
    if re.fullmatch(r'(Transaction|Bank|Hdfc|Icici|Remarks|Sent Using)( Bank)?', cleaned, flags=re.I):
        return True

    compact = cleaned.replace(" ", "")
    if re.fullmatch(r'[A-Za-z]{2,}\d{6,}', compact):
        return True
    if re.fullmatch(r'[A-Z0-9]{10,}', compact):
        return True
    if re.fullmatch(r'\d{6,}', compact):
        return True

    return False


def _normalize_import_narration_for_counterparty(narration: str) -> str:
    """
    Import-time cleanup for Excel / PDF / CSV narrations before counterparty extraction.
    This is intentionally more aggressive than report-time display cleanup.
    """
    s = str(narration or "").strip()
    if not s:
        return ""

    # remove obvious transport prefixes but keep merchant/person name
    s = re.sub(r'^(?:WDL\s+TFR|DEP\s+TFR|WDL\s+CASH|DEP\s+CASH)\s+', '', s, flags=re.I)
    s = re.sub(r'^(?:NEFT|RTGS|IMPS)\s+(?:CR|DR)\s*[-/ ]*', '', s, flags=re.I)
    s = re.sub(r'^IB\s+FUNDS\s+TRANSFER\s+(?:CR|DR)\s*[-/ ]*', '', s, flags=re.I)
    s = re.sub(r'^(?:ACH|NACH)\s+(?:C|D|CR|DR)\s*[-/ ]*', '', s, flags=re.I)
    s = re.sub(r'^UPI/(?:DR|CR)/\d+/', 'UPI/', s, flags=re.I)

    # remove long refs / ids / UTR-like garbage
    s = re.sub(r'\b(?:UTR|RRN|REF|TXN|TXNID|CHQ|CHEQUE|NETBANK|TRACE|SEQ)\s*[:#-]?\s*[A-Z0-9-]{6,}\b', ' ', s, flags=re.I)
    s = re.sub(r'\b[A-Z]{2,}\d{6,}\b', ' ', s)
    s = re.sub(r'\b\d{10,}\b', ' ', s)
    s = re.sub(r'(?<![A-Za-z])\d{6,}(?![A-Za-z])', ' ', s)

    # remove VPA tail and masked fragments
    s = re.sub(r'@\S+', ' ', s)
    s = re.sub(r'\b[X*]{2,}\d{3,6}\b', ' ', s)

    # normalize separators
    s = re.sub(r'[_|]+', ' ', s)
    s = re.sub(r'\s*[/\-]+\s*', ' / ', s)
    s = re.sub(r'\s+', ' ', s).strip(' /-')

    return s


def _best_import_counterparty(
    narration: str,
    classified_counterparty: str = "",
    existing_counterparty: str = "",
    user_id: str = "",
    known_counterparties: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Decide the best counterparty to store at import time.
    Priority:
      1. classifier-produced counterparty if clean
      2. explicitly supplied existing counterparty if clean
      3. display extractor from cleaned narration
      4. base extractor from cleaned narration
      5. display extractor from raw narration
      6. cleaned existing/classified fallback
      7. Unknown
    """
    raw_narr = str(narration or "").strip()
    clean_narr = _normalize_import_narration_for_counterparty(raw_narr)
    known_match = _match_known_counterparty(
        raw_narr,
        known_counterparties=known_counterparties,
    )
    known_display_name = str((known_match or {}).get("display_name", "") or "").strip()
    if known_display_name and not _looks_like_bad_counterparty(known_display_name):
        return known_display_name

    candidates = [
        known_display_name,
        classified_counterparty,
        existing_counterparty,
        _extract_display_party_from_narration(clean_narr) if clean_narr else "",
        _get_extract_counterparty()(clean_narr) if clean_narr else "",
        _extract_display_party_from_narration(raw_narr) if raw_narr else "",
        _get_extract_counterparty()(raw_narr) if raw_narr else "",
        _clean_counterparty_name(classified_counterparty),
        _clean_counterparty_name(existing_counterparty),
    ]

    seen = set()
    for c in candidates:
        c = _clean_counterparty_name(c)
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)

        if _looks_like_bad_counterparty(c):
            continue

        # reject generic leftovers that should never be stored as final counterparties
        if re.fullmatch(r'(Salary|Tpt|Ach Credit|Unknown|Unknown Party)', c, flags=re.I):
            continue

        # reject obvious bank-only leftovers
        if re.fullmatch(r'(Hdfc|Icici|Axis|Sbi|Yes|Kotak|Federal)( Bank)?', c, flags=re.I):
            continue

        compact = c.replace(" ", "")
        if re.fullmatch(r'[A-Z0-9]{9,}', compact):
            continue
        if re.fullmatch(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+[A-Za-z ]+', c, flags=re.I):
            continue

        return c

    fallback = _clean_counterparty_name(
        _extract_display_party_from_narration(_normalize_import_narration_for_counterparty(raw_narr))
    )
    return fallback if fallback != "Unknown" else "Unknown"

def _derive_party_name_from_txn(t: Dict[str, Any]) -> str:
    narr = str(t.get("narration") or "").strip()
    cp = str(t.get("counterparty") or "").strip()
    stock_name = str(t.get("stock_name") or "").strip()

    if stock_name:
        cleaned = _clean_counterparty_name(stock_name)
        if cleaned and cleaned != "Unknown":
            return cleaned

    from_narr = _extract_display_party_from_narration(narr)
    if from_narr and from_narr not in ("Unknown", "—", "Ach", "Nach", "Cr", "Dr", "C", "D", "Ach C"):
        return from_narr

    cleaned_cp = _clean_counterparty_name(cp)
    if cleaned_cp and cleaned_cp not in ("Unknown", "—", "Ach", "Nach", "Cr", "Dr", "C", "D", "Ach C"):
        compact = cleaned_cp.replace(" ", "")
        if not re.fullmatch(r'[A-Za-z]{2,}\d{6,}', compact) and not re.fullmatch(r'[A-Z0-9]{10,}', compact):
            return cleaned_cp

    return "Unknown"

def _display_party_name_from_txn(t: Dict[str, Any]) -> str:
    narr = str(t.get("narration") or "").strip()
    cp = str(t.get("counterparty") or "").strip()
    stock_name = str(t.get("stock_name") or "").strip()

    if stock_name:
        cleaned = _clean_counterparty_name(stock_name)
        if cleaned and cleaned != "Unknown":
            return cleaned

    if narr:
        derived = _extract_display_party_from_narration(narr)
        if derived and derived not in ("Unknown", "—", "Ach", "Nach", "C", "Cr", "Dr"):
            return derived

    cleaned_cp = _clean_counterparty_name(cp)
    if cleaned_cp and cleaned_cp not in ("Unknown", "—", "Ach", "Nach", "C", "Cr", "Dr"):
        return cleaned_cp

    return "Unknown"

def _build_party_outstanding_schedule(
    txns: List[Dict[str, Any]],
    ledger_key: str,
    party_role: str = "Party",
) -> List[Dict[str, Any]]:
    """
    Build party wise outstanding schedule from classified ledger rows.

    CREDIT on liability_loan_outstanding  = loan received  = outstanding grows
    DEBIT  on liability_loan_outstanding  = repayment made = outstanding shrinks
    """
    party_map: Dict[str, Dict[str, float]] = {}

    for t in txns:
        if (t.get("ledger_key") or "") != ledger_key:
            continue

        party = (
            t.get("counterparty")
            or _clean_counterparty_name(t.get("counterparty", ""))
            or _extract_display_party_from_narration(t.get("narration", ""))
            or "Unknown party"
        ).strip()

        amt = float(t.get("amount") or 0)
        txn_type = (t.get("txn_type") or "").lower()

        bucket = party_map.setdefault(party, {
            "party": party,
            "received": 0.0,
            "repaid": 0.0,
            "owed": 0.0,
        })

        if txn_type == "credit":
            bucket["received"] += amt
        elif txn_type == "debit":
            bucket["repaid"] += amt

    out = []
    for party, row in party_map.items():
        row["owed"] = row["received"] - row["repaid"]

        # FIX: keep all parties with any loan history, even if net owed is 0 or negative
        if row["received"] > 0 or row["repaid"] > 0:
            out.append(row)

    # sort by absolute economic relevance first
    out.sort(key=lambda r: (-abs(r["owed"]), -r["received"], r["party"]))
    return out

def _get_ledger_rows(edb: ExtendedDBStore, user_id: str) -> List[Dict]:
    return edb.get_ledger_summary(user_id)


def _filter_txns(
    txns: List[Dict],
    date_from: Optional[str],
    date_to: Optional[str],
    account_id: Optional[str],
) -> List[Dict]:
    """Filter raw transaction list by date range and/or bank account."""
    out = []
    for t in txns:
        d = (t.get("txn_date") or "")[:10]
        if date_from and d and d < date_from: continue
        if date_to   and d and d > date_to:   continue
        if account_id and (t.get("account_id") or "main") != account_id: continue
        out.append(t)
    return out


def _filter_rows(
    rows: List[Dict],
    date_from: Optional[str],
    date_to: Optional[str],
    account_id: Optional[str],
    edb: ExtendedDBStore,
    user_id: str,
) -> List[Dict]:
    """
    get_ledger_summary is pre-aggregated — no per-row date.
    Without a filter return it as-is (fast path).
    With a filter, re-aggregate from raw transactions.
    """
    if not date_from and not date_to and not account_id:
        return rows
    all_txns = edb.get_txns(user_id, limit=999999)
    filtered = _filter_txns(all_txns, date_from, date_to, account_id)
    agg: Dict[tuple, Dict] = {}
    for t in filtered:
        key = (
            t.get("book", ""),
            t.get("section", ""),
            t.get("grp", "") or t.get("group", ""),
            t.get("account", ""),
            t.get("txn_type") or "debit",
        )
        if key not in agg:
            agg[key] = {"book": key[0], "section": key[1], "grp": key[2],
                        "account": key[3], "txn_type": key[4],
                        "total": 0.0, "cnt": 0}
        agg[key]["total"] += float(t.get("amount", 0) or 0)
        agg[key]["cnt"]   += 1
    return list(agg.values())


def _period_label(date_from, date_to, account_id):
    if date_from or date_to:
        def fd(d):
            try: return datetime.strptime(d, "%Y-%m-%d").strftime("%d-%b-%Y")
            except: return d or "—"
        label = f"Period: {fd(date_from)} to {fd(date_to)}"
    else:
        label = "All transactions"
    if account_id:
        label += f"  |  Account: {account_id}"
    return label

def _get_statement_period_from_batches(
    edb: ExtendedDBStore,
    user_id: str,
    account_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Return the most relevant batch period that overlaps with the requested date range.
    If the user requested a specific date range, find the batch that best matches.
    """
    with edb._conn() as c:
        sql = """
            SELECT statement_from_date, statement_to_date, created_at
            FROM raw_import_batches
            WHERE user_id=?
              AND COALESCE(statement_from_date,'') <> ''
              AND COALESCE(statement_to_date,'') <> ''
        """
        params: List[Any] = [user_id]

        if account_id:
            sql += " AND account_id=?"
            params.append(account_id)

        # If user specified a date range, find batch that overlaps
        if date_from:
            sql += " AND statement_to_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND statement_from_date <= ?"
            params.append(date_to)

        sql += " ORDER BY created_at DESC, statement_to_date DESC, statement_from_date DESC LIMIT 1"

        row = c.execute(sql, params).fetchone()
        if not row:
            return None, None

        return (
            str(row["statement_from_date"])[:10] if row["statement_from_date"] else None,
            str(row["statement_to_date"])[:10] if row["statement_to_date"] else None,
        )

def _get_opening_balance_from_batches(
    edb: ExtendedDBStore,
    user_id: str,
    account_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Optional[float]:
    """
    Return the most relevant opening balance from imported statement batches.

    Priority:
      1. Batch whose statement period overlaps the selected date range
      2. Same account only if account_id is supplied
      3. Most recent matching batch
    """
    with edb._conn() as c:
        sql = """
            SELECT opening_balance, statement_from_date, statement_to_date, created_at
            FROM raw_import_batches
            WHERE user_id=?
              AND opening_balance IS NOT NULL
        """
        params: List[Any] = [user_id]

        if account_id:
            sql += " AND account_id=?"
            params.append(account_id)

        if date_from:
            sql += " AND COALESCE(statement_to_date, '') >= ?"
            params.append(date_from)

        if date_to:
            sql += " AND COALESCE(statement_from_date, '') <= ?"
            params.append(date_to)

        sql += " ORDER BY created_at DESC, statement_from_date DESC, id DESC LIMIT 1"

        row = c.execute(sql, params).fetchone()
        if not row:
            return None

        try:
            return float(row["opening_balance"])
        except Exception:
            return None

def _get_closing_balance_from_batches(
    edb: ExtendedDBStore,
    user_id: str,
    account_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Optional[float]:
    """Return the statement closing balance with the same batch precedence as opening."""
    with edb._conn() as c:
        sql = """
            SELECT closing_balance, statement_from_date, statement_to_date, created_at
            FROM raw_import_batches
            WHERE user_id=?
              AND closing_balance IS NOT NULL
        """
        params: List[Any] = [user_id]

        if account_id:
            sql += " AND account_id=?"
            params.append(account_id)
        if date_from:
            sql += " AND COALESCE(statement_to_date, '') >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND COALESCE(statement_from_date, '') <= ?"
            params.append(date_to)

        sql += " ORDER BY created_at DESC, statement_from_date DESC, id DESC LIMIT 1"
        row = c.execute(sql, params).fetchone()
        if not row:
            return None
        try:
            return float(row["closing_balance"])
        except Exception:
            return None
        
def _auto_period_from_txns(txns: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    dates = []
    today = datetime.now().strftime("%Y-%m-%d")

    for t in txns:
        d = str(t.get("txn_date") or "").strip()[:10]
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', d):
            continue
        # ignore synthetic fallback dates equal to "today" when parser marked missing date
        note = str(t.get("note") or "")
        if d == today and "missing_date" in note:
            continue
        dates.append(d)

    if not dates:
        return None, None
    return min(dates), max(dates)

def _signed_income_amount(row: Dict[str, Any]) -> float:
    amt = float(row["total"])
    return amt if row.get("txn_type") == "credit" else -amt


def _signed_expense_amount(row: Dict[str, Any]) -> float:
    amt = float(row["total"])
    return amt if row.get("txn_type") == "debit" else -amt

def _extract_first_matching_line(text: str, prefixes: List[str]) -> str:
    for line in (text or "").splitlines():
        s = line.strip()
        for p in prefixes:
            if s.startswith(p):
                return s
    return ""

def _strip_user_facing_debug(text: str) -> str:
    s = str(text or "")
    if not s:
        return ""

    s = re.sub(r'\s*\[TRACE:[^\]]+\]', '', s, flags=re.I)
    debug_patterns = [
        r'parser_txn_type=[^|]+',
        r'derived_txn_type=[^|]+',
        r'classifier_source=[^|]+',
        r'confidence=[^|]+',
        r'initial_ledger_key=[^|]+',
        r'final_ledger_key=[^|]+',
        r'conflict_resolved=[^|]+',
        r'parser_notes=[^|]+',
    ]
    for pat in debug_patterns:
        s = re.sub(r'(?:^|\s*\|\s*)' + pat + r'(?=\s*\||$)', ' | ', s, flags=re.I)

    s = re.sub(r'\s*\|\s*\|\s*', ' | ', s)
    s = re.sub(r'(?:\s*\|\s*){2,}', ' | ', s)
    s = re.sub(r'^\s*\|\s*', '', s)
    s = re.sub(r'\s*\|\s*$', '', s)
    s = re.sub(r'\s{2,}', ' ', s)
    return s.strip()

def _build_summary_report_text(
    edb: ExtendedDBStore,
    user_id: str,
    report_name: str = "",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    account_id: Optional[str] = None,
) -> str:
    """
    Build a compact section-wise summary for PDF export.

    For balance_sheet:
      shows section totals and account lines, without transaction trace rows.

    For income_statement:
      shows income/expense group totals and account lines, without trace rows.

    For blank report_name:
      returns the existing combined short financial summary.
    """
    reports = generate_reports_for_user(edb, user_id, date_from, date_to, account_id)

    def _keep_main_summary_lines(text: str) -> List[str]:
        out = []
        for line in (text or "").splitlines():
            line = _strip_user_facing_debug(line.rstrip())
            s = line.strip()

            # keep key section/account lines
            if s.startswith("◆ "):
                out.append(line)
                continue
            if s.startswith("├ "):
                out.append(line)
                continue

            # keep dotted separators under section headers
            if s and set(s) == {"·"}:
                out.append(line)
                continue

            # keep total lines / balance check / net worth / period
            keep_prefixes = (
                "Period:",
                "TOTAL INCOME",
                "TOTAL EXPENSES",
                "NET INCOME",
                "TOTAL ASSETS",
                "TOTAL LIABILITIES",
                "NET WORTH",
                "BALANCE CHECK",
                "TOTAL REVENUE",
                "TOTAL OPERATING EXPENSES",
                "TOTAL FINANCIAL EXPENSES",
                "TOTAL TAX",
                "NET PROFIT",
            )
            if any(s.startswith(p) for p in keep_prefixes):
                out.append(line)
                continue

        # remove repeated blanks
        cleaned = []
        prev_blank = False
        for line in out:
            blank = (line.strip() == "")
            if blank and prev_blank:
                continue
            cleaned.append(line)
            prev_blank = blank
        return cleaned

    if report_name in ("balance_sheet", "income_statement", "profit_loss"):
        src = reports.get(report_name, "")
        return "\n".join(_keep_main_summary_lines(src))

    # fallback combined short financial summary
    lines = []
    lines.append("=" * 88)
    lines.append("FINANCIAL SUMMARY")
    lines.append("=" * 88)

    income_txt = reports.get("income_statement", "")
    balance_txt = reports.get("balance_sheet", "")
    pl_txt = reports.get("profit_loss", "")

    def _extract_first_matching_line(text: str, prefixes: List[str]) -> str:
        for line in (text or "").splitlines():
            s = _strip_user_facing_debug(line).strip()
            for p in prefixes:
                if s.startswith(p):
                    return s
        return ""

    for src, groups in [
        (income_txt, [["Period:"], ["TOTAL INCOME"], ["TOTAL EXPENSES"], ["NET INCOME"]]),
        (balance_txt, [["TOTAL ASSETS"], ["TOTAL LIABILITIES"], ["NET WORTH"], ["BALANCE CHECK"]]),
        (pl_txt, [["TOTAL REVENUE"], ["NET PROFIT"]]),
    ]:
        for prefixes in groups:
            hit = _extract_first_matching_line(src, prefixes)
            if hit:
                lines.append(hit)

    lines.append("")
    lines.append("Generated from current filtered statements.")
    return "\n".join(lines)

def _compact_pdf_detail_lines(text: str) -> str:
    """
    Keep statement text readable for PDF / export.
    - strip TRACE tags
    - collapse repeated blank lines
    - simplify detail rows to Date | Counterparty | CR/DR | Amount where possible
    """
    out = []
    prev_blank = False

    detail_pat = re.compile(
        r'^\s*[│|]\s*'
        r'(?:(\d{4}-\d{2}-\d{2})\s+)?'
        r'(.+?)'
        r'\s+\|\s+'
        r'(CR|DR)\s+'
        r'((?:₹|Rs\s*)?\s*[-0-9,]+\.\d{2})\s*$',
        re.I
    )

    for line in (text or "").splitlines():
        raw = _strip_user_facing_debug(line.rstrip())

        m = detail_pat.match(raw)
        if m:
            dt = (m.group(1) or "").strip()
            party = re.sub(r'\s+', ' ', (m.group(2) or '').strip())
            party = re.sub(r'\b[A-Z]{2,}\d{6,}\b', ' ', party)
            party = re.sub(r'(?<![A-Za-z])\d{8,}(?![A-Za-z])', ' ', party)
            party = re.sub(r'\s+', ' ', party).strip(' -|/')
            typ = (m.group(3) or '').upper()
            amt = re.sub(r'\s+', ' ', (m.group(4) or '').replace('₹', 'Rs ')).strip()
            raw = f"      |  {dt}  {party} | {typ} {amt}".rstrip()

        blank = (raw.strip() == "")
        if blank and prev_blank:
            continue

        out.append(_strip_user_facing_debug(raw))
        prev_blank = blank

    return "\n".join(out)

def _render_text_pdf_bytes(title: str, text: str) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Preformatted
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER
    except Exception as e:
        raise ValueError(f"PDF library unavailable: {e}")

    def _pdf_safe(s: str) -> str:
        s = str(s or "")
        replacements = {
            "₹": "Rs ",
            "—": "-",
            "–": "-",
            "−": "-",
            "═": "=",
            "─": "-",
            "│": "|",
            "├": "|-",
            "└": "|-",
            "◆": "*",
            "▶": ">",
            "⚠": "!",
            "✅": "[OK]",
            "❌": "[X]",
            "🏦": "[ASSETS]",
            "📜": "[LIABILITIES]",
            "👤": "[EQUITY]",
            "📈": "[INCOME]",
            "💼": "[OPEX]",
            "🧾": "[TAX]",
            "↔": "[CONTRA]",
            "✦": "*",
        }
        for old, new in replacements.items():
            s = s.replace(old, new)
        return s.encode("latin-1", "replace").decode("latin-1")

    title = _strip_user_facing_debug(title)
    text = "\n".join(_strip_user_facing_debug(line) for line in str(text or "").splitlines())

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=28,
        leftMargin=28,
        topMargin=30,
        bottomMargin=28,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        spaceAfter=10,
    )
    body_style = ParagraphStyle(
        "ReportBody",
        parent=styles["BodyText"],
        fontName="Courier",
        fontSize=8.2,
        leading=10,
        spaceAfter=0,
    )

    story = []
    story.append(Paragraph(_pdf_safe(title), title_style))
    story.append(Spacer(1, 6))
    story.append(Preformatted(_pdf_safe(text), body_style))

    doc.build(story)
    return buf.getvalue()

def _send_pdf_bytes(self, filename: str, pdf_bytes: bytes):
    self.send_response(200)
    self.send_header("Content-Type", "application/pdf")
    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    self.send_header("Content-Length", str(len(pdf_bytes)))
    self.send_header("Cache-Control", "no-store")
    self.end_headers()
    self.wfile.write(pdf_bytes)

def _render_text_report_to_pdf_bytes(title: str, text: str) -> bytes:
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    left = 36
    top = height - 36
    line_h = 11
    y = top

    c.setTitle(title)
    c.setFont("Courier", 8.5)

    def new_page():
        nonlocal y
        c.showPage()
        c.setFont("Courier", 8.5)
        y = top

    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip()

        # wrap long lines instead of clipping
        max_chars = 108
        chunks = [line[i:i + max_chars] for i in range(0, len(line), max_chars)] or [""]

        for chunk in chunks:
            if y < 36:
                new_page()
            c.drawString(left, y, chunk)
            y -= line_h

    c.save()
    return buf.getvalue()


# ── 4A: INDIVIDUAL — Income Statement ────────────────────────────────────────

def generate_income_statement(
    edb: ExtendedDBStore, user_id: str,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    account_id: Optional[str] = None,
) -> str:
    """
    Individual Income Statement with optional date range / account filter.
    Loans and advances are excluded from P&L presentation.
    """
    rows = _filter_rows(_get_ledger_rows(edb, user_id),
                        date_from, date_to, account_id, edb, user_id)
    user  = edb.get_user(user_id)
    uname = user["name"] if user else "Account Holder"

    # Actual filtered period
    all_txns = _filter_txns(edb.get_txns(user_id, limit=999999), date_from, date_to, account_id)
    batch_from, batch_to = _get_statement_period_from_batches(
        edb, user_id, account_id, date_from, date_to
    )
    auto_from, auto_to = _auto_period_from_txns(all_txns)

    # PERIOD LABEL FIX:
    # 1. If user applied an explicit date filter, show that exact filter.
    # 2. Otherwise, prefer the batch/header period extracted from the file.
    # 3. Only fall back to transaction min/max if no batch period exists.
    if date_from or date_to:
        period_from, period_to = date_from, date_to
    elif batch_from and batch_to:
        period_from, period_to = batch_from, batch_to
    elif auto_from and auto_to:
        period_from, period_to = auto_from, auto_to
    else:
        period_from, period_to = None, None

    print(
        f"[REPORT PERIOD INPUTS] batch_from={batch_from} batch_to={batch_to} auto_from={auto_from} auto_to={auto_to} date_from={date_from} date_to={date_to}",
        flush=True
    )
    print(f"[REPORT PERIOD FINAL] period_from={period_from} period_to={period_to}", flush=True)
    period_lbl = _period_label(period_from, period_to, account_id)

    # Detail lookup for trace-style rendering
    txn_detail: Dict[tuple, list] = {}
    for t in all_txns:
        if t.get("book") != "INCOME_EXPENSE":
            continue
        sec_key = t.get("section", "")
        grp_key = t.get("grp", "")
        acct_key = t.get("account", "")
        if acct_key:
            txn_detail.setdefault((sec_key, grp_key, acct_key), []).append(t)

    income_by_grp, expense_by_grp, total_income, total_expense, net_income = _compute_income_statement_buckets(rows)

    W = 92
    lines = [
        "=" * W,
        f"{'INDIVIDUAL INCOME STATEMENT  —  DETAILED':^{W}}",
        f"{'Account Holder : ' + uname:^{W}}",
        f"{'Generated      : ' + datetime.now().strftime('%d-%b-%Y  %H:%M'):^{W}}",
        f"{period_lbl:^{W}}",
        "=" * W,
        "",
        f"  INCOME",
        f"  {_bar('-', W-4)}",
    ]

    for grp in sorted(income_by_grp):
        accts = income_by_grp[grp]
        grp_total = sum(accts.values())
        lines.append("")
        lines.append(f"  ◆ {grp.upper():<60} {_fmt(grp_total)}")
        lines.append(f"    {_bar('·', W-6)}")

        for acct in sorted(accts, key=lambda a: -abs(accts[a])):
            acct_amt = accts[acct]
            lines.append(f"    ├ {acct:<60} {_fmt(acct_amt)}  [TRACE:INCOME_EXPENSE:Income:{grp}:{acct}]")

            txns = txn_detail.get(("Income", grp, acct), [])
            txns_sorted = sorted(txns, key=lambda t: t.get("txn_date") or "", reverse=True)
            for t in txns_sorted:
                cpty = _display_party_name_from_txn(t)
                t_amt = float(t.get("amount") or 0)
                direction = "CR" if str(t.get("txn_type") or "").lower() == "credit" else "DR reversal"
                lines.append(
                    f"      │  {cpty:<32}  {direction:<11} {_fmt(t_amt)}"
                )
    lines += [
        "",
        f"  {_bar('─', W-4)}",
        f"  {'TOTAL INCOME':<66} {_fmt(total_income)}",
        "",
        f"  EXPENSES",
        f"  {_bar('-', W-4)}",
    ]

    for grp in sorted(expense_by_grp, key=lambda g: -sum(expense_by_grp[g].values())):
        accts = expense_by_grp[grp]
        grp_total = sum(accts.values())
        lines.append("")
        lines.append(f"  ◆ {grp.upper():<60} {_fmt(grp_total)}")
        lines.append(f"    {_bar('·', W-6)}")

        for acct in sorted(accts, key=lambda a: -abs(accts[a])):
            acct_amt = accts[acct]
            lines.append(f"    ├ {acct:<60} {_fmt(acct_amt)}  [TRACE:INCOME_EXPENSE:Expenditure:{grp}:{acct}]")

            txns = txn_detail.get(("Expenditure", grp, acct), [])
            txns_sorted = sorted(txns, key=lambda t: t.get("txn_date") or "", reverse=True)
            for t in txns_sorted:
                cpty = _display_party_name_from_txn(t)
                t_amt = float(t.get("amount") or 0)
                direction = "DR" if str(t.get("txn_type") or "").lower() == "debit" else "CR reversal"
                lines.append(
                    f"      │  {cpty:<32}  {direction:<11} {_fmt(t_amt)}"
                )

    lines += [
        "",
        f"  {_bar('─', W-4)}",
        f"  {'TOTAL EXPENSES':<66} {_fmt(total_expense)}",
        f"  {_bar('═', W-4)}",
        f"  {'NET INCOME  (Income – Expenses)':<66} {_fmt(net_income)}",
        f"  {_bar('═', W-4)}",
        "",
        f"  {'▶ Positive = Surplus   |   Negative = Deficit for the period.':^{W-4}}",
        "=" * W,
    ]
    return "\n".join(lines)


# ── 4B: INDIVIDUAL — Balance Sheet ───────────────────────────────────────────

def _compute_net_trading_transfer_balance(
    current_assets: Dict[str, float],
    funds_added_account: str,
    payout_account: str,
) -> float:
    funds_added_amt = float(current_assets.get(funds_added_account, 0) or 0)
    payout_amt = float(current_assets.get(payout_account, 0) or 0)
    return funds_added_amt - payout_amt

def _compute_income_statement_buckets(
    rows: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Dict[str, float]], float, float, float]:
    """
    Canonical cash-basis P&L signing:
      income credits are positive; income debits are reversals;
      expense debits are positive; expense credits are reversals.
    """
    LOAN_PNL_EXCLUDE_KEYS = {
        "liability_loan_outstanding",
        "asset_loan_repayment_received",
        "asset_loans_advances_given",
    }
    income_by_grp: Dict[str, Dict[str, float]] = {}
    expense_by_grp: Dict[str, Dict[str, float]] = {}

    for r in rows:
        if r.get("book") != "INCOME_EXPENSE":
            continue
        if r.get("ledger_key") in LOAN_PNL_EXCLUDE_KEYS:
            continue

        amt = float(r.get("total") or 0)
        grp = r.get("grp") or "Other"
        acct = r.get("account") or "Unspecified"
        ttype = str(r.get("txn_type") or "").lower()

        if r.get("section") == "Income":
            signed = amt if ttype == "credit" else -amt
            income_by_grp.setdefault(grp, {})
            income_by_grp[grp][acct] = income_by_grp[grp].get(acct, 0) + signed
        elif r.get("section") == "Expenditure":
            signed = amt if ttype == "debit" else -amt
            expense_by_grp.setdefault(grp, {})
            expense_by_grp[grp][acct] = expense_by_grp[grp].get(acct, 0) + signed

    total_income = sum(v for accts in income_by_grp.values() for v in accts.values())
    total_expense = sum(v for accts in expense_by_grp.values() for v in accts.values())
    return income_by_grp, expense_by_grp, total_income, total_expense, total_income - total_expense

def generate_balance_sheet_individual(
    edb: ExtendedDBStore, user_id: str,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    account_id: Optional[str] = None,
) -> str:
    """
    Individual Balance Sheet / Net Worth Statement with optional date/account filter.
    """
    rows     = _filter_rows(_get_ledger_rows(edb, user_id),
                            date_from, date_to, account_id, edb, user_id)
    user     = edb.get_user(user_id)
    uname    = user["name"] if user else "Account Holder"
    all_txns = _filter_txns(edb.get_txns(user_id, limit=999999),
                             date_from, date_to, account_id)

    # NEW: derive report period from actual filtered transactions
    batch_from, batch_to = _get_statement_period_from_batches(
        edb, user_id, account_id, date_from, date_to
    )
    auto_from, auto_to = _auto_period_from_txns(all_txns)

    # PERIOD LABEL FIX:
    # 1. If user applied an explicit date filter, show that exact filter.
    # 2. Otherwise, prefer the batch/header period extracted from the file.
    # 3. Only fall back to transaction min/max if no batch period exists.
    if date_from or date_to:
        period_from, period_to = date_from, date_to
    elif batch_from and batch_to:
        period_from, period_to = batch_from, batch_to
    elif auto_from and auto_to:
        period_from, period_to = auto_from, auto_to
    else:
        period_from, period_to = None, None

    print(
        f"[REPORT PERIOD INPUTS] batch_from={batch_from} batch_to={batch_to} auto_from={auto_from} auto_to={auto_to} date_from={date_from} date_to={date_to}",
        flush=True
    )
    print(f"[REPORT PERIOD FINAL] period_from={period_from} period_to={period_to}", flush=True)
    period_lbl = _period_label(period_from, period_to, account_id)
    lender_schedule = _build_party_outstanding_schedule(
        all_txns,
        "liability_loan_outstanding",
        "Lender",
    )

    borrower_schedule = _build_party_outstanding_schedule(
        all_txns,
        "asset_loans_advances_given",
        "Borrower",
    )
    # ── Bank balance ──────────────────────────────────────────────────────────
    # The synthetic bank balance is every cash movement recorded in the statement.
    # ALL transactions affect the bank account — whether they are classified as
    # Income/Expense, Balance Sheet assets, or Suspense.
    # Only exclude NON-CASH liability accounts from bank balance.
    # Credit card outstanding and other payables are balance entries — no cash moved.
    # Loan outstanding IS a cash event (money received) — keep it in bank balance.
    NON_CASH_LIABILITY_ACCTS = {
        "Credit Card Outstanding Balance",
        "Other Payables Outstanding",
    }
    BANK_BALANCE_EXCLUDE = NON_CASH_LIABILITY_ACCTS
    batch_opening = _get_opening_balance_from_batches(
        edb,
        user_id,
        account_id=account_id,
        date_from=period_from,
        date_to=period_to,
    )

    manual_opening = float((user or {}).get("opening_balance") or 0)
    effective_opening = batch_opening if batch_opening is not None else manual_opening

    period_bank_net = sum(
        (float(t.get("amount", 0) or 0) if t.get("txn_type") == "credit"
         else -float(t.get("amount", 0) or 0))
        for t in all_txns
        if t.get("account") not in BANK_BALANCE_EXCLUDE
    )
    bank_balance = effective_opening + period_bank_net
    displayed_bank_balance = max(bank_balance, 0.0)
    displayed_period_net = displayed_bank_balance - effective_opening

    # ── Transaction detail lookup: account name → [txn, ...] ─────────────────
    txn_detail: Dict[str, list] = {}
    suspense_detail: Dict[str, list] = {}
    for t in all_txns:
        book = t.get("book", "")
        acct_key = t.get("account", "")
        if not acct_key:
            continue
        if book == "BALANCE_SHEET":
            txn_detail.setdefault(acct_key, []).append(t)
        elif book == "SUSPENSE":
            suspense_detail.setdefault(acct_key, []).append(t)

    # Bank balance detail = all txns except contra AND liability balance entries
    # (Liability rows represent outstanding balances, not cash movements)
    bank_detail = [t for t in all_txns
                   if t.get("account") not in BANK_BALANCE_EXCLUDE]

    # ── Asset signing ─────────────────────────────────────────────────────────
    # DEBIT_POSITIVE: cash goes OUT to create the asset (investment, loan given)
    # CREDIT_POSITIVE: cash comes IN to recover/realise the asset
    # Own transfers are CONTRA — they net to zero and are excluded from assets.
    DEBIT_POSITIVE_ASSETS = {
        "Loans & Advances Given (Receivable)",
        "Capital Work-in-Progress (Land / Plot)",
        "Investment – Mutual Funds (SIP/Lump Sum)",
        "Investment – Equity Shares (Purchased)",
        "Investment – Fixed Deposit (Placed)",
        "Investment – PPF",
        "Investment – NPS",
        "Long-Term Investments – Other",
        "Trading Account – Funds Added",
    }
    CREDIT_POSITIVE_ASSETS = {
        "Advance Received Back",
        "Advance Returned by Party",
        "Loan Repayment Received",
        "Refunds Received (Realised)",
        "Broker Payout Received",
        "Broker / Trading Account Balance",
        "Foreign Currency Inward Remittance",
        "FD Maturity – Principal Returned",
        "MF Redemption – Principal Returned",
        "Equity Sale Proceeds (Principal)",
        "Trading Account – Payout to Bank",
    }
    # Own Account Transfer In is a contra — CR and DR cancel each other out.
    # Show it but don't let it distort the asset total.
    CONTRA_ACCOUNTS = {"Own Account Transfer In (Contra)"}

    # Report-only netting for trading account transfers
    TRADING_FUNDS_ADDED_ACCT = "Trading Account – Funds Added"
    TRADING_PAYOUT_ACCT      = "Trading Account – Payout to Bank"
    TRADING_NET_ACCT         = "Trading Account Receivable (Net)"
    TRADING_NET_LIAB_ACCT    = "Trading Account Payable (Net)"
    TRACE_ACCOUNT_ALIASES = {
        TRADING_NET_ACCT: {
            TRADING_FUNDS_ADDED_ACCT,
            TRADING_PAYOUT_ACCT,
            TRADING_NET_ACCT,
        },
        TRADING_NET_LIAB_ACCT: {
            TRADING_FUNDS_ADDED_ACCT,
            TRADING_PAYOUT_ACCT,
            TRADING_NET_LIAB_ACCT,
        },
    }

    # ── FIX: Equity schedule aggregation ─────────────────────────────────────
    equity_rows = [
        t for t in all_txns
        if t.get("ledger_key") in ("asset_investment_equity", "asset_equity_sale_proceeds")
    ]

    equity_schedule = {}
    for t in equity_rows:
        # Extract stock name from account field if available, otherwise from stock_name column
        acct = t.get("account", "")
        stock_match = re.search(r'Investment\s+–\s+Equity\s+Shares\s*\((.*?)\)\s*$', acct, flags=re.I)
        if stock_match:
            sname = (stock_match.group(1) or "").strip().upper()
        else:
            sname = (t.get("stock_name") or t.get("counterparty") or "UNKNOWN").strip().upper()

        if not sname or sname == "UNKNOWN":
            # Try to extract from narration as fallback
            narr = t.get("narration", "")
            narr_match = re.search(r'\b([A-Z]{2,5})\b', narr)
            if narr_match:
                sname = narr_match.group(1).upper()

        row = equity_schedule.setdefault(sname, {
            "symbol": sname,
            "qty": 0.0,
            "value": 0.0,
            "tds": 0.0,
            "last_price": 0.0,
            "buy_count": 0,
            "sell_count": 0,
        })

        amt = float(t.get("amount", 0) or 0)
        qty = float(t.get("trade_qty", 0) or 0)
        tds = float(t.get("trade_tds", 0) or 0)
        price = float(t.get("trade_price", 0) or 0)
        txn_type = (t.get("txn_type") or "").lower()
        trade_type = (t.get("trade_type") or "").upper()

        # Prefer explicit trade_type when present
        if trade_type in ("BUY", "PURCHASE"):
            row["qty"] += qty if qty > 0 else (amt / price if price > 0 else 1)
            row["value"] += amt
            row["buy_count"] += 1
        elif trade_type in ("SELL", "SALE"):
            row["qty"] -= qty if qty > 0 else (amt / price if price > 0 else 1)
            row["value"] -= amt
            row["sell_count"] += 1
        else:
            if txn_type == "debit":
                # Purchase
                row["qty"] += qty if qty > 0 else (amt / price if price > 0 else 1)
                row["value"] += amt
                row["buy_count"] += 1
            elif txn_type == "credit":
                # Sale
                row["qty"] -= qty if qty > 0 else (amt / price if price > 0 else 1)
                row["value"] -= amt
                row["sell_count"] += 1

        row["tds"] += tds
        if price > 0:
            row["last_price"] = price
        elif row["qty"] != 0 and row["value"] != 0:
            row["last_price"] = abs(row["value"] / row["qty"]) if row["qty"] != 0 else 0

    # ── Populate buckets ──────────────────────────────────────────────────────
    buckets: Dict[str, Dict[str, Dict[str, float]]] = {
        "Assets": {}, "Liabilities": {}, "Equity": {}, "Suspense": {}, "Contra": {}
    }

    # Map granular DB section strings to the four top-level bucket keys.
    # grp still carries the original value (e.g. "Non-Current Liabilities")
    # for display purposes — only sec is normalised for bucket assignment.
    SEC_NORMALISE = {
        "Non-Current Liabilities": "Liabilities",
        "Current Liabilities":     "Liabilities",
        "Current Assets":          "Assets",
        "Non-Current Assets":      "Assets",
        "Owners Equity":           "Equity",
        "Retained Earnings":       "Equity",
        "Equity":                  "Equity",       # FIX C8: direct section label passthrough
        "Liabilities":             "Liabilities",  # FIX C8: direct section label passthrough
        "Assets":                  "Assets",       # FIX C8: direct section label passthrough
    }

    LIABILITY_LIKE_ACCOUNTS = {
        "Loan EMI / Repayment (Paid)",
        "Credit Card Outstanding",
        "Credit Card Payment",
        "Credit Card Bill Payment",
        "Bank Overdraft",
        "Loan Outstanding",
        "Home Loan Outstanding",
        "Personal Loan Outstanding",
        "Vehicle Loan Outstanding",
    }

    for r in rows:
        book = r["book"]
        if book == "BALANCE_SHEET":
            sec  = SEC_NORMALISE.get(r["section"], r["section"])
            grp  = r["grp"]
            acct = r["account"]

            if sec not in buckets:
                continue

            amt   = float(r["total"])
            ttype = r.get("txn_type")

            if sec == "Assets":
                if acct in CONTRA_ACCOUNTS:
                    # Contra: keep for display but put in separate bucket
                    # so it does NOT distort total assets
                    contra_grp = "Own Account Transfers"
                    if contra_grp not in buckets["Contra"]:
                        buckets["Contra"][contra_grp] = {}
                    signed = amt if ttype == "credit" else -amt
                    buckets["Contra"][contra_grp][acct] = (
                        buckets["Contra"][contra_grp].get(acct, 0) + signed
                    )
                    continue  # do NOT add to Assets
                elif acct in DEBIT_POSITIVE_ASSETS or acct.startswith("Investment – Equity Shares"):
                    signed = amt if ttype == "debit" else -amt
                elif acct in CREDIT_POSITIVE_ASSETS:
                    signed = amt if ttype == "credit" else -amt
                else:
                    signed = amt if ttype == "debit" else -amt

            elif sec == "Liabilities":
                # Skip misclassified payment rows — EMI/CC payments that were
                # settled in cash should live in Expenditure, not Liabilities.
                if acct in {
                    "Loan EMI / Repayment (Paid)",
                    "Credit Card Payment (Settled)",
                    "Credit Card Payment",
                    "Credit Card Bill Payment",
                }:
                    continue
                # Outstanding liability balance = always positive (amount you owe).
                # These rows may be stored with txn_type="debit" if auto-classified
                # from a bank debit, but conceptually the figure is a positive liability.
                # We use abs(amt) so the sign is never inverted by a wrong stored direction.
                signed = amt if ttype == "credit" else -amt

            else:  # Equity only in the individual view
                signed = amt if ttype == "credit" else -amt

            if grp not in buckets[sec]:
                buckets[sec][grp] = {}
            buckets[sec][grp][acct] = buckets[sec][grp].get(acct, 0) + signed

        elif book == "SUSPENSE":
            amt    = float(r["total"] or 0)
            ttype  = (r.get("txn_type") or "debit")
            signed = amt if ttype == "credit" else -amt
            grp    = r.get("grp") or "Unclassified"
            acct   = r.get("account") or "Pending Review"
            if grp not in buckets["Suspense"]:
                buckets["Suspense"][grp] = {}
            buckets["Suspense"][grp][acct] = (
                buckets["Suspense"][grp].get(acct, 0) + signed
            )

    # ── Bank balance → always shown under Assets on cash-basis individual view ─────
    # Never synthesize Bank Overdraft here. A temporary negative bank balance should
    # remain a negative asset, not create a synthetic liability. Real liabilities, if any,
    # come only from classified BALANCE_SHEET liability rows.
    ca_grp = "Current Assets"
    if ca_grp not in buckets["Assets"]:
        buckets["Assets"][ca_grp] = {}

    # Clear any prior synthetic bank lines before setting them
    buckets["Assets"][ca_grp].pop("Bank Balance (Cash & Bank)", None)
    buckets["Assets"][ca_grp]["Bank Balance (Cash & Bank)"] = displayed_bank_balance

    # ── P&L net income → Retained Earnings in Equity ─────────────────────────
    pl_income_by_grp, pl_expense_by_grp, pl_income, pl_expense, net_pl = _compute_income_statement_buckets(rows)
    re_grp = "Retained Earnings / Current Period"
    if re_grp not in buckets["Equity"]:
        buckets["Equity"][re_grp] = {}
    buckets["Equity"][re_grp]["Current Period Net Income"] = net_pl

    # Net broker transfer rows into one presentation-only receivable line.
    # Funds added are debit-positive assets; payouts back to bank are credit-positive
    # recoveries of that same receivable. Storage stays unchanged; only the report rolls
    # them up so the asset listing shows a single clean net line.
    current_assets = buckets["Assets"].get("Current Assets", {})
    net_trading_amt = _compute_net_trading_transfer_balance(
        current_assets,
        TRADING_FUNDS_ADDED_ACCT,
        TRADING_PAYOUT_ACCT,
    )

    current_assets.pop(TRADING_FUNDS_ADDED_ACCT, None)
    current_assets.pop(TRADING_PAYOUT_ACCT, None)
    current_assets.pop(TRADING_NET_ACCT, None)
    buckets["Liabilities"].setdefault("Current Liabilities", {}).pop(TRADING_NET_LIAB_ACCT, None)

    trading_alias_has_history = (
        bool(txn_detail.get(TRADING_FUNDS_ADDED_ACCT))
        or bool(txn_detail.get(TRADING_PAYOUT_ACCT))
    )

    if trading_alias_has_history:
        trading_detail = (
            txn_detail.get(TRADING_FUNDS_ADDED_ACCT, []) +
            txn_detail.get(TRADING_PAYOUT_ACCT, [])
        )

    if net_trading_amt > 0.0001:
        current_assets[TRADING_NET_ACCT] = net_trading_amt
        buckets["Liabilities"].setdefault("Current Liabilities", {}).pop(TRADING_NET_LIAB_ACCT, None)
        if trading_alias_has_history:
            txn_detail[TRADING_NET_ACCT] = trading_detail
            txn_detail.pop(TRADING_NET_LIAB_ACCT, None)
    elif net_trading_amt < -0.0001:
        current_assets.pop(TRADING_NET_ACCT, None)
        buckets["Liabilities"].setdefault("Current Liabilities", {})
        buckets["Liabilities"]["Current Liabilities"][TRADING_NET_LIAB_ACCT] = abs(net_trading_amt)
        if trading_alias_has_history:
            txn_detail[TRADING_NET_LIAB_ACCT] = trading_detail
            txn_detail.pop(TRADING_NET_ACCT, None)
    elif trading_alias_has_history:
        txn_detail[TRADING_NET_ACCT] = trading_detail
        txn_detail[TRADING_NET_LIAB_ACCT] = trading_detail

    # ── Section totals ────────────────────────────────────────────────────────
    def _sec_total(sec: str) -> float:
        return sum(v for accts in buckets[sec].values() for v in accts.values())

    # Keep only real outstanding liabilities. Never synthesize Bank Overdraft from
    # the bank balance line, and never allow repayment flows to show as liabilities.
    LIABILITY_EXCLUDE = {
        "Loan EMI / Repayment (Paid)",
        "Credit Card Payment (Settled)",
        "Credit Card Payment",
        "Credit Card Bill Payment",
    }

    def _account_has_report_history(sec: str, acct: str) -> bool:
        detail_src = suspense_detail if sec == "Suspense" else txn_detail
        if detail_src.get(acct):
            return True
        if acct == "Long-Term Loan Outstanding" and lender_schedule:
            return True
        if acct == "Loans & Advances Given (Receivable)" and borrower_schedule:
            return True
        if acct in (TRADING_NET_ACCT, TRADING_NET_LIAB_ACCT) and txn_detail.get(acct):
            return True
        return False

    def _should_render_account(sec: str, acct: str, amount: float) -> bool:
        return abs(float(amount or 0)) > 0.0001 or _account_has_report_history(sec, acct)

    # ── Display normalization: totals follow the rendered sheet values. ──────
    # Keep raw stored rows and txn_detail intact for trace; only report buckets
    # are normalized to avoid misleading negative holdings / owed amounts.
    clean_assets: Dict[str, Dict[str, float]] = {}
    for grp, accts in buckets["Assets"].items():
        for acct, val in accts.items():
            raw_val = float(val or 0)
            if not _should_render_account("Assets", acct, raw_val):
                continue

            display_val = max(raw_val, 0.0)
            clean_assets.setdefault(grp, {})
            clean_assets[grp][acct] = clean_assets[grp].get(acct, 0.0) + display_val
    buckets["Assets"] = clean_assets

    total_assets = _sec_total("Assets")

    # ── Filter liabilities: preserve real history even if net is zero/negative ─
    # On cash basis, bank debits = expenses already paid → belong in Expenditure.
    # Still exclude clearly settled payment rows, but keep presentation visibility
    # for liability accounts whose transaction/schedule history nets to zero.
    FULLY_PAID_INDICATORS = {
        "Loan EMI / Repayment (Paid)",
        "Credit Card Payment (Settled)",
        "Credit Card Payment",
        "Credit Card Bill Payment",
    }

    clean_liabilities = {}
    for grp, accts in buckets["Liabilities"].items():
        for acct, val in accts.items():
            if acct in FULLY_PAID_INDICATORS:
                continue
            if not _should_render_account("Liabilities", acct, float(val or 0)):
                continue
            clean_liabilities.setdefault(grp, {})
            display_val = max(float(val or 0), 0.0)
            clean_liabilities[grp][acct] = clean_liabilities[grp].get(acct, 0) + display_val

    buckets["Liabilities"] = clean_liabilities

    total_liabilities = sum(
        sum(sub.values()) for sub in buckets["Liabilities"].values()
    )
    net_worth         = total_assets - total_liabilities
    rendered_equity_before_recon = _sec_total("Equity")
    equity_recon_diff = net_worth - rendered_equity_before_recon
    EQUITY_RECON_ACCT = "Opening / Unexplained Capital Balance"
    if abs(equity_recon_diff) > 0.01:
        buckets["Equity"].setdefault("Owners Equity", {})
        buckets["Equity"]["Owners Equity"][EQUITY_RECON_ACCT] = (
            buckets["Equity"]["Owners Equity"].get(EQUITY_RECON_ACCT, 0.0) + equity_recon_diff
        )

    total_equity      = _sec_total("Equity")
    total_suspense_signed = _sec_total("Suspense")
    total_suspense_abs = sum(abs(v) for accts in buckets["Suspense"].values() for v in accts.values())
    total_contra      = _sec_total("Contra")
    # Balance check: Assets = Liabilities + Equity
    # Contra and Suspense sit outside this equation and are noted separately.
    balance_diff = total_assets - (total_liabilities + total_equity)
    in_balance   = abs(balance_diff) < 0.01

    suspense_trace_accounts: Dict[str, str] = {}
    if buckets["Suspense"]:
        display_suspense: Dict[str, Dict[str, float]] = {}
        for grp, accts in buckets["Suspense"].items():
            for acct, val in accts.items():
                signed_val = float(val or 0)
                display_acct = "Unclassified Credits" if signed_val >= 0 else "Unclassified Debits"
                if display_acct in display_suspense.get(grp, {}) and acct != suspense_trace_accounts.get(display_acct):
                    display_acct = f"{display_acct} ({acct})"
                display_suspense.setdefault(grp, {})
                display_suspense[grp][display_acct] = display_suspense[grp].get(display_acct, 0.0) + abs(signed_val)
                suspense_trace_accounts[display_acct] = acct
        buckets["Suspense"] = display_suspense

    # ── Render ────────────────────────────────────────────────────────────────
    W = 92
    lines = [
        "=" * W,
        f"{'INDIVIDUAL BALANCE SHEET  /  NET WORTH STATEMENT':^{W}}",
        f"{'Account Holder : ' + uname:^{W}}",
        f"{'Generated      : ' + datetime.now().strftime('%d-%b-%Y  %H:%M'):^{W}}",
        f"{period_lbl:^{W}}",
        "=" * W,
    ]

    section_icons = {"Assets": "🏦", "Liabilities": "📜",
                     "Equity": "👤", "Suspense": "⚠", "Contra": "↔"}

    render_order = ["Assets", "Liabilities", "Equity"]
    if buckets["Contra"]:
        render_order.append("Contra")
    if buckets["Suspense"]:
        render_order.append("Suspense")

    for sec in render_order:
        icon    = section_icons[sec]
        sec_tot = _sec_total(sec)
        lines.append(f"\n  {icon} {sec.upper()}")
        lines.append(f"  {_bar('-', W-4)}")

        if sec == "Liabilities" and not buckets[sec]:
            lines.append("")
            lines.append(f"  ◆ {'CURRENT LIABILITIES':<64} {_fmt(0.0)}")
            lines.append(f"    {_bar('·', W-6)}")
            lines.append(f"    ├ {'No Outstanding Liabilities':<62} {_fmt(0.0)}  [TRACE:BALANCE_SHEET:Liabilities:Current Liabilities:No Outstanding Liabilities]")

        for grp in sorted(buckets[sec]):
            accts     = buckets[sec][grp]
            grp_total = sum(accts.values())
            lines.append(f"")
            lines.append(f"  ◆ {grp.upper():<64} {_fmt(grp_total)}")
            lines.append(f"    {_bar('·', W-6)}")

            render_accts = {
                acct: val for acct, val in accts.items()
                if _should_render_account(sec, acct, val)
            }

            for acct in sorted(render_accts, key=lambda a: -abs(render_accts[a])):
                acct_amt = render_accts[acct]
                # Use the real DB book for each section so /trace filters correctly.
                # Suspense rows live under book=SUSPENSE, not BALANCE_SHEET.
                trace_book = "SUSPENSE" if sec == "Suspense" else "BALANCE_SHEET"

                # Presentation-only sections must trace back to stored sections/accounts
                trace_section = sec
                trace_account = acct

                if sec == "Contra":
                    trace_section = "Assets"
                    trace_account = "Own Account Transfer In (Contra)"
                elif sec == "Suspense":
                    trace_account = suspense_trace_accounts.get(acct, acct)

                if acct == TRADING_NET_ACCT:
                    trace_section = "Assets"

                lines.append(
                    f"    ├ {acct:<62} {_fmt(acct_amt)}"
                    f"  [TRACE:{trace_book}:{trace_section}:{grp}:{trace_account}]"
                )
                if acct == "Current Period Net Income":
                    lines.append("      │")
                    lines.append("      │  COMPUTED INCOME STATEMENT SUMMARY")
                    for income_grp in sorted(pl_income_by_grp):
                        income_total = sum(pl_income_by_grp[income_grp].values())
                        lines.append(f"      │  Income - {income_grp:<34} {_fmt(income_total)}")
                    lines.append(f"      │  {'Total Income':<43} {_fmt(pl_income)}")
                    for expense_grp in sorted(pl_expense_by_grp):
                        expense_total = sum(pl_expense_by_grp[expense_grp].values())
                        lines.append(f"      │  Expenses - {expense_grp:<32} {_fmt(expense_total)}")
                    lines.append(f"      │  {'Total Expenses':<43} {_fmt(pl_expense)}")
                    lines.append(f"      │  {'Net Income':<43} {_fmt(net_pl)}")
                    continue
                if acct == EQUITY_RECON_ACCT:
                    lines.append("      │")
                    lines.append("      │  SYNTHETIC EQUITY RECONCILIATION")
                    lines.append(f"      │  {'Net Worth':<43} {_fmt(net_worth)}")
                    lines.append(f"      │  {'Rendered Equity Before Reconciliation':<43} {_fmt(rendered_equity_before_recon)}")
                    lines.append(f"      │  {'Balancing Capital':<43} {_fmt(equity_recon_diff)}")
                    continue
                if acct == "Long-Term Loan Outstanding" and lender_schedule:
                    lines.append(" |")
                    lines.append(" | LENDER-WISE OUTSTANDING")

                    for s in lender_schedule:
                        party = s.get("party", "Unknown")
                        owed = max(float(s.get("owed", s.get("outstanding", 0)) or 0), 0.0)
                        received = float(s.get("received", 0) or 0)
                        repaid = float(s.get("repaid", 0) or 0)

                        lines.append(
                            f" | {party} | Owed {_fmt(owed, 'Rs ')} "
                            f"(Received {_fmt(received, 'Rs ')} Repaid {_fmt(repaid, 'Rs ')})"
                        )

                    # DO NOT print individual raw transaction rows here
                    continue

                if sec == "Assets" and acct == "Loans & Advances Given (Receivable)" and borrower_schedule:
                    lines.append(f"      │")
                    lines.append(f"      │  PARTY-WISE LOANS / ADVANCES GIVEN")
                    for item in borrower_schedule:
                        party = item.get("party", "Unknown")
                        receivable = max(float(item.get("owed", item.get("outstanding", 0)) or 0), 0.0)
                        given = float(item.get("received", item.get("given", 0)) or 0)
                        received_back = float(item.get("repaid", item.get("received_back", 0)) or 0)

                        lines.append(
                            f"      │  {party:<24} | Receivable {_fmt(receivable)}  "
                            f"(Given {_fmt(given)}  Received Back {_fmt(received_back)})"
                        )

                # ── FIX: Equity holdings display ─────────────────────────────────
                if sec == "Assets" and "Investment – Equity Shares" in acct and equity_schedule:
                    # Filter holdings with non-zero quantity or value
                    active_holdings = {sym: data for sym, data in equity_schedule.items()
                                       if abs(data["qty"]) > 0.0001 or abs(data["value"]) > 0.0001}

                    if active_holdings:
                        lines.append(f"      │")
                        lines.append(f"      │  EQUITY HOLDINGS DETAIL")

                        for sym, data in sorted(active_holdings.items(), key=lambda x: -abs(x[1]["value"])):
                            if sym == "UNKNOWN":
                                continue
                            qty = data["qty"]
                            value = data["value"]
                            avg_price = abs(value / qty) if qty != 0 else 0
                            tds = data["tds"]
                            buy_count = data.get("buy_count", 0)
                            sell_count = data.get("sell_count", 0)

                            lines.append(
                                f"      │  {sym:<12} | Qty: {qty:>8,.2f} | Value: {_fmt(value, '₹ ')} | "
                                f"Avg: {_fmt(avg_price, '₹ ')} | TDS: {_fmt(tds, '₹ ')} | "
                                f"Trades: {buy_count}B/{sell_count}S"
                            )

                    # Also show individual transactions for this account
                    for t in sorted(txn_detail.get(acct, []),
                                    key=lambda t: t.get("txn_date") or "",
                                    reverse=True)[:5]:  # limit to last 5 for readability
                        date = (t.get("txn_date") or "")[:10]
                        stock_name = (t.get("stock_name") or "").strip().upper()
                        cpty = stock_name or _derive_party_name_from_txn(t)
                        t_amt = float(t.get("amount") or 0)
                        direction = "CR" if t.get("txn_type") == "credit" else "DR"
                        qty = float(t.get("trade_qty") or 0)
                        price = float(t.get("trade_price") or 0)
                        tds = float(t.get("trade_tds") or 0)

                        trade_info = []
                        if qty > 0:
                            trade_info.append(f"Qty: {qty:,.2f}")
                        if price > 0:
                            trade_info.append(f"@ {_fmt(price, '₹ ')}")
                        if tds > 0:
                            trade_info.append(f"TDS: {_fmt(tds, '₹ ')}")
                        trade_str = f" | {' | '.join(trade_info)}" if trade_info else ""

                        lines.append(
                            f"      │  {date}  {cpty:<18} | {direction} {_fmt(t_amt)}{trade_str}"
                        )

                    if len(txn_detail.get(acct, [])) > 5:
                        lines.append(f"      │  ... and {len(txn_detail.get(acct, [])) - 5} more transactions")

                # Bank Balance detail
                elif acct == "Bank Balance (Cash & Bank)":
                    # Show opening/net note then all income/expense txns as detail
                    lines.append(
                        f"      │  ↳ Opening {_fmt(effective_opening)}"
                        f"  +  Period Net {_fmt(displayed_period_net)}"
                        f"  =  {_fmt(displayed_bank_balance)}"
                    )
                    # Show last 10 transactions for bank balance
                    for t in sorted(bank_detail,
                                    key=lambda t: t.get("txn_date") or "",
                                    reverse=True)[:10]:
                        date  = (t.get("txn_date") or "")[:10]
                        cpty = _derive_party_name_from_txn(t)
                        t_amt = float(t.get("amount") or 0)
                        direction = "CR" if t.get("txn_type") == "credit" else "DR"
                        lines.append(
                            f"      │  {date}  {cpty:<24} | {direction} {_fmt(t_amt)}"
                        )
                    if len(bank_detail) > 10:
                        lines.append(f"      │  ... and {len(bank_detail) - 10} more transactions")

                else:
                    detail_src = suspense_detail if sec == "Suspense" else txn_detail
                    detail_key = suspense_trace_accounts.get(acct, acct) if sec == "Suspense" else acct
                    txns = detail_src.get(detail_key, [])
                    for t in sorted(txns,
                                    key=lambda t: t.get("txn_date") or "",
                                    reverse=True)[:10]:  # limit to last 10 for readability
                        date  = (t.get("txn_date") or "")[:10]
                        cpty = _derive_party_name_from_txn(t)
                        t_amt = float(t.get("amount") or 0)
                        direction = "CR" if t.get("txn_type") == "credit" else "DR"
                        lines.append(
                            f"      │  {date}  {cpty:<24}  | {direction}  {_fmt(t_amt)}"
                        )
                    if len(txns) > 10:
                        lines.append(f"      │  ... and {len(txns) - 10} more transactions")

        lines.append(f"  {_bar('─', W-4)}")
        total_label = "TOTAL SUSPENSE (PROVISIONAL)" if sec == "Suspense" else "TOTAL " + sec.upper()
        lines.append(f"  {total_label:<66} {_fmt(abs(sec_tot) if sec == 'Suspense' else sec_tot)}")
        lines.append(f"  {_bar('─', W-4)}")

    # ── Net Worth + Balance Check ─────────────────────────────────────────────
    # ── Equity breakdown for net worth note ─────────────────────────────────
    # Family transfers / capital introduced sit in Equity but aren't income.
    equity_retained = sum(
        v for grp, accts in buckets["Equity"].items()
        for acct, v in accts.items()
        if "Net Income" in acct or "Retained" in acct
    )
    equity_capital = total_equity - equity_retained

    # Balance check note: suspense remains outside Assets/Liabilities/Equity.
    suspense_explains_diff = (
        total_suspense_abs > 0.01
        and abs(abs(balance_diff) - total_suspense_abs) < 0.01
    )
    if abs(balance_diff) < 0.01:
        balance_status = "✅ IN BALANCE"
    elif suspense_explains_diff:
        balance_status = (
            f"⚠ Out of balance by {_fmt(balance_diff)}, fully explained by Suspense {_fmt(total_suspense_abs)}. "
            "Reclassify suspense to close."
        )
    else:
        balance_status = f"❌ OUT OF BALANCE — difference not fully explained by suspense ({_fmt(balance_diff)})"

    notes = []
    if total_suspense_abs != 0:
        dominant = "credits" if total_suspense_signed >= 0 else "debits"
        notes.append(
            f"⚠ Suspense {_fmt(total_suspense_abs)} ({dominant}, signed net {_fmt(total_suspense_signed)}) — statements are provisional until reclassified"
        )
    if total_contra != 0:
        notes.append(f"↔ Contra transfers {_fmt(total_contra)} — excluded from Assets total")

    lines += [
        "",
        f"  {_bar('═', W-4)}",
        f"  NET WORTH CALCULATION",
        f"  {_bar('-', W-4)}",
        f"    {'Total Assets                           (A)':<64} {_fmt(total_assets)}",
        f"    {'Less: Total Liabilities                (B)':<64} {_fmt(total_liabilities)}",
        f"    {'NET WORTH  (A – B)':<64} {_fmt(net_worth)}",
        f"  {_bar('-', W-4)}",
        f"    {'Total Equity  (should equal Net Worth) (C)':<64} {_fmt(total_equity)}",
        f"    {'  of which: Current Period Net Income':<64} {_fmt(net_pl)}",
        f"    {'  of which: Capital / Family Transfers':<64} {_fmt(equity_capital)}",
        f"  {_bar('-', W-4)}",
        f"  BALANCE CHECK  A – (B + C)  =  {_fmt(balance_diff)}",
        f"  {balance_status}",
    ]
    if notes:
        for n in notes:
            lines.append(f"  {n}")
    lines += [
        f"  {_bar('═', W-4)}",
        "=" * W,
    ]
    return "\n".join(lines)


# Backward compatible alias used by some callers
def generate_balance_sheet(
    edb: ExtendedDBStore, user_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    account_id: Optional[str] = None,
) -> str:
    return generate_balance_sheet_individual(edb, user_id, date_from, date_to, account_id)


# ── 4C: ORGANISATION — Profit & Loss Statement ───────────────────────────────

def generate_profit_loss(
    edb: ExtendedDBStore, user_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    account_id: Optional[str] = None,
) -> str:
    """
    Organisation Profit & Loss Statement.

    Maps:
        INCOME_EXPENSE / Income       → Revenue
        INCOME_EXPENSE / Expenditure  → Operating / Financial / Tax Expenses
    """
    rows = _filter_rows(_get_ledger_rows(edb, user_id), date_from, date_to, account_id, edb, user_id)
    user = edb.get_user(user_id)
    uname = user["name"] if user else "Organisation"

    # Revenue buckets
    revenue: Dict[str, float] = {}
    # Expense buckets — split into Operating, Financial, Taxation
    operating_exp: Dict[str, float] = {}
    financial_exp: Dict[str, float] = {}
    tax_exp: Dict[str, float] = {}

    FINANCIAL_GROUPS = {"Financial Costs", "Bank & Demat Charges"}
    TAX_GROUPS       = {"Taxation"}

    for r in rows:
        if r["book"] != "INCOME_EXPENSE":
            continue
        amt  = float(r["total"])
        grp  = r["grp"]
        sec  = r["section"]

        if sec == "Income":
            signed = amt if r.get("txn_type") == "credit" else -amt
            revenue[grp] = revenue.get(grp, 0) + signed
        elif sec == "Expenditure":
            signed = amt if r.get("txn_type") == "debit" else -amt
            if grp in TAX_GROUPS:
                tax_exp[grp] = tax_exp.get(grp, 0) + signed
            elif grp in FINANCIAL_GROUPS:
                financial_exp[grp] = financial_exp.get(grp, 0) + signed
            else:
                operating_exp[grp] = operating_exp.get(grp, 0) + signed

    total_revenue  = sum(revenue.values())
    total_op_exp   = sum(operating_exp.values())
    total_fin_exp  = sum(financial_exp.values())
    total_tax      = sum(tax_exp.values())
    total_expenses = total_op_exp + total_fin_exp + total_tax
    ebitda         = total_revenue - total_op_exp
    ebit           = ebitda - total_fin_exp
    net_profit     = ebit - total_tax
    all_txns = _filter_txns(edb.get_txns(user_id, limit=999999), date_from, date_to, account_id)
    batch_from, batch_to = _get_statement_period_from_batches(edb, user_id, account_id, date_from, date_to)
    auto_from, auto_to = _auto_period_from_txns(all_txns)

    # PERIOD LABEL FIX:
    # 1. If user applied an explicit date filter, show that exact filter.
    # 2. Otherwise, prefer the batch/header period extracted from the file.
    # 3. Only fall back to transaction min/max if no batch period exists.
    if date_from or date_to:
        period_from, period_to = date_from, date_to
    elif batch_from and batch_to:
        period_from, period_to = batch_from, batch_to
    elif auto_from and auto_to:
        period_from, period_to = auto_from, auto_to
    else:
        period_from, period_to = None, None

    print(
        f"[REPORT PERIOD INPUTS] batch_from={batch_from} batch_to={batch_to} auto_from={auto_from} auto_to={auto_to} date_from={date_from} date_to={date_to}",
        flush=True
    )
    print(f"[REPORT PERIOD FINAL] period_from={period_from} period_to={period_to}", flush=True)
    period_lbl = _period_label(period_from, period_to, account_id)
    W = 88
    lines = [
        "=" * W,
        f"{'ORGANISATION  —  PROFIT & LOSS STATEMENT':^{W}}",
        f"{'Entity     : ' + uname:^{W}}",
        f"{'Generated  : ' + datetime.now().strftime('%d-%b-%Y  %H:%M'):^{W}}",
        f"{period_lbl:^{W}}",
        "=" * W,
        "",
        f"  📈 REVENUE",
        f"  {_bar('-', W-4)}",
    ]

    for grp, amt in sorted(revenue.items(), key=lambda x: -x[1]):
        lines.append(f"    {'├ ' + grp:<62} {_fmt(amt)}")

    lines += [
        f"  {_bar('─', W-4)}",
        f"  {'TOTAL REVENUE':<64} {_fmt(total_revenue)}",
        "",
        f"  💼 OPERATING EXPENSES",
        f"  {_bar('-', W-4)}",
    ]
    for grp, amt in sorted(operating_exp.items(), key=lambda x: -x[1]):
        lines.append(f"    {'├ ' + grp:<62} {_fmt(amt)}")
    lines += [
        f"  {_bar('─', W-4)}",
        f"  {'TOTAL OPERATING EXPENSES':<64} {_fmt(total_op_exp)}",
        f"  {_bar('─', W-4)}",
        f"  {'EBITDA  (Revenue – Operating Expenses)':<64} {_fmt(ebitda)}",
        "",
        f"  🏦 FINANCIAL EXPENSES",
        f"  {_bar('-', W-4)}",
    ]
    for grp, amt in sorted(financial_exp.items(), key=lambda x: -x[1]):
        lines.append(f"    {'├ ' + grp:<62} {_fmt(amt)}")
    lines += [
        f"  {_bar('─', W-4)}",
        f"  {'TOTAL FINANCIAL EXPENSES':<64} {_fmt(total_fin_exp)}",
        f"  {_bar('─', W-4)}",
        f"  {'EBIT  (EBITDA – Financial Expenses)':<64} {_fmt(ebit)}",
        "",
        f"  🧾 TAXATION",
        f"  {_bar('-', W-4)}",
    ]
    for grp, amt in sorted(tax_exp.items(), key=lambda x: -x[1]):
        lines.append(f"    {'├ ' + grp:<62} {_fmt(amt)}")
    lines += [
        f"  {_bar('─', W-4)}",
        f"  {'TOTAL TAX':<64} {_fmt(total_tax)}",
        "",
        f"  {_bar('═', W-4)}",
        f"  {'NET PROFIT  (EBIT – Tax)':<64} {_fmt(net_profit)}",
        f"  {_bar('═', W-4)}",
        "",
        f"  {'▶ Positive = Profit. Negative = Loss for the period.':^{W-4}}",
        "=" * W,
    ]
    return "\n".join(lines)


# ── 4D: Dispatcher — picks the right reports by user_type ────────────────────

def generate_reports_for_user(
    edb: ExtendedDBStore,
    user_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    account_id: Optional[str] = None,
) -> Dict[str, str]:
    """
    Returns a dict of report_name → report_text based on user_type.

    INDIVIDUAL  → income_statement + balance_sheet
    ORGANISATION → profit_loss

    Important: date/account filters must be forwarded all the way through.
    If this dispatcher ignores them, the statements tab can appear empty or stale
    after a refresh because the frontend always posts those filter fields.
    """
    user = edb.get_user(user_id)
    if not user:
        raise ValueError(f"User {user_id} not found.")

    def _clean(v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        return v or None

    date_from = _clean(date_from)
    date_to = _clean(date_to)
    account_id = _clean(account_id)

    user_type = (user.get("user_type") or "INDIVIDUAL").upper()

    def _guard_report(name: str, fn) -> str:
        try:
            return fn()
        except Exception as e:
            return f"{name} could not be generated.\n\nError: {e}"

    if user_type == "INDIVIDUAL":
        return {
            "income_statement": _guard_report(
                "Income statement",
                lambda: generate_income_statement(edb, user_id, date_from, date_to, account_id),
            ),
            "balance_sheet": _guard_report(
                "Balance sheet",
                lambda: generate_balance_sheet_individual(edb, user_id, date_from, date_to, account_id),
            ),
        }
    elif user_type == "ORGANISATION":
        return {
            "profit_loss": _guard_report(
                "Profit and loss statement",
                lambda: generate_profit_loss(edb, user_id, date_from, date_to, account_id),
            ),
        }
    else:
        raise ValueError(f"Unknown user_type '{user_type}'.")

def generate_yearly_reports_for_user(
    edb: ExtendedDBStore,
    user_id: str,
    account_id: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    txns = _filter_txns(edb.get_txns(user_id, limit=999999), None, None, account_id)

    fy_starts = set()
    for t in txns:
        txn_date = str(t.get("txn_date") or "")[:10]
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', txn_date):
            continue
        year = int(txn_date[:4])
        month = int(txn_date[5:7])
        fy_start = year if month >= 4 else year - 1
        fy_starts.add(str(fy_start))

    years = sorted(fy_starts)

    out: Dict[str, Dict[str, str]] = {}
    for yr in years:
        fy = int(yr)
        y_from = f"{fy}-04-01"
        y_to = f"{fy + 1}-03-31"
        out[yr] = {
            "income_statement": generate_income_statement(edb, user_id, y_from, y_to, account_id),
            "balance_sheet": generate_balance_sheet_individual(edb, user_id, y_from, y_to, account_id),
        }
    return out

# ══════════════════════════════════════════════════════════════════════════════
# 5.  HANDLER MIXIN  —  add to Handler.do_GET / do_POST directly
# ══════════════════════════════════════════════════════════════════════════════
#
# Instead of monkey-patching (which fights Python's module-global _current_user_id),
# this module provides two plain functions:
#
#   handle_get_extension(handler_self, path, current_user_id, workflow, edb)
#   handle_post_extension(handler_self, path, current_user_id, workflow, edb)
#
# Each returns True if it handled the route, False if the caller should fall through.
#
# See hni_integration_patch.py for the exact 3-line additions to hni_accounting_system.py.
# ══════════════════════════════════════════════════════════════════════════════

def handle_get_extension(
    self,
    path: str,
    current_user_id: Optional[str],
    workflow: "ReviewWorkflow",
    edb: "ExtendedDBStore",
) -> bool:
    """
    Handle new GET routes.  Returns True if the route was handled.
    Call from Handler.do_GET BEFORE the existing route checks:

        # top of do_GET, after `def do_GET(self):`
        from hni_ledger_extension import handle_get_extension
        if handle_get_extension(self, self.path, _current_user_id, _wflow, _edb):
            return
    """
    parsed_url = urlparse(path)
    route_path = parsed_url.path
    qs = parse_qs(parsed_url.query)

    def _qp(k: str) -> str:
        vals = qs.get(k, [])
        return vals[0].strip() if vals else ""

    if route_path == "/pending-count":
        if not current_user_id:
            self._json(200, {"count": 0, "summary": {}})
        else:
            summary = workflow.pending_summary(current_user_id)
            self._json(200, {"count": summary["total"], "summary": summary})
        return True

    if route_path == "/profile":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
            return True
        self._json(200, edb.get_user_profile(current_user_id) or {})
        return True

    if route_path == "/profile-status":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
            return True
        is_complete, missing_reason = is_min_profile_complete(current_user_id)
        self._json(200, {
            "ok": True,
            "profile_required": not is_complete,
            "is_complete": is_complete,
            "missing_reason": missing_reason,
        })
        return True

    if route_path == "/known-counterparties":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
            return True
        active_only = (_qp("all") != "1")
        self._json(200, {"rows": edb.get_known_counterparties(current_user_id, active_only=active_only)})
        return True

    if route_path == "/known-accounts":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
            return True
        active_only = (_qp("all") != "1")
        self._json(200, {"rows": edb.get_known_accounts(current_user_id, active_only=active_only)})
        return True

    if route_path == "/pending":
        if not current_user_id:
            self._json(200, [])   # return empty list — frontend handles cleanly
            return True
        try:
            rows = workflow.get_pending_transactions(current_user_id)
            try:
                limit = int((qs.get('limit',[500])[0]) or 500)
            except Exception:
                limit = 500
            if limit <= 0:
                limit = 500
            rows = rows[:min(limit, 2000)]
        except Exception as e:
            print(f"  [pending] DB error: {e}")
            self._json(200, [])
            return True

        # FIX B8: ensure every row has all fields the frontend expects
        _REQUIRED_FIELDS = {
            "id": "", "narration": "", "amount": 0, "txn_type": "debit",
            "predicted_ledger_key": "suspense_debit", "reclassified_key": None,
            "book": "SUSPENSE", "section": "Unclassified", "grp": "Unclassified",
            "account": "Requires Review", "status": "PENDING", "confidence": 0,
            "is_anomaly": 0, "counterparty": "", "source": "", "note": "",
            "txn_date": "", "account_id": "main",
        }
        safe_rows = []
        for row in rows:
            safe_row = dict(_REQUIRED_FIELDS)
            safe_row.update({k: v for k, v in row.items() if v is not None})
            safe_rows.append(safe_row)
        self._json(200, safe_rows)
        return True

    if route_path == "/uploaded-files":
        if not current_user_id:
            self._json(200, {"files": []})
            return True
        try:
            with edb._conn() as c:
                rows = c.execute(
                    """SELECT id, source_file_name, account_id, statement_type, created_at,
                              statement_from_date, statement_to_date, import_status,
                              source_file_hash, staged_rows, approved_rows, file_size_bytes
                       FROM raw_import_batches
                       WHERE user_id=?
                       ORDER BY datetime(COALESCE(created_at, updated_at)) DESC, id DESC""",
                    (current_user_id,),
                ).fetchall()
            files = []
            for r in rows:
                row = dict(r)
                fpath = _find_uploaded_file(row.get("id", ""), row.get("source_file_name", ""))
                files.append({
                    "id": row.get("id", ""),
                    "file_name": row.get("source_file_name") or "Uploaded statement",
                    "account_id": row.get("account_id") or "main",
                    "statement_type": row.get("statement_type") or "bank_statement",
                    "created_at": row.get("created_at") or "",
                    "statement_from_date": row.get("statement_from_date") or "",
                    "statement_to_date": row.get("statement_to_date") or "",
                    "import_status": row.get("import_status") or "",
                    "source_file_hash": row.get("source_file_hash") or "",
                    "staged_rows": int(row.get("staged_rows") or 0),
                    "approved_rows": int(row.get("approved_rows") or 0),
                    "file_size_bytes": int(row.get("file_size_bytes") or 0),
                    "download_available": bool(fpath),
                })
            self._json(200, {"files": files})
        except Exception as e:
            print(f"  [uploaded-files] DB error: {e}", flush=True)
            self._json(500, {"error": str(e), "files": []})
        return True

    if route_path == "/download-uploaded-file":
        if not current_user_id:
            self._text(400, "No active user.")
            return True
        batch_id = _qp("id")
        if not batch_id:
            self._text(400, "Missing uploaded file id.")
            return True
        try:
            with edb._conn() as c:
                row = c.execute(
                    """SELECT id, source_file_name
                       FROM raw_import_batches
                       WHERE id=? AND user_id=?""",
                    (batch_id, current_user_id),
                ).fetchone()
            if not row:
                self._text(404, "Uploaded file not found.")
                return True
            row = dict(row)
            fpath = _find_uploaded_file(row.get("id", ""), row.get("source_file_name", ""))
            if not fpath:
                self._text(404, "Uploaded file bytes are not available for this older import.")
                return True
            import mimetypes
            fname = os.path.basename(row.get("source_file_name") or f"uploaded_statement{_safe_upload_ext(fpath)}")
            mime, _ = mimetypes.guess_type(fname)
            mime = mime or "application/octet-stream"
            with open(fpath, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._text(500, f"Download failed: {e}")
        return True

    if route_path == "/financial-reports":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
        else:
            try:
                payload = self._body() if getattr(self, "command", "").upper() == "POST" else {}
                date_from = (payload.get("date_from") or _qp("date_from") or "").strip() or None
                date_to = (payload.get("date_to") or _qp("date_to") or "").strip() or None
                account_id = (payload.get("account_id") or _qp("account_id") or "").strip() or None

                reports = generate_reports_for_user(
                    edb,
                    current_user_id,
                    date_from,
                    date_to,
                    account_id
                )
                self._json(200, reports)
            except Exception as e:
                import traceback; traceback.print_exc()
                self._json(500, {"error": str(e)})
        return True

    if route_path == "/financial-reports-yearly":
        if not current_user_id:
            self._json(400, {"error": "No active user."})
            return True
        try:
            account_id = _qp("account_id") or None
            yearly = generate_yearly_reports_for_user(edb, current_user_id, account_id)
            self._json(200, yearly)
        except Exception as e:
            self._json(500, {"error": str(e)})
        return True

    if route_path == "/trace":
        if not current_user_id:
            self._json(400, {"error": "No active user."})
            return True

        book    = _qp("book")
        section = _qp("section")
        grp     = _qp("grp")
        account = _qp("account")
        account_aliases = set()
        ledger_key_aliases = set()
        grp_aliases = set()
        if account:
            account_aliases.add(account)
        if grp:
            grp_aliases.add(grp)

        # Presentation aliases -> stored accounts
        TRACE_ACCOUNT_ALIASES = {
            "Trading Account Receivable (Net)": {
                "Trading Account – Funds Added",
                "Trading Account – Payout to Bank",
                "Trading Account Receivable (Net)",
            },
            "Trading Account Payable (Net)": {
                "Trading Account – Funds Added",
                "Trading Account – Payout to Bank",
                "Trading Account Payable (Net)",
            },
        }
        if account in TRACE_ACCOUNT_ALIASES:
            account_aliases.update(TRACE_ACCOUNT_ALIASES[account])
            if account == "Trading Account Payable (Net)":
                grp_aliases.update({"Assets", "Current Assets", "Liabilities", "Current Liabilities"})

        if account == "Own Account Transfer In (Contra)":
            account_aliases.update({
                "Own Account Transfer In (Contra)",
            })
            ledger_key_aliases.update({
                "asset_own_transfer_in",
            })
            grp_aliases.update({
                "Contra",
                "Own Account Transfers",
                "Current Assets",
                "Assets",
            })
        date_from = _qp("date_from") or None
        date_to = _qp("date_to") or None
        account_id = _qp("account_id") or None

        conn = sqlite3.connect(edb._db.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        all_rows = conn.execute(
            "SELECT * FROM ledger WHERE user_id=? ORDER BY txn_date DESC",
            (current_user_id,)
        ).fetchall()
        conn.close()
        all_rows = [dict(r) for r in all_rows]
        all_rows = _filter_txns(all_rows, date_from, date_to, account_id)

        # Synthetic Bank Balance = every real cash-movement transaction with signed impact.
        if account in ("Bank Balance", "Bank Balance (Cash & Bank)"):
            bank_trace_exclude = {
                "Credit Card Outstanding Balance",
                "Other Payables Outstanding",
            }
            result = []
            for r in all_rows:
                if r.get("account", "") in bank_trace_exclude:
                    continue
                amt    = float(r.get("amount", 0) or 0)
                signed = amt if r.get("txn_type") == "credit" else -amt
                result.append({
                    "id":          r.get("id", ""),
                    "ledger_key":  r.get("ledger_key", ""),
                    "txn_date":    r.get("txn_date", ""),
                    "narration":   r.get("narration", ""),
                    "description": r.get("narration", ""),
                    "amount":      amt,
                    "signed":      signed,
                    "txn_type":    r.get("txn_type", ""),
                    "ledger_key":  r.get("ledger_key", ""),
                    "book":        r.get("book", ""),
                    "section":     r.get("section", ""),
                    "grp":         r.get("grp", ""),
                    "account":     r.get("account", ""),
                    "counterparty": r.get("counterparty", ""),
                    "confidence":  r.get("confidence", 0),
                    "source":      r.get("source", ""),
                    "note":        _strip_user_facing_debug(r.get("note", "")),
                })
            running = 0.0
            for row in reversed(result):
                running += row["signed"]
                row["running_balance"] = round(running, 2)
            self._json(200, {
                "transactions": result,
                "count": len(result),
                "synthetic": True,
                "editable": False,
                "can_reclassify": False,
                "can_save_rule": False,
                "trace_kind": "computed_income_summary",
            })
            return True

        if account == "Current Period Net Income":
            agg: Dict[tuple, Dict[str, Any]] = {}
            for r in all_rows:
                if r.get("book", "") != "INCOME_EXPENSE":
                    continue
                key = (
                    r.get("book", ""),
                    r.get("section", ""),
                    r.get("grp", "") or r.get("group", ""),
                    r.get("account", ""),
                    r.get("txn_type") or "debit",
                )
                if key not in agg:
                    agg[key] = {
                        "book": key[0], "section": key[1], "grp": key[2],
                        "account": key[3], "txn_type": key[4],
                        "total": 0.0, "cnt": 0,
                    }
                agg[key]["total"] += float(r.get("amount", 0) or 0)
                agg[key]["cnt"] += 1
            income_by_grp, expense_by_grp, total_income, total_expense, net_income = _compute_income_statement_buckets(list(agg.values()))

            result = []

            def _synthetic_row(row_id: str, label: str, amount: float, signed: float, section_name: str, grp_name: str) -> Dict[str, Any]:
                return {
                    "id":          row_id,
                    "ledger_key":  "synthetic_current_period_net_income",
                    "txn_date":    "",
                    "narration":   label,
                    "description": label,
                    "amount":      abs(float(amount or 0)),
                    "signed":      float(signed or 0),
                    "txn_type":    "computed",
                    "book":        "BALANCE_SHEET",
                    "section":     section_name,
                    "grp":         grp_name,
                    "account":     "Current Period Net Income",
                    "counterparty": "Computed from income statement",
                    "confidence":  1.0,
                    "source":      "synthetic",
                    "note":        "Synthetic subtotal; raw dividend/salary/expense rows remain traceable in the Income Statement.",
                    "synthetic":    True,
                    "editable":     False,
                    "can_reclassify": False,
                    "can_save_rule": False,
                    "trace_kind":   "computed_income_summary",
                }

            for grp_name in sorted(income_by_grp):
                subtotal = sum(income_by_grp[grp_name].values())
                result.append(_synthetic_row(f"income-{grp_name}", f"Income subtotal: {grp_name}", subtotal, subtotal, "Income", grp_name))
            result.append(_synthetic_row("total-income", "Total Income", total_income, total_income, "Income", "Total Income"))
            for grp_name in sorted(expense_by_grp):
                subtotal = sum(expense_by_grp[grp_name].values())
                result.append(_synthetic_row(f"expense-{grp_name}", f"Expense subtotal: {grp_name}", subtotal, -subtotal, "Expenditure", grp_name))
            result.append(_synthetic_row("total-expenses", "Total Expenses", total_expense, -total_expense, "Expenditure", "Total Expenses"))
            result.append(_synthetic_row("net-income", "Net Income", net_income, net_income, "Equity", "Retained Earnings / Current Period"))
            self._json(200, {"transactions": result, "count": len(result), "synthetic": True})
            return True

        if account == "Opening / Unexplained Capital Balance":
            result = [{
                "id":          "equity-reconciliation",
                "ledger_key":  "synthetic_equity_reconciliation",
                "txn_date":    "",
                "narration":   "Opening / Unexplained Capital Balance",
                "description": "Opening / Unexplained Capital Balance",
                "amount":      0.0,
                "signed":      0.0,
                "txn_type":    "computed",
                "book":        "BALANCE_SHEET",
                "section":     "Equity",
                "grp":         "Owners Equity",
                "account":     "Opening / Unexplained Capital Balance",
                "counterparty": "Computed balance sheet reconciliation",
                "confidence":  1.0,
                "source":      "synthetic",
                "note":        "Report-only equity reconciliation line; no ledger transaction exists.",
                "synthetic":    True,
                "editable":     False,
                "can_reclassify": False,
                "can_save_rule": False,
                "trace_kind":   "equity_reconciliation",
            }]
            self._json(200, {
                "transactions": result,
                "count": len(result),
                "synthetic": True,
                "editable": False,
                "can_reclassify": False,
                "can_save_rule": False,
                "trace_kind": "equity_reconciliation",
            })
            return True

        # Normal bucket trace — filter by whatever params were passed
        def _section_match(row_section: str, wanted: str) -> bool:
            row_section = (row_section or "").strip()
            wanted = (wanted or "").strip()
            if not wanted:
                return True
            if row_section == wanted:
                return True
            aliases = {
                'Assets': {'Assets', 'Current Assets', 'Non-Current Assets'},
                'Liabilities': {'Liabilities', 'Current Liabilities', 'Non-Current Liabilities'},
                'Equity': {'Equity', 'Owners Equity', 'Retained Earnings'},
                'Suspense': {'Suspense'},
                'Contra': {'Contra', 'Assets', 'Current Assets'},
            }
            if account == "Trading Account Payable (Net)" and wanted == "Liabilities":
                return row_section in {'Assets', 'Current Assets', 'Liabilities', 'Current Liabilities'}
            return row_section in aliases.get(wanted, {wanted})

        matched = []
        for r in all_rows:
            if book and r.get("book", "") != book:
                continue
            if not _section_match(r.get("section", ""), section):
                continue
            if grp_aliases:
                if r.get("grp", "") not in grp_aliases and r.get("section", "") not in grp_aliases:
                    continue
            elif grp and (r.get("grp", "") != grp and r.get("section", "") != grp):
                continue
            if account_aliases:
                if r.get("account", "") not in account_aliases and r.get("ledger_key", "") not in ledger_key_aliases:
                    continue
            matched.append({
                "id":          r.get("id", ""),
                "ledger_key":  r.get("ledger_key", ""),
                "txn_date":    r.get("txn_date", ""),
                "narration":   r.get("narration", ""),
                "description": r.get("narration", ""),
                "amount":      float(r.get("amount", 0) or 0),
                "txn_type":    r.get("txn_type", ""),
                "ledger_key":  r.get("ledger_key", ""),
                "book":        r.get("book", ""),
                "section":     r.get("section", ""),
                "grp":         r.get("grp", ""),
                "account":     r.get("account", ""),
                "counterparty": r.get("counterparty", ""),
                "confidence":  r.get("confidence", 0),
                "source":      r.get("source", ""),
                "note":        _strip_user_facing_debug(r.get("note", "")),
            })
        self._json(200, {"transactions": matched, "count": len(matched), "synthetic": False})
        return True

    if route_path == "/ledger-dictionary":
        """
        Returns a structured ledger dictionary explaining every ledger key
        grouped by Book → Section → Group, with human-readable descriptions.
        No active user required — this is reference data.
        """
        live_ledger_map = _get_ledger_map()
        print("DICT SIZE:", len(live_ledger_map))
        if not live_ledger_map:
            live_ledger_map = LEDGER_MAP
        live_attr = _get_live_attribution()

        DESCRIPTIONS = {
            # Balance Sheet – Assets – Current
            "asset_advance_received_back":   "Money you previously gave as an advance (e.g. to a vendor or friend) has been returned to you. This reduces your receivables.",
            "asset_advance_returned":        "An advance you paid was refunded by the other party. Your cash increases and the receivable is settled.",
            "asset_loan_repayment_received": "A loan you gave to someone has been partly or fully repaid. Your 'Loans Receivable' asset decreases as cash comes back.",
            "asset_loans_advances_given":    "Cash you lent out or paid as an advance. It sits as a receivable (someone owes you) until repaid.",
            "asset_own_transfer_in":         "A credit from your own account in another bank. No income here — it is a contra entry (money just moving between your accounts).",
            "asset_refund_received":         "A refund from a merchant, UPI reversal, or cashback. Increases your cash; no P&L effect since the original expense was already recorded.",
            "asset_broker_payout":           "Your broker is releasing trade proceeds (sale value) to your bank. Converts broker-balance asset to cash.",
            "asset_broker_balance":          "Funds sitting in your broker / trading account, not yet paid out. A current asset until transferred.",
            "asset_fcy_inward":              "Foreign currency received as an inward wire remittance (e.g. overseas client payment). Converted to INR at prevailing rate.",
            "asset_fd_maturity":             "The principal of a Fixed Deposit has been returned on maturity. Interest is recorded separately under income.",
            "asset_mf_redemption":           "Principal portion returned when you redeem Mutual Fund units. Capital gain/loss is recorded separately under income.",
            "asset_equity_sale_proceeds":    "Cash received when you sell shares. Principal is an asset inflow; capital gain is recorded under income.",
            # Balance Sheet – Assets – Non-Current
            "asset_land_cwip":               "Payment made towards land purchase or ongoing construction (Capital Work-in-Progress). Long-term asset until project completion.",
            "asset_investment_mf":           "Money deployed into a Mutual Fund (SIP or lump-sum). Long-term asset that grows over time.",
            "asset_investment_equity":       "Equity shares purchased. A non-current asset reflecting ownership in companies.",
            "asset_investment_fd":           "A Fixed Deposit placed with a bank. Earns interest and matures on a fixed date.",
            "asset_investment_ppf":          "Contribution to Public Provident Fund — a tax-advantaged government-backed long-term savings scheme.",
            "asset_investment_nps":          "National Pension Scheme contribution. Locked in until retirement; eligible for income-tax deduction.",
            "asset_long_term_other":         "Other long-term investments not covered by specific categories above.",
            # Balance Sheet – Liabilities
            "liability_loan_outstanding":    "Outstanding principal on a long-term loan (home, car, personal) still owed to the lender.",
            "liability_credit_card":         "Outstanding credit card balance still unpaid. The liability exists until you settle the bill.",
            "liability_other":               "Other outstanding payable amounts not covered by the specific liability keys above.",
            # Balance Sheet – Equity
            "equity_capital_introduced":     "Fresh capital brought into your personal accounts (e.g. gift from parents, inheritance). Increases net worth without being income.",
            "equity_retained_earnings":      "Cumulative net income retained over time. Automatically calculated as Income minus Expenditure.",
            # Income & Expenditure – Expenditure – Financial Costs
            "exp_loan_emi":                  "Loan EMI or repayment already paid from your bank account (cash basis — it is an expense, not a remaining liability).",
            "exp_credit_card":               "Credit card bill payment already settled from your bank account (cash basis).",
            # Income & Expenditure – Income
            "income_salary":                 "Gross salary or wages credited by your employer, including arrears and bonuses.",
            "income_professional":           "Fees received for freelance, consulting, or professional services rendered.",
            "income_rental":                 "Rent received from tenants for residential or commercial property you own.",
            "income_dividend":               "Dividend declared by a company or mutual fund and credited to your account.",
            "income_interest":               "Interest earned on Savings Account, Fixed Deposits, or bonds.",
            "income_capital_gains":          "Profit realised on sale of equity shares, mutual fund units, or other capital assets.",
            "income_other":                  "Miscellaneous income not fitting the above categories (prize money, one-off receipts, etc.).",
            "income_gift_family":            "Money transferred by a family member. Treated as capital introduced (equity), not taxable income in most cases.",
            "income_inward_payment":         "Unidentified credit. Parked in suspense until you confirm the nature (loan, payment, gift, etc.).",
            # Income & Expenditure – Expenditure
            "exp_food":                      "Spending on restaurants, cafes, food delivery apps (Swiggy, Zomato).",
            "exp_grocery":                   "Supermarket and quick-commerce purchases (BigBasket, Blinkit, Zepto).",
            "exp_household":                 "General household supplies — cleaning products, kitchenware, small appliances.",
            "exp_shopping_online":           "Online retail purchases (Amazon, Flipkart, Myntra, Meesho) not in a more specific category.",
            "exp_clothing":                  "Clothes, footwear, and fashion accessories.",
            "exp_personal_care":             "Salon, spa, cosmetics, personal grooming products.",
            "exp_gifts":                     "Gifts purchased for others, greeting cards, stationery.",
            "exp_health":                    "Doctor fees, pharmacy purchases, diagnostic tests, health insurance OPD claims.",
            "exp_travel":                    "Cab, auto, metro, bus, air and rail tickets, hotel bookings.",
            "exp_entertainment":             "OTT subscriptions (Netflix, Prime, Hotstar), cinema, events, gaming.",
            "exp_education":                 "School/college fees, online courses, tuition, exam registrations.",
            "exp_home_decor":                "Furniture, home décor items, furnishings (IKEA, Pepperfry, Urban Ladder).",
            "exp_maintenance":               "Plumber, electrician, carpenter, AMC contracts for home appliances.",
            "exp_utilities":                 "Electricity, water, gas (PNG/LPG), broadband, mobile recharge bills.",
            "exp_staff_wages":               "Salary/wages paid to domestic staff — cook, driver, housekeeping.",
            "exp_consultant":                "Professional fee paid to a consultant, CA, lawyer, or freelancer.",
            "exp_bank_charges":              "Bank service charges, SMS fees, NACH/NEFT transaction charges.",
            "exp_broker_charges":            "Brokerage, STT (Securities Transaction Tax), GST on trade.",
            "exp_broker_dp":                 "Annual Depository Participant (DP) charges from CDSL/NSDL.",
            "exp_broker_interest":           "Interest charged by broker for delayed payment on margin trades.",
            "exp_insurance":                 "Life, health, vehicle, or home insurance premium payments.",
            "exp_tax":                       "Direct tax payments — Advance Tax, Self-Assessment Tax, GST paid to government.",
            "exp_personal_transfer":         "Money sent to family or friends (not a loan, not a gift capital — a routine transfer).",
            "exp_misc":                      "Miscellaneous debits that do not fit any specific category. Review periodically.",
            "exp_cheque_misc":               "Cheque payments whose purpose is unclear. Reclassify once identified.",
            "exp_fcy_outward":               "Outward foreign currency remittance — overseas tuition, travel, investment.",
            # Suspense
            "suspense_credit":               "Unclassified inward credit. Requires manual review to determine correct book/section.",
            "suspense_debit":                "Unclassified outward debit. Requires manual review to determine correct book/section.",
        }

        dictionary = []
        seen_groups: Dict[Tuple, int] = {}

        for key, (book, section, grp, account) in live_ledger_map.items():
            combo = (book, section, grp)
            if combo not in seen_groups:
                seen_groups[combo] = len(dictionary)
                dictionary.append({
                    "book":    book,
                    "section": section,
                    "group":   grp,
                    "entries": [],
                })
            dictionary[seen_groups[combo]]["entries"].append({
                "key":         key,
                "account":     account,
                "description": DESCRIPTIONS.get(key, ""),
                "attribution": live_attr.get(key, ""),
            })

        self._json(200, {
            "dictionary": dictionary,
            "total_keys": len(live_ledger_map),
            "groups": len(dictionary)
        })
        return True

    if route_path == "/accounts":
        if not current_user_id:
            self._json(400, {"error": "No active user."})
            return True
        self._json(200, edb.get_accounts(current_user_id))
        return True

    if route_path == "/custom-rules":
        if not current_user_id:
            self._json(200, [])
            return True
        self._json(200, edb.get_custom_rules(current_user_id))
        return True

    # ── /trading-cashflow (GET) ───────────────────────────────────────────────
    if route_path == "/trading-cashflow":
        if not current_user_id:
            self._json(400, {"error": "No active user."})
            return True
        date_from  = (qs.get("date_from",  [None])[0]) or None
        date_to    = (qs.get("date_to",    [None])[0]) or None
        account_id = (qs.get("account_id", [None])[0]) or None
        try:
            report  = generate_trading_cashflow(edb, current_user_id,
                                                date_from, date_to, account_id)
            summary = _build_trading_cashflow_summary(edb, current_user_id,
                                                      date_from, date_to, account_id)
            self._json(200, {
                "report": report,
                "summary": summary,
                "reconciled": summary.get("reconciliation_ok", False),
            })
        except Exception as exc:
            self._json(500, {"error": str(exc)})
        return True

    if route_path == "/download-report-pdf":
        if not current_user_id:
            self._text(400, "No active user.")
            return True

        kind = (_qp("kind") or "detailed").strip().lower()
        report_name = (_qp("report") or "").strip().lower()
        date_from = (_qp("date_from") or "").strip() or None
        date_to = (_qp("date_to") or "").strip() or None
        account_id = (_qp("account_id") or "").strip() or None
        year = (_qp("year") or "").strip()

    
        if year:
            fy = int(year)
            date_from = f"{fy}-04-01"
            date_to = f"{fy + 1}-03-31"

        try:
            reports = generate_reports_for_user(
                edb,
                current_user_id,
                date_from,
                date_to,
                account_id,
            )

            text = ""
            filename = "financial_report.pdf"

            # ── SUMMARY PDF ───────────────────────────────────────────────
            if kind == "summary":
                if report_name == "income_statement":
                    filename = f"income_statement_{year or 'all'}_summary.pdf"
                elif report_name == "balance_sheet":
                    filename = f"balance_sheet_{year or 'all'}_summary.pdf"
                elif report_name == "profit_loss":
                    filename = f"profit_loss_{year or 'all'}_summary.pdf"
                else:
                    filename = f"financial_summary_{year or 'all'}.pdf"

                text = _build_summary_report_text(
                    edb,
                    current_user_id,
                    report_name=report_name,
                    date_from=date_from,
                    date_to=date_to,
                    account_id=account_id,
                ) or ""

            # ── SINGLE DETAILED REPORT PDF ───────────────────────────────
            elif report_name:
                if report_name not in reports:
                    self._text(400, f"Unknown report '{report_name}'")
                    return True

                if report_name == "income_statement":
                    filename = f"income_statement_{year or 'all'}.pdf"
                elif report_name == "balance_sheet":
                    filename = f"balance_sheet_{year or 'all'}.pdf"
                elif report_name == "profit_loss":
                    filename = f"profit_loss_{year or 'all'}.pdf"
                else:
                    filename = f"{report_name}_{year or 'all'}.pdf"

                text = _compact_pdf_detail_lines(reports.get(report_name, "") or "")

            # ── FULL DETAILED PACK PDF ───────────────────────────────────
            else:
                filename = f"financial_detailed_{year or 'all'}.pdf"
                parts = []

                if reports.get("income_statement"):
                    parts.append(_compact_pdf_detail_lines(reports["income_statement"]))

                if reports.get("balance_sheet"):
                    parts.append(_compact_pdf_detail_lines(reports["balance_sheet"]))

                if reports.get("profit_loss"):
                    parts.append(_compact_pdf_detail_lines(reports["profit_loss"]))

                text = "\n\n".join([p for p in parts if (p or "").strip()])

            if not (text or "").strip():
                self._text(400, "No statement data available for the selected filters.")
                return True

            pdf_bytes = _render_text_pdf_bytes(filename, text)

            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(pdf_bytes)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(pdf_bytes)
            return True

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._text(500, f"PDF export failed: {e}")
            return True

    return False  # not our route


def handle_post_extension(
    self,
    path: str,
    workflow: "ReviewWorkflow",
    edb: "ExtendedDBStore",
    current_user_id: Optional[str] = None,
) -> bool:
    """
    Handle extension-only POST routes. Returns True if handled, False to fall through.
    Does NOT handle /create-user, /login, /logout — those stay in do_POST
    so that _current_user_id is set in the correct module scope.
    """
    # Parse path once at the top so every branch can use route_path safely.
    # path may include a query-string on POST (rare but possible).
    parsed_url = urlparse(path)
    route_path = parsed_url.path

    def _profile_gate_block() -> bool:
        if not current_user_id:
            return False
        is_complete, missing_reason = is_min_profile_complete(current_user_id)
        if is_complete:
            return False
        self._json(400, {
            "ok": False,
            "error": "Complete basic classification profile before importing statements",
            "profile_required": True,
            "missing_reason": missing_reason,
        })
        return True

    # ── /upload-pending ───────────────────────────────────────────────────────
    if route_path == "/upload-pending":
        if not current_user_id:
            self._json(400, {
                "error": "No active user. Create one in Setup.",
                "staged": 0, "transactions": [], "account_id": "main",
            })
            return True
        if _profile_gate_block():
            return True
        from hni_accounting_system import _parse_multipart, parse_excel_statement, _is_pdf_bytes, _pdf_library_status
        ct   = self.headers.get("Content-Type", "")
        body = self._body_bytes()
        parts = _parse_multipart(body, ct)
        file_part = parts.get("file")
        if not file_part:
            self._json(400, {
                "error": "No file received.",
                "staged": 0, "transactions": [], "account_id": "main",
            })
            return True
        if isinstance(file_part, dict):
            file_bytes = file_part.get("content", b"")
            filename = file_part.get("filename", "")
        else:
            file_bytes = file_part
            filename = ""

        password = str(parts.get("password") or "").strip() or None
        account_id = str(parts.get("account_id") or "main").strip() or "main"

        # ── Log upload metadata ───────────────────────────────────────────────
        ext = os.path.splitext(filename)[1].lower() if filename else "(unknown)"
        print(
            f"  [UPLOAD] filename='{filename}' ext='{ext}' size={len(file_bytes)} bytes "
            f"account='{account_id}' password_supplied={'yes' if password else 'no'}"
        )

        is_pdf_upload = (
            (filename or "").lower().endswith(".pdf")
            or _is_pdf_bytes(file_bytes)
        )

        try:
            parsed = parse_excel_statement(file_bytes, filename, password=password)
            records = parsed.get("records", [])
            opening_balance = parsed.get("opening_balance")
            closing_balance = parsed.get("closing_balance")
            statement_from_date = parsed.get("statement_from_date")
            statement_to_date = parsed.get("statement_to_date")
            print("[UPLOAD PARSED PERIOD]", statement_from_date, statement_to_date, flush=True)
        except ValueError as e:
            import traceback; traceback.print_exc()
            err_msg = str(e)
            print(f"  [UPLOAD] Parse error: {err_msg}")

            parser_label = "PDF parse failed" if is_pdf_upload else "Excel parse failed"

            warnings = [err_msg]
            if is_pdf_upload:
                libs = _pdf_library_status()
                if not libs.get("tesseract", False):
                    warnings.append(
                        "Scanned PDFs need OCR. Install pytesseract, pillow, and the system tesseract binary."
                    )

            self._json(400, {
                "ok": False,
                "error": f"{parser_label}: {err_msg}",
                "staged": 0,
                "transactions": [],
                "account_id": account_id,
                "warnings": warnings,
            })
            return True
        except Exception as e:
            import traceback; traceback.print_exc()
            err_msg = str(e)
            print(f"  [UPLOAD] Unexpected parse error: {err_msg}")

            parser_label = "PDF parse error" if is_pdf_upload else "Parse error"

            self._json(400, {
                "ok": False,
                "error": f"{parser_label}: {err_msg}",
                "staged": 0,
                "transactions": [],
                "account_id": account_id,
                "warnings": [err_msg],
            })
            return True
        
        # Do not auto-write users.opening_balance from uploaded files.
        # Each file's opening balance is already stored in raw_import_batches.
        # Reports should derive the relevant opening balance from the selected
        # account and statement period, while users.opening_balance remains only
        # a manual fallback if no batch opening balance is available.

        print(f"  [UPLOAD] Rows extracted: {len(records)}")

        if not records:
            if is_pdf_upload:
                libs = _pdf_library_status()
                warn = (
                    "No transactions found in the PDF. "
                    "No valid transaction rows could be extracted. "
                    "The PDF may be scanned, image only, password protected, or use a non standard table layout."
                )
                warnings = [warn]
                if not libs.get("tesseract", False):
                    warnings.append(
                        "OCR is not fully available. For scanned PDFs install pytesseract, pillow, and the system tesseract binary."
                    )
            else:
                warn = (
                    "No transactions found. Workbook was read but no valid transaction "
                    "table was detected. Check that the file has a recognisable header row and data rows."
                )
                warnings = [warn]

            print(f"  [UPLOAD] Zero transactions: {warn}")
            self._json(200, {
                "ok": False,
                "staged": 0,
                "transactions": [],
                "account_id": account_id,
                "warnings": warnings,
            })
            return True

        try:
            print(f"[PERIOD STAGED] statement_from_date={statement_from_date} statement_to_date={statement_to_date}", flush=True)
            res = workflow.process_import_batch(
                user_id=current_user_id,
                account_id=account_id,
                statement_type="bank_statement",
                file_name=filename,
                file_bytes=file_bytes,
                raw_records=records,
                statement_from_date=statement_from_date,
                statement_to_date=statement_to_date,
                opening_balance=opening_balance,
                closing_balance=closing_balance,
            )
        except Exception as e:
            import traceback; traceback.print_exc()
            self._json(500, {
                "error": f"Classification/staging error: {e}",
                "staged": 0, "transactions": [], "account_id": account_id,
            })
            return True

        try:
            pending = workflow.get_pending_transactions(current_user_id)
        except Exception as e:
            print(f"  [PENDING] fetch failed: {e}")
            pending = []

        if pending is None:
            pending = []

        print(f"  [PENDING] rows returned to UI: {len(pending)}")

        # FIX B8: ensure every row has all fields the frontend expects
        _REQUIRED_FIELDS = {
            "id": "", "narration": "", "amount": 0, "txn_type": "debit",
            "predicted_ledger_key": "suspense_debit", "reclassified_key": None,
            "book": "SUSPENSE", "section": "Unclassified", "grp": "Unclassified",
            "account": "Requires Review", "status": "PENDING", "confidence": 0,
            "is_anomaly": 0, "counterparty": "", "source": "", "note": "",
            "txn_date": "", "account_id": account_id,
        }
        safe_pending = []
        for row in pending:
            safe_row = dict(_REQUIRED_FIELDS)
            safe_row.update({k: v for k, v in row.items() if v is not None})
            safe_pending.append(safe_row)
        print(f"  [REVIEW] safe_pending rows: {len(safe_pending)}")

        res["pending_total"] = len(safe_pending)
        res["transactions"] = safe_pending[:500]
        res["staged"] = res.get("staged_rows", 0)
        self._json(200, res)
        return True

    # ── /reclassify ───────────────────────────────────────────────────────────
    if route_path == "/reclassify":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
            return True

        p = self._body() or {}
        txn_id = str(p.get("txn_id") or "").strip()
        new_key = str(p.get("new_key") or "").strip()

        if not txn_id or not new_key:
            self._json(400, {"error": "txn_id and new_key are required."})
            return True

        try:
            updated = workflow.reclassify_transaction(txn_id, new_key)
            self._json(200, updated)
        except ValueError as e:
            self._json(400, {"error": str(e)})
        except Exception as e:
            import traceback; traceback.print_exc()
            self._json(500, {"error": str(e)})
        return True

    # ── /reclassify-approved ──────────────────────────────────────────────────
    if route_path == "/reclassify-approved":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
            return True

        p = self._body()
        txn_id = str(p.get("txn_id") or "").strip()
        new_key = str(p.get("new_key") or "").strip()
        stock_name = str(p.get("stock_name") or "").strip()
        trade_type = str(p.get("trade_type") or "").strip()
        trade_price = str(p.get("trade_price") or "").strip()
        trade_qty = str(p.get("trade_qty") or "").strip()
        trade_tds = str(p.get("trade_tds") or "").strip()

        if not txn_id or not new_key:
            self._json(400, {"error": "txn_id and new_key are required."})
            return True

        try:
            updated = edb.reclassify_approved_transaction(
                txn_id,
                new_key,
                stock_name=stock_name,
                trade_type=trade_type,
                trade_price=trade_price,
                trade_qty=trade_qty,
                trade_tds=trade_tds,
            )
            self._json(200, updated)
        except ValueError as e:
            self._json(400, {"error": str(e)})
        except Exception as e:
            import traceback; traceback.print_exc()
            self._json(500, {"error": str(e)})
        return True

    if route_path == "/add-custom-rule":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
            return True

        p = self._body() or {}
        try:
            row = edb.add_custom_rule(
                current_user_id,
                pattern=str(p.get("pattern") or "").strip(),
                ledger_key=str(p.get("ledger_key") or "").strip(),
                txn_type=str(p.get("txn_type") or "").strip().lower(),
                match_mode=str(p.get("match_mode") or "contains").strip().lower(),
                priority=int(p.get("priority", 100) or 100),
            )
            self._json(200, {"ok": True, "rule": row})
        except ValueError as e:
            self._json(400, {"error": str(e)})
        except Exception as e:
            import traceback; traceback.print_exc()
            self._json(500, {"error": str(e)})
        return True

    if route_path == "/save-profile":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
            return True
        p = self._body() or {}
        payload = {
            "entity_type": str(p.get("entity_type", "") or "").strip(),
            "legal_name": str(p.get("legal_name", "") or "").strip(),
            "huf_name": str(p.get("huf_name", "") or "").strip(),
            "dob": str(p.get("dob", "") or "").strip(),
            "is_nri": int(bool(p.get("is_nri", 0))),
            "has_family_transactions": int(bool(p.get("has_family_transactions", 0))),
            "is_salaried": int(bool(p.get("is_salaried", 0))),
            "employer_name": str(p.get("employer_name", "") or "").strip(),
            "has_consultancy": int(bool(p.get("has_consultancy", 0))),
            "has_trading": int(bool(p.get("has_trading", 0))),
            "has_multiple_bank_accounts": int(bool(p.get("has_multiple_bank_accounts", 0))),
            "has_credit_cards": int(bool(p.get("has_credit_cards", 0))),
            "has_rental_income": int(bool(p.get("has_rental_income", 0))),
        }
        try:
            row = edb.upsert_user_profile(current_user_id, payload)
        except ValueError as e:
            self._json(400, {"ok": False, "error": str(e)})
            return True
        is_complete, missing_reason = is_min_profile_complete(current_user_id)
        self._json(200, {
            "ok": True,
            "profile": row,
            "profile_required": not is_complete,
            "is_complete": is_complete,
            "missing_reason": missing_reason,
        })
        return True

    if route_path == "/save-known-counterparties":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
            return True
        p = self._body() or {}
        rows = p.get("rows") if isinstance(p.get("rows"), list) else []
        saved = edb.upsert_known_counterparties(current_user_id, rows)
        self._json(200, {"ok": True, "rows": saved})
        return True

    if route_path == "/save-known-accounts":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
            return True
        p = self._body() or {}
        rows = p.get("rows") if isinstance(p.get("rows"), list) else []
        saved = edb.upsert_known_accounts(current_user_id, rows)
        self._json(200, {"ok": True, "rows": saved})
        return True

    if route_path == "/apply-profile-to-pending":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
            return True
        try:
            result = edb.apply_profile_to_pending(current_user_id)
            self._json(200, {"ok": True, **result})
        except Exception as e:
            import traceback; traceback.print_exc()
            self._json(500, {"ok": False, "error": str(e)})
        return True

    if route_path == "/apply-profile-to-approved-safe":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
            return True
        try:
            result = edb.apply_profile_to_approved_safe(current_user_id)
            self._json(200, {"ok": True, **result})
        except Exception as e:
            import traceback; traceback.print_exc()
            self._json(500, {"ok": False, "error": str(e)})
        return True

    # ── /approve ──────────────────────────────────────────────────────────────
    if route_path == "/approve":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
            return True
        count = workflow.approve_transactions(current_user_id)
        try:
            reports = generate_reports_for_user(edb, current_user_id)
        except Exception as e:
            import traceback; traceback.print_exc()
            reports = {}
        self._json(200, {
            "approved": count,
            "msg":      f"{count} transactions approved and posted to ledger.",
            "reports":  reports,
        })
        return True

    # ── /discard-pending ──────────────────────────────────────────────────────
    if route_path == "/discard-pending":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
            return True
        workflow.discard_pending(current_user_id)
        self._json(200, {"status": "ok", "msg": "Pending batch discarded."})
        return True

    # ── /financial-reports ────────────────────────────────────────────────────
    if route_path == "/financial-reports":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
            return True
        try:
            p = self._body() or {}
            date_from = str(p.get("date_from") or "").strip() or None
            date_to = str(p.get("date_to") or "").strip() or None
            account_id = str(p.get("account_id") or "").strip() or None
            reports = generate_reports_for_user(edb, current_user_id, date_from, date_to, account_id)
            self._json(200, reports)
        except Exception as e:
            import traceback; traceback.print_exc()
            self._json(500, {"error": str(e)})
        return True

    # ── /set-opening-balance ──────────────────────────────────────────────────
    if route_path == "/set-opening-balance":
        if not current_user_id:
            self._json(400, {"error": "No active user."})
            return True
        p = self._body()
        amt = float(p.get("amount", 0) or 0)
        edb.set_opening_balance(current_user_id, amt)
        self._json(200, {"status": "ok", "opening_balance": amt})
        return True

    # ── /delete-account-data ──────────────────────────────────────────────────
    if route_path == "/delete-account-data":
        if not current_user_id:
            self._json(400, {"error": "No active user."})
            return True
        p = self._body()
        acct = str(p.get("account_id") or "").strip()
        if not acct:
            self._json(400, {"error": "account_id is required."})
            return True
        deleted = edb.delete_account_data(current_user_id, acct)
        self._json(200, {"status": "ok", "deleted": deleted,
                         "msg": f"Deleted all data for account '{acct}'."})
        return True

    # ── /delete-all-data ──────────────────────────────────────────────────────
    if route_path == "/delete-all-data":
        if not current_user_id:
            self._json(400, {"error": "No active user."})
            return True
        deleted = edb.delete_all_data(current_user_id)
        self._json(200, {"status": "ok", "deleted": deleted,
                         "msg": "All transaction data deleted. User account preserved."})
        return True

    # ── /delete-user ─────────────────────────────────────────────────────────
    if route_path == "/delete-user":
        if not current_user_id:
            self._json(400, {"error": "No active user."})
            return True
        user = edb.get_user(current_user_id) or {}
        p = self._body() or {}
        confirm_name = str(p.get("confirm_name") or "").strip()
        actual_name = str(user.get("name") or "").strip()

        if not actual_name:
            self._json(400, {"error": "Active user record not found."})
            return True
        if confirm_name != actual_name:
            self._json(400, {"error": "Confirmation name does not match active user name."})
            return True

        deleted = edb.delete_user(current_user_id)
        self._json(200, {
            "status": "ok",
            "deleted": deleted,
            "msg": f"User '{actual_name}' and all related data were deleted."
        })
        return True

    # ── /upload-trading-ledger ────────────────────────────────────────────────
    if route_path == "/upload-trading-ledger":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
            return True
        if _profile_gate_block():
            return True
        from hni_accounting_system import _parse_multipart
        ct   = self.headers.get("Content-Type", "")
        body = self._body_bytes()
        parts = _parse_multipart(body, ct)
        file_part = parts.get("file")
        if not file_part:
            self._json(400, {"error": "No file received."})
            return True
        if isinstance(file_part, dict):
            file_bytes = file_part.get("content", b"")
        else:
            file_bytes = file_part
        acct_id = str(parts.get("account_id") or "trading").strip()
        try:
            records = _parse_trading_ledger_bytes(file_bytes)
        except Exception as exc:
            self._json(400, {"error": f"Parse error: {exc}"})
            return True
        inserted, skipped = edb.insert_trading_rows(current_user_id, acct_id, records)
        self._json(200, {
            "status": "ok",
            "rows_parsed":   len(records),
            "rows_inserted": inserted,
            "rows_skipped":  skipped,
            "account_id":    acct_id,
            "msg": f"Trading ledger uploaded: {inserted} rows stored, {skipped} skipped.",
        })
        return True

    # ── /trading-cashflow (POST) ──────────────────────────────────────────────
    if route_path == "/trading-cashflow":
        if not current_user_id:
            self._json(400, {"error": "No active user. Create one in Setup."})
            return True
        p          = self._body()
        date_from  = p.get("date_from")  or None
        date_to    = p.get("date_to")    or None
        account_id = p.get("account_id") or None
        try:
            report  = generate_trading_cashflow(edb, current_user_id,
                                                date_from, date_to, account_id)
            summary = _build_trading_cashflow_summary(edb, current_user_id,
                                                      date_from, date_to, account_id)
            self._json(200, {
                "report": report,
                "summary": summary,
                "reconciled": summary.get("reconciliation_ok", False),
            })
        except Exception as exc:
            self._json(500, {"error": str(exc)})
        return True

    return False


# ══════════════════════════════════════════════════════════════════════════════
# 5B.  TRADING ACCOUNT CASH FLOW STATEMENT  (Indirect Method)
# ══════════════════════════════════════════════════════════════════════════════

def _parse_trading_ledger_bytes(file_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Parse a 5paisa / broker trading ledger XLS (actually a ZIP/XLSX).
    Column layout (0-indexed):
      0 Transaction Date  1 Segment  2 Particular  3 Description
      4 Debit             5 Credit   6 Balance

    Date format in cell: "Mar 16 2026"
    Returns list of dicts with keys: txn_date, segment, particular, description,
    debit, credit, balance.
    """
    import zipfile
    import io as _io
    from xml.etree import ElementTree as ET

    HEADER_COLS = [
        "transaction date", "segment", "particular",
        "description", "debit", "credit", "balance",
    ]

    def _strip_ns(tree):
        for el in tree.iter():
            el.tag = _re.sub(r'\{[^}]+\}', '', el.tag)

    def _col_idx(col_str: str) -> int:
        idx = 0
        for ch in col_str.upper():
            idx = idx * 26 + (ord(ch) - ord('A') + 1)
        return idx - 1

    def _try_date(raw) -> str:
        if not raw:
            return ""
        s = str(raw).strip()
        # "Mar 16 2026" format first
        for fmt in ("%b %d %Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
                    "%d %b %Y", "%d-%b-%Y", "%d/%m/%y", "%d-%b-%y"):
            try:
                from datetime import datetime as _dt
                return _dt.strptime(s, fmt).strftime("%Y-%m-%d")
            except Exception:
                pass
        return s

    def _try_float(raw) -> float:
        if raw is None or str(raw).strip() in ("", "-", "—"):
            return 0.0
        try:
            return float(_re.sub(r"[^\d.\-]", "", str(raw)))
        except Exception:
            return 0.0

    # ── Open as ZIP (handles both .xlsx and .xls-named-as-xlsx) ──────────────
    try:
        z = zipfile.ZipFile(_io.BytesIO(file_bytes))
    except Exception as exc:
        raise ValueError(f"File is not a valid ZIP/XLSX: {exc}")

    names = z.namelist()
    sheet_path = next(
        (n for n in names if _re.match(r'xl/worksheets/sheet1\.xml', n)), None
    )
    if not sheet_path:
        sheet_path = next(
            (n for n in names if _re.match(r'xl/worksheets/sheet\d+\.xml', n)), None
        )
    if not sheet_path:
        raise ValueError("No worksheet found in ZIP (xl/worksheets/sheet*.xml missing)")

    # Shared strings
    shared: List[str] = []
    if 'xl/sharedStrings.xml' in names:
        ss_tree = ET.fromstring(z.read('xl/sharedStrings.xml'))
        _strip_ns(ss_tree)
        for si in ss_tree.findall('.//si'):
            t_el = si.find('t')
            if t_el is not None:
                shared.append(t_el.text or '')
            else:
                shared.append(''.join(t.text or '' for t in si.findall('.//t')))

    # Sheet
    sheet_tree = ET.fromstring(z.read(sheet_path))
    _strip_ns(sheet_tree)

    raw_rows: List[List[Any]] = []
    for row_el in sheet_tree.findall('.//row'):
        cells: Dict[int, Any] = {}
        max_c = 0
        for c in row_el.findall('c'):
            ref = c.get('r', '')
            letters = ''.join(filter(str.isalpha, ref))
            if not letters:
                continue
            ci = _col_idx(letters)
            max_c = max(max_c, ci)
            t = c.get('t', '')
            v_el = c.find('v')
            val = v_el.text if v_el is not None else None
            if t == 's' and val is not None:
                try:
                    val = shared[int(val)]
                except (IndexError, ValueError):
                    val = None
            elif val is not None:
                try:
                    val = float(val)
                except ValueError:
                    pass
            cells[ci] = val
        if cells:
            raw_rows.append([cells.get(i) for i in range(max_c + 1)])

    # Locate header row
    header_idx = None
    for i, row in enumerate(raw_rows):
        normed = [str(c or "").strip().lower() for c in row]
        # 5paisa: transaction date + particular/segment
        if any("transaction date" in v or "txn date" in v for v in normed) and \
           any("particular" in v or "segment" in v for v in normed):
            header_idx = i
            break
        # Zerodha: date + particulars (note the 's')
        if any("date" in v and "transaction" not in v or "posting date" in v for v in normed) and \
           any("particulars" in v for v in normed):
            header_idx = i
            break
    if header_idx is None:
        for i, row in enumerate(raw_rows):
            normed = [str(c or "").strip().lower() for c in row]
            if all(h in normed for h in ("debit", "credit", "balance")):
                header_idx = i
                break
    if header_idx is None:
        raise ValueError(
            "Could not find header row. Expected columns: Date, Particular/Particulars, Debit, Credit, Balance"
        )

    headers = [str(c or "").strip().lower() for c in raw_rows[header_idx]]

    def _col(candidates):
        for kw in candidates:
            for i, h in enumerate(headers):
                if kw in h:
                    return i
        return None

    date_c  = _col(["transaction date", "txn date", "posting date", "date"])
    seg_c   = _col(["segment"])
    # Support both 5paisa 'particular' and Zerodha 'particulars'
    part_c  = _col(["particulars", "particular"])
    desc_c  = _col(["description", "narration", "remarks"])
    deb_c   = _col(["debit", "withdrawal", "dr"])
    cred_c  = _col(["credit", "deposit", "cr"])
    bal_c   = _col(["balance", "closing"])

    def _safe(row, idx):
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    records: List[Dict[str, Any]] = []
    for row in raw_rows[header_idx + 1:]:
        if not any(c is not None and str(c).strip() for c in row):
            continue
        date_raw = _safe(row, date_c)
        if not date_raw:
            continue
        date_str = _try_date(date_raw)
        if not date_str:
            continue
        seg  = str(_safe(row, seg_c)  or "").strip()
        part = str(_safe(row, part_c) or "").strip()
        desc = str(_safe(row, desc_c) or "").strip()
        deb  = _try_float(_safe(row, deb_c))
        cred = _try_float(_safe(row, cred_c))
        bal  = _try_float(_safe(row, bal_c))
        records.append({
            "txn_date":    date_str,
            "segment":     seg,
            "particular":  part,
            "description": desc,
            "debit":       deb,
            "credit":      cred,
            "balance":     bal,
        })
    return records


def generate_trading_cashflow(
    edb: "ExtendedDBStore",
    user_id: str,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    account_id: Optional[str] = None,
) -> str:
    """
    Generate an Indirect-Method Cash Flow Statement for a trading account
    from rows stored in the trading_ledger table.

    Section A — Operating Activities (trading P&L and costs)
    Section B — Financing Activities (funds in/out with bank)
    Section C — Net Change reconciliation vs reported closing balance
    """
    W = 92
    sym = "₹"

    rows = edb.get_trading_rows(
        user_id, account_id=account_id,
        date_from=date_from, date_to=date_to,
    )

    user = edb.get_user(user_id)
    uname = (user or {}).get("name", "Account Holder")

    if not rows:
        return (
            "=" * W + "\n"
            + f"{'TRADING ACCOUNT  —  CASH FLOW STATEMENT':^{W}}\n"
            + f"{'No trading ledger rows found. Upload a ledger first.':^{W}}\n"
            + "=" * W
        )

    # ── Aggregate by Particular type ─────────────────────────────────────────
    # Keys: equity_cr, equity_dr, fno_cr, fno_dr, bill_debit_total,
    #       dp_dr, nb_dr, funds_added, payout
    eq_cr = eq_dr = fno_cr = fno_dr = 0.0
    bill_costs = 0.0
    dp_dr = nb_dr = margin_interest_dr = 0.0
    funds_added = 0.0
    payout = 0.0
    mtf_funding_cr = mtf_funding_dr = 0.0

    # Opening = first balance - first credit + first debit (or directly stored)
    opening_balance = 0.0
    closing_balance = 0.0

    # We'll collect line-level detail for the report
    detail: Dict[str, List[Dict]] = {
        "equity": [], "fno": [], "bill_cost": [],
        "dp": [], "nb": [], "funds": [], "payout": [],
        "margin_interest": [], "mtf": [],
    }

    for r in rows:
        part  = (r.get("particular") or "").strip().lower()
        seg   = (r.get("segment")    or "").strip().upper()
        deb   = float(r.get("debit")  or 0)
        cred  = float(r.get("credit") or 0)
        date  = r.get("txn_date", "")
        desc  = r.get("description", "")

        # Detect inflows to trading account from bank (funds added / transfers in)
        _is_funds_in = (
            "funds added" in part or "fund added" in part
            or "funds transferred" in part or "fund transferred" in part
            or ("transfer" in part and cred > 0 and deb == 0 and "bank" in part)
            or ("receipt" in part and cred > 0 and deb == 0)
            # Zerodha: bank inward entries — description contains NEFT/IMPS/UPI
            or (cred > 0 and deb == 0 and any(k in desc.upper() for k in ("NEFT", "IMPS", "UPI", "RTGS")))
        )
        # Detect payouts from trading account to bank
        _is_payout = (
            "payout" in part
            or "funds withdrawn" in part
            or ("withdrawal" in part and deb > 0)
            or ("transfer to bank" in part)
        )
        # Equity bill rows (5paisa: "bill - cash eq", Zerodha: segment EQ)
        _is_equity_bill = (
            "bill - cash" in part or "bill - cash eq" in part
            or "brokerage reversal" in part
            or (seg in ("NSEEQ", "EQ", "NSE_EQ") and "bill" in part)
            or (seg == "EQ" and (cred > 0 or deb > 0) and "bill" in part)
        )
        # F&O bill rows
        _is_fno_bill = (
            "bill - fno" in part or "bill - fno opt" in part or "bill - fno fut" in part
            or (seg in ("NSEFO", "FO", "NSE_FO", "F&O") and "bill" in part)
        )
        # DP / demat charges
        _is_dp = (
            "dp txn" in part or "dp charge" in part or "demat" in part
            or "dp amc" in part or "dp annual" in part
            or ("dp" in part and deb > 0)
        )
        # Net banking / gateway charges
        _is_nb = (
            "net banking" in part or "gateway" in part
            or "payment gateway" in part or "netbanking" in part
        )
        # Margin / delayed payment / MTF interest charges
        _is_margin_interest = (
            "margin plus" in part or "delayed payment" in part
            or "mtf interest" in part or "margin interest" in part
        )
        # MTF (Margin Trade Funding) — broker lending / repayment, financing activity
        _is_mtf = "mtf funding" in part

        if _is_funds_in and not _is_equity_bill and not _is_fno_bill:
            funds_added += cred
            detail["funds"].append({"date": date, "cr": cred, "desc": desc or part})

        elif _is_payout:
            payout += deb
            detail["payout"].append({"date": date, "dr": deb, "desc": desc or part})

        elif _is_mtf:
            mtf_funding_cr += cred
            mtf_funding_dr += deb
            detail["mtf"].append({"date": date, "dr": deb, "cr": cred, "desc": desc or part})

        elif _is_equity_bill:
            # Equity bills: credit = realised gain; debit = cost/loss
            eq_cr += cred
            eq_dr += deb
            if deb > 0:
                bill_costs += deb
                detail["bill_cost"].append({"date": date, "dr": deb, "seg": seg or "NSEEQ", "desc": desc or part})
            detail["equity"].append({"date": date, "dr": deb, "cr": cred, "desc": desc or part})

        elif _is_fno_bill:
            fno_cr += cred
            fno_dr += deb
            if deb > 0:
                bill_costs += deb
                detail["bill_cost"].append({"date": date, "dr": deb, "seg": seg or "NSEFO", "desc": desc or part})
            detail["fno"].append({"date": date, "dr": deb, "cr": cred, "desc": desc or part})

        elif _is_dp:
            dp_dr += deb
            detail["dp"].append({"date": date, "dr": deb, "desc": desc or part})

        elif _is_nb:
            nb_dr += deb
            detail["nb"].append({"date": date, "dr": deb, "desc": desc or part})

        elif _is_margin_interest:
            margin_interest_dr += deb
            detail["margin_interest"].append({"date": date, "dr": deb, "desc": desc or part})

    # Reported opening & closing from the row balances
    # Opening = balance of the very first row adjusted back
    first_row = rows[0]
    last_row  = rows[-1]
    first_bal  = float(first_row.get("balance") or 0)
    first_cred = float(first_row.get("credit")  or 0)
    first_deb  = float(first_row.get("debit")   or 0)
    # Back-calculate: opening = first_balance - first_credit + first_debit
    opening_balance = first_bal - first_cred + first_deb

    # Store reported closing balance for comparison
    reported_closing = float(last_row.get("balance") or 0)

    # ── Compute section totals ────────────────────────────────────────────────
    net_equity_pl   = eq_cr - eq_dr   # net realised P&L from equity
    net_fno_pl      = fno_cr - fno_dr # net realised P&L from F&O
    # bill_costs already accumulated above (only debit side of Bill rows)
    # dp_dr, nb_dr are costs
    net_operating   = net_equity_pl + net_fno_pl - dp_dr - nb_dr - margin_interest_dr
    # Note: bill_costs are already embedded in eq_dr/fno_dr net so we don't double-deduct

    net_mtf         = mtf_funding_cr - mtf_funding_dr   # net MTF broker lending (financing)
    net_financing   = funds_added - payout + net_mtf
    net_change      = net_operating + net_financing
    closing_calc    = opening_balance + net_change
    recon_diff      = closing_calc - reported_closing
    reconciled      = abs(recon_diff) < 1.0  # within ₹1 tolerance

    # ── Format helpers ────────────────────────────────────────────────────────
    def amtf(v: float) -> str:
        return f"{sym}{v:>16,.2f}"

    def signed_amtf(v: float) -> str:
        sign = "+" if v >= 0 else "−"
        return f"{sign} {sym}{abs(v):>14,.2f}"

    # ── Build report lines ────────────────────────────────────────────────────
    from datetime import datetime as _dt_cls

    period_label = _period_label(date_from, date_to, account_id)
    lines = [
        "=" * W,
        f"{'TRADING ACCOUNT  —  CASH FLOW STATEMENT  (INDIRECT METHOD)':^{W}}",
        f"{'Account Holder : ' + uname:^{W}}",
        f"{'Generated      : ' + _dt_cls.now().strftime('%d-%b-%Y  %H:%M'):^{W}}",
        f"{period_label:^{W}}",
        "=" * W,
        "",
        f"  Opening Balance                                                      {amtf(opening_balance)}",
        f"  {_bar('─', W-4)}",
        "",
        f"  A.  OPERATING ACTIVITIES  (Trading P&L & Costs)",
        f"  {_bar('─', W-4)}",
        "",
        f"      Net Realised P&L — Equity Cash (NSEEQ)         "
        f"  [TRACE:INCOME_EXPENSE:Income:Investment Income:F&O Net Realised P&L]",
        f"          Credits (sale proceeds / gains)              {amtf(eq_cr)}",
        f"          Less: Debits (purchase cost / brokerage)     {amtf(eq_dr)}",
        f"        ─────────────────────────────────────────────────────────────",
        f"        Net Equity P&L                                {signed_amtf(net_equity_pl)}",
        "",
        f"      Net Realised P&L — F&O / Options (NSEFO)       "
        f"  [TRACE:INCOME_EXPENSE:Income:Investment Income:F&O Net Realised P&L]",
        f"          Credits (profit / premium received)          {amtf(fno_cr)}",
        f"          Less: Debits (loss / premium paid)           {amtf(fno_dr)}",
        f"        ─────────────────────────────────────────────────────────────",
        f"        Net F&O P&L                                   {signed_amtf(net_fno_pl)}",
        "",
        f"      Less: DP & Demat Charges                        "
        f"  [TRACE:INCOME_EXPENSE:Expenditure:Financial Costs:DP & Demat Charges]",
        f"        DP txn Charges (debit total)                  {amtf(dp_dr)}",
        "",
        f"      Less: Net Banking / Gateway Charges             "
        f"  [TRACE:INCOME_EXPENSE:Expenditure:Financial Costs:Net Banking / Gateway Charges]",
        f"        Net Banking Charges (debit total)             {amtf(nb_dr)}",
        "",
        f"      Less: Margin / Delayed Payment Interest         ",
        f"        Charges - Margin Plus / MTF Interest          {amtf(margin_interest_dr)}",
        "",
        f"  {_bar('─', W-4)}",
        f"  NET CASH FROM OPERATING ACTIVITIES                 {signed_amtf(net_operating)}",
        f"  {_bar('═', W-4)}",
        "",
        f"  B.  FINANCING ACTIVITIES  (Capital Movements)",
        f"  {_bar('─', W-4)}",
        "",
        f"      Add: Funds Added from Bank                      "
        f"  [TRACE:BALANCE_SHEET:Assets:Current Assets:Trading Account – Funds Added]",
        f"        Total Funds Added (credit)                    {amtf(funds_added)}",
        "",
        f"      Less: Payouts to Bank                           "
        f"  [TRACE:BALANCE_SHEET:Assets:Current Assets:Trading Account – Payout to Bank]",
        f"        Total Payouts (debit)                         {amtf(payout)}",
        "",
        f"      MTF Funding (Broker Margin Lending)             ",
        f"        MTF Credited (funding received)               {amtf(mtf_funding_cr)}",
        f"        MTF Debited  (repayment made)                 {amtf(mtf_funding_dr)}",
        f"        Net MTF                                       {signed_amtf(net_mtf)}",
        "",
        f"  {_bar('─', W-4)}",
        f"  NET CASH FROM FINANCING ACTIVITIES                 {signed_amtf(net_financing)}",
        f"  {_bar('═', W-4)}",
        "",
        f"  C.  NET CHANGE IN TRADING ACCOUNT BALANCE",
        f"  {_bar('─', W-4)}",
        "",
        f"      Opening Balance                                  {amtf(opening_balance)}",
        f"      + Net Cash from Operating Activities            {signed_amtf(net_operating)}",
        f"      + Net Cash from Financing Activities            {signed_amtf(net_financing)}",
        f"  {_bar('·', W-4)}",
        f"  ✦   CALCULATED CLOSING BALANCE                       {amtf(closing_calc)}",
        f"      Broker-Reported Closing Balance                   {amtf(reported_closing)}",
        f"      (= Opening + Net Operating + Net Financing)",
        "",
    ]

    # Reconciliation against broker-reported balance
    if reconciled:
        lines.append(f"  ✅  RECONCILIATION OK  :  Calculated {amtf(closing_calc).strip()} = Broker-Reported {amtf(reported_closing).strip()}  (diff < ₹1)")
    else:
        lines.append(f"  ⚠️   RECONCILIATION DIFFERENCE  :  Calculated {amtf(closing_calc).strip()} vs Broker-Reported {amtf(reported_closing).strip()}")
        lines.append(f"       Difference  {sym}{recon_diff:+,.2f}  — check for un-categorised or missing rows")

    lines += [
        "",
        f"  {_bar('═', W-4)}",
        f"  {'NET CHANGE IN TRADING ACCOUNT':<66} {signed_amtf(net_change)}",
        f"  {_bar('═', W-4)}",
        "",
        "=" * W,
        f"  TRANSACTION COUNTS:",
        f"    Equity (NSEEQ) Bill rows  : {len(detail['equity'])}",
        f"    F&O    (NSEFO) Bill rows  : {len(detail['fno'])}",
        f"    DP / Demat charge rows    : {len(detail['dp'])}",
        f"    Net Banking charge rows   : {len(detail['nb'])}",
        f"    Funds Added rows          : {len(detail['funds'])}",
        f"    Payout rows               : {len(detail['payout'])}",
        f"    Total rows parsed         : {len(rows)}",
        "=" * W,
    ]

    return "\n".join(lines)


def _build_trading_cashflow_summary(
    edb: "ExtendedDBStore",
    user_id: str,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compute the numeric summary dict returned alongside the report text.
    Re-aggregates from trading_ledger rows without rendering the full text.
    """
    rows = edb.get_trading_rows(
        user_id, account_id=account_id,
        date_from=date_from, date_to=date_to,
    )
    if not rows:
        return {
            "opening": 0.0, "closing": 0.0, "closing_calculated": 0.0,
            "net_operating": 0.0, "net_financing": 0.0, "net_change": 0.0,
            "recon_diff": 0.0, "reconciliation_ok": False,
        }

    eq_cr = eq_dr = fno_cr = fno_dr = 0.0
    dp_dr = nb_dr = margin_interest_dr = funds_added = payout = 0.0
    mtf_funding_cr = mtf_funding_dr = 0.0

    for r in rows:
        part = (r.get("particular") or "").strip().lower()
        seg  = (r.get("segment")    or "").strip().upper()
        desc = (r.get("description") or "").upper()
        deb  = float(r.get("debit")  or 0)
        cred = float(r.get("credit") or 0)

        _is_funds_in = (
            "funds added" in part or "fund added" in part
            or "funds transferred" in part or "fund transferred" in part
            or ("transfer" in part and cred > 0 and deb == 0 and "bank" in part)
            or ("receipt" in part and cred > 0 and deb == 0)
            or (cred > 0 and deb == 0 and any(k in desc for k in ("NEFT", "IMPS", "UPI", "RTGS")))
        )
        _is_payout = (
            "payout" in part
            or "funds withdrawn" in part
            or ("withdrawal" in part and deb > 0)
            or "transfer to bank" in part
        )
        _is_equity_bill = (
            "bill - cash" in part or "bill - cash eq" in part
            or "brokerage reversal" in part
            or (seg in ("NSEEQ", "EQ", "NSE_EQ") and "bill" in part)
        )
        _is_fno_bill = (
            "bill - fno" in part or "bill - fno opt" in part or "bill - fno fut" in part
            or (seg in ("NSEFO", "FO", "NSE_FO", "F&O") and "bill" in part)
        )
        _is_dp = ("dp txn" in part or "dp charge" in part or "demat" in part or "dp amc" in part or ("dp" in part and deb > 0))
        _is_nb = ("net banking" in part or "gateway" in part or "netbanking" in part)
        _is_margin_interest = ("margin plus" in part or "delayed payment" in part or "mtf interest" in part or "margin interest" in part)
        _is_mtf = "mtf funding" in part

        if _is_funds_in and not _is_equity_bill and not _is_fno_bill:
            funds_added += cred
        elif _is_payout:
            payout += deb
        elif _is_mtf:
            mtf_funding_cr += cred; mtf_funding_dr += deb
        elif _is_equity_bill:
            eq_cr += cred; eq_dr += deb
        elif _is_fno_bill:
            fno_cr += cred; fno_dr += deb
        elif _is_dp:
            dp_dr += deb
        elif _is_nb:
            nb_dr += deb
        elif _is_margin_interest:
            margin_interest_dr += deb

    first_row = rows[0]
    last_row  = rows[-1]
    opening   = float(first_row.get("balance") or 0) \
                - float(first_row.get("credit") or 0) \
                + float(first_row.get("debit")  or 0)
    closing   = float(last_row.get("balance") or 0)

    net_operating  = (eq_cr - eq_dr) + (fno_cr - fno_dr) - dp_dr - nb_dr - margin_interest_dr
    net_mtf        = mtf_funding_cr - mtf_funding_dr
    net_financing  = funds_added - payout + net_mtf
    net_change     = net_operating + net_financing
    closing_calc   = opening + net_change
    recon_diff     = closing_calc - closing

    return {
        "opening":            round(opening, 2),
        "closing":            round(closing, 2),
        "closing_calculated": round(closing_calc, 2),
        "net_operating":      round(net_operating, 2),
        "net_financing":      round(net_financing, 2),
        "net_change":         round(net_change, 2),
        "recon_diff":         round(recon_diff, 2),
        "reconciliation_ok":  abs(recon_diff) < 1.0,
    }
