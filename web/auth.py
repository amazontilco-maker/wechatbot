"""Passwords, login sessions and the login rate limit.

Passwords are stored as salted scrypt hashes. A session is a random token in an HttpOnly
cookie; only its SHA-256 is stored, so a copy of the database can't be used to log in.
Each session also has a CSRF token that every form must send back.
"""
import hashlib
import hmac
import secrets
import time

from .db import now_ms

MIN_PASSWORD = 10
SESSION_IDLE_MS = 14 * 24 * 3600 * 1000     # logged out after 2 weeks without use
SESSION_MAX_MS = 30 * 24 * 3600 * 1000      # and after 30 days in any case
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${h.hex()}"


def check_password(password, stored):
    try:
        _, n, r, p, salt, h = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), h)


_DUMMY = hash_password(secrets.token_hex(8))


def password_problem(password):
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    return ""


def login(db, name, password):
    """Returns the users row if name + password are right and the user is active, else None."""
    user = db.execute("SELECT * FROM users WHERE name = ?", (name.strip(),)).fetchone()
    if not user:
        check_password(password, _DUMMY)   # take as long as a real check
        return None
    if not check_password(password, user["pw_hash"]) or not user["active"]:
        return None
    return user


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(db, user_id):
    token = secrets.token_urlsafe(32)
    t = now_ms()
    db.execute("INSERT INTO sessions (token_hash, user_id, csrf, created_ts, last_ts) VALUES (?, ?, ?, ?, ?)",
               (_token_hash(token), user_id, secrets.token_urlsafe(24), t, t))
    db.commit()
    return token


def session_user(db, token):
    """(users row, csrf token) for a valid session cookie, else (None, None)."""
    if not token:
        return None, None
    row = db.execute(
        "SELECT s.token_hash, s.csrf, s.created_ts, s.last_ts, u.* FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (_token_hash(token),)).fetchone()
    if not row:
        return None, None
    t = now_ms()
    if not row["active"] or t - row["last_ts"] > SESSION_IDLE_MS or t - row["created_ts"] > SESSION_MAX_MS:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
        db.commit()
        return None, None
    if t - row["last_ts"] > 60_000:
        db.execute("UPDATE sessions SET last_ts = ? WHERE token_hash = ?", (t, row["token_hash"]))
        db.commit()
    return row, row["csrf"]


def end_session(db, token):
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
        db.commit()


def end_all_sessions(db, user_id):
    db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    db.commit()


class RateLimit:
    """At most `limit` failed logins per key in `window` seconds (kept in memory)."""

    def __init__(self, limit, window, clock=time.monotonic):
        self.limit, self.window, self.clock, self.fails = limit, window, clock, {}

    def _recent(self, key):
        t = self.clock()
        keep = [f for f in self.fails.get(key, []) if t - f < self.window]
        if keep:
            self.fails[key] = keep
        else:
            self.fails.pop(key, None)
        return keep

    def wait_seconds(self, key):
        """0 if another attempt is allowed now, else seconds until it is."""
        recent = self._recent(key)
        if len(recent) < self.limit:
            return 0
        return int(self.window - (self.clock() - recent[0])) + 1

    def fail(self, key):
        self.fails.setdefault(key, []).append(self.clock())

    def clear(self, key):
        self.fails.pop(key, None)
