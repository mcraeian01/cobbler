#!/usr/bin/env python3
"""Tests for bin/accounts.py. Run: python3 tests/test_accounts.py"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import accounts  # noqa: E402
import live  # noqa: E402

FAILS = []


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


tmp = Path(tempfile.mkdtemp())
f = tmp / "accts.toml"
f.write_text('# header comment kept\n\n[cobbler-1]\nlogin = "me+cobbler@example.com"\nnumber = "PA1"\nkey_id = "k1"\nsecret_key = "s1"\n\n'
             '[default-a]\nlogin = "me+cobbler@example.com"\nnumber = "PA0"\nuse = false\n')
(tmp / "state").mkdir()
cfg = {"root": tmp, "state": tmp / "state", "books": {"inv": {"mode": "alpaca", "accounts_file": str(f)}},
       "accounts": {"login_pattern": "me+{alias}@example.com", "per_login": 3, "table": "_private/ACCOUNTS.md"}}

lines = accounts.plan(cfg, 10, today="2026-11-11")
a = accounts.read(f)
logins = {}
for name, v in a.items():
    logins.setdefault(v["login"], []).append(name)
ok(len(a) == 12, "10 new accounts reserved")
ok(logins["me+cobbler@example.com"] == ["cobbler-1", "default-a", "awl"], "the existing login's last free slot is used first")
ok(all(len(v) == 3 for v in logins.values()) and set(logins) == {"me+cobbler@example.com", "me+welt@example.com",
   "me+sole@example.com", "me+lace@example.com"}, "then new logins named after their first account, 3 accounts each")
new = [k for k in a if k not in ("cobbler-1", "default-a")]
ok(all(k.isalpha() and k.islower() for k in new) and len(set(new)) == 10, "every new name is a unique single word, no numbers")
ok("(new login: sign up first)" in "\n".join(lines), "the checklist flags new logins")
lines2 = accounts.plan(cfg, 2, today="2026-11-12")
b2 = accounts.read(f)
ok(len(b2) == 14 and not set(b2) - set(a) & set(a) and all(b2[k]["login"] != b2["awl"]["login"] or k in a for k in b2),
   "a second plan never reuses a name or overfills a login")
for k in [k for k in b2 if k not in a]:
    del b2[k]
accounts.write(f, b2)
text = f.read_text()
ok(text.startswith("# header comment kept") and 'secret_key = "s1"' in text and "use = false" in text, "rewriting keeps the header, keys and flags")
ok(oct(f.stat().st_mode)[-3:] == "600", "the file stays private")


class Fake:
    def __init__(self, key, secret):
        self.key = key

    def account(self):
        if self.key == "bad":
            raise live.broker.BrokerError("Alpaca 401: unauthorized")
        return {"account_number": {"k1": "PA1", "k2": "PA2", "k3": "PA9"}[self.key], "status": "ACTIVE",
                "equity": "1000000" if self.key != "k3" else "100000", "options_trading_level": 3}


live.CLIENT = Fake
txt = f.read_text().replace('[awl]\n', '[awl]\nkey_id = "k2"\nsecret_key = "s2"\n') \
                   .replace('[welt]\n', '[welt]\nnumber = "PA3"\nkey_id = "k3"\nsecret_key = "s3"\n') \
                   .replace('[vamp]\n', '[vamp]\nkey_id = "bad"\nsecret_key = "x"\n')
f.write_text(txt)
out = "\n".join(accounts.check(cfg))
a = accounts.read(f)
ok("awl: ready (PA2" in out and a["awl"]["number"] == "PA2", "check fills in the number and marks good keys ready")
ok("keys belong to PA9, not PA3" in out and "balance is $100,000" in out, "wrong account and wrong balance are caught")
ok("vamp: keys rejected" in out, "bad keys are caught")
ok(set(live.load_accounts({"accounts_file": str(f)})) == {"cobbler-1", "awl"}, "books only use accounts that passed the check: no failed keys or numbers, never use=false ones")
out2 = accounts.check(cfg)
ok(not any("awl" in x for x in out2), "a second check doesn't repeat accounts already ready")

(tmp / "topics/inv/theses").mkdir(parents=True)
(tmp / "topics/inv/theses/T4.md").write_text("### T4 · An example thesis\n")
(tmp / "state/books-inv.json").write_text(json.dumps({"books": {"B002": {"account": "awl", "thesis": "T4", "status": "live"}}}))
t = accounts.table(cfg).read_text()
ok("| awl | me+cobbler@example.com | PA2 | ready | B002 (live) | T4 · An example thesis |" in t, "the table shows which book and thesis each account holds")
ok("| default-a |" in t and "not for books" in t and "s1" not in t and "k2" not in t, "the table lists every account but never a key")
# ---- asking for accounts only when needed, one login at a time
tmp2 = Path(tempfile.mkdtemp())
f2 = tmp2 / "a.toml"
f2.write_text('[one]\nlogin = "me+one@example.com"\nkey_id = "k1"\nsecret_key = "s1"\nadded = "2026-11-01"\n\n'
              '[two]\nlogin = "me+one@example.com"\nkey_id = "k2"\nsecret_key = "s2"\nadded = "2026-11-01"\n')
(tmp2 / "state").mkdir()
cfg2 = {"root": tmp2, "state": tmp2 / "state", "books": {"inv": {"mode": "alpaca", "accounts_file": str(f2)}},
        "accounts": {"login_pattern": "me+{alias}@example.com", "per_login": 3}}
ok(accounts.needs(cfg2) == [], "two free accounts: nothing to ask")
(tmp2 / "state/books-inv.json").write_text(json.dumps({"books": {"B001": {"account": "one", "thesis": "T1", "status": "live"}}, "verdicts": {}}))
n = accounts.needs(cfg2, today="2026-11-12")
ok(len(n) == 1 and "1 free account left" in n[0] and "In me+one@example.com, make sure awl exists" in n[0],
   "one free left: it reserves more and asks for the open slot in the existing login first")
r = accounts.read(f2)
new = [k for k in r if k not in ("one", "two")]
ok(len(new) == 3 and r["awl"]["login"] == "me+one@example.com" and r["welt"]["login"] == "me+welt@example.com",
   "it reserves a login's worth of names, filling the open login before starting a new one")
ok(accounts.needs(cfg2, today="2026-11-12") == n, "asking again doesn't reserve more")
(tmp2 / "state/books-inv.json").write_text(json.dumps({"books": {"B001": {"account": "one"}, "B002": {"account": "two"}},
                                                        "verdicts": {"B003": "held: no free Alpaca paper account; the owner needs to add one"}}))
n = accounts.needs(cfg2, today="2026-11-13")
ok("B003 is waiting for an account" in n[0], "a waiting book is named")
shutil.rmtree(tmp2)

shutil.rmtree(tmp)
print(f"\n{'All passed' if not FAILS else str(len(FAILS)) + ' failed'}")
sys.exit(1 if FAILS else 0)
