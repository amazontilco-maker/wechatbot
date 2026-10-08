"""The team web app: login, Today, chat inbox and thread view, stock risk, search and notes.

Pages are plain server-rendered HTML (no JavaScript needed). The server listens on
127.0.0.1 only; people outside the office reach it through Cloudflare Tunnel.
"""
import json
import os
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from scanner.store import DEFAULT_DB

from . import auth, gsheet, stock
from .db import CATEGORIES, audit, can, connect, get_setting, now_ms, set_setting

HERE = Path(__file__).resolve().parent
COOKIE = "wb_session"
PAGE = 100   # messages per thread page
MAX_UPLOAD = 80 * 1024 * 1024

templates = Jinja2Templates(directory=str(HERE / "templates"))


def fmt_ts(ts):
    return datetime.fromtimestamp(ts / 1000).strftime("%d %b %H:%M") if ts else ""


templates.env.filters["ts"] = fmt_ts
templates.env.filters["q"] = lambda s: quote(str(s), safe="")


def ago(ms):
    """'5 min ago', '3 h ago', '2 days ago'."""
    if not ms:
        return "never"
    sec = max(0, (now_ms() - ms) // 1000)
    if sec < 90:
        return "just now"
    if sec < 5400:
        return f"{sec // 60} min ago"
    if sec < 172800:
        return f"{sec // 3600} h ago"
    return f"{sec // 86400} days ago"


templates.env.filters["ago"] = ago


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


def create_app(db_path=None, background=True, sheets_client=None):
    """background: re-read the stock Google Sheet every 15 minutes while the app runs.
    sheets_client: stand-in for the Google API (tests)."""
    @asynccontextmanager
    async def lifespan(app):
        if background:
            app.state.sync.start()
        yield
        app.state.sync.stop()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    sheets_client = sheets_client or gsheet.GoogleSheets
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
    sync = gsheet.SheetSync(db_path, data_dir, client_factory=sheets_client)
    app.state.sync = sync
    cache, cache_lock = {}, threading.Lock()

    def stock_file(db):
        """Without a Google Sheet: a path set on the PC, else the last file uploaded in the app."""
        return Path(get_setting(db, "stock_path") or data_dir / "stock.xlsx")

    def stock_settings(db, request=None):
        q = request.query_params if request else {}
        st = q.get("stock") if q.get("stock") in stock.STOCK_MEASURES else get_setting(db, "stock_measure", "onhand")
        sa = q.get("sales") if q.get("sales") in stock.SALES_MEASURES else get_setting(db, "sales_measure", "max")
        return {"tabs": json.loads(get_setting(db, "stock_tabs", "[]")),
                "threshold": int(get_setting(db, "stock_threshold", "60")),
                "stale_days": int(get_setting(db, "stale_days", "2")), "stock": st, "sales": sa}

    def cached(key, make):
        """Parsed tabs, worked out again only when the data changes (a big sheet takes seconds)."""
        with cache_lock:
            if key not in cache:
                cache.clear()
                cache[key] = make()
            return cache[key]

    def sheet_tabs(snap, names):
        def make():
            return [stock.parse_rows(snap["rows"][n], n) if n in snap["rows"] else
                    stock.Tab(n, problem="not read yet, press Refresh" if n in dict(snap["tabs_all"])
                              else "tab not in the sheet") for n in names]
        return cached(("sheet", snap["sheet_id"], snap["fetched_ms"], tuple(names)), make)

    def stock_report(db, request=None):
        """Everything the Stock risk page and the Today card show."""
        cfg = stock_settings(db, request)
        rep = {"cfg": cfg, "ready": False, "error": "", "warnings": [], "all_tabs": [], "tabs": []}
        sid = get_setting(db, "sheet_id")
        if sid:
            snap = sync.snapshot(sid)
            rep["source"] = {"kind": "sheet", "url": gsheet.sheet_url(sid), "title": (snap or {}).get("title", ""),
                             "fetched_ms": (snap or {}).get("fetched_ms"),
                             "modified_ms": (snap or {}).get("modified_ms"),
                             "modified_by": (snap or {}).get("modified_by", "")}
            rep["error"] = get_setting(db, "sheet_error")
            if snap:
                rep["all_tabs"] = snap["tabs_all"]
                rep["tabs"] = sheet_tabs(snap, cfg["tabs"]) if cfg["tabs"] else []
                now = now_ms()
                if snap.get("modified_ms") and now - snap["modified_ms"] > cfg["stale_days"] * 86_400_000:
                    rep["warnings"].append(f"Nobody has edited the sheet for {(now - snap['modified_ms']) // 86_400_000} "
                                           "days, so sales and stock figures may be out of date.")
                if now - snap["fetched_ms"] > 2 * 3600_000:
                    rep["warnings"].append("These figures were read more than 2 hours ago; the app can't refresh "
                                           "them right now (see the message above).")
        else:
            path = stock_file(db)
            rep["source"] = {"kind": "file", "path": path, "exists": path.exists()}
            if path.exists():
                try:
                    mtime = path.stat().st_mtime_ns
                    rep["source"]["modified_ms"] = mtime // 1_000_000
                    rep["all_tabs"] = cached(("names", str(path), mtime), lambda: stock.tab_names(path))
                    if cfg["tabs"]:
                        rep["tabs"] = cached(("file", str(path), mtime, tuple(cfg["tabs"])),
                                             lambda: stock.read_tabs(path, cfg["tabs"]))
                except Exception as e:   # a broken or half-synced file shouldn't take the page down
                    rep["error"] = f"Could not read the file: {e}"
        try:
            rows, counts = stock.risk_rows(rep["tabs"], cfg["stock"], cfg["sales"], cfg["threshold"])
        except Exception as e:   # odd values in the sheet must never take the pages down
            rows, counts = [], {k: 0 for k in ("out", "critical", "low", "nostock", "ok", "nosales")}
            rep["error"] = f"Could not work out days of stock: {e}"
        rep.update(rows=rows, counts=counts, ready=bool(rep["tabs"]),
                   flagged=counts["out"] + counts["critical"] + counts["low"])
        return rep

    @app.get("/risk", response_class=HTMLResponse)
    def risk(request: Request, tab: str = "", show: str = "flagged", user=Depends(current), db=Depends(get_db)):
        rep = stock_report(db, request)
        rows = rep["rows"]
        if tab:
            rows = [r for r in rows if r["item"].tab == tab]
        if show == "flagged":
            rows = [r for r in rows if r["level"] in ("out", "critical", "low", "nostock")]
        can_set = can(user, "approver")
        return page(request, "risk.html", section="risk", rep=rep, rows=rows, tab=tab, show=show, can_set=can_set,
                    sheet_link=get_setting(db, "sheet_url"), from_pc=bool(get_setting(db, "stock_path")),
                    robot=gsheet.robot_email(sync.key_path) if can_set else "",
                    stock_measures=stock.STOCK_MEASURES, sales_measures=stock.SALES_MEASURES)

    @app.post("/risk/refresh")
    def risk_refresh(request: Request, csrf: str = Form(""), user=Depends(current), db=Depends(get_db)):
        check_csrf(request, csrf)
        last = int(get_setting(db, "sheet_attempt_ms", "0") or 0)
        if get_setting(db, "sheet_id") and now_ms() - last > 20_000:   # one read at a time, not every click
            sync.refresh()
        return RedirectResponse("/risk", status_code=303)

    @app.post("/risk/reset")
    def risk_reset(request: Request, confirm: str = Form(""), csrf: str = Form(""),
                   user=Depends(needs("approver")), db=Depends(get_db)):
        """Forget the stock sheet: unlink it and delete every stock figure the app saved
        (the sheet copy, an uploaded file, the ticked tabs). The rules (60 days etc.) stay."""
        check_csrf(request, csrf)
        if confirm != "yes":
            return page(request, "message.html", 400, title="Nothing was reset",
                        text="Tick the box to confirm, then press Reset.")
        with sync.lock:   # wait for any read in progress, so it can't write the old data back
            for key in ("sheet_id", "sheet_url", "sheet_error", "sheet_attempt_ms", "stock_tabs", "stock_path"):
                set_setting(db, key, "[]" if key == "stock_tabs" else "")
            for name in (gsheet.SNAPSHOT_NAME, "stock.xlsx", "stock.upload.xlsx"):
                (data_dir / name).unlink(missing_ok=True)
        with cache_lock:
            cache.clear()
        audit(db, user["name"], "stock reset", "sheet unlinked, stock data deleted")
        return RedirectResponse("/risk", status_code=303)

    @app.post("/risk/sheet")
    def risk_sheet(request: Request, link: str = Form(""), csrf: str = Form(""),
                   user=Depends(needs("approver")), db=Depends(get_db)):
        check_csrf(request, csrf)
        link = link.strip()
        if not link:
            set_setting(db, "sheet_id", "")
            set_setting(db, "sheet_url", "")
            audit(db, user["name"], "stock sheet", "removed")
            return RedirectResponse("/risk", status_code=303)
        try:
            sid = gsheet.sheet_id_from(link)
        except gsheet.SheetError as e:
            return page(request, "message.html", 400, title="That link can't be used", text=str(e))
        set_setting(db, "sheet_id", sid)
        set_setting(db, "sheet_url", gsheet.sheet_url(sid))
        audit(db, user["name"], "stock sheet", gsheet.sheet_url(sid))
        sync.refresh()
        return RedirectResponse("/risk", status_code=303)

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

        def number(name, default, low, high):
            try:
                return max(low, min(high, int(form.get(name) or default)))
            except ValueError:
                return default
        threshold = number("threshold", 60, 1, 365)
        set_setting(db, "stock_tabs", json.dumps(tabs))
        set_setting(db, "stock_threshold", threshold)
        set_setting(db, "stale_days", number("stale_days", 2, 1, 60))
        if form.get("stock") in stock.STOCK_MEASURES:
            set_setting(db, "stock_measure", form.get("stock"))
        if form.get("sales") in stock.SALES_MEASURES:
            set_setting(db, "sales_measure", form.get("sales"))
        audit(db, user["name"], "stock settings", f"{threshold} days; tabs: {', '.join(tabs)}")
        if get_setting(db, "sheet_id"):
            await run_in_threadpool(sync.refresh)   # read the newly ticked tabs now
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
