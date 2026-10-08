"""WeChat phone scanner (read-only).

  py -m scanner scan                 read every chat with unread messages into data/wechat.db
  py -m scanner scan --all           read every chat visible on the Chats tab
  py -m scanner scan --chat "Amna"   read one chat (must be visible on the Chats tab)
  py -m scanner scan --dry-run       print what would be stored, store nothing
  py -m scanner parse-chat a.png b.png   test the parser on saved screenshots (bottom screen first)
  py -m scanner parse-list list.png      test the chat-list parser on a saved screenshot

Options: --adb PATH (or env WB_ADB) when adb is not on PATH; --pages N max scrolls per chat.
It only taps, scrolls, presses Back and takes screenshots. It never types or sends anything.
"""
import argparse
import sys
import time

from . import parse
from .parse import fuzzy_same, parse_chat, parse_list, parse_list_title, stitch

LAUNCH_WAIT, OPEN_WAIT, SCROLL_WAIT, BACK_WAIT = 3.0, 2.0, 1.2, 1.5


def show(messages):
    for m in messages:
        who = {"in": "THEM", "out": "ME", "image": "IMG"}[m.side]
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
    img, lines = to_chat_list(dev, ocr, w, h)
    unread_total = parse_list_title(lines, w)
    if unread_total is None:
        sys.exit("Could not find the WeChat chat list. Is the phone unlocked with WeChat logged in?")
    rows = parse_list(lines, w, h)
    for r in rows:
        r.unread = has_unread_badge(img, r.y, w)

    if args.chat:
        targets = [r for r in rows if fuzzy_same(r.name, args.chat)]
        if not targets:
            sys.exit(f"'{args.chat}' is not visible on the Chats tab. Visible: {', '.join(r.name for r in rows)}")
    elif args.all:
        targets = rows
    else:
        targets = [r for r in rows if r.unread]
        print(f"WeChat shows {unread_total} unread; {len(targets)} chat(s) with a badge on screen.")
    if not targets:
        print("Nothing to read.")
        return

    def tail_for(title):
        name = group_name(title) if is_group(title) else title
        return stored_tail(db, conv_id(name))

    for row in targets:
        print(f"- {row.name} ({row.time})")
        title, messages = read_chat(dev, ocr, db, row, w, h, args.pages, tail_for)
        if not title:
            continue
        if args.dry_run:
            show(messages)
            continue
        added, skipped = ingest(db, title, messages)
        print(f"  stored {added} new, {skipped} already known")


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
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
