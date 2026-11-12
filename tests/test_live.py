#!/usr/bin/env python3
"""Tests for bin/live.py (one real Alpaca paper account per book) against a fake Alpaca.
Run: python3 tests/test_live.py"""
import datetime as dt
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import books  # noqa: E402
import live  # noqa: E402

FAILS = []


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


PRICES = {"AAA": 100.0, "BBB": 50.0, "SPY": 500.0, "REFUSE": 10.0}
OPT = "AAA270617C00100000"
ACCOUNTS = {}   # key -> account state


class FakeAlpaca:
    def __init__(self, key, secret):
        self.a = ACCOUNTS.setdefault(key, {"cash": 1_000_000.0, "pos": {}, "orders": []})

    def account(self):
        eq = self.a["cash"] + sum(q * PRICES.get(s, 10.0) * (100 if live.is_option(s) else 1) for s, q in self.a["pos"].items())
        return {"equity": str(eq), "cash": str(self.a["cash"])}

    def positions(self):
        return [{"symbol": s, "qty": str(q), "current_price": str(PRICES.get(s, 10.0))} for s, q in self.a["pos"].items() if q]

    def orders(self, status, limit=50):
        return [o for o in self.a["orders"] if (o["status"] in ("new", "accepted")) == (status == "open")
                or (status == "closed" and o["status"] == "filled")]

    def submit(self, body):
        if body["symbol"] == "REFUSE":
            raise live.broker.BrokerError("Alpaca 422: asset not tradable")
        o = {**body, "id": f"o{len(self.a['orders'])}", "status": "new"}
        self.a["orders"].append(o)
        return o

    def latest_price(self, sym):
        return PRICES[sym]

    def _req(self, method, url, body=None):
        if "options/quotes" in url:
            return {"quotes": {OPT: {"bp": 9.5, "ap": 10.0}}}
        return {}


def market_open(fill_options=True):
    """Fill every working order at the current price, as the open would."""
    for a in ACCOUNTS.values():
        for o in a["orders"]:
            if o["status"] != "new" or (live.is_option(o["symbol"]) and not fill_options):
                if o["status"] == "new" and live.is_option(o["symbol"]):
                    o["status"] = "expired"
                continue
            q = float(o["qty"]) * (1 if o["side"] == "buy" else -1)
            m = 100 if live.is_option(o["symbol"]) else 1
            a["pos"][o["symbol"]] = round(a["pos"].get(o["symbol"], 0) + q, 6)
            a["cash"] -= q * PRICES.get(o["symbol"], 10.0) * m
            o["status"], o["filled_at"] = "filled", "2026-11-12T14:30:00Z"


class FakeMarket:
    def bars(self, symbols, start):
        days = ["2026-11-12", "2026-11-13"]
        return {s: {d: {"o": PRICES.get(s, 10.0), "c": PRICES.get(s, 10.0)} for d in days} for s in symbols}


def when(day, hh):
    return dt.datetime.combine(dt.date.fromisoformat(day), dt.time(hh), books.ET)


live.CLIENT = FakeAlpaca
tmp = Path(tempfile.mkdtemp())
(tmp / "topics/inv/books").mkdir(parents=True)
(tmp / "state").mkdir()
acc = tmp / "accounts.toml"
acc.write_text('[acct-1]\nnumber = "PA1"\nkey_id = "k1"\nsecret_key = "s1"\n')
cfg = {"root": tmp, "state": tmp / "state", "books": {"inv": {"mode": "alpaca", "accounts_file": str(acc)}}}
LIQ = lambda sym: {"open_interest": 10000, "close_price_date": "2026-11-11"}


def write(bid, **spec):
    base = {"id": bid, "thesis": "T1", "targets": [{"symbol": "AAA", "kind": "etf", "weight": 0.5},
            {"symbol": "BBB", "kind": "stock", "weight": 0.2}, {"symbol": "SPY", "kind": "etf", "weight": -0.1},
            {"symbol": OPT, "kind": "option", "weight": 0.1}], "kill": [{"if": "nav_below", "value": 0.8}]}
    base.update(spec)
    (tmp / f"topics/inv/books/{bid}.json").write_text(json.dumps(base))


write("B001")
books.accept(cfg, "inv", now=when("2026-11-12", 5), lookup=LIQ)
st = books.load_state(cfg, "inv")
b = st["books"]["B001"]
orders = {o["symbol"]: o for o in ACCOUNTS["k1"]["orders"]}
ok(b["account"] == "acct-1", "a new book is given a free account")
ok(orders["AAA"]["qty"] == "5000" and orders["AAA"]["type"] == "market" and orders["AAA"]["side"] == "buy",
   "50% of $1M at $100: a 5,000-share market buy on the book's own account")
ok(orders["SPY"]["side"] == "sell" and orders["SPY"]["qty"] == "200", "a -10% weight is a whole-share short sale")
ok(orders[OPT]["type"] == "limit" and orders[OPT]["qty"] == "100" and orders[OPT]["limit_price"] == "10.30",
   "10% in options: 100 contracts, as a limit order a little above the ask")
ok(all(o["client_order_id"].startswith("B001-") for o in ACCOUNTS["k1"]["orders"]), "every order names its book")
ok("orders placed on acct-1" in st["verdicts"]["B001"], "the verdict says where the orders went")

write("B002")
books.accept(cfg, "inv", now=when("2026-11-13", 5), lookup=LIQ)
v = books.load_state(cfg, "inv")["verdicts"]["B002"]
ok(v.startswith("held") and "no free Alpaca paper account" in v, "with no free account, a new book waits")

market_open(fill_options=False)
books.update(cfg, "inv", market=FakeMarket(), now=when("2026-11-12", 17))
b = books.load_state(cfg, "inv")["books"]["B001"]
ok(b["status"] == "live" and b["start"]["date"] == "2026-11-12", "after the open, the book is live from Alpaca's fills")
ok(b["positions"]["AAA"] == 5000 and OPT not in b["positions"], "positions are read back from Alpaca")
resent = [o for o in ACCOUNTS["k1"]["orders"] if o["symbol"] == OPT and o["status"] == "new"]
ok(len(resent) == 1, "the option limit that expired unfilled is sent again")
market_open(fill_options=True)
books.update(cfg, "inv", market=FakeMarket(), now=when("2026-11-13", 17))
b = books.load_state(cfg, "inv")["books"]["B001"]
ok(b["positions"].get(OPT) == 100, "and fills the next day")
ok(b["history"] and b["history"][-1]["date"] == "2026-11-13", "a value history is kept, one point per session")

PRICES["AAA"] = 40.0
books.update(cfg, "inv", market=FakeMarket(), now=when("2026-11-14", 5))
b = books.load_state(cfg, "inv")["books"]["B001"]
ok(b["status"] == "closing" and any("kill rule hit" in e["text"] for e in b["events"]), "a 20% fall breaks nav_below 0.8")
sells = [o for o in ACCOUNTS["k1"]["orders"] if o["status"] == "new"]
ok({o["symbol"] for o in sells} == {"AAA", "BBB", "SPY", OPT} and all(
    (o["side"] == "sell") == (o["symbol"] != "SPY") for o in sells), "it sends orders to close every position, buying back the short")
market_open()
books.update(cfg, "inv", market=FakeMarket(), now=when("2026-11-14", 17))
b = books.load_state(cfg, "inv")["books"]["B001"]
ok(b["status"] == "killed" and not b["positions"] and b["end"]["nav"] > 0, "once Alpaca shows no positions, the book is killed")
md = (tmp / "topics/inv/BOOKS.md").read_text()
ok("Held at Alpaca: paper account acct-1 (PA1)" in md and "(simulated)" not in md, "BOOKS.md names the account and its number")

(tmp / "topics/inv/books/B002.json").unlink()
write("B003", targets=[{"symbol": "REFUSE", "kind": "etf", "weight": 0.5}])
acc.write_text(acc.read_text() + '[acct-2]\nnumber = "PA2"\nkey_id = "k2"\nsecret_key = "s2"\n')
books.accept(cfg, "inv", now=when("2026-11-15", 5), lookup=LIQ)
b = books.load_state(cfg, "inv")["books"]["B003"]
ok(b["account"] == "acct-2" and any("refused" in e["text"] for e in b["events"]), "an account is never reused; a refusal is reported")
shutil.rmtree(tmp)
print(f"\n{'All passed' if not FAILS else str(len(FAILS)) + ' failed'}")
sys.exit(1 if FAILS else 0)
