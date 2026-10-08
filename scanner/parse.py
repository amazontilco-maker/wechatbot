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

    if group:
        _mark_member_names(items, s)

    label, prev, sender = "", None, ""
    for kind, text, l in items:
        if kind == "time":
            label, prev = text, None
            continue
        if kind == "system":
            prev = None
            continue
        if kind == "name":
            sender, prev = text, None
            continue
        # consecutive lines of one bubble are ~61px apart; separate bubbles are 130px+.
        # all text in one run of pictures/cards is kept together as one item
        if prev and prev.side == kind and (kind == "image" or l.y1 - prev_y <= 90 * s):
            prev.text += ("\n" if kind != "image" else " ") + text
        else:
            prev = Message(side=kind, text=text, time_label=label, y=l.y1,
                           sender=sender if group and kind != "out" else "")
            out.messages.append(prev)
            sender = ""   # each group message carries its own name label
        prev_y = l.y1
    return out


def _mark_member_names(items, s):
    """A group member name is a short line in a smaller font that starts a block of
    their text and sits ~40-80px above the first bubble line."""
    heights = sorted(l.y2 - l.y1 for k, _, l in items if k == "in")
    if not heights:
        return
    typical = heights[len(heights) // 2]
    for i, (kind, _, l) in enumerate(items):
        if kind != "in" or i + 1 >= len(items):
            continue
        nxt = items[i + 1]
        starts_block = i == 0 or items[i - 1][0] != "in" or l.y1 - items[i - 1][2].y1 > 90 * s
        small = (l.y2 - l.y1) < 0.82 * typical
        if starts_block and small and nxt[0] in ("in", "image") and 30 * s < nxt[2].y1 - l.y1 < 85 * s:
            items[i][0] = "name"


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


def fuzzy_same(a, b):
    """OCR may misread a letter or two in names ('Ilqa' vs 'Ilga')."""
    from difflib import SequenceMatcher
    a, b = a.lower().strip(), b.lower().strip()
    return a == b or a in b or b in a or SequenceMatcher(None, a, b).ratio() >= 0.75
