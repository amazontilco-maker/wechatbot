"""Tests use real OCR output captured from a Samsung A51 (x1-x2,y  text)."""
import unittest

from scanner.parse import Line, Message, parse_chat, parse_list, parse_list_title, stitch
from scanner.store import ingest, open_db


def lines(raw):
    out = []
    for row in raw.strip().splitlines():
        pos, text = row.strip().split("  ", 1)
        xs, y = pos.split(",")
        x1, x2 = xs.split("-")
        out.append(Line(float(x1), float(y), float(x2), float(y) + 50, text))
    return out


BOTTOM = lines("""
79-184,44  19:50
811-1002,45  3%98
403-651,136  umer Javaid
481-602,300  6:38 PM
333-509,565  TILCO
336-507,618  TRADINGPTE.LTD.
625-833,1320  Scan to add me as a friend.
479-603,1462  7:49 PM
808-884,1596  Gre
799-882,1734  Fdd
780-881,1876  Xddr
794-881,2016  Ddd
""")

TOP = lines("""
79-180,43  19:51
811-1002,45  3%981
404-651,136  umer Javaid
481-602,287  6:32 PM
706-880,421  I'm Tilco
346-737,543  Greetings shown above
201-866,650  I've accepted your friend request.
202-501,711  Now let's chat!
202-651,847  Hello nice to meet you
203-528,989  What is up dude
480-603,1144  6:38 PM
334-509,1411  TILCO
337-506,1464  TRADINGPTE.LTD.
625-672,1719  Tilco
""")

LIST = lines("""
80-270,42  19:55  0
810-1003,44  .l 87%白
423-652,131  WeChat (1)
204-453,271  umer Javaid
918-1040,264  7:54 PM
205-650,332  What is wrong with you dude
203-398,462  Ilqa Khan
919-1040,458  6:48 PM
93-177,486  TILCO
93-173,510  TECHNOLOCYLAB
201-711,526  Hey hello bye bye who are you??
206-497,657  Amna Tanveer
919-1040,652  6:42 PM
203-788,720  Hello Amna Nice to connect with you
207-487,851  WeChat Team
920-1039,846  6:31 PM
204-1009,912  Welcome back! Feel free to tell me if you have any...
77-194,2222  WeChat
611-741,2222  Discover
341-471,2223  Contacts
918-972,2221  Me
""")


class ChatTests(unittest.TestCase):
    def test_top_screen(self):
        s = parse_chat(TOP)
        self.assertEqual(s.title, "umer Javaid")
        got = [(m.side, m.text, m.time_label) for m in s.messages]
        self.assertEqual(got[:4], [
            ("out", "I'm Tilco", "6:32 PM"),
            ("in", "I've accepted your friend request.\nNow let's chat!", "6:32 PM"),
            ("in", "Hello nice to meet you", "6:32 PM"),
            ("in", "What is up dude", "6:32 PM"),
        ])
        self.assertEqual(got[4][0], "image")
        self.assertEqual(got[4][2], "6:38 PM")

    def test_bottom_screen(self):
        s = parse_chat(BOTTOM)
        outs = [m.text for m in s.messages if m.side == "out"]
        self.assertEqual(outs, ["Gre", "Fdd", "Xddr", "Ddd"])
        self.assertTrue(all(m.time_label == "7:49 PM" for m in s.messages if m.side == "out"))

    def test_stitch_no_duplicates(self):
        allm = stitch([parse_chat(BOTTOM).messages, parse_chat(TOP).messages])
        texts = [m.text for m in allm]
        self.assertEqual(texts[0], "I'm Tilco")
        self.assertEqual(texts[-1], "Ddd")
        self.assertEqual(texts.count("Hello nice to meet you"), 1)
        images = [m.text for m in allm if m.side == "image"]
        self.assertEqual(images, ["TILCO TRADINGPTE.LTD. Scan to add me as a friend."])


class ListTests(unittest.TestCase):
    def test_list(self):
        self.assertEqual(parse_list_title(LIST), 1)
        rows = parse_list(LIST)
        self.assertEqual([r.name for r in rows], ["umer Javaid", "Ilqa Khan", "Amna Tanveer", "WeChat Team"])
        self.assertEqual(rows[0].time, "7:54 PM")
        self.assertEqual(rows[1].preview, "Hey hello bye bye who are you??")


class StoreTests(unittest.TestCase):
    def test_ingest_skips_known(self):
        db = open_db(":memory:")
        first = [Message("in", "a"), Message("out", "b")]
        self.assertEqual(ingest(db, "Supplier X", first, now=1000), (2, 0))
        again = [Message("in", "a"), Message("out", "b"), Message("in", "c")]
        self.assertEqual(ingest(db, "Supplier X", again, now=2000), (1, 2))
        self.assertEqual(ingest(db, "Supplier X", again, now=3000), (0, 3))
        kind = db.execute("SELECT kind FROM convs").fetchone()[0]
        self.assertEqual(kind, "person")

    def test_group_title(self):
        db = open_db(":memory:")
        ingest(db, "Cable Suppliers (12)", [Message("in", "hi")], now=1)
        self.assertEqual(db.execute("SELECT id, kind FROM convs").fetchone(), ("ui:Cable Suppliers", "room"))


if __name__ == "__main__":
    unittest.main()


def hlines(raw):
    """x1-x2,y1-y2  text  (with real heights)"""
    out = []
    for row in raw.strip().splitlines():
        pos, text = row.strip().split("  ", 1)
        xs, ys = pos.split(",")
        x1, x2 = xs.split("-")
        y1, y2 = ys.split("-")
        out.append(Line(float(x1), float(y1), float(x2), float(y2), text))
    return out


# Real OCR (ocr-dump) of a supplier group on the Samsung A51.
GROUP = hlines("""
79-271,43-80  21:00  0
788-980,44-78  .l 100%
45-139,130-177  <1
179-870,131-180  John - Male Urin...anufacturer (12)
177-395,544-574  Anthony Castro
205-811,612-662  @John These 3 shipments for
200-880,673-719  USA ZIM< will be pick up by Forest
481-601,831-858  8:52 PM
172-248,936-968  John
198-258,1007-1056  ok
176-395,1124-1157  Anthony Castro
176-394,1507-1537  Anthony Castro
202-822,1579-1622  These 2 ZIM shipments will be
201-563,1636-1686  pick up by Yulong.
196-470,1756-1789  Anthony Castro:
172-249,1887-1919  John
200-777,1959-2006  can you make a list I cannot
200-481,2019-2061  remember all
""")


class GroupTests(unittest.TestCase):
    def test_real_group_screen(self):
        s = parse_chat(GROUP)
        self.assertEqual(s.title, "John - Male Urin...anufacturer (12)")
        got = [(m.side, m.sender, m.text, m.time_label) for m in s.messages]
        self.assertEqual(got, [
            ("in", "Anthony Castro", "@John These 3 shipments for\nUSA ZIM< will be pick up by Forest", ""),
            ("in", "John", "ok", "8:52 PM"),
            ("image", "Anthony Castro", "[picture/file]", "8:52 PM"),   # spreadsheet image, no readable text
            ("in", "Anthony Castro", "These 2 ZIM shipments will be\npick up by Yulong.\n(quoting Anthony Castro:)", "8:52 PM"),
            ("in", "John", "can you make a list I cannot\nremember all", "8:52 PM"),
        ])

    def test_one_to_one_has_no_member_names(self):
        self.assertTrue(all(m.sender == "" for m in parse_chat(TOP).messages))

    def test_group_stored_with_members(self):
        db = open_db(":memory:")
        ingest(db, "John - Male Urin...anufacturer (12)", parse_chat(GROUP).messages, now=1)
        self.assertEqual(db.execute("SELECT id, kind FROM convs").fetchone(),
                         ("ui:John - Male Urin...anufacturer", "room"))
        senders = [r[0] for r in db.execute("SELECT sender FROM messages ORDER BY ts")]
        self.assertEqual(senders, ["Anthony Castro", "John", "Anthony Castro", "Anthony Castro", "John"])

    def test_truncated_titles_match(self):
        from scanner.parse import fuzzy_same
        self.assertTrue(fuzzy_same("John - Male Urin...anufacturer (12)", "John - Male Urinal Manufacturer"))
        self.assertTrue(fuzzy_same("John - Male Urinal Manuf...", "John - Male Urin...anufacturer (12)"))
        self.assertTrue(fuzzy_same("Ilqa Khan", "Ilga Khan"))
        self.assertFalse(fuzzy_same("Amna Tanveer", "Ilqa Khan"))


class ListWalkTests(unittest.TestCase):
    def make(self, pages, unread=()):
        from scanner.parse import ListRow
        self.pos, self.opened, self.unread = 0, [], set(unread)
        self.pages = pages

        def snapshot():
            return [ListRow(n, "", "", 300 + i * 194, unread=n in self.unread)
                    for i, n in enumerate(self.pages[self.pos])]

        def open_row(r):
            self.opened.append(r.name)
            self.unread.discard(r.name)

        def scroll():
            self.pos = min(self.pos + 1, len(self.pages) - 1)
        return snapshot, open_row, scroll

    def test_all_scrolls_to_end(self):
        from scanner.__main__ import walk_list
        snap, op, sc = self.make([["A", "B", "C", "D"], ["C", "D", "E", "F"], ["E", "F", "G"]])
        walk_list(snap, op, sc, lambda r: True, max_pages=10)
        self.assertEqual(self.opened, list("ABCDEFG"))

    def test_unread_stops_at_first_page_without_badges(self):
        from scanner.__main__ import walk_list
        snap, op, sc = self.make([["A", "B", "C"], ["C", "D", "E"], ["E", "F", "G"]], unread={"B", "D"})
        walk_list(snap, op, sc, lambda r: r.unread, max_pages=10, unread_only=True)
        self.assertEqual(self.opened, ["B", "D"])

    def test_one_chat_found_further_down(self):
        from scanner.__main__ import walk_list
        snap, op, sc = self.make([["A", "B"], ["C", "D"], ["E", "F"]])
        walk_list(snap, op, sc, lambda r: r.name == "E", max_pages=10, limit=1)
        self.assertEqual(self.opened, ["E"])


class SendTests(unittest.TestCase):
    # input bar after typing, keyboard open (Send button right of the box)
    TYPED = [Line(170, 1180, 640, 1225, "Price OK, please send PI"),
             Line(905, 1175, 1010, 1230, "Send"),
             Line(40, 1500, 110, 1550, "q")]

    def test_send_button_and_typed_text(self):
        from scanner.parse import find_send_button, typed_text_matches
        b = find_send_button(self.TYPED)
        self.assertEqual(b.text, "Send")
        self.assertTrue(typed_text_matches(self.TYPED, b, "Price OK, please send PI"))
        self.assertTrue(typed_text_matches(self.TYPED, b, "Price 0K, please send PI"))  # OCR slip
        self.assertFalse(typed_text_matches(self.TYPED, b, "Price is too high"))

    def test_long_text_scrolled_in_box(self):
        from scanner.parse import find_send_button, typed_text_matches
        box = [Line(170, 1120, 860, 1165, "for the 2 ZIM shipments to Yulong"),
               Line(170, 1180, 640, 1225, "by Friday thanks"), Line(905, 1175, 1010, 1230, "Send")]
        text = "Hi John, please make the list and confirm pick up dates " \
               "for the 2 ZIM shipments to Yulong by Friday thanks"
        self.assertTrue(typed_text_matches(box, find_send_button(box), text))

    def test_send_word_in_chat_is_not_the_button(self):
        from scanner.parse import find_send_button
        self.assertIsNone(find_send_button([Line(202, 900, 300, 950, "send")]))

    def test_text_problem_and_quoting(self):
        from scanner.device import shell_input_arg, text_problem
        self.assertEqual(text_problem("Price OK, $5.20 (FOB)"), "")
        self.assertIn("line", text_problem("a\nb"))
        self.assertIn("not", text_problem("价格 ok"))
        self.assertTrue(text_problem("   "))
        self.assertEqual(shell_input_arg("it's $5"), "'it'\\''s%s$5'")
