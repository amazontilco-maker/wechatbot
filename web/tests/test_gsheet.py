import json
import os
import tempfile
import unittest
from urllib.parse import unquote

from fastapi.testclient import TestClient

from web import gsheet
from web.app import create_app
from web.db import connect, get_setting
from web.tests.test_app import add_user

SID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"

UK = [  # the UK layout: group row, names, route row, then products
    [None, "UK", None, None, None, "ON HAND STOCK", None, None, None, None, None, None, None,
     "INBOUND UNITS FROM CHINA", None, None, None, None, None, None, "ONHAND + INBOUND STOCK"],
    ["Products", "SKU", "ASIN", "7 days", "30 days", "Units in Amazon", "", "", "", "Stocks with 3PL",
     "TOTAL UNITS", "", "", "Transit to Amazon", "", "", "", "TOTAL UNITS", "", "", "Total units"],
    ["", "", "", "", "", "", "", "", "", "", "", "", "", "AIR", "FAST OCEAN", "OCEAN"],
    ["Electric Pill Crusher", "PMEC-001", "B093", 1.14, 0.97, 60, "", "", "", 0, 60, "", "", 0, 0, 100, "",
     100, "", "", 160],
    ["Pill Crusher V.2", "PMPC-UK002", "B075", 4.43, 4.83, 685, "", "", "", 0, 685],
    ["Total Pill Crusher", "", "", 5.57],
]


class Resp:
    def __init__(self, code, body):
        self.status_code, self.body, self.text = code, body, json.dumps(body)

    def json(self):
        return self.body


class FakeGoogle:
    """Answers the three Google API calls from a dict of tabs."""

    def __init__(self, tabs, shared=True, modified="2026-01-05T10:00:00Z"):
        self.tabs, self.shared, self.modified, self.calls = tabs, shared, modified, 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if not self.shared:
            return Resp(403, {"error": {"message": "The caller does not have permission"}})
        if "drive" in url:
            return Resp(200, {"modifiedTime": self.modified, "lastModifyingUser": {"displayName": "Tilen"}})
        if url.endswith("values:batchGet"):
            out = []
            for k, v in params:
                if k != "ranges":
                    continue
                name, _, rng = v.partition("!")
                rows = self.tabs[name.strip("'").replace("''", "'")]
                out.append({"values": rows[:3] if rng == "1:3" else rows})
            return Resp(200, {"valueRanges": out})
        return Resp(200, {"properties": {"title": "Stock count LIVE"},
                          "sheets": [{"properties": {"title": t}} for t in self.tabs]})


class LinkTest(unittest.TestCase):
    def test_sheet_id_from(self):
        self.assertEqual(gsheet.sheet_id_from(f"https://docs.google.com/spreadsheets/d/{SID}/edit#gid=0"), SID)
        self.assertEqual(gsheet.sheet_id_from(SID), SID)
        with self.assertRaises(gsheet.SheetError) as e:
            gsheet.sheet_id_from("https://docs.google.com/spreadsheets/d/1fGDIkxG_QDuQtZUKssmz399nWGntz56O/"
                                 "edit?usp=sharing&rtpof=true&sd=true")
        self.assertIn("Save as Google Sheets", str(e.exception))
        with self.assertRaises(gsheet.SheetError):
            gsheet.sheet_id_from("https://example.com/hello")

    def test_quote_tab(self):
        self.assertEqual(gsheet.quote_tab("Sam's UK"), "'Sam''s UK'")


class SheetAppTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "wechat.db")
        db = connect(self.path)
        add_user(db, "Ann", "admin")
        add_user(db, "Vic", "viewer")
        db.close()
        with open(os.path.join(self.dir, gsheet.KEY_NAME), "w") as f:
            json.dump({"type": "service_account", "client_email": "robot@tilco.iam.gserviceaccount.com"}, f)
        self.google = FakeGoogle({"UK": UK, "SKU Status": [["Products", "SKU", "ASIN", "Market"]]})
        factory = lambda key: gsheet.GoogleSheets(key, session=self.google)
        self.client = TestClient(create_app(self.path, background=False, sheets_client=factory))
        self.client.post("/login", data={"name": "Ann", "password": "correct horse 1"})

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir)

    def csrf(self):
        html = self.client.get("/risk").text
        return html.split('name="csrf" value="')[1].split('"')[0]

    def test_link_tabs_and_refresh(self):
        page = self.client.get("/risk").text
        self.assertIn("robot@tilco.iam.gserviceaccount.com", page)
        self.assertIn("No stock sheet linked yet", page)
        r = self.client.post("/risk/sheet", data={"csrf": self.csrf(),
                                                  "link": f"https://docs.google.com/spreadsheets/d/{SID}/edit"})
        self.assertIn("Stock count LIVE", r.text)                       # the sheet's name is shown
        self.assertIn(f'href="https://docs.google.com/spreadsheets/d/{SID}/edit"', r.text)
        self.assertIn("by Tilen", r.text)
        self.assertIn('value="SKU Status" disabled', r.text)            # not a stock tab
        r = self.client.post("/risk/settings", data={"csrf": self.csrf(), "tabs": ["UK"], "threshold": "60"})
        self.assertIn("PMEC-001", r.text)                               # 60 / 1.14 = 53 days
        self.assertNotIn("PMPC-UK002", r.text)
        self.assertIn("Nobody has edited the sheet", r.text)           # modified date is old in the fake

        calls = self.google.calls
        self.client.post("/risk/refresh", data={"csrf": self.csrf()})  # just refreshed: not again yet
        self.assertEqual(self.google.calls, calls)

        viewer = TestClient(create_app(self.path, background=False))
        viewer.post("/login", data={"name": "Vic", "password": "correct horse 1"})
        page = viewer.get("/risk").text
        self.assertIn("PMEC-001", page)
        self.assertIn("Stock count LIVE", page)
        self.assertNotIn("robot@", page)
        self.assertNotIn("Save link", page)

    def test_not_shared_keeps_last_copy(self):
        self.client.post("/risk/sheet", data={"csrf": self.csrf(), "link": SID})
        self.client.post("/risk/settings", data={"csrf": self.csrf(), "tabs": ["UK"]})
        self.google.shared = False
        db = connect(self.path)
        db.execute("UPDATE settings SET value = '0' WHERE key = 'sheet_attempt_ms'")
        db.commit()
        r = self.client.post("/risk/refresh", data={"csrf": self.csrf()})
        self.assertIn("Share it with robot@tilco.iam.gserviceaccount.com", r.text)
        self.assertIn("PMEC-001", r.text)                               # last good copy still shown
        self.assertIn("Share it with", get_setting(db, "sheet_error"))

    def test_reset_removes_link_and_data(self):
        self.client.post("/risk/sheet", data={"csrf": self.csrf(), "link": SID})
        self.client.post("/risk/settings", data={"csrf": self.csrf(), "tabs": ["UK"], "threshold": "45"})
        snapshot = os.path.join(self.dir, gsheet.SNAPSHOT_NAME)
        self.assertTrue(os.path.exists(snapshot))
        r = self.client.post("/risk/reset", data={"csrf": self.csrf()})            # box not ticked
        self.assertEqual(r.status_code, 400)
        self.assertTrue(os.path.exists(snapshot))
        r = self.client.post("/risk/reset", data={"csrf": self.csrf(), "confirm": "yes"})
        self.assertFalse(os.path.exists(snapshot))
        self.assertIn("No stock sheet linked yet", r.text)
        self.assertNotIn("PMEC-001", r.text)
        self.assertNotIn("Stock count LIVE", r.text)
        db = connect(self.path)
        self.assertEqual(get_setting(db, "sheet_id"), "")
        self.assertEqual(get_setting(db, "stock_tabs"), "[]")
        self.assertEqual(get_setting(db, "stock_threshold"), "45")              # rules are kept
        self.assertIn("stock reset", [r[0] for r in db.execute("SELECT action FROM audit")])

    def test_excel_link_refused(self):
        r = self.client.post("/risk/sheet", data={"csrf": self.csrf(), "link":
                             "https://docs.google.com/spreadsheets/d/1fGDIkxG_QDuQtZUKssmz399nWGntz56O/edit?rtpof=true"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Save as Google Sheets", r.text)

    def test_viewer_cannot_change_link(self):
        viewer = TestClient(create_app(self.path, background=False))
        viewer.post("/login", data={"name": "Vic", "password": "correct horse 1"})
        html = viewer.get("/risk").text
        csrf = html.split('name="csrf" value="')[1].split('"')[0]
        self.assertEqual(viewer.post("/risk/sheet", data={"csrf": csrf, "link": SID}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
