r"""Team web app for the supplier WeChat inbox.

  py -m web serve                      start the app on http://127.0.0.1:8000
  py -m web adduser NAME ROLE          add a login (asks for the password); ROLE = viewer|sender|approver|admin
  py -m web setrole NAME ROLE          change someone's role
  py -m web passwd NAME                set a new password (logs them out everywhere)
  py -m web disable NAME               block a login (logs them out); `enable NAME` undoes it
  py -m web users                      list logins
  py -m web stockfile "G:\My Drive\stock.xlsx"   read the stock file from this path (e.g. Google Drive
                                       for desktop); `stockfile --upload` goes back to uploading it in the app
  py -m web stockcheck FILE.xlsx       show which columns each stock tab has and the most urgent SKUs
  py -m web google KEY.json            install the Google robot login (service account key) and show its email
  py -m web sheetcheck                 read the linked Google Sheet now and show what was found

Roles: viewer reads chats; sender also writes notes and replies (sending needs approval);
approver also approves & sends and sees the log; admin also manages logins.
"""
import argparse
import getpass
import sys

from . import auth
from .db import ROLES, audit, connect, now_ms, set_setting


def ask_password():
    while True:
        pw = getpass.getpass("New password (typing is hidden): ")
        problem = auth.password_problem(pw)
        if problem:
            print(problem)
            continue
        if getpass.getpass("Same password again: ") != pw:
            print("The two didn't match, try again.")
            continue
        return pw


def find_user(db, name):
    user = db.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
    if not user:
        sys.exit(f"No login called '{name}'. See them with: py -m web users")
    return user


def cmd_serve(args):
    import uvicorn
    from .app import create_app
    db = connect(args.db)
    if not db.execute("SELECT 1 FROM users WHERE role = 'admin' AND active = 1").fetchone():
        sys.exit("Add an admin login first:  py -m web adduser YourName admin")
    db.close()
    print(f"Open http://127.0.0.1:{args.port} in your browser. Press Ctrl+C to stop.")
    uvicorn.run(create_app(args.db), host=args.host, port=args.port, log_level="warning",
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")


def cmd_adduser(args):
    db = connect(args.db)
    if db.execute("SELECT 1 FROM users WHERE name = ?", (args.name,)).fetchone():
        sys.exit(f"'{args.name}' already exists.")
    db.execute("INSERT INTO users (name, role, pw_hash, created_ts) VALUES (?, ?, ?, ?)",
               (args.name.strip(), args.role, auth.hash_password(ask_password()), now_ms()))
    audit(db, "(PC)", "add user", f"{args.name} as {args.role}")
    print(f"Added {args.name} ({args.role}).")


def cmd_setrole(args):
    db = connect(args.db)
    user = find_user(db, args.name)
    db.execute("UPDATE users SET role = ? WHERE id = ?", (args.role, user["id"]))
    audit(db, "(PC)", "set role", f"{user['name']}: {user['role']} -> {args.role}")
    print(f"{user['name']} is now {args.role}.")


def cmd_passwd(args):
    db = connect(args.db)
    user = find_user(db, args.name)
    db.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (auth.hash_password(ask_password()), user["id"]))
    auth.end_all_sessions(db, user["id"])
    audit(db, "(PC)", "password", user["name"])
    print(f"Password changed for {user['name']}.")


def set_active(args, active):
    db = connect(args.db)
    user = find_user(db, args.name)
    db.execute("UPDATE users SET active = ? WHERE id = ?", (int(active), user["id"]))
    if not active:
        auth.end_all_sessions(db, user["id"])
    audit(db, "(PC)", "enable" if active else "disable", user["name"])
    print(f"{user['name']} {'enabled' if active else 'disabled'}.")


def cmd_users(args):
    db = connect(args.db)
    rows = db.execute("SELECT name, role, active FROM users ORDER BY name").fetchall()
    for r in rows:
        print(f"  {r['name']:<20} {r['role']:<9} {'' if r['active'] else 'DISABLED'}")
    if not rows:
        print("No logins yet. Add one with: py -m web adduser YourName admin")


def cmd_stockfile(args):
    from pathlib import Path
    from . import stock
    db = connect(args.db)
    if args.upload:
        set_setting(db, "stock_path", "")
        print("The app now uses the file uploaded on the Stock risk page.")
        return
    if not args.path:
        sys.exit('Give the file path in quotes, e.g. py -m web stockfile "G:\\My Drive\\stock.xlsx"')
    path = Path(args.path.strip('"')).expanduser().resolve()
    if not path.is_file():
        sys.exit(f"No file at {path}")
    tabs = stock.tab_names(path)
    set_setting(db, "stock_path", str(path))
    audit(db, "(PC)", "stock file", f"path {path}")
    print(f"The app now reads {path}")
    print("Stock tabs found: " + ", ".join(n for n, ok in tabs if ok))


def data_dir(args):
    from pathlib import Path
    from scanner.store import DEFAULT_DB
    import os
    return Path(args.db or os.environ.get("WB_DB") or DEFAULT_DB).resolve().parent


def cmd_google(args):
    import json
    import shutil
    from . import gsheet
    src = args.key.strip('"')
    try:
        key = json.loads(open(src, encoding="utf-8").read())
    except (OSError, ValueError) as e:
        sys.exit(f"Could not read {src}: {e}")
    if key.get("type") != "service_account" or not key.get("client_email"):
        sys.exit("That isn't a service account key (the .json downloaded from Keys > Add key > JSON).")
    dest = data_dir(args) / gsheet.KEY_NAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    print(f"Saved the robot login to {dest}")
    print("You can delete the downloaded copy now.")
    print("\nShare the Google Sheet with this email as Viewer:")
    print(f"  {key['client_email']}")


def cmd_sheetcheck(args):
    import json
    from . import gsheet, stock
    from .db import get_setting
    db = connect(args.db)
    sid = get_setting(db, "sheet_id")
    if not sid:
        sys.exit("No Google Sheet linked yet. Paste its link on the Stock risk page (Settings).")
    sync = gsheet.SheetSync(args.db, data_dir(args))
    problem = sync.refresh()
    if problem:
        sys.exit(problem)
    snap = sync.snapshot(sid)
    print(f"Read '{snap['title']}' (last edited by {snap['modified_by'] or '?'})")
    print("Stock tabs: " + ", ".join(t for t, ok in snap["tabs_all"] if ok))
    chosen = json.loads(get_setting(db, "stock_tabs", "[]"))
    if not chosen:
        print("No tabs ticked yet: tick the live tabs on the Stock risk page.")
    for name in chosen:
        tab = stock.parse_rows(snap["rows"].get(name, []), name)
        rows, counts = stock.risk_rows([tab])
        print(f"\n== {name}: {len(tab.items)} SKUs {('- ' + tab.problem) if tab.problem else ''}")
        print(f"   out {counts['out']}, critical {counts['critical']}, low {counts['low']}, "
              f"no stock figure {counts['nostock']}, ok {counts['ok']}, no sales {counts['nosales']}")


def cmd_stockcheck(args):
    from string import ascii_uppercase as AZ
    from . import stock
    letter = lambda i: (AZ[i // 26 - 1] if i >= 26 else "") + AZ[i % 26]
    names = [n for n, ok in stock.tab_names(args.path) if ok or args.all]
    for tab in stock.read_tabs(args.path, names):
        print(f"\n== {tab.name}: {len(tab.items)} SKUs {('- ' + tab.problem) if tab.problem else ''}")
        print("   " + ", ".join(f"{role}={letter(i)}" for role, i in tab.columns.items()))
        rows, counts = stock.risk_rows([tab], threshold=args.days)
        print(f"   out {counts['out']}, critical {counts['critical']}, low {counts['low']}, "
              f"no stock figure {counts['nostock']}, ok {counts['ok']}, no sales {counts['nosales']}")
        for r in rows[:5]:
            it = r["item"]
            if r["level"] in ("out", "critical", "low"):
                print(f"   row {it.row}: {it.sku} sales/day {r['rate']} on hand {it.onhand} -> {r['days']:.0f} days")


def main(argv=None):
    p = argparse.ArgumentParser(prog="web", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="database file (default data/wechat.db)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--host", default="127.0.0.1")
    s.set_defaults(fn=cmd_serve)
    a = sub.add_parser("adduser")
    a.add_argument("name")
    a.add_argument("role", choices=ROLES)
    a.set_defaults(fn=cmd_adduser)
    r = sub.add_parser("setrole")
    r.add_argument("name")
    r.add_argument("role", choices=ROLES)
    r.set_defaults(fn=cmd_setrole)
    for cmd, fn in (("passwd", cmd_passwd), ("disable", lambda a: set_active(a, False)),
                    ("enable", lambda a: set_active(a, True))):
        x = sub.add_parser(cmd)
        x.add_argument("name")
        x.set_defaults(fn=fn)
    sub.add_parser("users").set_defaults(fn=cmd_users)
    f = sub.add_parser("stockfile")
    f.add_argument("path", nargs="?")
    f.add_argument("--upload", action="store_true")
    f.set_defaults(fn=cmd_stockfile)
    k = sub.add_parser("stockcheck")
    k.add_argument("path")
    k.add_argument("--days", type=int, default=60)
    k.add_argument("--all", action="store_true", help="also tabs that don't look like stock tabs")
    k.set_defaults(fn=cmd_stockcheck)
    g = sub.add_parser("google")
    g.add_argument("key")
    g.set_defaults(fn=cmd_google)
    sub.add_parser("sheetcheck").set_defaults(fn=cmd_sheetcheck)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
