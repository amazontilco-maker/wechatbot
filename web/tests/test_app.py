import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from web import auth
from web.app import create_app
from web.db import connect, now_ms


def add_user(db, name, role, pw="correct horse 1"):
    db.execute("INSERT INTO users (name, role, pw_hash, created_ts) VALUES (?, ?, ?, ?)",
               (name, role, auth.hash_password(pw), now_ms()))
    db.commit()


class AuthTest(unittest.TestCase):
    def test_password_hash(self):
        h = auth.hash_password("secret password")
        self.assertTrue(auth.check_password("secret password", h))
        self.assertFalse(auth.check_password("secret passwore", h))
        self.assertFalse(auth.check_password("x", "garbage"))

    def test_rate_limit(self):
        t = [0.0]
        rl = auth.RateLimit(3, 60, clock=lambda: t[0])
        for _ in range(3):
            self.assertEqual(rl.wait_seconds("a"), 0)
            rl.fail("a")
        self.assertGreater(rl.wait_seconds("a"), 0)
        self.assertEqual(rl.wait_seconds("b"), 0)
        t[0] = 61
        self.assertEqual(rl.wait_seconds("a"), 0)


class AppTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db = connect(self.path)
        add_user(db, "Ann", "admin")
        add_user(db, "Vic", "viewer")
        db.execute("INSERT INTO convs (id, name, kind) VALUES ('ui:SZ Ocean', 'SZ Ocean', 'room')")
        for i, (d, s, t) in enumerate([("in", "Kevin", "ETA Oct 2"), ("out", "me", "Thanks"),
                                       ("in", "Kevin", "<script>alert(1)</script>")]):
            db.execute("INSERT INTO messages (id, conv_id, sender, direction, type, text, ts) "
                       "VALUES (?, 'ui:SZ Ocean', ?, ?, 'text', ?, ?)", (f"m{i}", s, d, t, 1000 + i))
        db.commit()
        db.close()
        self.client = TestClient(create_app(self.path, background=False))

    def tearDown(self):
        stock_file = os.path.join(os.path.dirname(self.path), "stock.xlsx")
        for p in (self.path, self.path + "-wal", self.path + "-shm", stock_file):
            if os.path.exists(p):
                os.remove(p)

    def login(self, name="Ann", pw="correct horse 1"):
        return self.client.post("/login", data={"name": name, "password": pw, "next": "/"},
                                follow_redirects=False)

    def csrf(self, html):
        return html.split('name="csrf" value="')[1].split('"')[0]

    def test_needs_login(self):
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/login"))

    def test_wrong_password_and_lockout(self):
        for _ in range(5):
            self.assertEqual(self.login(pw="nope").status_code, 401)
        r = self.login()   # right password, but locked for now
        self.assertEqual(r.status_code, 429)

    def test_inbox_thread_and_unread(self):
        self.assertEqual(self.login().status_code, 303)
        r = self.client.get("/messages")
        self.assertIn("SZ Ocean", r.text)
        self.assertIn('class="badge">2<', r.text)
        self.assertIn("<b>2</b><span>new messages", self.client.get("/").text)
        r = self.client.get("/chat", params={"id": "ui:SZ Ocean"})
        self.assertIn("Kevin", r.text)
        self.assertIn("&lt;script&gt;", r.text)        # message text is escaped
        self.assertNotIn("<script>alert", r.text)
        self.assertNotIn('class="badge"', self.client.get("/messages").text)   # read now

    def test_notes_need_csrf_and_role(self):
        self.login()
        page = self.client.get("/chat", params={"id": "ui:SZ Ocean"}).text
        r = self.client.post("/chat/notes", data={"id": "ui:SZ Ocean", "notes": "x", "csrf": "bad"})
        self.assertEqual(r.status_code, 403)
        self.client.post("/chat/notes", data={"id": "ui:SZ Ocean", "notes": "Ocean forwarder", "csrf": self.csrf(page)})
        self.assertIn("Ocean forwarder", self.client.get("/chat", params={"id": "ui:SZ Ocean"}).text)

        viewer = TestClient(create_app(self.path, background=False))
        viewer.post("/login", data={"name": "Vic", "password": "correct horse 1"})
        page = viewer.get("/chat", params={"id": "ui:SZ Ocean"}).text
        self.assertNotIn("Save notes", page)
        self.assertEqual(viewer.get("/audit").status_code, 403)

    def test_category_and_sections(self):
        self.login()
        page = self.client.get("/chat", params={"id": "ui:SZ Ocean"}).text
        self.client.post("/chat/category", data={"id": "ui:SZ Ocean", "category": "forwarder",
                                                 "csrf": self.csrf(page)})
        self.assertIn("SZ Ocean", self.client.get("/messages", params={"cat": "forwarder"}).text)
        self.assertNotIn("SZ Ocean", self.client.get("/messages", params={"cat": "supplier"}).text)
        for path in ("/shipments", "/updates", "/risk", "/approvals", "/chasers", "/health"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_stock_risk_upload_and_settings(self):
        from web.tests.test_stock import make_workbook
        self.login()
        page = self.client.get("/risk").text
        self.assertIn("No stock sheet linked yet", page)
        xlsx = self.path + ".xlsx"
        make_workbook(xlsx)
        try:
            with open(xlsx, "rb") as f:
                r = self.client.post("/risk/upload", data={"csrf": self.csrf(page)},
                                     files={"file": ("stock.xlsx", f.read())})
        finally:
            os.remove(xlsx)
        self.assertEqual(r.status_code, 200)
        page = r.text
        self.assertIn("Live tabs and rules", page)
        r = self.client.post("/risk/settings", data={"csrf": self.csrf(page), "tabs": ["UK", "USA old"],
                                                     "threshold": "60", "stock": "onhand", "sales": "max"})
        self.assertIn("PMEC-001", r.text)            # 53 days: flagged
        self.assertNotIn("PMPC-UK002", r.text)       # 142 days: only under "All SKUs"
        self.assertIn("PMPC-UK002", self.client.get("/risk", params={"show": "all"}).text)
        self.assertIn("SKUs under 60 days of stock", self.client.get("/").text)
        bad = self.client.post("/risk/upload", data={"csrf": self.csrf(page)}, files={"file": ("x.xlsx", b"hello")})
        self.assertEqual(bad.status_code, 400)
        viewer = TestClient(create_app(self.path, background=False))
        viewer.post("/login", data={"name": "Vic", "password": "correct horse 1"})
        self.assertIn("PMEC-001", viewer.get("/risk").text)
        self.assertNotIn("Save settings", viewer.get("/risk").text)

    def test_search_and_logout(self):
        self.login()
        self.assertIn("ETA Oct 2", self.client.get("/search", params={"q": "eta"}).text)
        page = self.client.get("/").text
        self.client.post("/logout", data={"csrf": self.csrf(page)})
        self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 303)

    def test_open_redirect_blocked(self):
        r = self.client.post("/login", data={"name": "Ann", "password": "correct horse 1", "next": "//evil.com"},
                             follow_redirects=False)
        self.assertEqual(r.headers["location"], "/")


if __name__ == "__main__":
    unittest.main()
