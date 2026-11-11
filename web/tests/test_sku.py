import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from web import sku
from web.app import create_app
from web.db import connect
from web.tests.test_app import add_user


class SkuTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = connect(self.path)

    def tearDown(self):
        self.db.close()
        for p in (self.path, self.path + "-wal", self.path + "-shm"):
            if os.path.exists(p):
                os.remove(p)

    def make(self, **kw):
        args = dict(brand="TC", product_name="Insulated Ice Pack", product_code="IPW",
                    manufacturer="Ningbo Cool Co", reason="N")
        args.update(kw)
        return sku.generate(self.db, "Ann", **args)

    def test_suggest_code(self):
        self.assertEqual(sku.suggest_code("Insulated Ice Pack"), "IIP")
        self.assertEqual(sku.suggest_code("Gloves"), "GLO")
        self.assertEqual(sku.suggest_code("Pill Crusher V.2"), "PCV2")

    def test_format_and_unique_numbers(self):
        a = self.make()
        self.assertEqual(a["sku"], "TC-IPW-M01-N-0001")
        b = self.make(reason="S", manufacturer="  ningbo cool co ")     # same manufacturer, any spelling of case
        self.assertEqual(b["sku"], "TC-IPW-M01-S-0002")
        c = self.make(brand="PM", product_name="Pill Crusher", product_code="", manufacturer="Other Factory",
                      reason="X", reason_note="sample run")
        self.assertEqual(c["sku"], "PM-PC-M02-X-0003")
        sku.cancel(self.db, "Ann", c["sku"], "typo")
        d = self.make()
        self.assertEqual(d["sku"], "TC-IPW-M01-N-0004")                  # a cancelled number is not reused
        self.assertEqual(len({r["sku"] for r in sku.search(self.db, "")}), 4)

    def test_rules(self):
        self.make()
        with self.assertRaises(sku.SkuError):
            self.make(reason="X")                                         # Misc needs the reason typed
        with self.assertRaises(sku.SkuError):
            self.make(product_code="ICE")                                 # the product already has a code
        with self.assertRaises(sku.SkuError):
            self.make(product_name="Ice Wrap", product_code="IPW")        # the code belongs to another product
        with self.assertRaises(sku.SkuError):
            self.make(brand="ZZ")
        with self.assertRaises(sku.SkuError):
            self.make(product_name="New Thing", product_code="TOOLONG")
        self.assertEqual(self.make(product_code="")["product_code"], "IPW")   # empty = reuse the product's code
        self.assertEqual(len(sku.search(self.db, "")), 2)                 # failed tries left nothing behind

    def test_skips_sku_already_in_sheet(self):
        row = sku.generate(self.db, "Ann", "TC", "Insulated Ice Pack", "IPW", "Ningbo Cool Co", "N",
                           taken={"TC-IPW-M01-N-0001"})
        self.assertEqual(row["sku"], "TC-IPW-M01-N-0002")

    def test_decode_and_cancel(self):
        row = self.make(reason="E", reason_note="thicker gel")
        d = sku.decode(self.db, " tc-ipw-m01-e-0001 ")
        self.assertEqual([p[2] for p in d["parts"]][:4], ["Tilcare", "Insulated Ice Pack", "Ningbo Cool Co",
                                                           "Engineering modification: thicker gel"])
        self.assertEqual(d["record"]["sku"], row["sku"])
        self.assertIsNone(sku.decode(self.db, "PMPC-UK002"))               # old style: not decodable
        stranger = sku.decode(self.db, "TW-ZZ-M09-N-0777")                 # right format, never generated
        self.assertIsNone(stranger["record"])
        with self.assertRaises(sku.SkuError):
            sku.cancel(self.db, "Ann", row["sku"], "")
        sku.cancel(self.db, "Ann", row["sku"], "wrong brand")
        self.assertEqual(sku.get(self.db, row["sku"])["status"], "cancelled")
        with self.assertRaises(sku.SkuError):
            sku.cancel(self.db, "Ann", row["sku"], "again")


class SkuPageTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "wechat.db")
        db = connect(self.path)
        add_user(db, "Ann", "admin")
        add_user(db, "Vic", "viewer")
        db.close()
        self.client = TestClient(create_app(self.path, background=False))
        self.client.post("/login", data={"name": "Ann", "password": "correct horse 1"})

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir)

    def csrf(self, client=None):
        html = (client or self.client).get("/sku").text
        return html.split('name="csrf" value="')[1].split('"')[0]

    def test_generate_search_cancel(self):
        form = {"csrf": self.csrf(), "brand": "TC", "product_name": "Insulated Ice Pack", "product_code": "IPW",
                "manufacturer": "Ningbo Cool Co", "reason": "N"}
        r = self.client.post("/sku/generate", data=form)
        self.assertIn("New SKU generated", r.text)
        self.assertIn("TC-IPW-M01-N-0001", r.text)
        self.assertIn("Not used yet", r.text)
        bad = self.client.post("/sku/generate", data=dict(form, reason="X"))
        self.assertEqual(bad.status_code, 400)
        self.assertIn("For Misc, type the reason", bad.text)
        self.assertIn('value="Ningbo Cool Co"', bad.text)                  # the form keeps what was typed
        found = self.client.get("/sku", params={"q": "tc-ipw-m01-n-0001"}).text
        self.assertIn("Tilcare", found)
        self.assertIn("Generated by Ann", found)
        self.assertIn("TC-IPW-M01-N-0001", self.client.get("/sku", params={"q": "ningbo"}).text)
        r = self.client.post("/sku/cancel", data={"csrf": self.csrf(), "sku": "TC-IPW-M01-N-0001", "note": "typo"})
        self.assertIn("Cancelled by Ann", r.text)

        viewer = TestClient(create_app(self.path, background=False))
        viewer.post("/login", data={"name": "Vic", "password": "correct horse 1"})
        page = viewer.get("/sku").text
        self.assertIn("TC-IPW-M01-N-0001", page)
        self.assertNotIn("Generate a SKU", page)
        self.assertEqual(viewer.post("/sku/generate", data=dict(form, csrf=self.csrf(viewer))).status_code, 403)

    def test_in_use_when_in_stock_sheet(self):
        import json
        from web import gsheet
        from web.tests.test_gsheet import SID, UK, FakeGoogle
        with open(os.path.join(self.dir, gsheet.KEY_NAME), "w") as f:
            json.dump({"type": "service_account", "client_email": "robot@x.iam.gserviceaccount.com"}, f)
        rows = UK + [["Insulated Ice Pack", "TC-IPW-M01-N-0001", "B0C1", 2, 2, 500, "", "", "", 0, 500]]
        google = FakeGoogle({"UK": rows})
        client = TestClient(create_app(self.path, background=False,
                                       sheets_client=lambda key: gsheet.GoogleSheets(key, session=google)))
        client.post("/login", data={"name": "Ann", "password": "correct horse 1"})
        client.post("/risk/sheet", data={"csrf": self.csrf(client), "link": SID})
        client.post("/risk/settings", data={"csrf": self.csrf(client), "tabs": ["UK"]})
        form = {"csrf": self.csrf(client), "brand": "TC", "product_name": "Insulated Ice Pack",
                "product_code": "IPW", "manufacturer": "Ningbo Cool Co", "reason": "N"}
        r = client.post("/sku/generate", data=form)
        self.assertIn("TC-IPW-M01-N-0002", r.text)        # 0001 is already in the sheet, so it was skipped
        self.assertIn("not used yet (1)", client.get("/sku").text)
        # the team starts using the new SKU: it appears in the stock sheet
        rows.append(["Insulated Ice Pack", "TC-IPW-M01-N-0002", "B0C2", 2, 2, 100, "", "", "", 0, 100])
        client.app.state.sync.refresh()
        page = client.get("/sku").text
        self.assertIn("not used yet (0)", page)                         # gone from the history
        self.assertNotIn("TC-IPW-M01-N-0002", page)
        found = client.get("/sku", params={"q": "TC-IPW-M01-N-0002"}).text
        self.assertIn("Adopted in the stock sheet", found)              # still decodable by search
        self.assertIn("Ningbo Cool Co", found)
        self.assertIn("TC-IPW-M01-N-0002", client.get("/sku", params={"q": "ice pack"}).text)
        client.post("/risk/reset", data={"csrf": self.csrf(client), "confirm": "yes"})
        self.assertIn("not used yet (0)", client.get("/sku").text)      # stays adopted without the sheet
        client.post("/risk/sheet", data={"csrf": self.csrf(client), "link": SID})
        client.post("/risk/settings", data={"csrf": self.csrf(client), "tabs": ["UK"]})
        old = client.get("/sku", params={"q": "PMEC"}).text
        self.assertIn("Existing SKUs from the stock sheet", old)
        self.assertIn("PMEC-001", old)


if __name__ == "__main__":
    unittest.main()
