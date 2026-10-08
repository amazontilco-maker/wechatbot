"""Write scanned messages into the same SQLite database the `wb` commands read (data/wechat.db)."""
import hashlib
import os
import re
import sqlite3
import time
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "wechat.db"


def open_db(path=None):
    path = path or os.environ.get("WB_DB") or DEFAULT_DB
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path))
    db.executescript("""
        CREATE TABLE IF NOT EXISTS convs (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
          notes TEXT DEFAULT '', last_read_ts INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS messages (
          id TEXT PRIMARY KEY, conv_id TEXT NOT NULL REFERENCES convs(id),
          sender TEXT NOT NULL, direction TEXT NOT NULL, type TEXT NOT NULL,
          text TEXT NOT NULL, ts INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_msg_conv_ts ON messages(conv_id, ts);
    """)
    cols = {r[1] for r in db.execute("PRAGMA table_info(messages)")}
    if "shown_time" not in cols:  # the time label WeChat displayed above the message
        db.execute("ALTER TABLE messages ADD COLUMN shown_time TEXT DEFAULT ''")
    return db


def conv_id(name):
    return f"ui:{name}"


def is_group(title):
    """WeChat shows group titles as 'Name (12)'."""
    return bool(re.search(r"\(\d+\)\s*$", title))


def group_name(title):
    return re.sub(r"\s*\(\d+\)\s*$", "", title).strip()


def _key(direction, text):
    return (direction, text)


def stored_tail(db, cid, n=5):
    rows = db.execute(
        "SELECT direction, type, text FROM messages WHERE conv_id = ? ORDER BY ts DESC, rowid DESC LIMIT ?",
        (cid, n)).fetchall()
    return [("image", "") if t == "image" else _key(d, txt) for d, t, txt in reversed(rows)]


def new_part(tail, batch_keys):
    """Index in batch where new messages start, given the stored tail."""
    if not tail:
        return 0
    # the stored tail (or its last few messages) appears somewhere in the batch: take what follows it
    for size in range(min(3, len(tail)), 0, -1):
        want = tail[-size:]
        if all(k == ("image", "") for k in want):
            continue  # pictures alone are too vague to line up on
        for j in range(len(batch_keys) - size, -1, -1):
            if batch_keys[j:j + size] == want:
                return j + size
    return 0


def ingest(db, title, messages, now=None):
    """messages: oldest-first parse.Message list. Returns (added, skipped)."""
    kind = "room" if is_group(title) else "person"
    name = group_name(title) if kind == "room" else title
    cid = conv_id(name)
    db.execute(
        "INSERT INTO convs (id, name, kind) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET name = excluded.name",
        (cid, name, kind))
    keys = [m.key() for m in messages]
    start = new_part(stored_tail(db, cid), keys)
    fresh = messages[start:]
    last = db.execute("SELECT MAX(ts) FROM messages WHERE conv_id = ?", (cid,)).fetchone()[0] or 0
    base = max(int((now or time.time()) * 1000), last + 1)
    for i, m in enumerate(fresh):
        direction = "out" if m.side == "out" else "in"
        sender = "me" if direction == "out" else (m.sender or name)
        mtype = "image" if m.side == "image" else "text"
        text = f"[image text] {m.text}" if mtype == "image" else m.text
        mid = "scan:" + hashlib.sha1(f"{cid}|{base}|{i}|{m.text}".encode()).hexdigest()[:16]
        db.execute(
            "INSERT OR IGNORE INTO messages (id, conv_id, sender, direction, type, text, ts, shown_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (mid, cid, sender, direction, mtype, text, base + i, m.time_label))
    db.commit()
    return len(fresh), start
