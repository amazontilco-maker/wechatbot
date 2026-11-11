"""SKU generator: one coding standard for new SKUs, and decoding them again.

    TC-IPW-M03-N-0042
    |  |   |   | '--- running number: unique across all SKUs, never reused
    |  |   |   '----- reason: N new product, S supplier change, E engineering modification, X misc
    |  |   '--------- manufacturer: each manufacturer keeps the number it got the first time
    |  '------------- product code: 2-4 letters/digits, one code per product
    '---------------- brand: TC Tilcare, TW Tilchew, TR Tilcore, PM Pill Mill
"""
import re
import secrets
import sqlite3

from .db import now_ms

BRANDS = {"TC": "Tilcare", "TW": "Tilchew", "TR": "Tilcore", "PM": "Pill Mill"}
REASONS = {"N": "New product", "S": "Supplier change", "E": "Engineering modification", "X": "Misc"}
SKU_RE = re.compile(r"^(TC|TW|TR|PM)-([A-Z0-9]{2,4})-M(\d{2,})-([NSEX])-(\d{4,})$")
CODE_RE = re.compile(r"^[A-Z0-9]{2,4}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS manufacturers (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE, created_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS skus (
  id INTEGER PRIMARY KEY AUTOINCREMENT,   -- the running number; AUTOINCREMENT never reuses one
  sku TEXT NOT NULL UNIQUE, brand TEXT NOT NULL, product_name TEXT NOT NULL, product_code TEXT NOT NULL,
  manufacturer_id INTEGER NOT NULL REFERENCES manufacturers(id), reason TEXT NOT NULL,
  reason_note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
  created_by TEXT NOT NULL, created_ts INTEGER NOT NULL,
  cancelled_by TEXT NOT NULL DEFAULT '', cancelled_ts INTEGER, cancel_note TEXT NOT NULL DEFAULT '',
  used_ts INTEGER, used_tab TEXT NOT NULL DEFAULT '');   -- first seen in the stock sheet: adopted for good
"""


def migrate(db):
    cols = {r[1] for r in db.execute("PRAGMA table_info(skus)")}
    if "used_ts" not in cols:
        db.execute("ALTER TABLE skus ADD COLUMN used_ts INTEGER")
        db.execute("ALTER TABLE skus ADD COLUMN used_tab TEXT NOT NULL DEFAULT ''")


def mark_used(db, in_sheet):
    """Remember generated SKUs that now appear in the stock sheet (in_sheet: SKU -> (tab, product)).
    Once adopted a SKU stays 'in use', even if the sheet is later unlinked. Returns how many were new."""
    new = 0
    for row in db.execute("SELECT sku FROM skus WHERE used_ts IS NULL AND status = 'active'").fetchall():
        if row[0] in in_sheet:
            new += db.execute("UPDATE skus SET used_ts = ?, used_tab = ? WHERE sku = ?",
                              (now_ms(), in_sheet[row[0]][0], row[0])).rowcount
    if new:
        db.commit()
    return new

SELECT = ("SELECT s.*, m.name AS manufacturer FROM skus s JOIN manufacturers m ON m.id = s.manufacturer_id ")


class SkuError(Exception):
    pass


def clean(text, limit=80):
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def normalize(sku):
    """How SKUs are compared: upper case, no spaces."""
    return re.sub(r"\s+", "", str(sku or "")).upper()


def suggest_code(name):
    """'Insulated Ice Pack' -> IIP, 'Gloves' -> GLO, 'Pill Crusher V.2' -> PCV2."""
    words = re.findall(r"[A-Za-z0-9]+", name.upper())
    if not words:
        return ""
    if len(words) == 1:
        return words[0][:3]
    code = ""
    for w in words:
        code += w if w.isdigit() else w[0]
    return code[:4]


def manufacturer_code(mid):
    return f"M{mid:02d}"


def generate(db, user, brand, product_name, product_code, manufacturer, reason, reason_note="", taken=()):
    """Create the next SKU and return its row. `taken`: SKUs already in the stock sheet
    (normalized), which a new SKU must not equal. Raises SkuError with what to fix."""
    product_name, manufacturer, reason_note = clean(product_name), clean(manufacturer), clean(reason_note, 200)
    product_code = normalize(product_code)
    if brand not in BRANDS:
        raise SkuError("Pick a brand.")
    if reason not in REASONS:
        raise SkuError("Pick a reason for the SKU.")
    if reason == "X" and not reason_note:
        raise SkuError("For Misc, type the reason.")
    if len(product_name) < 2:
        raise SkuError("Type the product name.")
    if len(manufacturer) < 2:
        raise SkuError("Type the manufacturer name.")
    db.execute("BEGIN IMMEDIATE")   # one generator at a time, so numbers and codes can't collide
    try:
        same = db.execute("SELECT product_code FROM skus WHERE product_name = ? COLLATE NOCASE LIMIT 1",
                          (product_name,)).fetchone()
        if not product_code:        # one product keeps one code
            product_code = same[0] if same else suggest_code(product_name)
        if not CODE_RE.match(product_code):
            raise SkuError("The product code must be 2 to 4 letters or digits (for example IPW).")
        if same and same[0] != product_code:
            raise SkuError(f"'{product_name}' already uses the product code {same[0]}. "
                           "Use that code, or leave the code box empty.")
        other = db.execute("SELECT product_name FROM skus WHERE product_code = ? AND product_name <> ? "
                           "COLLATE NOCASE LIMIT 1", (product_code, product_name)).fetchone()
        if other:
            raise SkuError(f"The product code {product_code} already belongs to '{other[0]}'. "
                           "Choose a different code for this product.")
        row = db.execute("SELECT id FROM manufacturers WHERE name = ?", (manufacturer,)).fetchone()
        mid = row[0] if row else db.execute("INSERT INTO manufacturers (name, created_ts) VALUES (?, ?)",
                                            (manufacturer, now_ms())).lastrowid
        while True:   # normally once; a number is skipped if the stock sheet already has that exact SKU
            sid = db.execute(
                "INSERT INTO skus (sku, brand, product_name, product_code, manufacturer_id, reason, reason_note, "
                "created_by, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("pending-" + secrets.token_hex(8), brand, product_name, product_code, mid, reason,
                 reason_note, user, now_ms())).lastrowid
            sku = f"{brand}-{product_code}-{manufacturer_code(mid)}-{reason}-{sid:04d}"
            if sku not in taken:
                break
            db.execute("DELETE FROM skus WHERE id = ?", (sid,))
        db.execute("UPDATE skus SET sku = ? WHERE id = ?", (sku, sid))
        db.commit()
    except (SkuError, sqlite3.Error):
        db.rollback()
        raise
    return get(db, sku)


def get(db, sku):
    return db.execute(SELECT + "WHERE s.sku = ?", (normalize(sku),)).fetchone()


def cancel(db, user, sku, note):
    note = clean(note, 200)
    if not note:
        raise SkuError("Type why it is cancelled.")
    done = db.execute("UPDATE skus SET status = 'cancelled', cancelled_by = ?, cancelled_ts = ?, cancel_note = ? "
                      "WHERE sku = ? AND status = 'active'", (user, now_ms(), note, normalize(sku))).rowcount
    db.commit()
    if not done:
        raise SkuError("That SKU doesn't exist or is already cancelled.")


def search(db, q, limit=100, used=None):
    """Generated SKUs matching q in the SKU, product, manufacturer or creator; newest first.
    used: None = all, False = only never adopted into the stock sheet, True = only adopted."""
    q = clean(q)
    where = {None: "1", False: "s.used_ts IS NULL", True: "s.used_ts IS NOT NULL"}[used]
    if not q:
        return db.execute(SELECT + f"WHERE {where} ORDER BY s.id DESC LIMIT ?", (limit,)).fetchall()
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return db.execute(SELECT + f"WHERE {where} AND (s.sku LIKE ? ESCAPE '\\' OR s.product_name LIKE ? ESCAPE '\\' "
                      "OR m.name LIKE ? ESCAPE '\\' OR s.created_by LIKE ? ESCAPE '\\') ORDER BY s.id DESC LIMIT ?",
                      (like, like, like, like, limit)).fetchall()


def decode(db, text):
    """What a SKU's parts mean, read from the SKU itself and checked against the records.
    Returns None if `text` isn't in the standard format."""
    m = SKU_RE.match(normalize(text))
    if not m:
        return None
    brand, code, mnum, reason, number = m.groups()
    row = get(db, text)
    maker = db.execute("SELECT name FROM manufacturers WHERE id = ?", (int(mnum),)).fetchone()
    product = db.execute("SELECT product_name FROM skus WHERE product_code = ? LIMIT 1", (code,)).fetchone()
    return {"sku": normalize(text), "record": row, "parts": [
        (brand, "Brand", BRANDS[brand]),
        (code, "Product", row["product_name"] if row else (product[0] if product else "code not in the records")),
        ("M" + mnum, "Manufacturer", maker[0] if maker else "number not in the records"),
        (reason, "Reason", REASONS[reason] + (f": {row['reason_note']}" if row and row["reason_note"] else "")),
        (number, "Running number", "unique, never reused"),
    ]}
