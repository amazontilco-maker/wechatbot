# Handover: where the project stands (11 Nov 2026)

For whoever continues this work (a person or a new Claude Code chat). Start a new chat with:
**"Read HANDOFF.md, ROADMAP.md, README.md and CLAUDE.md. Let's continue."**

## Where everything is
- **Source code**: GitHub `amazontilco-maker/wechatbot`. All work described here is on the branch
  `claude/tender-pasteur-wckt5m`; it is **not merged into `main` yet**.
- **Office PC (Windows)**: the same code at `C:\Users\Umer\wechatbot` (`%USERPROFILE%\wechatbot`).
  Python is run with `py`; the adb path is saved in `WB_ADB`.
- **Live data, only on the office PC, never in GitHub**: `C:\Users\Umer\wechatbot\data\`
  - `wechat.db`: logins, scanned chats, notes, settings, generated SKUs, activity log
  - `google-service-account.json`: the Google robot login key (treat as a password)
  - `stock_sheet.json`: the last copy read from the stock Google Sheet
- **Phone**: Samsung A51 on USB with the dedicated WeChat account (scan and send work from the terminal).

## Folder map
```
wechatbot/
  HANDOFF.md      this file
  ROADMAP.md      milestones M0-M6 and the decisions already made
  README.md       setup and commands
  CLAUDE.md       rules for Claude (never sends WeChat messages itself)
  scanner/        reads the phone: ADB screenshots + OCR -> data/wechat.db; `py -m scanner scan|send`
    __main__.py   scan and send commands      device.py  adb control
    parse.py      OCR lines -> messages       ocr.py     RapidOCR wrapper
    store.py      writes messages to SQLite   tests/     23 parser tests
  web/            the team web app; `py -m web serve` -> http://127.0.0.1:8000
    __main__.py   commands: serve, adduser, setrole, passwd, disable, enable, users,
                  google, sheetcheck, stockfile, stockcheck
    app.py        all pages and routes (FastAPI, server-rendered, almost no JavaScript)
    auth.py       passwords (scrypt), sessions, login rate limit
    db.py         web tables: users, sessions, user_reads, settings, audit
    stock.py      reads stock tabs, works out days of stock and risk levels
    gsheet.py     reads the Google Sheet with the service account, refresh every 15 min
    sku.py        SKU generator: the coding standard, unique numbers, decode
    templates/    one HTML file per page      static/    style.css, app.js
    tests/        27 tests: `py -m unittest discover -s web/tests -t .`
  src/, test/     older Node tools: `node src/cli.js digest|convs|thread|search|note|read`
  data/           created on the PC, not in Git (see above)
```

## What is built
- **Logins and roles**: viewer < sender < approver < admin; activity log.
- **Workspace layout** (Lark style): Today, Shipments, ETA updates, Stock risk, SKU generator, Messages,
  Approvals, ETA chasers, Search, Health, Activity log. Shipments, ETA updates, Approvals, ETA chasers and
  Health are placeholders that say which milestone fills them.
- **Messages**: chat list + conversation, supplier/forwarder/internal/other label per chat, notes, search.
- **Stock risk**: reads the stock Google Sheet read-only every 15 minutes (+ Refresh now), shows which sheet
  it reads, flags SKUs under 60 days of stock (out = light red, critical = light orange, low = yellow,
  "No stock figure" = sells but stock cell empty), stale-data warning, Reset button. Excel upload is a fallback.
- **SKU generator**: `BRAND-PRODUCT-MANUFACTURER-REASON-NUMBER`, e.g. `TC-IPW-M03-N-0042`.
  Brands TC Tilcare, TW Tilchew, TR Tilcore, PM Pill Mill. Reasons N new product, S supplier change,
  E engineering modification, X misc (text required). One number per manufacturer, one code per product,
  running number never reused. Copy button, search and decode, cancel with a reason. A SKU that appears in
  the stock sheet is marked adopted and leaves the "not used yet" list (still decodable by search).

## Not confirmed yet (needs the office PC)
- The crash fix for SKUs with sales but an empty stock cell, and everything after it, has not been confirmed
  on the office PC. Run `git pull` there, then `py -m web serve`.
- Google setup may be unfinished: convert the stock file to a real Google Sheet, create the service account
  and key, `py -m web google KEY.json`, share the sheet with the robot email as Viewer, paste the link in
  Stock risk -> Settings, tick the live tabs. The file shared so far was an old Excel file with 34 tabs;
  which tabs are live is the owner's call.
- Nothing has been checked against the real sheet's numbers yet.

## Open questions for the owner
- Should cancelled SKUs also leave the "not used yet" list (today they stay, labelled Cancelled)?
- Are the brand letters (TC/TW/TR/PM) and reason letters (N/S/E/X) final? Changing them later makes old
  and new SKUs inconsistent.
- No market (US/UK/...) is in the SKU; add one?

## Still to build
- **M1 step 2**: reply box with @tag -> Request send -> Approve & send through one phone queue. A sender's
  reply waits for an approver; an approver or admin sends their own reply in one click. Every send logged.
- **M1 step 3**: WeChat scans every 15 minutes through the same queue; Health page.
- **M1 step 4**: Cloudflare Tunnel (team link from anywhere) + a page to manage logins.
- **M1b** (on `main`'s ROADMAP): reading pictures and files in chats.
- **M2-M6**: see ROADMAP.md. Decided for later: pull sales and stock from Amazon SP-API (and Walmart)
  instead of typing them into the sheet.

## How the owner likes to work
- Not a developer: every command in its own copyable block, one line each, with a plain explanation.
- Show a short plan and ask questions before building; build in small steps that can be tested on the PC.
- Goal: automate as much as possible; people mostly see overview dashboards and approve actions.
