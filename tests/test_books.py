#!/usr/bin/env python3
"""Tests for bin/books.py against a fake market. Run: python3 tests/test_books.py"""
import datetime as dt
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import books  # noqa: E402

ET = books.ET
FAILS = []


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


class FakeMarket:
    def __init__(self, bars):
        self.data = bars

    def bars(self, symbols, start):
        return {s: dict(self.data.get(s, {})) for s in symbols}


def bar(o, c=None):
    return {"o": o, "c": c if c is not None else o}


def setup(**over):
    tmp = Path(tempfile.mkdtemp())
    (tmp / "topics/inv/books").mkdir(parents=True)
    (tmp / "state").mkdir()
    cfg = {"root": tmp, "state": tmp / "state", "books": {"inv": {"start_cash": 1_000_000, **over}}}
    return tmp, cfg


def write(tmp, bid, **spec):
    base = {"id": bid, "thesis": "T1", "targets": [{"symbol": "AAA", "kind": "etf", "weight": 0.6}],
            "kill": [{"if": "nav_below", "value": 0.9}]}
    base.update(spec)
    (tmp / f"topics/inv/books/{bid}.json").write_text(json.dumps(base))


def when(day, hh, mm=0):
    return dt.datetime.combine(dt.date.fromisoformat(day), dt.time(hh, mm), ET)


S = dict(books.DEFAULTS)
chk = lambda spec, stem="B001": books.check_spec({"id": stem, "thesis": "T1", "kill": [{"if": "after", "value": "2027-06-30"}], **spec}, stem, S)

# ---- spec checks
ok(chk({"targets": [{"symbol": "XLE", "kind": "etf", "weight": 1.0}]}) is None, "100% in one ETF is allowed")
ok("one company" in chk({"targets": [{"symbol": "XOM", "kind": "stock", "weight": 0.25}]}), "a single stock over 20% is rejected")
ok("no margin" in chk({"targets": [{"symbol": "XLE", "kind": "etf", "weight": 0.7}, {"symbol": "XOP", "kind": "etf", "weight": 0.4}]}), "longs over 100% are rejected")
ok(chk({"targets": [{"symbol": "XLE", "kind": "etf", "weight": 1.0}, {"symbol": "SPY", "kind": "etf", "weight": -0.5}]}) is None, "a 50% short hedge is allowed")
ok("only be bought" in chk({"targets": [{"symbol": "SPY271217C00700000", "kind": "option", "weight": -0.1}]}), "writing options is rejected")
ok("OCC" in chk({"targets": [{"symbol": "SPY", "kind": "option", "weight": 0.1}]}), "an option needs an OCC symbol")
ok("crypto" in chk({"targets": [{"symbol": "BTC/USD", "kind": "etf", "weight": 0.1}]}), "crypto is rejected for now")
ok("kill rule" in books.check_spec({"id": "B001", "thesis": "T1", "targets": [{"symbol": "XLE", "kind": "etf", "weight": 1}]}, "B001", S), "a book without kill rules is rejected")
ok("file name" in chk({"targets": [{"symbol": "XLE", "kind": "etf", "weight": 1}]}, stem="book1"), "an id that doesn't match B001 style is rejected")

# ---- open, fill at the next open, kill, sell at the next open
tmp, cfg = setup()
write(tmp, "B001")
lines = books.accept(cfg, "inv", now=when("2026-11-04", 20))           # written Wed evening
ok(any("open queued" in x for x in lines), "accept queues a new book")
mkt = FakeMarket({"SPY": {"2026-11-04": bar(500), "2026-11-05": bar(500, 505), "2026-11-06": bar(505, 506), "2026-11-09": bar(506)},
                  "AAA": {"2026-11-04": bar(90), "2026-11-05": bar(100, 100), "2026-11-06": bar(100, 80), "2026-11-09": bar(78)}})
books.update(cfg, "inv", market=mkt, now=when("2026-11-09", 17))
st = books.load_state(cfg, "inv"); b = st["books"]["B001"]
ok(b["start"]["date"] == "2026-11-05", "fills at Thursday's open, not Wednesday's (written after Wednesday's open)")
buy = [t for t in b["trades"] if t["qty"] > 0][0]
ok(abs(buy["qty"] - 6000) < 1e-6, "60% of $1M at $100 is 6,000 shares")
ok(abs(buy["price"] - 100.05) < 1e-9, "5 bps slippage charged on the buy")
ok(b["status"] == "killed", "a 20% fall breaks nav_below 0.9 and the book is sold out")
kill_day = [e for e in b["events"] if "kill rule hit" in e["text"]][0]["date"]
sell = [t for t in b["trades"] if t["qty"] < 0][0]
ok(kill_day == "2026-11-06" and sell["date"] == "2026-11-09", "the kill is seen at Friday's close and sold at Monday's open")
ok(abs(b["end"]["nav"] - (b["cash"])) < 0.01 and not b["positions"], "a killed book is all cash")
md = (tmp / "topics/inv/BOOKS.md").read_text()
ok("| B001 | T1 | killed |" in md, "BOOKS.md lists the book and its status")
shutil.rmtree(tmp)

# ---- written before the open fills the same day; rebalance; short; close
tmp, cfg = setup()
write(tmp, "B002", targets=[{"symbol": "AAA", "kind": "etf", "weight": 1.0}, {"symbol": "SPY", "kind": "etf", "weight": -0.5}],
      kill=[{"if": "after", "value": "2027-12-31"}])
books.accept(cfg, "inv", now=when("2026-11-05", 4, 50))
data = {"SPY": {"2026-11-05": bar(500, 500), "2026-11-06": bar(500, 500), "2026-11-09": bar(500, 500)},
        "AAA": {"2026-11-05": bar(100, 110), "2026-11-06": bar(110, 110), "2026-11-09": bar(110, 110)},
        "BBB": {"2026-11-06": bar(50, 50), "2026-11-09": bar(50, 50)}}
books.update(cfg, "inv", market=FakeMarket(data), now=when("2026-11-05", 17))
b = books.load_state(cfg, "inv")["books"]["B002"]
ok(b["start"]["date"] == "2026-11-05", "written at 04:50 fills at that day's open")
ok(abs(b["positions"]["SPY"] + 1000) < 1e-6, "a -50% weight is a 1,000-share SPY short")
ok(abs(b["history"][0]["nav"] - (b["cash"] + 10000 * 110 - 1000 * 500)) < 0.01, "value counts the short as a liability")
write(tmp, "B002", targets=[{"symbol": "BBB", "kind": "etf", "weight": 1.0}], kill=[{"if": "after", "value": "2027-12-31"}])
books.accept(cfg, "inv", now=when("2026-11-05", 20))
books.update(cfg, "inv", market=FakeMarket(data), now=when("2026-11-06", 17))
b = books.load_state(cfg, "inv")["books"]["B002"]
ok(set(b["positions"]) == {"BBB"} and b["status"] == "live", "a changed spec rebalances: old names sold, new one bought")
write(tmp, "B002", status="close", targets=[], kill=[{"if": "after", "value": "2027-12-31"}])
books.accept(cfg, "inv", now=when("2026-11-06", 20))
books.update(cfg, "inv", market=FakeMarket(data), now=when("2026-11-09", 17))
b = books.load_state(cfg, "inv")["books"]["B002"]
ok(b["status"] == "closed" and not b["positions"], "status close sells everything at the next open")
write(tmp, "B002", targets=[{"symbol": "AAA", "kind": "etf", "weight": 1}], kill=[{"if": "after", "value": "2027-12-31"}])
v = books.accept(cfg, "inv", now=when("2026-11-09", 20)) and books.load_state(cfg, "inv")["verdicts"]["B002"]
ok("ignored" in books.load_state(cfg, "inv")["verdicts"]["B002"], "a closed book can't be reopened")
shutil.rmtree(tmp)

# ---- pacing and limits
tmp, cfg = setup(max_new_per_week=3, max_books=20)
for i in range(1, 5):
    write(tmp, f"B00{i}")
books.accept(cfg, "inv", now=when("2026-11-05", 20))
v = books.load_state(cfg, "inv")["verdicts"]
ok(sum(1 for x in v.values() if x.startswith("accepted")) == 3 and v["B004"].startswith("held"), "only 3 new books a week; the 4th is held")
books.accept(cfg, "inv", now=when("2026-11-13", 20))
ok(books.load_state(cfg, "inv")["verdicts"]["B004"].startswith("accepted"), "the held book opens once the 7-day window allows")
shutil.rmtree(tmp)

# ---- options: whole contracts, waiting for a trade, expiry at intrinsic value
tmp, cfg = setup()
opt = "AAA261120C00100000"   # AAA call, strike 100, expires 2026-11-20
write(tmp, "B005", targets=[{"symbol": opt, "kind": "option", "weight": 0.2}, {"symbol": "AAA", "kind": "etf", "weight": 0.5}],
      kill=[{"if": "after", "value": "2027-12-31"}])
books.accept(cfg, "inv", now=when("2026-11-04", 20))
days = ["2026-11-05", "2026-11-06", "2026-11-09", "2026-11-20", "2026-11-23"]
data = {"SPY": {d: bar(500) for d in days},
        "AAA": {"2026-11-05": bar(100), "2026-11-06": bar(100), "2026-11-09": bar(100), "2026-11-20": bar(120, 130), "2026-11-23": bar(130)},
        opt: {"2026-11-06": bar(3.0, 3.0), "2026-11-09": bar(3.0)}}     # no option trade on 11-05
books.update(cfg, "inv", market=FakeMarket(data), now=when("2026-11-23", 17))
b = books.load_state(cfg, "inv")["books"]["B005"]
ok(b["start"]["date"] == "2026-11-06", "waits for a session where the option actually traded")
ok(sum(t["qty"] for t in b["trades"] if t["symbol"] == opt and t["why"] != "expired") == 666, "20% of $1M at $3.00 x 100 is 666 whole contracts")
exp = [t for t in b["trades"] if t["why"] == "expired"]
ok(exp and exp[0]["price"] == 30.0 and opt not in b["positions"], "after expiry it settles at intrinsic value: 130 - 100 = $30")
shutil.rmtree(tmp)

# ---- a symbol that never trades is dropped after 5 sessions
tmp, cfg = setup()
write(tmp, "B006", targets=[{"symbol": "ZZZZ", "kind": "etf", "weight": 0.5}])
books.accept(cfg, "inv", now=when("2026-11-04", 20))
days = ["2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"]
books.update(cfg, "inv", market=FakeMarket({"SPY": {d: bar(500) for d in days}}), now=when("2026-11-12", 17))
b = books.load_state(cfg, "inv")["books"]["B006"]
ok(b["status"] == "rejected" and any("no trades in ZZZZ" in e["text"] for e in b["events"]), "a never-traded symbol drops the book after 5 sessions")
shutil.rmtree(tmp)

# ---- a run during market hours ignores today's still-forming bar
tmp, cfg = setup()
write(tmp, "B007", kill=[{"if": "nav_below", "value": 0.95}])
books.accept(cfg, "inv", now=when("2026-11-05", 4, 50))
data = {"SPY": {"2026-11-05": bar(500, 500)}, "AAA": {"2026-11-05": bar(100, 50)}}   # a mid-day price, not a close
books.update(cfg, "inv", market=FakeMarket(data), now=when("2026-11-05", 12))
b = books.load_state(cfg, "inv")["books"]["B007"]
ok(b["status"] == "pending" and not b["history"], "mid-session: no fill and no kill check on a forming bar")
books.update(cfg, "inv", market=FakeMarket(data), now=when("2026-11-05", 17))
b = books.load_state(cfg, "inv")["books"]["B007"]
ok(b["start"]["date"] == "2026-11-05" and b["status"] == "closing", "after the close: fills at that open, then checks the close")
shutil.rmtree(tmp)

print(f"\n{'All passed' if not FAILS else str(len(FAILS)) + ' failed'}")
sys.exit(1 if FAILS else 0)

