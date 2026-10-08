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
