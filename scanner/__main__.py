"""WeChat phone scanner (read-only).

  py -m scanner scan                 read every chat with unread messages into data/wechat.db
  py -m scanner scan --all           read every chat visible on the Chats tab
  py -m scanner scan --chat "Amna"   read one chat (must be visible on the Chats tab)
  py -m scanner scan --dry-run       print what would be stored, store nothing
  py -m scanner parse-chat a.png b.png   test the parser on saved screenshots (bottom screen first)
  py -m scanner parse-list list.png      test the chat-list parser on a saved screenshot
  py -m scanner ocr-dump shot.png        raw OCR lines with positions and heights (for tuning)

Options: --adb PATH (or env WB_ADB) when adb is not on PATH; --pages N max scrolls per chat;
--list-pages N max scrolls down the Chats list (default 8).
It only taps, scrolls, presses Back and takes screenshots. It never types or sends anything.
"""
import argparse
import sys
import time

from . import parse
from .parse import fuzzy_same, parse_chat, parse_list, parse_list_title, stitch

LAUNCH_WAIT, OPEN_WAIT, SCROLL_WAIT, BACK_WAIT = 3.0, 2.0, 1.2, 1.5


def walk_list(snapshot, open_row, scroll, select, max_pages, unread_only=False, limit=None):
    """Visit every selected chat row once, scrolling down the Chats list as needed.

    snapshot() -> rows currently on screen; open_row(row) reads one chat and returns to
    the list; scroll() moves the list down by a few rows. Stops at the end of the list
    (screen stops changing), after max_pages scrolls, after `limit` chats, or - when only
    reading unread chats - at the first freshly scrolled screen with no unread badges
    (WeChat lists chats newest first, so unread ones sit near the top).
    """
    done, last_names, scrolls, fresh = [], None, 0, True
    while True:
        rows = snapshot()
        if unread_only and fresh and scrolls > 0 and not any(r.unread for r in rows):
            break
        fresh = False
        todo = [r for r in rows if select(r) and not any(fuzzy_same(r.name, d) for d in done)]
        if todo:
            done.append(todo[0].name)
            open_row(todo[0])
            if limit and len(done) >= limit:
                break
            continue
        names = [r.name for r in rows]
        if names == last_names or scrolls >= max_pages:
            break
        last_names = names
        scroll()
        scrolls += 1
        fresh = True
    return done


def show(messages):
    for m in messages:
        who = {"in": m.sender or "THEM", "out": "ME", "image": f"IMG {m.sender}".strip()}[m.side]
        label = f"[{m.time_label}] " if m.time_label else ""
        print(f"  {label}{who}: {m.text}")


def to_chat_list(dev, ocr, w, h):
    """Get to the Chats tab, backing out of any open chat. Returns (img, lines)."""
    s = w / 1080
    for _ in range(4):
        img = dev.screenshot()
        lines = ocr(img)
        if parse_list_title(lines, w) is not None:
            return img, lines
        dev.back()
        time.sleep(BACK_WAIT)
    dev.tap(135 * s, h - 178 * s)  # "WeChat" (Chats) tab
    time.sleep(BACK_WAIT)
    img = dev.screenshot()
    return img, ocr(img)


def read_chat(dev, ocr, db, row, w, h, max_pages, tail_for):
    """Open a chat row, read it bottom-up until known messages appear. Returns (title, messages)."""
    from .store import new_part
    s = w / 1080
    dev.tap(w / 2, row.y + 30 * s)
    time.sleep(OPEN_WAIT)
    pages, title, tail = [], "", None
    try:
        for i in range(max_pages):
            screen = parse_chat(ocr(dev.screenshot()), w, h)
            if i == 0:
                title = screen.title
                if not fuzzy_same(title, row.name):
                    print(f"  ! opened '{title}' but expected '{row.name}', skipping")
                    return None, []
                tail = tail_for(title)
            keys = [m.key() for m in screen.messages]
            if pages and keys == [m.key() for m in pages[-1]]:
                break  # reached the top of the chat
            pages.append(screen.messages)
            if tail and new_part(tail, [m.key() for m in stitch(pages)]) > 0:
                break  # reached messages we already have
            dev.swipe(w / 2, 700 * s, w / 2, 1700 * s, 400)
            time.sleep(SCROLL_WAIT)
    finally:
        dev.back()
        time.sleep(BACK_WAIT)
    return title, stitch(pages)


def cmd_scan(args):
    from .device import Device
    from .ocr import has_unread_badge, ocr
    from .store import conv_id, group_name, ingest, is_group, open_db, stored_tail

    dev = Device(adb=args.adb, serial=args.serial)
    db = open_db(args.db)
    w, h = dev.size()
    dev.wake()
    dev.open_wechat(LAUNCH_WAIT)
    _, lines = to_chat_list(dev, ocr, w, h)
    unread_total = parse_list_title(lines, w)
    if unread_total is None:
        sys.exit("Could not find the WeChat chat list. Is the phone unlocked with WeChat logged in?")

    def snapshot():
        shot, shot_lines = to_chat_list(dev, ocr, w, h)
        rows = parse_list(shot_lines, w, h)
        for r in rows:
            r.unread = has_unread_badge(shot, r.y, w)
        return rows

    def tail_for(title):
        name = group_name(title) if is_group(title) else title
        return stored_tail(db, conv_id(name))

    def open_row(row):
        print(f"- {row.name} ({row.time})")
        title, messages = read_chat(dev, ocr, db, row, w, h, args.pages, tail_for)
        if not title:
            return
        if args.dry_run:
            show(messages)
            return
        added, skipped = ingest(db, title, messages)
        print(f"  stored {added} new, {skipped} already known")

    def scroll():
        s = w / 1080
        dev.swipe(w / 2, 1700 * s, w / 2, 900 * s, 500)   # about 4 rows, so screens overlap
        time.sleep(SCROLL_WAIT)

    if args.chat:
        select = lambda r: fuzzy_same(r.name, args.chat)
    elif args.all:
        select = lambda r: True
    else:
        print(f"WeChat shows {unread_total} unread message(s).")
        select = lambda r: r.unread
    done = walk_list(snapshot, open_row, scroll, select, args.list_pages,
                     unread_only=not (args.chat or args.all), limit=1 if args.chat else None)
    if args.chat and not done:
        sys.exit(f"'{args.chat}' was not found in the Chats list.")
    print(f"Done: read {len(done)} chat(s).")


def cmd_parse_chat(args):
    from .ocr import load, ocr
    pages, title = [], ""
    for path in args.images:
        img = load(path)
        h, w = img.shape[:2]
        screen = parse_chat(ocr(img), w, h)
        title = title or screen.title
        pages.append(screen.messages)
    print(f"Chat: {title}")
    show(stitch(pages))


def cmd_ocr_dump(args):
    from .ocr import load, ocr
    for l in sorted(ocr(load(args.image)), key=lambda l: (l.y1, l.x1)):
        print(f"{int(l.x1)}-{int(l.x2)},{int(l.y1)}-{int(l.y2)} h{int(l.y2 - l.y1)}  {l.text}")


def cmd_parse_list(args):
    from .ocr import has_unread_badge, load, ocr
    img = load(args.image)
    h, w = img.shape[:2]
    lines = ocr(img)
    print(f"Unread in title: {parse_list_title(lines, w)}")
    for r in parse_list(lines, w, h):
        badge = "UNREAD " if has_unread_badge(img, r.y, w) else "       "
        print(f"  {badge}{r.name} | {r.time} | {r.preview}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="scanner", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--all", action="store_true")
    s.add_argument("--chat")
    s.add_argument("--pages", type=int, default=4)
    s.add_argument("--list-pages", type=int, default=8)
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--adb")
    s.add_argument("--serial")
    s.add_argument("--db")
    s.set_defaults(fn=cmd_scan)
    c = sub.add_parser("parse-chat")
    c.add_argument("images", nargs="+")
    c.set_defaults(fn=cmd_parse_chat)
    l = sub.add_parser("parse-list")
    l.add_argument("image")
    l.set_defaults(fn=cmd_parse_list)
    d = sub.add_parser("ocr-dump")
    d.add_argument("image")
    d.set_defaults(fn=cmd_ocr_dump)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
