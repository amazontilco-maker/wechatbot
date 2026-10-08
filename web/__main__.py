"""Team web app for the supplier WeChat inbox.

  py -m web serve                      start the app on http://127.0.0.1:8000
  py -m web adduser NAME ROLE          add a login (asks for the password); ROLE = viewer|sender|approver|admin
  py -m web setrole NAME ROLE          change someone's role
  py -m web passwd NAME                set a new password (logs them out everywhere)
  py -m web disable NAME               block a login (logs them out); `enable NAME` undoes it
  py -m web users                      list logins

Roles: viewer reads chats; sender also writes notes and replies (sending needs approval);
approver also approves & sends and sees the log; admin also manages logins.
"""
import argparse
import getpass
import sys

from . import auth
from .db import ROLES, audit, connect, now_ms


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
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
