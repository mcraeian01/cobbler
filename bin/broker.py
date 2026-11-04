#!/usr/bin/env python3
"""Broker step for topics that trade: an Alpaca *paper* account, driven by plain code.

The topic agent never sees the keys. It reads BROKER.md (written here) and writes
orders.json. The runner calls this module outside the sandbox:

  snapshot()  before the agent runs: account, positions, fills, decisions -> BROKER.md
  execute()   after the agent runs: check each order in orders.json against the limits
              in config.toml, place the ones that pass, record every decision

Keys come from the env file (ALPACA_KEY_ID / ALPACA_SECRET_KEY by default). Only the paper
endpoint is ever called; there is no setting that points this at a live account.

By hand:
  broker.py SLUG --snapshot     refresh BROKER.md
  broker.py SLUG --check        validate orders.json and print decisions, place nothing
  broker.py SLUG --execute      validate and place, then refresh BROKER.md
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PAPER = "https://paper-api.alpaca.markets"   # hard-wired: paper only
DATA = "https://data.alpaca.markets"
REASONS = {"open", "add", "trim", "close-kill", "close-target", "reallocate"}
OCC = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")   # option symbol, e.g. AAPL270115C00200000


class BrokerError(Exception):
    pass


class Alpaca:
    def __init__(self, key: str, secret: str):
        self.h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
                  "Accept": "application/json", "Content-Type": "application/json"}

    def _req(self, method: str, url: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self.h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("message", "")
            except Exception:
                msg = ""
            raise BrokerError(f"Alpaca {e.code}: {msg or e.reason}") from None
        except urllib.error.URLError as e:
            raise BrokerError(f"Alpaca unreachable: {e.reason}") from None

    def account(self):
        return self._req("GET", f"{PAPER}/v2/account")

    def positions(self):
        return self._req("GET", f"{PAPER}/v2/positions")

    def orders(self, status: str, limit: int = 20):
        return self._req("GET", f"{PAPER}/v2/orders?status={status}&limit={limit}&direction=desc")

    def submit(self, body: dict):
        return self._req("POST", f"{PAPER}/v2/orders", body)

    def cancel(self, order_id: str):
        return self._req("DELETE", f"{PAPER}/v2/orders/{order_id}")

    def latest_price(self, symbol: str) -> float:
        if asset_class(symbol) == "crypto":
            q = urllib.parse.quote(symbol, safe="")
            r = self._req("GET", f"{DATA}/v1beta3/crypto/us/latest/trades?symbols={q}")
            return float(r["trades"][symbol]["p"])
        r = self._req("GET", f"{DATA}/v2/stocks/{urllib.parse.quote(symbol)}/trades/latest?feed=iex")
        return float(r["trade"]["p"])


def asset_class(symbol: str) -> str:
    if "/" in symbol:
        return "crypto"
    if OCC.match(symbol):
        return "option"
    return "equity"


def pos_key(symbol: str) -> str:
    return symbol.replace("/", "")      # Alpaca lists crypto positions as BTCUSD


# ---------- config and state ----------

def broker_cfg(cfg: dict, slug: str) -> dict | None:
    b = cfg.get("brokers", {}).get(slug)
    if not b:
        return None
    if b.get("provider", "alpaca-paper") != "alpaca-paper":
        raise BrokerError(f"unsupported provider {b.get('provider')!r}")
    return b


def client(b: dict) -> Alpaca:
    key = os.environ.get(b.get("key_id_env", "ALPACA_KEY_ID"))
    secret = os.environ.get(b.get("secret_env", "ALPACA_SECRET_KEY"))
    if not key or not secret:
        raise BrokerError("Alpaca keys missing from the env file")
    return Alpaca(key, secret)


def state_path(cfg: dict, slug: str) -> Path:
    return cfg["state"] / f"broker-{slug}.json"


def load_state(cfg: dict, slug: str) -> dict:
    p = state_path(cfg, slug)
    s = json.loads(p.read_text()) if p.exists() else {}
    s.setdefault("start", None)
    s.setdefault("history", [])
    s.setdefault("processed", [])
    s.setdefault("decisions", [])
    return s


def save_state(cfg: dict, slug: str, s: dict) -> None:
    s["processed"] = s["processed"][-500:]
    s["decisions"] = s["decisions"][-200:]
    state_path(cfg, slug).write_text(json.dumps(s, indent=1))


def stamp() -> str:
    return dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


def local(ts: str) -> str:
    """Alpaca's UTC timestamps (nanoseconds, trailing Z) as local 'YYYY-MM-DD HH:MM'."""
    try:
        return dt.datetime.fromisoformat(ts[:19] + "+00:00").astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(ts)[:16].replace("T", " ")


# ---------- checking orders ----------

def check_order(o: dict, equity: float, positions: dict, price_of, b: dict) -> tuple[str, dict | None]:
    """Return (reason it was rejected, or 'ok'), and the Alpaca order body if ok."""
    probe_max = equity * b.get("probe_max_pct", 5) / 100
    pos_max = equity * b.get("position_max_pct", 25) / 100

    sym = str(o.get("symbol", "")).upper().strip()
    side = o.get("side")
    qty, notional = o.get("qty"), o.get("notional")
    otype = o.get("type", "market")
    limit = o.get("limit_price")
    if not sym:
        return "no symbol", None
    if side not in ("buy", "sell"):
        return "side must be buy or sell", None
    if not re.fullmatch(r"T\d+", str(o.get("thesis", ""))):
        return "thesis must be a thesis ID like T3", None
    if o.get("reason") not in REASONS:
        return f"reason must be one of {', '.join(sorted(REASONS))}", None
    if (qty is None) == (notional is None):
        return "give exactly one of qty or notional", None
    if otype not in ("market", "limit"):
        return "type must be market or limit", None
    if otype == "limit" and not limit:
        return "limit order needs limit_price", None

    cls = asset_class(sym)
    mult = 100 if cls == "option" else 1
    if cls == "option" and (notional is not None or otype != "limit"):
        return "options need qty and a limit order", None
    try:
        px = float(limit) if limit else price_of(sym)
    except Exception as e:
        return f"couldn't price {sym}: {e}", None
    size = float(notional) if notional is not None else float(qty) * px * mult
    if size <= 0:
        return "size must be positive", None

    held = positions.get(pos_key(sym))
    held_qty = float(held["qty"]) if held else 0.0          # negative when short
    held_val = abs(float(held["market_value"])) if held else 0.0
    order_qty = float(qty) if qty is not None else size / px
    after = held_qty + (order_qty if side == "buy" else -order_qty)
    flips = held_qty != 0 and after != 0 and (after > 0) != (held_qty > 0)
    reducing = held_qty != 0 and not flips and abs(after) <= abs(held_qty) + 1e-9

    if not reducing:
        if held_qty == 0 or flips:   # a new position, or a long turned short (or back)
            new_val = abs(after) * px * mult
            if new_val > probe_max:
                return f"new position ${new_val:,.0f} is over the probe limit ${probe_max:,.0f}", None
        elif held_val + size > pos_max:
            return (f"position would be ${held_val + size:,.0f}, over the "
                    f"${pos_max:,.0f} limit"), None

    body = {"symbol": sym, "side": side, "type": otype,
            "time_in_force": o.get("time_in_force") or ("gtc" if cls == "crypto" else "day")}
    if qty is not None:
        body["qty"] = str(qty)
    else:
        body["notional"] = str(round(float(notional), 2))
    if limit:
        body["limit_price"] = str(limit)
    return "ok", body


def execute(cfg: dict, slug: str, place: bool = True) -> list[str]:
    """Check and place the topic's orders.json. Returns one line per order for the digest."""
    b = broker_cfg(cfg, slug)
    if not b:
        return []
    path = cfg["root"] / "topics" / slug / "orders.json"
    if not path.exists():
        return []
    try:
        orders = json.loads(path.read_text() or "{}").get("orders", [])
    except (json.JSONDecodeError, AttributeError) as e:
        return [f"orders.json unreadable ({e}); nothing placed"]
    if not orders:
        return []

    s = load_state(cfg, slug)
    api = client(b)
    acct = api.account()
    equity = float(acct["equity"])
    positions = {p["symbol"]: p for p in api.positions()}
    cache: dict[str, float] = {}

    def price_of(sym: str) -> float:
        if sym not in cache:
            cache[sym] = api.latest_price(sym)
        return cache[sym]

    lines = []
    for o in orders:
        oid = str(o.get("id", "")).strip()
        label = f"{oid or '?'} {o.get('side', '?')} {o.get('qty') or ('$' + str(o.get('notional')))} {o.get('symbol', '?')}"
        if not oid:
            verdict = "rejected: no id"
        elif oid in s["processed"]:
            verdict = "skipped: id already processed"
        elif not b.get("enabled", True):
            verdict = "held: trading disabled in config.toml"
        else:
            why, body = check_order(o, equity, positions, price_of, b)
            if why != "ok":
                verdict = f"rejected: {why}"
            elif not place:
                verdict = "would place"
            else:
                body["client_order_id"] = f"{slug}-{oid}"[:48]
                try:
                    r = api.submit(body)
                    verdict = f"placed ({r.get('status')})"
                except BrokerError as e:
                    verdict = f"rejected by Alpaca: {e}"
        if place and oid and not verdict.startswith(("skipped", "held")):
            s["processed"].append(oid)
        s["decisions"].append({"ts": stamp(), "order": o, "verdict": verdict})
        lines.append(f"{label}: {verdict}")

    if place:
        save_state(cfg, slug, s)
        held = [o for o in orders if o.get("id") and not b.get("enabled", True)]
        path.write_text(json.dumps({"orders": held}, indent=1) + "\n")
    return lines


# ---------- BROKER.md ----------

def snapshot(cfg: dict, slug: str) -> None:
    b = broker_cfg(cfg, slug)
    if not b:
        return
    api = client(b)
    acct = api.account()
    positions = api.positions()
    open_orders = api.orders("open", 50)
    fills = [o for o in api.orders("closed", 50) if o.get("filled_at")][:20]
    equity = float(acct["equity"])
    try:
        spy = api.latest_price("SPY")
    except BrokerError:
        spy = None

    s = load_state(cfg, slug)
    today = dt.date.today().isoformat()
    if not s["start"]:
        s["start"] = {"date": today, "equity": equity, "spy": spy}
    if fills and not s["start"].get("first_fill"):
        s["start"]["first_fill"] = min(local(o["filled_at"])[:10] for o in fills)
    if not s["history"] or s["history"][-1]["date"] != today:
        s["history"].append({"date": today, "equity": equity, "spy": spy})
    else:
        s["history"][-1].update(equity=equity, spy=spy)
    save_state(cfg, slug, s)

    st = s["start"]

    def pct(a, b0):
        return f"{(a / b0 - 1) * 100:+.2f}%" if a and b0 else "-"

    L = ["# Broker account", "",
         "Written by the runner from the Alpaca paper account before each of your runs. "
         "Read-only: your edits are overwritten. Place orders through `orders.json`.", "",
         f"As of {stamp()}.", "",
         "| | |", "|---|---|",
         f"| Account value | ${equity:,.2f} |",
         f"| Cash | ${float(acct['cash']):,.2f} |",
         f"| Buying power | ${float(acct['buying_power']):,.2f} |",
         f"| Since tracking began ({st['date']}) | {pct(equity, st['equity'])} |",
         f"| SPY over the same period (price only) | {pct(spy, st.get('spy'))} |",
         f"| First fill | {st.get('first_fill') or 'none yet'} |",
         f"| Limits | new position ≤ {b.get('probe_max_pct', 5)}% of value, "
         f"any position ≤ {b.get('position_max_pct', 25)}% after an add |",
         f"| Trading | {'enabled' if b.get('enabled', True) else 'DISABLED in config.toml'} |", "",
         "## Positions", ""]
    if positions:
        L += ["| Symbol | Qty | Avg cost | Price | Value | P/L | % of account |",
              "|---|---|---|---|---|---|---|"]
        for p in sorted(positions, key=lambda p: -abs(float(p["market_value"]))):
            mv = float(p["market_value"])
            L.append(f"| {p['symbol']} | {p['qty']} | {float(p['avg_entry_price']):,.2f} | "
                     f"{float(p['current_price']):,.2f} | {mv:,.2f} | "
                     f"{float(p['unrealized_plpc']) * 100:+.1f}% | {abs(mv) / equity * 100:.1f}% |")
    else:
        L.append("None.")
    L += ["", "## Open orders", ""]
    L += [f"- {local(o['submitted_at'])} {o['side']} {o.get('qty') or '$' + str(o.get('notional'))} "
          f"{o['symbol']} {o['type']}{' @ ' + o['limit_price'] if o.get('limit_price') else ''} "
          f"({o['status']}, id {o.get('client_order_id')})" for o in open_orders] or ["None."]
    L += ["", "## Recent fills", ""]
    L += [f"- {local(o['filled_at'])} {o['side']} {o['filled_qty']} {o['symbol']} "
          f"@ {float(o['filled_avg_price']):,.2f} (id {o.get('client_order_id')})" for o in fills] or ["None."]
    L += ["", "## What the executor did with your orders (newest first)", ""]
    L += [f"- {d['ts']} · {d['order'].get('id')} {d['order'].get('side')} {d['order'].get('symbol')} "
          f"({d['order'].get('thesis')}, {d['order'].get('reason')}): {d['verdict']}"
          for d in reversed(s["decisions"][-20:])] or ["None yet."]
    L += ["", "## Value history", "", "| Date | Account | SPY |", "|---|---|---|"]
    L += [f"| {h['date']} | {h['equity']:,.2f} | {h['spy'] if h['spy'] is not None else '-'} |"
          for h in s["history"][-60:]]
    (cfg["root"] / "topics" / slug / "BROKER.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    import argparse
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cycle

    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--snapshot", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    cfg = cycle.load_config()
    try:
        if a.snapshot:
            snapshot(cfg, a.slug)
            print("BROKER.md written")
        else:
            for line in execute(cfg, a.slug, place=a.execute) or ["no orders"]:
                print(line)
            if a.execute:
                snapshot(cfg, a.slug)
    except BrokerError as e:
        sys.exit(f"broker: {e}")
