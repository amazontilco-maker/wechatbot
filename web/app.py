"""The team web app: login, Today, chat inbox and thread view, stock risk, search and notes.

Pages are plain server-rendered HTML (no JavaScript needed). The server listens on
127.0.0.1 only; people outside the office reach it through Cloudflare Tunnel.
"""
import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from scanner.store import DEFAULT_DB

from . import auth, stock
from .db import CATEGORIES, audit, can, connect, get_setting, set_setting

HERE = Path(__file__).resolve().parent
COOKIE = "wb_session"
PAGE = 100   # messages per thread page
MAX_UPLOAD = 80 * 1024 * 1024

templates = Jinja2Templates(directory=str(HERE / "templates"))


def fmt_ts(ts):
    return datetime.fromtimestamp(ts / 1000).strftime("%d %b %H:%M") if ts else ""


templates.env.filters["ts"] = fmt_ts
templates.env.filters["q"] = lambda s: quote(str(s), safe="")


class LoginNeeded(Exception):
    pass


class Forbidden(Exception):
    pass


def client_ip(request):
    # cloudflared passes the visitor's address; local visitors come straight in
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")


def is_https(request):
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def safe_next(path):
    return path if path and path.startswith("/") and not path.startswith("//") and "\\" not in path else "/"


def create_app(db_path=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    user_limit = auth.RateLimit(5, 15 * 60)    # 5 wrong passwords per name per 15 minutes
    ip_limit = auth.RateLimit(20, 15 * 60)     # 20 per address

    def get_db():
        db = connect(db_path)
        try:
            yield db
        finally:
            db.close()

    def current(request: Request, db: sqlite3.Connection = Depends(get_db)):
        user, csrf = auth.session_user(db, request.cookies.get(COOKIE))
        if not user:
            raise LoginNeeded()
        request.state.user, request.state.csrf = user, csrf
        return user

    def needs(role):
        def check(user=Depends(current)):
            if not can(user, role):
                raise Forbidden()
            return user
        return check

    def check_csrf(request, token):
        if not token or token != request.state.csrf:
            raise Forbidden()

    def page(request, name, status_code=200, **ctx):
        state = request.state
        ctx.update(request=request, me=getattr(state, "user", None), csrf=getattr(state, "csrf", ""))
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)

    @app.middleware("http")
    async def headers(request, call_next):
        resp = await call_next(request)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; form-action 'self'"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(LoginNeeded)
    async def to_login(request, exc):
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)

    @app.exception_handler(Forbidden)
    async def forbidden(request, exc):
        return page(request, "message.html", 403, title="Not allowed",
                    text="Your login doesn't have permission for this. Ask an admin if you need it.")

    # ---------- login ----------
    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/"):
        return page(request, "login.html", next=safe_next(next), error="")

    @app.post("/login")
    def login_submit(request: Request, name: str = Form(""), password: str = Form(""),
                     next: str = Form("/"), db=Depends(get_db)):
        ip, who = client_ip(request), name.strip().lower()
        wait = max(user_limit.wait_seconds(who), ip_limit.wait_seconds(ip))
        if wait:
            return page(request, "login.html", 429, next=safe_next(next),
                        error=f"Too many wrong attempts. Try again in {wait // 60 + 1} minutes.")
        user = auth.login(db, name, password)
        if not user:
            user_limit.fail(who)
            ip_limit.fail(ip)
            audit(db, name.strip()[:60] or "?", "login failed", ip)
            return page(request, "login.html", 401, next=safe_next(next), error="Wrong name or password.")
        user_limit.clear(who)
        audit(db, user["name"], "login", ip)
        resp = RedirectResponse(safe_next(next), status_code=303)
        resp.set_cookie(COOKIE, auth.new_session(db, user["id"]), max_age=auth.SESSION_MAX_MS // 1000,
                        httponly=True, samesite="lax", secure=is_https(request), path="/")
        return resp

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form(""), user=Depends(current), db=Depends(get_db)):
        check_csrf(request, csrf)
        auth.end_session(db, request.cookies.get(COOKIE))
        audit(db, user["name"], "logout")
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE, path="/")
        return resp

    # ---------- workspace ----------
    def chat_list(db, user, category=""):
        rows = db.execute("""
            SELECT c.id, c.name, c.kind, c.notes, c.category, s.seen,
              (SELECT MAX(ts) FROM messages m WHERE m.conv_id = c.id) AS last_ts,
              (SELECT COUNT(*) FROM messages m WHERE m.conv_id = c.id AND m.direction = 'in'
                 AND m.ts > s.seen) AS new,
              (SELECT m.sender || ': ' || m.text FROM messages m WHERE m.conv_id = c.id
                 ORDER BY m.ts DESC LIMIT 1) AS preview
            FROM convs c
            JOIN (SELECT c2.id AS cid, COALESCE(r.last_read_ts, c2.last_read_ts) AS seen FROM convs c2
                  LEFT JOIN user_reads r ON r.conv_id = c2.id AND r.user_id = ?) s ON s.cid = c.id
            ORDER BY last_ts IS NULL, last_ts DESC""", (user["id"],)).fetchall()
        return [r for r in rows if not category or r["category"] == category]

    @app.get("/", response_class=HTMLResponse)
    def today(request: Request, user=Depends(current), db=Depends(get_db)):
        chats = chat_list(db, user)
        waiting = [c for c in chats if c["new"]]
        counts = {k: sum(1 for c in chats if c["category"] == k) for k in CATEGORIES}
        risk = stock_report(db)
        return page(request, "today.html", section="today", waiting=waiting[:8], risk=risk,
                    new_total=sum(c["new"] for c in chats), chats_waiting=len(waiting), counts=counts)

    @app.get("/messages", response_class=HTMLResponse)
    def messages(request: Request, new: int = 0, cat: str = "", user=Depends(current), db=Depends(get_db)):
        cat = cat if cat in CATEGORIES else ""
        chats = [c for c in chat_list(db, user, cat) if c["new"] or not new]
        return page(request, "messages.html", section="messages", chats=chats, only_new=bool(new),
                    cat=cat, categories=CATEGORIES, conv=None)

    @app.get("/chat", response_class=HTMLResponse)
    def thread(request: Request, id: str, before: int = 0, cat: str = "", user=Depends(current),
               db=Depends(get_db)):
        cat = cat if cat in CATEGORIES else ""
        conv = db.execute("SELECT * FROM convs WHERE id = ?", (id,)).fetchone()
        if not conv:
            return page(request, "message.html", 404, title="Not found", text="That chat doesn't exist.")
        seen = db.execute("SELECT last_read_ts FROM user_reads WHERE user_id = ? AND conv_id = ?",
                          (user["id"], id)).fetchone()
        seen = seen[0] if seen else conv["last_read_ts"]
        rows = db.execute(
            "SELECT * FROM messages WHERE conv_id = ? AND (? = 0 OR ts < ?) ORDER BY ts DESC, rowid DESC LIMIT ?",
            (id, before, before, PAGE + 1)).fetchall()
        older = len(rows) > PAGE
        msgs = list(reversed(rows[:PAGE]))
        if not before and msgs:   # looking at the latest messages marks the chat read for this user
            db.execute("INSERT INTO user_reads (user_id, conv_id, last_read_ts) VALUES (?, ?, ?) "
                       "ON CONFLICT(user_id, conv_id) DO UPDATE SET last_read_ts = "
                       "MAX(last_read_ts, excluded.last_read_ts)", (user["id"], id, msgs[-1]["ts"]))
            db.commit()
        return page(request, "messages.html", section="messages", conv=conv, msgs=msgs, seen=seen,
                    older=msgs[0]["ts"] if older else 0, can_edit=can(user, "sender"),
                    chats=chat_list(db, user, cat), only_new=False, cat=cat, categories=CATEGORIES)

    @app.post("/chat/category")
    def save_category(request: Request, id: str = Form(...), category: str = Form(...), csrf: str = Form(""),
                      user=Depends(needs("sender")), db=Depends(get_db)):
        check_csrf(request, csrf)
        if category in CATEGORIES and db.execute(
                "UPDATE convs SET category = ? WHERE id = ?", (category, id)).rowcount:
            db.commit()
            audit(db, user["name"], "chat type", f"{id}: {category}")
        return RedirectResponse(f"/chat?id={quote(id, safe='')}", status_code=303)

    # ---------- stock risk ----------
    data_dir = Path(db_path or os.environ.get("WB_DB") or DEFAULT_DB).resolve().parent
    cache, cache_lock = {}, threading.Lock()

    def stock_file(db):
        """The workbook to read: a path set on the PC (e.g. a Google Drive for desktop folder),
        else the last file uploaded in the app."""
        return Path(get_setting(db, "stock_path") or data_dir / "stock.xlsx")

    def stock_settings(db, request=None):
        q = request.query_params if request else {}
        st = q.get("stock") if q.get("stock") in stock.STOCK_MEASURES else get_setting(db, "stock_measure", "onhand")
        sa = q.get("sales") if q.get("sales") in stock.SALES_MEASURES else get_setting(db, "sales_measure", "max")
        return {"tabs": json.loads(get_setting(db, "stock_tabs", "[]")),
                "threshold": int(get_setting(db, "stock_threshold", "60")), "stock": st, "sales": sa}

    def load_tabs(path, names):
        """Parsed tabs, re-read only when the file changes (a 5 MB workbook takes seconds)."""
        key = (str(path), path.stat().st_mtime_ns, tuple(names))
        with cache_lock:
            if key not in cache:
                cache.clear()
                cache[key] = stock.read_tabs(path, names)
            return cache[key]

    def stock_report(db, request=None):
        """Everything the Stock risk page and the Today card show; None if not set up yet."""
        cfg = stock_settings(db, request)
        path = stock_file(db)
        if not path.exists():
            return {"cfg": cfg, "path": path, "error": "", "ready": False}
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%d %b %Y %H:%M")
            tabs = load_tabs(path, cfg["tabs"]) if cfg["tabs"] else []
        except Exception as e:   # a broken or half-synced file shouldn't take the page down
            return {"cfg": cfg, "path": path, "error": f"Could not read the file: {e}", "ready": False}
        rows, counts = stock.risk_rows(tabs, cfg["stock"], cfg["sales"], cfg["threshold"])
        return {"cfg": cfg, "path": path, "error": "", "ready": bool(cfg["tabs"]), "mtime": mtime,
                "tabs": tabs, "rows": rows, "counts": counts,
                "flagged": counts["out"] + counts["critical"] + counts["low"]}

    @app.get("/risk", response_class=HTMLResponse)
    def risk(request: Request, tab: str = "", show: str = "flagged", user=Depends(current), db=Depends(get_db)):
        rep = stock_report(db, request)
        all_tabs = []
        if rep["path"].exists() and not rep["error"]:
            try:
                all_tabs = stock.tab_names(rep["path"])
            except Exception as e:
                rep["error"] = f"Could not read the file: {e}"
        rows = rep.get("rows", [])
        if tab:
            rows = [r for r in rows if r["item"].tab == tab]
        if show == "flagged":
            rows = [r for r in rows if r["level"] in ("out", "critical", "low")]
        return page(request, "risk.html", section="risk", rep=rep, rows=rows, tab=tab, show=show,
                    all_tabs=all_tabs, can_set=can(user, "approver"), from_pc=bool(get_setting(db, "stock_path")),
                    stock_measures=stock.STOCK_MEASURES, sales_measures=stock.SALES_MEASURES)

    @app.post("/risk/upload")
    async def risk_upload(request: Request, file: UploadFile = File(...), csrf: str = Form(""),
                          user=Depends(needs("approver")), db=Depends(get_db)):
        check_csrf(request, csrf)
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD or not data.startswith(b"PK"):   # .xlsx files are zip archives
            return page(request, "message.html", 400, title="Upload failed",
                        text="That isn't an Excel .xlsx file (or it is over 80 MB).")
        data_dir.mkdir(parents=True, exist_ok=True)
        tmp = data_dir / "stock.upload.xlsx"
        tmp.write_bytes(data)
        try:
            stock.tab_names(tmp)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            return page(request, "message.html", 400, title="Upload failed", text=f"Could not open it as Excel: {e}")
        os.replace(tmp, data_dir / "stock.xlsx")
        audit(db, user["name"], "stock file", f"uploaded {file.filename} ({len(data) // 1024} KB)")
        return RedirectResponse("/risk", status_code=303)

    @app.post("/risk/settings")
    async def risk_settings(request: Request, user=Depends(needs("approver")), db=Depends(get_db)):
        form = await request.form()
        check_csrf(request, form.get("csrf"))
        tabs = [t for t in form.getlist("tabs") if isinstance(t, str)]
        try:
            threshold = max(1, min(365, int(form.get("threshold") or 60)))
        except ValueError:
            threshold = 60
        set_setting(db, "stock_tabs", json.dumps(tabs))
        set_setting(db, "stock_threshold", threshold)
        if form.get("stock") in stock.STOCK_MEASURES:
            set_setting(db, "stock_measure", form.get("stock"))
        if form.get("sales") in stock.SALES_MEASURES:
            set_setting(db, "sales_measure", form.get("sales"))
        audit(db, user["name"], "stock settings", f"{threshold} days; tabs: {', '.join(tabs)}")
        return RedirectResponse("/risk", status_code=303)

    # sections that later milestones fill in; each page says what will appear there
    SOON = {
        "shipments": ("Shipments", "M2", "Every inbound shipment from the stock sheet, per market: SKU, route "
                      "(Air / Fast ocean / Ocean to FBA, AWD or 3PL), units, forwarder, ETA and when it was "
                      "last confirmed.", ["Market", "SKU", "Route", "Units", "Forwarder", "ETA", "Last confirmed"]),
        "updates": ("ETA updates", "M3", "New ETAs and delays that Claude finds in forwarder chats, matched to "
                    "shipments: old ETA, new ETA and the message it came from. Approve, edit or reject; approved "
                    "ones are written to the stock sheet with a note (M4).",
                    ["Shipment", "Old ETA", "New ETA", "Source message", "Status"]),
        "chasers": ("ETA chasers", "M5", "Shipments nobody has confirmed for a few days, grouped by forwarder, "
                    "with a drafted message asking for an update. Approve & send goes through the phone.",
                    ["Forwarder", "Shipments", "Last confirmed", "Draft"]),
        "approvals": ("Approvals", "step 2", "Replies waiting to be approved, with Approve & send and Reject.",
                      ["Chat", "Message", "Asked by", "When"]),
        "health": ("Health", "step 3", "Phone connected, WeChat logged in, last scan, next scan, phone queue.",
                   ["Check", "Status"]),
    }

    def soon_page(key):
        def view(request: Request, user=Depends(current)):
            title, when, text, cols = SOON[key]
            return page(request, "soon.html", section=key, title=title, when=when, text=text, cols=cols)
        app.get(f"/{key}", response_class=HTMLResponse)(view)

    for key in SOON:
        soon_page(key)

    @app.post("/chat/notes")
    def save_notes(request: Request, id: str = Form(...), notes: str = Form(""), csrf: str = Form(""),
                   user=Depends(needs("sender")), db=Depends(get_db)):
        check_csrf(request, csrf)
        notes = notes.strip()[:4000]
        if db.execute("UPDATE convs SET notes = ? WHERE id = ?", (notes, id)).rowcount:
            audit(db, user["name"], "notes", f"{id}: {notes[:200]}")
        return RedirectResponse(f"/chat?id={quote(id, safe='')}", status_code=303)

    @app.get("/search", response_class=HTMLResponse)
    def search(request: Request, q: str = "", user=Depends(current), db=Depends(get_db)):
        q = q.strip()
        rows = []
        if q:
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            rows = db.execute(
                "SELECT m.*, c.name AS conv FROM messages m JOIN convs c ON c.id = m.conv_id "
                "WHERE m.text LIKE ? ESCAPE '\\' OR m.sender LIKE ? ESCAPE '\\' ORDER BY m.ts DESC LIMIT 100",
                (like, like)).fetchall()
        return page(request, "search.html", section="search", q=q, results=rows)

    @app.get("/audit", response_class=HTMLResponse)
    def audit_log(request: Request, user=Depends(needs("approver")), db=Depends(get_db)):
        rows = db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 300").fetchall()
        return page(request, "audit.html", section="log", rows=rows)

    return app
