# wechatbot

Reads WeChat supplier chats from an always-on Android phone, stores them in a local SQLite DB, and lets Claude brief you on them. Replies go out only through `py -m scanner send`, which you run and confirm yourself.

## How it works
WeChat hides its screen text from Android's accessibility tools, so the scanner reads it the way a person would:
screenshot over ADB -> local OCR (RapidOCR, offline) -> parse into messages -> `data/wechat.db`.
It only taps a chat, scrolls up, presses Back and takes screenshots.

## Setup (Windows PC + Android phone on USB)
1. Phone: Developer options -> USB debugging ON, Stay awake ON. Screen lock: None (the scanner can't unlock a PIN). WeChat logged in.
2. PC: Android platform-tools (adb), Python 3, Node 22.13+.
3. In this folder:
   ```
   py -m pip install -r scanner/requirements.txt
   ```
4. Check the phone: `adb devices` shows `device`.

## Use
```
py -m scanner scan --adb "C:\path\to\platform-tools\adb.exe"
```
reads every chat with an unread badge, scrolling down the Chats list until a screen has no unread badges. Also: `--all` (every chat, scrolling to the end of the list), `--chat "Amna"` (one chat, searched down the list), `--dry-run` (print, store nothing), `--pages N` (max scrolls up inside each chat, default 4), `--list-pages N` (max scrolls down the Chats list, default 8).
Set `WB_ADB` once to skip `--adb`: `setx WB_ADB "C:\path\to\platform-tools\adb.exe"`.

Test the parser on saved screenshots without the phone:
```
py -m scanner parse-chat chat.png chat2.png     (bottom screen first, then each scroll up)
py -m scanner parse-list list.png
py -m scanner ocr-dump shot.png                 (raw OCR with positions/heights, for tuning)
```

Send a reply (you confirm each one):
```
py -m scanner send --chat "Amna" --text "Price OK, please send PI"
```
It opens the chat, shows the last messages and your text, and waits for you to type `SEND`. Then it types the text, checks the input box with OCR (if it doesn't match, it clears the box and sends nothing), taps Send and checks the message appears. One line of plain English only for now: no line breaks, Chinese or emoji. It refuses to run without a person at the terminal.

In a group, `--tag "John"` @mentions a member: it types `@`, picks the member from WeChat's list (scrolling it if needed), then types the text. If the name isn't in the list or matches several members, it undoes the `@` and sends nothing.

## Team web app (M1, in progress)
`py -m web serve` runs the team inbox on http://127.0.0.1:8000 (same `data/wechat.db`). Every person has their own login:
```
py -m pip install -r web/requirements.txt
py -m web adduser YourName admin
py -m web serve
```
Roles: viewer (reads), sender (+ notes, replies that need approval), approver (+ approve & send, activity log), admin (+ logins).
Manage logins with `py -m web users|setrole|passwd|disable|enable`.
Stock risk: upload the stock workbook (.xlsx) on the Stock risk page (or point the app at a synced copy with
`py -m web stockfile "G:\My Drive\file.xlsx"`), tick the live tabs, and it lists SKUs under 60 days of stock
(days = units / average daily sales, columns found by header per tab). `py -m web stockcheck file.xlsx` shows
what it found on each tab. Tests: `py -m unittest discover -s web/tests -t .`

Then open Claude Code in this folder and say **"brief me"**. Claude reads the DB with `node src/cli.js digest` (see CLAUDE.md).

## Limits (known)
- Opening a chat marks it read on the phone.
- Times are WeChat's separator labels ("7:49 PM"), stored as `shown_time`; the DB timestamp is scan time.
- Group chats: the member name WeChat prints above each message is stored as the sender (checked on a real supplier group). If a name is missed, the sender falls back to the group name.
- Pictures/files with no readable text are stored as `[picture/file]` from that member; reply quotes are appended as `(quoting Name: ...)`.
- Text inside images (cards, price sheets) is stored as `[image text] ...` and may be partial.
- Coordinates were measured on a Samsung A51 (1080x2400) and scale by screen width; other phones may need tuning.

`src/bridge.js` is the earlier web-WeChat bridge; WeChat blocks web login for this account, so it's unused.
