"""The web app's tables, kept in the same data/wechat.db the scanner writes to:
users, login sessions, per-user read marks and an audit log of who did what."""
import sqlite3
import time

from scanner.store import open_db

ROLES = ("viewer", "sender", "approver", "admin")   # each role can do everything the ones before it can
CATEGORIES = ("supplier", "forwarder", "internal", "other")   # what kind of contact a chat is

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, role TEXT NOT NULL,
  pw_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  csrf TEXT NOT NULL, created_ts INTEGER NOT NULL, last_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS user_reads (
  user_id INTEGER NOT NULL, conv_id TEXT NOT NULL, last_read_ts INTEGER NOT NULL,
  PRIMARY KEY (user_id, conv_id));
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, user TEXT NOT NULL, action TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '');
"""


def now_ms():
    return int(time.time() * 1000)


def connect(path=None):
    db = open_db(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout = 10000")   # the scanner may be writing at the same moment
    if str(path) != ":memory:":
        db.execute("PRAGMA journal_mode = WAL")
    db.executescript(SCHEMA)
    if "category" not in {r[1] for r in db.execute("PRAGMA table_info(convs)")}:
        db.execute("ALTER TABLE convs ADD COLUMN category TEXT NOT NULL DEFAULT 'other'")
    return db


def can(user, role):
    """True if `user` (a users row) has `role` or a higher one."""
    return bool(user) and ROLES.index(user["role"]) >= ROLES.index(role)


def audit(db, user, action, detail=""):
    db.execute("INSERT INTO audit (ts, user, action, detail) VALUES (?, ?, ?, ?)",
               (now_ms(), user, action, detail))
    db.commit()
