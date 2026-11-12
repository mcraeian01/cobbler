#!/usr/bin/env python3
"""Live books: one Alpaca paper account per book, so the record is held by Alpaca, not by us.

Used by bin/books.py when [books.<slug>] has mode = "alpaca". The agent's spec format, the checks
and the pace are the same as for simulated books. The difference is where the book lives:

  - each new book is given its own paper account from the accounts file, never reused
  - a new or changed spec becomes a target quantity per symbol ("the plan"), and the runner places
    real orders on that account to reach it: market orders for stocks and ETFs, limit orders at the
    quote for options. Orders placed before the open fill at the open.
  - every cycle, positions and value are read back from Alpaca. Legs that haven't filled are re-sent
    (an option limit that expired, for example); a buy leg that fails 5 times is dropped.
  - kill rules are checked every cycle; a hit sets the plan to zero and the runner sells out.

Accounts file (private, outside every sandbox), one section per paper account:

  [awl]
  number = "PA00000000"
  key_id = "PK..."
  secret_key = "..."
"""
from __future__ import annotations

import datetime as dt
import json
import math
import tomllib
import urllib.parse
from pathlib import Path

import broker

ACCOUNTS_FILE = Path("~/.config/agent-runner/alpaca-accounts.toml").expanduser()
MAX_TRIES = 5
CLIENT = broker.Alpaca   # replaced in tests


def load_accounts(s: dict) -> dict[str, dict]:
    p = Path(s.get("accounts_file") or ACCOUNTS_FILE).expanduser()
    if not p.exists():
        return {}
    accts = tomllib.load(open(p, "rb"))
    # bin/accounts.py check records each account's status; one that failed it isn't used for books.
    side = p.with_suffix(".status.json")
    status = json.loads(side.read_text()) if side.exists() else {}
    return {k: v for k, v in accts.items()
            if isinstance(v, dict) and v.get("key_id") and v.get("secret_key") and v.get("use", True) is not False
            and (status.get(k) or {}).get("status") in (None, "ready")}


def free_account(st: dict, accts: dict) -> str | None:
    taken = {b.get("account") for b in st["books"].values()}
    free = [k for k in sorted(accts) if k not in taken]
    return free[0] if free else None


def api_for(accts: dict, name: str):
    a = accts[name]
    return CLIENT(a["key_id"], a["secret_key"])


def is_option(sym: str) -> bool:
    return bool(broker.OCC.match(sym))


def quote(api, sym: str) -> tuple[float, float]:
    """(bid, ask) for an option; for a stock or ETF, the last trade twice."""
    if not is_option(sym):
        p = api.latest_price(sym)
        return p, p
    q = urllib.parse.quote(sym)
    r = api._req("GET", f"{broker.DATA}/v1beta1/options/quotes/latest?symbols={q}&feed=indicative") or {}
    qt = (r.get("quotes") or {}).get(sym) or {}
    bid, ask = float(qt.get("bp") or 0), float(qt.get("ap") or 0)
    if not ask:
        c = api._req("GET", f"{broker.PAPER}/v2/options/contracts/{sym}") or {}
        ask = bid = float(c.get("close_price") or 0)
    return bid, ask


def make_plan(api, equity: float, targets: list[dict], held: dict[str, float]) -> dict[str, float]:
    """Target quantity per symbol: weight x equity at the current price. Whole contracts for options,
    whole shares for shorts (fractional shorts aren't allowed), 4 decimals for long stock."""
    plan = {sym: 0.0 for sym in held}
    for t in targets:
        sym, w = t["symbol"], t["weight"]
        bid, ask = quote(api, sym)
        price = ask if w > 0 else bid
        if price <= 0:
            plan[sym] = held.get(sym, 0.0)
            continue
        m = 100 if is_option(sym) else 1
        q = w * equity / (price * m)
        if is_option(sym) or q < 0:
            q = math.floor(q) if q > 0 else -math.floor(-q)
        else:
            q = math.floor(q * 1e4) / 1e4
        plan[sym] = q
    return plan


def qty_str(q: float) -> str:
    """Whole numbers without decimals (shorts must be whole shares); otherwise 4 decimals."""
    return str(int(round(q))) if abs(q - round(q)) < 1e-9 else f"{q:.4f}".rstrip("0")


def positions(api) -> dict[str, float]:
    return {p["symbol"]: float(p["qty"]) for p in api.positions()}


def reconcile(b: dict, api, now: dt.datetime, lines: list[str], bid: str, event) -> None:
    """Send orders for every leg that's short of its plan and has no order working."""
    held = positions(api)
    working = {o["symbol"] for o in api.orders("open", 200)}
    day = now.date().isoformat()
    for sym, want in sorted(b["plan"].items()):
        have = held.get(sym, 0.0)
        delta = want - have
        if abs(delta) < (0.5 if is_option(sym) else 1e-4) or sym in working:
            continue
        tries = b["tries"].get(sym, 0)
        growing = abs(want) > abs(have)
        if tries >= MAX_TRIES and growing:
            b["plan"][sym] = have
            event(b, day, f"{sym}: no fill after {MAX_TRIES} tries, so the rest of that leg was dropped; its weight stays in cash", lines, bid)
            continue
        side = "buy" if delta > 0 else "sell"
        body = {"symbol": sym, "side": side, "time_in_force": "day",
                "client_order_id": f"{bid}-{sym}-{now:%y%m%d%H%M}-{tries}"[:48]}
        if is_option(sym):
            bp, ap = quote(api, sym)
            px = ap * 1.03 if side == "buy" else bp * 0.97
            body.update(type="limit", qty=str(int(abs(round(delta)))), limit_price=f"{max(px, 0.01):.2f}")
        else:
            body.update(type="market", qty=qty_str(abs(delta)))
        b["tries"][sym] = tries + 1
        try:
            r = api.submit(body)
            b.setdefault("orders", []).append({"id": r.get("id"), "client_order_id": body["client_order_id"],
                                               "symbol": sym, "side": side, "qty": body["qty"], "at": now.isoformat()})
        except broker.BrokerError as e:
            event(b, day, f"{sym}: Alpaca refused the {side} ({e})", lines, bid)


def open_book(b: dict, accts: dict, now: dt.datetime, lines: list[str], bid: str, event) -> None:
    api = api_for(accts, b["account"])
    equity = float(api.account()["equity"])
    b["start_equity"] = equity
    b["plan"] = make_plan(api, equity, b["spec"]["targets"], positions(api))
    b["tries"] = {}
    reconcile(b, api, now, lines, bid, event)
    event(b, now.date().isoformat(), f"orders placed on {b['account']} ({accts[b['account']].get('number', '?')}); they fill at the next open", lines, bid)


def change_book(b: dict, accts: dict, now: dt.datetime, why: str, lines: list[str], bid: str, event) -> None:
    api = api_for(accts, b["account"])
    held = positions(api)
    if why == "close":
        b["plan"] = {sym: 0.0 for sym in held}
        b["status"] = "closing"
    else:
        b["plan"] = make_plan(api, float(api.account()["equity"]), b["spec"]["targets"], held)
    b["tries"] = {}
    reconcile(b, api, now, lines, bid, event)
    event(b, now.date().isoformat(), f"{why}: orders placed; they fill at the next open", lines, bid)


def broken_rule(b: dict, equity: float, bars: dict, last_session: str | None, today: str) -> str | None:
    for r in b["spec"]["kill"]:
        typ, v = r["if"], r["value"]
        if typ == "nav_below" and b.get("start_equity") and equity < v * b["start_equity"]:
            return f"value fell below {v:.0%} of start"
        if typ in ("close_below", "close_above") and last_session:
            c = bars.get(r["symbol"].upper(), {}).get(last_session, {}).get("c")
            if c is not None and ((typ == "close_below" and c < v) or (typ == "close_above" and c > v)):
                return f"{r['symbol'].upper()} closed at {c:,.2f}, {'below' if typ == 'close_below' else 'above'} {v:,.2f}"
        if typ == "after" and today > str(v):
            return f"past its end date {v}"
    return None


def update_book(b: dict, accts: dict, bars: dict, last_session: str | None, now: dt.datetime,
                lines: list[str], bid: str, event) -> None:
    """Read the account back from Alpaca, check kill rules, re-send unfilled legs."""
    if b["account"] not in accts:
        event(b, now.date().isoformat(), f"account {b['account']} is missing from the accounts file", lines, bid)
        return
    api = api_for(accts, b["account"])
    acct = api.account()
    equity = float(acct["equity"])
    pos = api.positions()
    b["positions"] = {p["symbol"]: float(p["qty"]) for p in pos}
    b["marks"] = {p["symbol"]: float(p["current_price"]) for p in pos}
    b["cash"] = float(acct["cash"])
    day = now.date().isoformat()
    if b["status"] == "pending" and b["positions"]:
        fills = [o for o in api.orders("closed", 200) if o.get("filled_at")]
        first = min((o["filled_at"] for o in fills), default=now.isoformat())[:10]
        spy = bars.get("SPY", {}).get(first, {}).get("o")
        b["start"] = {"date": first, "nav": b.get("start_equity") or equity, "spy": spy}
        b["status"] = "live"
        event(b, day, "opened: first fills recorded by Alpaca", lines, bid)
    if b["status"] == "live":
        rule = broken_rule(b, equity, bars, last_session, day)
        if rule:
            b["plan"] = {sym: 0.0 for sym in b["positions"]}
            b["tries"] = {}
            b["status"] = "closing"
            b["kill"] = rule
            event(b, day, f"kill rule hit ({rule}); selling out", lines, bid)
    if b["status"] == "closing" and not b["positions"] and not api.orders("open", 50):
        b["status"] = "killed" if b.get("kill") else "closed"
        b["end"] = {"date": day, "nav": round(equity, 2)}
        event(b, day, f"{b['status']}: all positions sold, value ${equity:,.0f}", lines, bid)
        return
    if b["status"] in ("pending", "live", "closing"):
        reconcile(b, api, now, lines, bid, event)
    if last_session and b.get("start") and (not b["history"] or b["history"][-1]["date"] != last_session):
        spy = bars.get("SPY", {}).get(last_session, {}).get("c")
        b["history"].append({"date": last_session, "nav": round(equity, 2), "spy": spy})
