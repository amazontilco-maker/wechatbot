"""Read the stock Google Sheet with a Google service account: a robot Google login (its key
file lives in data/) that the sheet is shared with as Viewer. Read-only.

Every refresh saves a snapshot (tab list, the chosen tabs' cells, when the sheet was last
edited and by whom) to data/stock_sheet.json, so pages load instantly and the last good
copy is still shown when Google can't be reached.
"""
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from . import stock
from .db import connect, get_setting, now_ms, set_setting

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly",
          "https://www.googleapis.com/auth/drive.metadata.readonly"]
KEY_NAME = "google-service-account.json"
SNAPSHOT_NAME = "stock_sheet.json"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets/"
DRIVE = "https://www.googleapis.com/drive/v3/files/"


class SheetError(Exception):
    pass


def sheet_id_from(text):
    """The spreadsheet id in a pasted Google Sheets link (or a bare id)."""
    text = (text or "").strip()
    if re.search(r"drive\.google\.com/file/d/|[?&]rtpof=true", text):
        raise SheetError("That link is an Excel file stored in Google Drive, not a Google Sheet. Open it, "
                         "choose File > Save as Google Sheets, and paste the link of the new sheet.")
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", text) or re.fullmatch(r"([A-Za-z0-9_-]{20,})", text)
    if not m:
        raise SheetError("That doesn't look like a Google Sheets link (docs.google.com/spreadsheets/d/...).")
    return m.group(1)


def sheet_url(sheet_id):
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"


def robot_email(key_path):
    try:
        return json.loads(Path(key_path).read_text(encoding="utf-8")).get("client_email", "")
    except (OSError, ValueError):
        return ""


def quote_tab(name):
    return "'" + name.replace("'", "''") + "'"


def iso_ms(text):
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
    except (AttributeError, ValueError):
        return None


class GoogleSheets:
    """The few Google API calls this app needs."""

    def __init__(self, key_path, session=None):
        if session is None:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account
            creds = service_account.Credentials.from_service_account_file(str(key_path), scopes=SCOPES)
            session = AuthorizedSession(creds)
        self.http = session
        self.email = robot_email(key_path)

    def _get(self, url, params):
        r = self.http.get(url, params=params, timeout=90)
        if r.status_code == 200:
            return r.json()
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            msg = r.text[:200]
        if "has not been used" in msg or "is disabled" in msg:
            api = "Google Drive API" if "drive" in url else "Google Sheets API"
            raise SheetError(f"The {api} is turned off in the Google Cloud project. Turn it on: "
                             f"APIs & Services > Library > {api} > Enable.")
        if r.status_code in (403, 404) and "drive" not in url:
            raise SheetError(f"Can't open the sheet. Share it with {self.email or 'the robot account'} "
                             f"as Viewer, and check the link. (Google said: {msg})")
        if "not supported for this document" in msg:
            raise SheetError("That file is an Excel file, not a Google Sheet. Open it, choose "
                             "File > Save as Google Sheets, and paste the new link.")
        raise SheetError(f"Google returned {r.status_code}: {msg}")

    def info(self, sid):
        return self._get(SHEETS + sid, {"fields": "properties.title,sheets.properties(title,hidden)"})

    def values(self, sid, ranges):
        if not ranges:
            return []
        params = [("ranges", r) for r in ranges] + [("valueRenderOption", "UNFORMATTED_VALUE"),
                                                    ("dateTimeRenderOption", "FORMATTED_STRING")]
        data = self._get(SHEETS + sid + "/values:batchGet", params)
        return [vr.get("values", []) for vr in data.get("valueRanges", [])]

    def modified(self, sid):
        return self._get(DRIVE + sid, {"fields": "modifiedTime,lastModifyingUser(displayName,emailAddress)",
                                       "supportsAllDrives": "true"})


def fetch_snapshot(client, sid, selected):
    info = client.info(sid)
    titles = [s["properties"]["title"] for s in info.get("sheets", [])]
    tops = client.values(sid, [quote_tab(t) + "!1:3" for t in titles])
    chosen = [t for t in selected if t in titles]
    data = client.values(sid, [quote_tab(t) for t in chosen])
    snap = {"sheet_id": sid, "title": info.get("properties", {}).get("title", ""), "fetched_ms": now_ms(),
            "tabs_all": [[t, stock.looks_like_stock(rows)] for t, rows in zip(titles, tops)],
            "rows": dict(zip(chosen, data)), "modified_ms": None, "modified_by": ""}
    try:   # when it was last edited; nice to have, so a Drive problem doesn't stop the refresh
        meta = client.modified(sid)
        user = meta.get("lastModifyingUser") or {}
        snap["modified_ms"] = iso_ms(meta.get("modifiedTime"))
        snap["modified_by"] = user.get("displayName") or user.get("emailAddress") or ""
    except SheetError:
        pass
    return snap


class SheetSync:
    """Re-reads the configured sheet now and then (and on demand), one read at a time."""

    def __init__(self, db_path, data_dir, interval=15 * 60, client_factory=GoogleSheets):
        self.db_path, self.data_dir, self.interval = db_path, Path(data_dir), interval
        self.client_factory = client_factory
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self._snap, self._snap_mtime = None, None

    @property
    def key_path(self):
        return self.data_dir / KEY_NAME

    @property
    def snapshot_path(self):
        return self.data_dir / SNAPSHOT_NAME

    def snapshot(self, sid):
        """The last saved snapshot for this sheet id, or None."""
        p = self.snapshot_path
        try:
            mtime = p.stat().st_mtime_ns
        except OSError:
            return None
        if mtime != self._snap_mtime:
            try:
                self._snap, self._snap_mtime = json.loads(p.read_text(encoding="utf-8")), mtime
            except (OSError, ValueError):
                return None
        return self._snap if self._snap and self._snap.get("sheet_id") == sid else None

    def refresh(self):
        """Read the sheet now. Returns "" on success, else the problem (also saved for the page)."""
        with self.lock:
            db = connect(self.db_path)
            try:
                sid = get_setting(db, "sheet_id")
                if not sid:
                    return ""
                set_setting(db, "sheet_attempt_ms", now_ms())
                if not self.key_path.exists():
                    problem = "The Google robot login isn't set up on the office PC yet (py -m web google KEYFILE)."
                else:
                    try:
                        client = self.client_factory(self.key_path)
                        snap = fetch_snapshot(client, sid, json.loads(get_setting(db, "stock_tabs", "[]")))
                        tmp = self.snapshot_path.with_suffix(".tmp")
                        tmp.write_text(json.dumps(snap), encoding="utf-8")
                        os.replace(tmp, self.snapshot_path)
                        problem = ""
                    except SheetError as e:
                        problem = str(e)
                    except Exception as e:   # network down, Google hiccup: keep the last good copy
                        if "invalid_grant" in str(e):
                            problem = ("Google doesn't accept the robot login key (it was deleted, or the PC clock "
                                       "is wrong). Make a new key and run py -m web google KEYFILE again.")
                        else:
                            problem = f"Could not reach Google: {e}"
                set_setting(db, "sheet_error", problem)
                return problem
            finally:
                db.close()

    def run_forever(self):
        while not self.stopping.is_set():
            try:
                self.refresh()
            except Exception as e:   # never let the background reader die
                print(f"stock sheet refresh failed: {e}")
            self.stopping.wait(self.interval)

    def start(self):
        threading.Thread(target=self.run_forever, name="sheet-sync", daemon=True).start()

    def stop(self):
        self.stopping.set()

