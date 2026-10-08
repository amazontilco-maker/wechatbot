"""The team web app: login, chat inbox, thread view, search and notes.

Pages are plain server-rendered HTML (no JavaScript needed). The server listens on
127.0.0.1 only; people outside the office reach it through Cloudflare Tunnel.
"""
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth
from .db import CATEGORIES, audit, can, connect

HERE = Path(__file__).resolve().parent
COOKIE = "wb_session"
PAGE = 100   # messages per thread page

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
        return page(request, "today.html", section="today", waiting=waiting[:8],
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
        "risk": ("Stock risk", "M6", "SKUs that run out before their next shipment arrives, with options "
                 "(air, AWD/3PL transfer, slower sales).", ["Market", "SKU", "Runs out", "Next ETA", "Gap"]),
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
