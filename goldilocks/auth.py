"""Opaque, revocable sessions. Email delivery is not required for recovery codes."""

import hashlib
import hmac
import secrets
import time
from flask import g, request, current_app, abort
from werkzeug.security import generate_password_hash, check_password_hash

COOKIE = "goldilocks_session"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def initialize(db):
    with db._conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS web_sessions (
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
          expires_at INTEGER NOT NULL, last_seen INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS web_recovery (user_id TEXT PRIMARY KEY, code_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS web_limits (bucket TEXT, window INTEGER, attempts INTEGER,
          PRIMARY KEY(bucket,window));
        CREATE INDEX IF NOT EXISTS web_sessions_user ON web_sessions(user_id);
        """)


def limit(db, bucket, maximum=12):
    now = int(time.time())
    window = now // 3600
    with db._conn() as c:
        c.execute(
            "INSERT INTO web_limits VALUES(?,?,1) ON CONFLICT(bucket,window) DO UPDATE SET attempts=attempts+1",
            (digest(bucket), window),
        )
        n = c.execute(
            "SELECT attempts FROM web_limits WHERE bucket=? AND window=?",
            (digest(bucket), window),
        ).fetchone()[0]
        c.execute("DELETE FROM web_limits WHERE window<?", (window - 48,))
    if n > maximum:
        abort(429, description="Too many attempts. Please try again later.")


def verify(password, stored):
    if not stored or len(password) > 128:
        return False
    if stored.startswith("pbkdf2_sha256$"):
        from hni_accounting_system import _verify_password

        return _verify_password(password, stored)
    try:
        return check_password_hash(stored, password)
    except (ValueError, TypeError):
        return False


def password_hash(password, confirmation):
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        abort(422, description="Use a password between 12 and 128 characters.")
    if not hmac.compare_digest(password.encode(), str(confirmation).encode()):
        abort(422, description="The passwords do not match.")
    return generate_password_hash(password, method="scrypt")


def public_user(row):
    return {k: row.get(k, "") for k in ("id", "name", "email", "user_type")}


def session_cookie(token, response):
    response.set_cookie(
        COOKIE,
        token,
        secure=current_app.config["COOKIE_SECURE"],
        httponly=True,
        samesite="Strict",
        max_age=7 * 86400,
        path="/",
    )
    return response


def issue_session(c, uid):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    c.execute(
        "DELETE FROM web_sessions WHERE expires_at<? OR last_seen<?", (now, now - 1800)
    )
    c.execute(
        "INSERT INTO web_sessions VALUES(?,?,?,?)",
        (digest(token), uid, now + 7 * 86400, now),
    )
    return token


def new_session(db, uid, response):
    with db._conn() as c:
        token = issue_session(c, uid)
    return session_cookie(token, response)


def current_user(db):
    token = request.cookies.get(COOKIE, "")
    if not token:
        return None
    now = int(time.time())
    with db._conn() as c:
        row = c.execute(
            """SELECT u.* FROM users u JOIN web_sessions s ON s.user_id=u.id
          WHERE s.token_hash=? AND s.expires_at>? AND s.last_seen>?""",
            (digest(token), now, now - 1800),
        ).fetchone()
        if row:
            c.execute(
                "UPDATE web_sessions SET last_seen=? WHERE token_hash=?",
                (now, digest(token)),
            )
    return dict(row) if row else None


def require_user(db):
    user = current_user(db)
    if not user:
        abort(401, description="Sign in to open your ledger workspace.")
    g.user_id = user["id"]
    g.user = user
    return user


def recovery(db, uid):
    code = secrets.token_urlsafe(32)
    with db._conn() as c:
        c.execute(
            "INSERT INTO web_recovery VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET code_hash=excluded.code_hash",
            (uid, digest(code)),
        )
    return code
