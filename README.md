# wechatbot

Reads WeChat supplier chats from an always-on Android phone, stores them in a local SQLite DB, and lets Claude brief you on them. **Read-only:** nothing here types or sends messages; you reply yourself.

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
reads every chat with an unread badge. Also: `--all` (every chat on screen), `--chat "Amna"` (one chat), `--dry-run` (print, store nothing), `--pages N` (max scrolls per chat, default 4).
Set `WB_ADB` once to skip `--adb`: `setx WB_ADB "C:\path\to\platform-tools\adb.exe"`.

Test the parser on saved screenshots without the phone:
```
py -m scanner parse-chat chat.png chat2.png     (bottom screen first, then each scroll up)
py -m scanner parse-list list.png
```

Then open Claude Code in this folder and say **"brief me"**. Claude reads the DB with `node src/cli.js digest` (see CLAUDE.md).

## Limits (known)
- Only chats visible on the first screen of the Chats tab are scanned (no list scrolling yet).
- Opening a chat marks it read on the phone.
- Times are WeChat's separator labels ("7:49 PM"), stored as `shown_time`; the DB timestamp is scan time.
- Group chats: messages are stored, but individual group members' names aren't separated yet (needs a group screenshot to tune).
- Text inside images (cards, price sheets) is stored as `[image text] ...` and may be partial.
- Coordinates were measured on a Samsung A51 (1080x2400) and scale by screen width; other phones may need tuning.

`src/bridge.js` is the earlier web-WeChat bridge; WeChat blocks web login for this account, so it's unused.
