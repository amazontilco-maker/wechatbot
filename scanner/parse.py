"""Turn OCR lines from WeChat screenshots into chat-list rows and chat messages.

Pure functions (no phone, no OCR) so they can be tested from saved OCR output.
Coordinates were measured on a 1080x2400 Samsung A51 and are scaled by screen width.
"""
import re
from dataclasses import dataclass, field

BASE_W = 1080

# Times WeChat shows: "7:49 PM", "19:49", "Yesterday 7:49 PM", "Monday", "10/6/26 7:49 PM", "Oct 6"
_CLOCK = r"\d{1,2}:\d{2}(?:\s*[AP]M)?"
_DAY = r"(?:Yesterday|Today|Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*"
_DATE = r"(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?|[A-Z][a-z]{2}\s+\d{1,2}(?:,\s*\d{4})?)"
TIME_RE = re.compile(rf"^(?:(?:{_DAY}|{_DATE})\s*)?{_CLOCK}$|^{_DAY}$|^{_DATE}$", re.I)


@dataclass
class Line:
    x1: float
    y1: float
    x2: float
    y2: float
    text: str


@dataclass
class Message:
    side: str            # "in" (them) | "out" (me) | "image" (text read inside a picture/card)
    text: str
    time_label: str = ""  # nearest WeChat time separator above the message, as shown
    y: float = 0
    sender: str = ""      # group chats: the member name WeChat shows above their message

    def key(self):
        # text read inside pictures varies with how much of the picture is on screen,
        # so a picture only matches "a picture" when lining screens up
        return ("image", "") if self.side == "image" else (self.side, self.text)


@dataclass
class ChatScreen:
    title: str = ""
    messages: list = field(default_factory=list)


@dataclass
class ListRow:
    name: str
    time: str
    preview: str
    y: float          # top of the name text
    unread: bool = False


def _scale(width):
    return width / BASE_W


def parse_list_title(lines, width=BASE_W):
    """'WeChat (3)' -> 3 unread in total, 'WeChat' -> 0, None if the title isn't visible."""
    s = _scale(width)
    for l in lines:
        if 100 * s < l.y1 < 200 * s:
            m = re.match(r"^\s*WeChat\s*(?:\((\d+)\))?\s*$", l.text, re.I)
            if m:
                return int(m.group(1) or 0)
    return None


def parse_list(lines, width=BASE_W, height=2400):
    """Chats tab -> rows. Each row is anchored on its right-aligned time."""
    s = _scale(width)
    body = [l for l in lines if 200 * s < l.y1 < height - 260 * s]
    times = [l for l in body if l.x1 > 860 * s and TIME_RE.match(l.text.strip())]
    left = [l for l in body if 185 * s <= l.x1 <= 240 * s]
    rows = []
    for t in sorted(times, key=lambda l: l.y1):
        name = [l for l in left if abs(l.y1 - t.y1) < 35 * s]
        prev = [l for l in left if 30 * s < l.y1 - t.y1 < 120 * s]
        if not name:
            continue
        n = min(name, key=lambda l: abs(l.y1 - t.y1))
        rows.append(ListRow(
            name=n.text.strip(),
            time=t.text.strip(),
            preview=prev[0].text.strip() if prev else "",
            y=n.y1,
        ))
    return rows


GROUP_TITLE = re.compile(r"\(\d+\)\s*$")   # WeChat group titles look like "Name (12)"


def parse_chat(lines, width=BASE_W, height=2400, group=None):
    """An open chat -> title + messages in screen order (oldest at top).

    Rules measured from real screenshots:
      - their text starts ~202px from the left (after the avatar);
      - my text ends ~882px from the left (before my avatar);
      - time separators and system notices are centred;
      - text that fits neither edge is inside an image/card bubble.
    Group chats (title "Name (N)"): WeChat prints the member's name in small grey text
    above each of their bubbles, starting a little left of the bubble text and in a
    smaller font. That line becomes the message's sender.
    """
    s = _scale(width)
    top, bottom = 218 * s, height - 280 * s
    out = ChatScreen()
    body = []
    for l in sorted(lines, key=lambda l: (l.y1, l.x1)):
        t = l.text.strip()
        if not t or l.y1 < 88 * s:          # status bar
            continue
        if l.y1 < top:                        # action bar: chat name
            if not out.title and l.x1 > 150 * s and l.x2 < 930 * s:
                out.title = t
            continue
        if l.y1 > bottom:                     # input bar / keyboard
            continue
        if l.x2 < 200 * s or l.x1 > 910 * s:  # text printed inside an avatar picture
            continue
        body.append(l)
    if group is None:
        group = bool(GROUP_TITLE.search(out.title))

    items = []
    for l in body:
        t = l.text.strip()
        mid = (l.x1 + l.x2) / 2
        centred = abs(mid - width / 2) < 60 * s
        if TIME_RE.match(t) and abs(mid - width / 2) < 120 * s:
            items.append(["time", t, l])
        elif group and 150 * s <= l.x1 < 180 * s:
            items.append(["name", t, l])
        elif abs(l.x1 - 202 * s) <= 22 * s:
            items.append(["in", t, l])
        elif abs(l.x2 - 882 * s) <= 22 * s:
            items.append(["out", t, l])
        elif centred:
            items.append(["system", t, l])
        else:
            items.append(["image", t, l])

    _join_bubble_lines(items, s)
    _mark_quotes(items)

    label, prev, sender, prev_y, prev_x = "", None, "", 0, 0
    out_msgs = out.messages

    def flush_name():
        # a member name with no readable text under it = they sent a picture/file/sticker
        nonlocal sender
        if sender:
            out_msgs.append(Message(side="image", text="[picture/file]", time_label=label, sender=sender))
            sender = ""

    for kind, text, l in items:
        if kind == "time":
            flush_name()
            label, prev = text, None
            continue
        if kind == "system":
            flush_name()
            prev = None
            continue
        if kind == "name":
            flush_name()
            sender, prev = text, None
            continue
        if kind == "quote":
            if prev:  # WeChat shows "Name: quoted text" in a grey box under the reply
                prev.text += f"\n(quoting {text})"
            continue
        # consecutive lines of one bubble are ~61px apart; separate bubbles are 130px+.
        # all text in one run of pictures/cards is kept together as one item
        # (a blank line inside a bubble leaves a ~115px gap, but the lines stay left-aligned)
        gap = l.y1 - prev_y
        if prev and prev.side == kind and (kind == "image" or gap <= 90 * s
                                           or (gap <= 125 * s and abs(l.x1 - prev_x) <= 12 * s)):
            prev.text += ("\n" if kind != "image" else " ") + text
        else:
            prev = Message(side=kind, text=text, time_label=label, y=l.y1,
                           sender=sender if group and kind != "out" else "")
            out_msgs.append(prev)
            sender = ""   # each group message carries its own name label
        prev_y, prev_x = l.y1, l.x1
    flush_name()
    return out


def _join_bubble_lines(items, s):
    """Lines of a multi-line bubble are left-aligned, so only the longest one reaches the
    bubble's right edge. My shorter lines then fit neither edge and look like picture text;
    give them the side of a left-aligned neighbouring line in the same bubble."""
    changed = True
    while changed:
        changed = False
        for i, (kind, _, l) in enumerate(items):
            if kind not in ("image", "system"):
                continue
            for j in (i - 1, i + 1):
                if 0 <= j < len(items) and items[j][0] in ("in", "out"):
                    o = items[j][2]
                    if abs(o.x1 - l.x1) <= 12 * s and abs(o.y1 - l.y1) <= 125 * s:
                        items[i][0] = items[j][0]
                        changed = True
                        break


def _mark_quotes(items):
    """A reply's quote box ("Name: text") sits under the reply bubble, in a smaller font.
    Measured: message lines are 42-50px tall, the quote line 33px."""
    for side in ("in", "out"):
        heights = sorted(l.y2 - l.y1 for k, _, l in items if k == side)
        if len(heights) < 3:
            continue
        typical = heights[len(heights) // 2]
        for i, (kind, text, l) in enumerate(items):
            if kind == side and i > 0 and items[i - 1][0] == side \
                    and (l.y2 - l.y1) < 0.8 * typical and re.search(r"[:：]", text):
                items[i][0] = "quote"


def _overlap(older, newer):
    """Largest k where the last k of `older` equal the first k of `newer`."""
    for k in range(min(len(older), len(newer)), 0, -1):
        if [m.key() for m in older[-k:]] == [m.key() for m in newer[:k]]:
            return k
    return 0


def stitch(pages):
    """pages[0] = bottom (newest) screen, pages[1] = one scroll up, ...
    Returns one oldest-first list without the duplicated overlap."""
    if not pages:
        return []
    combined = list(pages[-1])
    for page in reversed(pages[:-1]):
        # the top message of a screen may be cut off, so also try matching without it
        best = max(((_overlap(combined, page[i:]), i) for i in (0, 1) if len(page) > i),
                   default=(0, 0))
        k, i = best
        for j in range(k):  # keep the fuller reading of overlapping pictures
            a, b = combined[len(combined) - k + j], page[i + j]
            if a.side == "image" and len(b.text) > len(a.text):
                a.text = b.text
        combined += page[i + k:] if k else page
    return combined


def _norm_title(n):
    n = re.sub(r"\(\d+\)\s*$", "", n.lower()).strip()
    return n.replace("…", "...")


def fuzzy_same(a, b):
    """Chat names as OCR'd in two places: a letter may differ ('Ilqa' vs 'Ilga') and WeChat
    shortens long names with '...' differently in the list and the chat title."""
    from difflib import SequenceMatcher
    a, b = _norm_title(a), _norm_title(b)
    if a == b or a in b or b in a:
        return True
    for x, y in ((a, b), (b, a)):
        if "..." in x:
            head, _, tail = x.partition("...")
            head, tail = head.strip(), tail.strip()
            if len(head) >= 6 and y.startswith(head[:12]) and (not tail or y.replace("...", "").endswith(tail[-8:])):
                return True
    return SequenceMatcher(None, a, b).ratio() >= 0.75


def find_send_button(lines, width=BASE_W, height=2400):
    """The green 'Send' button WeChat shows right of the input box once text is typed."""
    s = _scale(width)
    hits = [l for l in lines if l.text.strip().lower() == "send" and l.x1 > 780 * s and l.y1 > height * 0.3]
    return max(hits, key=lambda l: l.y1) if hits else None


def _plain(t):
    return re.sub(r"[^a-z0-9]", "", t.lower())


def parse_member_picker(lines, width=BASE_W, height=2400):
    """The member list WeChat opens when '@' is typed in a group: a sheet titled 'Select'
    with a Search box, then one member name per row. Returns the name lines, or None
    if the list isn't open."""
    s = _scale(width)
    title = [l for l in lines if l.text.strip().lower() == "select"
             and abs((l.x1 + l.x2) / 2 - width / 2) < 120 * s]
    if not title:
        return None
    top = title[0].y2
    search = [l for l in lines if "search" in l.text.lower() and l.y1 > top]
    start = search[0].y2 if search else top
    return [l for l in lines if l.y1 > start + 20 * s and l.y1 < height - 150 * s
            and 150 * s <= l.x1 <= 260 * s]


def pick_member(rows, name):
    """Rows matching `name`: an exact match wins, otherwise every close match."""
    exact = [r for r in rows if _plain(r.text) == _plain(name)]
    return exact[:1] or [r for r in rows if fuzzy_same(r.text, name)]


def similar_text(a, b):
    """Same message, allowing for OCR misreading a few letters."""
    from difflib import SequenceMatcher
    a, b = _plain(a), _plain(b)
    return bool(a and b) and SequenceMatcher(None, a, b).ratio() >= 0.85


def typed_text_matches(lines, send, text, width=BASE_W):
    """Is `text` what the input box (left of the Send button) shows? A long text
    scrolls inside the box, so the visible part is compared with the end of `text`."""
    from difflib import SequenceMatcher
    s = _scale(width)
    left = [l for l in lines if l.x2 <= send.x1 + 5 * s and l.x1 > 100 * s]
    row = [l for l in left if abs(l.y1 - send.y1) < 40 * s]   # the box's bottom line, beside Send
    if not row:
        return False
    box = [min(row, key=lambda l: l.x1)]
    # a longer text wraps upward inside the box: same left edge, lines ~60px apart.
    # Chat bubbles above the input bar are further away, so they are not taken.
    for l in sorted(left, key=lambda l: -l.y1):
        if l.y1 < box[0].y1 and box[0].y1 - l.y1 <= 80 * s and abs(l.x1 - box[0].x1) <= 25 * s:
            box.insert(0, l)
    seen = _plain("".join(l.text for l in box))
    want = _plain(text)
    if not seen or not want:
        return False
    if len(seen) < min(len(want), 20) * 0.6:
        return False
    return seen in want or SequenceMatcher(None, seen, want[-len(seen):]).ratio() >= 0.8
