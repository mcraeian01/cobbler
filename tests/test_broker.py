#!/usr/bin/env python3
"""Tests for bin/broker.py against a fake Alpaca. Run: python3 tests/test_broker.py"""
import sys, json, os, subprocess, shutil, tempfile
from pathlib import Path
from pathlib import Path as _P; sys.path.insert(0, str(_P(__file__).resolve().parent.parent / "bin"))
import cycle, broker
ok = lambda c, m: print(("PASS " if c else "FAIL ") + m) or (c or sys.exit(1))
b = {"probe_max_pct": 5, "position_max_pct": 25}
pos = {"NVDA": {"qty": "20", "market_value": "4000"}, "TSLA": {"qty": "-10", "market_value": "-2000"},
       "BTCUSD": {"qty": "0.1", "market_value": "6000"}}
px = {"NVDA": 200.0, "SPY": 500.0, "TSLA": 200.0, "AAPL": 100.0, "BTC/USD": 60000.0}.__getitem__
base = {"thesis": "T1", "reason": "open"}
c = lambda **o: broker.check_order({**base, **o}, 100000, pos, px, b)
ok(c(symbol="AAPL", side="buy", qty=50)[0] == "ok", "probe of $5,000 allowed")
ok("probe limit" in c(symbol="AAPL", side="buy", qty=51)[0], "probe over 5% rejected")
ok(c(symbol="NVDA", side="buy", qty=100, reason="add")[0] == "ok", "add to $24k allowed")
ok("over the" in c(symbol="NVDA", side="buy", qty=110, reason="add")[0], "add past 25% rejected")
ok(c(symbol="NVDA", side="sell", qty=20, reason="close-kill")[0] == "ok", "full close always allowed")
ok("probe" in c(symbol="NVDA", side="sell", qty=60, reason="trim")[0], "selling past position = new short, checked")
ok(c(symbol="TSLA", side="buy", qty=10, reason="close-target")[0] == "ok", "cover short allowed")
ok(c(symbol="SPY", side="sell", qty=10)[0] == "ok", "new short probe allowed")
ok("thesis" in c(symbol="AAPL", side="buy", qty=1, thesis="growth")[0], "missing thesis ID rejected")
ok("reason" in c(symbol="AAPL", side="buy", qty=1, reason="yolo")[0], "bad reason rejected")
ok("exactly one" in c(symbol="AAPL", side="buy", qty=1, notional=100)[0], "qty+notional rejected")
r, body = c(symbol="AAPL270115C00200000", side="buy", qty=2, type="limit", limit_price=12.5)
ok(r == "ok" and body["time_in_force"] == "day", "option limit order priced x100 ($2,500) allowed")
ok("probe" in c(symbol="AAPL270115C00200000", side="buy", qty=5, type="limit", limit_price=12.5)[0], "option $6,250 over probe")
ok("options need" in c(symbol="AAPL270115C00200000", side="buy", qty=1)[0], "option market order rejected")
r, body = c(symbol="BTC/USD", side="buy", notional=10000, reason="add")
ok(r == "ok" and body["time_in_force"] == "gtc", "crypto add uses gtc, maps to BTCUSD position")

# full execute() against a fake Alpaca
tmp = Path(tempfile.mkdtemp()); (tmp / "topics/investor").mkdir(parents=True)
cfg = {"root": tmp, "state": tmp / "state", "brokers": {"investor": {"enabled": True, **b}}}
cfg["state"].mkdir()
placed = []
class Fake:
    def __init__(s, *a): pass
    def account(s): return {"equity": "100000", "cash": "100000", "buying_power": "200000"}
    def positions(s): return []
    def orders(s, st, n=20): return []
    def latest_price(s, sym): return px(sym)
    def submit(s, body): placed.append(body); return {"status": "accepted"}
broker.Alpaca = Fake
os.environ.update(ALPACA_KEY_ID="k", ALPACA_SECRET_KEY="s")
of = tmp / "topics/investor/orders.json"
of.write_text(json.dumps({"orders": [
    {"id": "o1", "symbol": "AAPL", "side": "buy", "qty": 10, **base},
    {"id": "o2", "symbol": "AAPL", "side": "buy", "qty": 999, **base}]}))
lines = broker.execute(cfg, "investor")
ok(len(placed) == 1 and placed[0]["client_order_id"] == "investor-o1", "one placed, with idempotent client id")
ok("rejected" in lines[1], "oversized order rejected in digest line")
ok(json.loads(of.read_text()) == {"orders": []}, "orders.json cleared after execute")
of.write_text(json.dumps({"orders": [{"id": "o1", "symbol": "AAPL", "side": "buy", "qty": 10, **base}]}))
ok("already processed" in broker.execute(cfg, "investor")[0] and len(placed) == 1, "re-sent id not placed twice")
broker.snapshot(cfg, "investor")
md = (tmp / "topics/investor/BROKER.md").read_text()
ok("Account value | $100,000.00" in md and "o2" in md, "BROKER.md shows account and decisions")
cfg["brokers"]["investor"]["enabled"] = False
of.write_text(json.dumps({"orders": [{"id": "o3", "symbol": "AAPL", "side": "buy", "qty": 1, **base}]}))
ok("held" in broker.execute(cfg, "investor")[0] and json.loads(of.read_text())["orders"], "disabled: held and kept")
shutil.rmtree(tmp)
