# Roadmap — Tilco supplier & forwarder assistant

Goal: an in-house version of the "Lark" demo (Sep 2026): a dedicated WeChat account that our team
uses through a **live web app reachable from anywhere** (link + login). It reads supplier and
forwarder chats, matches replies to shipments, keeps the stock sheet's ETAs current with a note
saying where each change came from, drafts chasers for stale ETAs, and sends nothing without a
person approving it.

Start each milestone in a **new Claude Code chat**: "Read ROADMAP.md. Let's build M<n>."
Merge to `main` when a milestone is done and tested on the real phone.

## Decisions already made
- **WeChat runs on a dedicated Android phone** (Samsung A51, separate account) on USB to an office
  Windows PC, driven over ADB with screenshots + local OCR. WeChat blocks web login and Android
  accessibility dumps for this account, so this is the only reading method that works. No Windows VM.
- **A person approves every outgoing message.** No bulk or unattended sending (account-ban risk).
  Claude only reads, matches and drafts. A sender's reply waits for an approver; an approver or
  admin who writes a reply sends it with one click (logged as requested + approved by them).
- **The app is an operations workspace, not a WeChat copy**: sidebar Today / Shipments / ETA updates /
  Stock risk / Messages / Approvals / ETA chasers / Search / Health / Log. Each chat is labelled
  supplier / forwarder / internal / other; forwarder chats feed M3.
- **Live web app, accessible from anywhere** with a specific link and a login. It runs on the office
  PC (next to the phone) and is published through an HTTPS tunnel (Cloudflare Tunnel; no open
  ports, works behind office routers). Optionally Cloudflare Access in front (email one-time code).
  A small domain (~$10/yr) gives a fixed link like `wechat.<company>.com`.
- **Every user has their own login** (no shared password): roles viewer / sender / approver,
  every send logged with who requested and who approved. HTTPS only, rate-limited login.
- **Data stays on the office PC** (SQLite `data/wechat.db`). Only new message text is sent to the
  Claude API for matching/drafting. Google Sheet (the existing stock file) stays the source of truth
  for stock; the app writes ETA cells + a cell note with the source message.
- **Token efficiency**: send Claude only new messages + that chat's notes + the relevant shipment
  rows, never screenshots; smallest model (Haiku) for extraction with fixed JSON output.
- One phone = one action at a time: all phone work (scan, send) goes through a single queue.

## Done: M0 — WeChat on the phone (this repo today)
- `py -m scanner scan [--all|--chat X]` reads chats into `data/wechat.db` (groups: who said what,
  quotes, `[picture/file]`, `[image text]`, wrapped bubbles, scrolls chat list and history).
- `py -m scanner send --chat X [--tag Member] --text "..."` types, verifies by OCR, sends after the
  user types SEND in a terminal (M1 replaces this with web approval).
- `node src/cli.js digest|convs|thread|search|note|read` to look at stored chats.
- 23 parser tests built from real phone screens (`python -m unittest discover -s scanner/tests -t .`).

## Milestones
**M1 — Live web app + approve & send** (replaces the terminal)
- Python web server (FastAPI) on the office PC, published via Cloudflare Tunnel; user accounts,
  roles, sessions, audit log.
- Pages: chat list with unread/new, chat thread view, search, notes.
- Reply box (+ @tag picker for groups) → "Request send" → approver clicks "Approve & send" →
  phone queue sends → status Sent / Failed shown on the message.
- Background scanner on a schedule (e.g. every 15 min) through the same phone queue.
- Health page: phone connected, WeChat logged in, last scan, queue length.
- Build steps: 1 logins + workspace (Today, Messages two-pane, chat labels, search, notes) — done;
  2 reply box + @tag + request / approve & send through the phone queue; 3 scheduled scans + health;
  4 Cloudflare Tunnel + admin page for logins.

**M2 — Shipments from the stock sheet**
- Read the Google Sheet (service account, read-only first). Per market tab: SKU, ASIN, inbound
  columns (Transit to Amazon FC / AWD / 3PL × Air / Fast ocean / Ocean), ETA columns.
- Split summed cells (`=150+150+100+150`) into separate shipments; store shipments with
  forwarder, route, units, ETA, last confirmed.
- Shipments page per market.

**M3 — Match forwarder replies to shipments (Claude)**
- For new messages in forwarder groups: Claude (Haiku, JSON output) extracts shipment refs,
  new ETAs, delays, deliveries; matched to M2 shipments. Each update links to its message.
- Review screen: proposed updates with old → new ETA; approve / edit / reject.

**M4 — Write approved updates to the sheet**
- Write ETA cells on the right tab + a cell note ("Ocean ETA Sep 28 → Oct 2. Source: WeChat,
  Kevin | SZ Ocean, 14:47"). Never touch formulas other than the ETA cells; log every write.

**M5 — ETA chasers**
- Find shipments not confirmed in N days, grouped by forwarder; draft one message per group
  listing the shipments; approve & send via M1; re-ask next morning if no reply.

**M6 — Stockout risk** (first version built early, in the web app: Stock risk page reads the .xlsx stock
Google Sheet every 15 min via a read-only service account, flags SKUs under N days of stock (default 60) by
Amazon / on hand / on hand + inbound, warns when the sheet hasn't been edited for N days.
Later: pull sales, FBA/AWD stock and inbound from Amazon SP-API (and Walmart) so nobody types them.)
- From the sheet's sales and stock columns: runs-out date vs ETA, flag gaps, suggest options
  (air, AWD/3PL transfer, slow sales). Drafts only.

Later: Jira tickets for delays, daily summary, WeCom if the business needs an official API.

## Phone/screen facts (measured, Samsung A51 1080x2400; scale by width/1080)
- Chat: status bar y<88, title y<218 (groups end "(N)", may be truncated with "..."); their text
  x1≈202, my text x2≈882 (±22); multi-line bubbles are left-aligned, only the longest line reaches
  the edge; group member names x1 150–180, ~30px tall, ~70px above the bubble; reply quotes are a
  smaller "Name: text" line under the reply; bottom limit h−280.
- Input box: tap (0.45w, h−178); after typing, green "Send" at x≈931–1038 on the input row
  (keyboard open, input row y≈1312); the box is read only on that row (+ wrapped lines above).
- Typing: `adb shell input text` (ASCII only, spaces as %s, single-quoted); `@` in a group opens
  the "Select" member sheet (Search box, names at x1≈192), tap the member to insert a mention.
- Chat list: title "WeChat (N)" = unread total; rows anchored on time at x1>860; name x1 185–240;
  unread badge = red pixels at x 140–205 above the name; Chats tab at (135, h−178).
- Opening a chat marks it read on the phone.
