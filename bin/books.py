#!/usr/bin/env python3
"""Books: many simulated portfolios, one per thesis, priced from real market data.

A trading topic's agent writes one small spec per book in `books/<ID>.json`: its thesis,
target weights, and kill rules. This module, plain code outside the sandbox, does the rest:

  accept()  after the agent's run: check each new or changed spec against config.toml limits
            and queue the change, timestamped
  update()  every cycle: fill queued changes at the open of the first trading session after
            they were written, check kill rules on each session's close (a hit sells the book at
            the next open), settle expired options, record each book's value, write BOOKS.md

Prices come from Alpaca's market data (the IEX feed for stocks and ETFs, the indicative feed for
options), read with the same paper keys as bin/broker.py. No orders are placed anywhere, so any
number of books can run, each starting at the same size.

Not modelled: dividends, splits, borrow fees, margin interest, early option exercise.

By hand:
  books.py SLUG --update        fill, check, refresh BOOKS.md
  books.py SLUG --accept        check books/*.json now, as the runner does after a run
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import sys
import urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import broker  # noqa: E402  (Alpaca client and errors)

ET = ZoneInfo("America/New_York")
OPEN, CLOSE = dt.time(9, 30), dt.time(16, 0)
KINDS = {"stock", "etf", "option"}
RULES = {"nav_below", "close_below", "close_above", "after"}
OCC = re.compile(r"^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")
LIVE = ("pending", "live", "closing")
DEFAULTS = dict(start_cash=1_000_000, max_books=20, max_new_per_week=3, max_single_stock=0.20,
                max_option_each=0.20, max_options_total=0.50, max_long=1.0, max_short=0.50,
                slippage_bps=5)


def settings(cfg: dict, slug: str) -> dict | None:
    b = cfg.get("books", {}).get(slug)
    return None if b is None else {**DEFAULTS, **b}


def mult(sym: str) -> int:
    return 100 if OCC.match(sym) else 1


def occ(sym: str):
    """(underlying, expiry date, 'C'|'P', strike) for an OCC option symbol, else None."""
    m = OCC.match(sym)
    if not m:
        return None
    root, yy, mm, dd, cp, k = m.groups()
    return root, dt.date(2000 + int(yy), int(mm), int(dd)), cp, int(k) / 1000


def at(day: str, t: dt.time) -> dt.datetime:
    return dt.datetime.combine(dt.date.fromisoformat(day), t, ET)


def now_et() -> dt.datetime:
    return dt.datetime.now(ET)


# ---------- market data ----------

class Market:
    """Daily bars from Alpaca market data: {symbol: {'YYYY-MM-DD': {'o': open, 'c': close}}}."""

    def __init__(self, api: broker.Alpaca):
        self.api = api

    def bars(self, symbols: list[str], start: dt.date) -> dict[str, dict[str, dict]]:
        out: dict[str, dict[str, dict]] = {s: {} for s in symbols}
        groups = (("/v2/stocks/bars", [s for s in symbols if not OCC.match(s)], "&feed=iex&adjustment=raw"),
                  ("/v1beta1/options/bars", [s for s in symbols if OCC.match(s)], ""))
        for path, group, extra in groups:
            for i in range(0, len(group), 50):
                chunk, token = group[i:i + 50], None
                while True:
                    url = (f"{broker.DATA}{path}?symbols={urllib.parse.quote(','.join(chunk))}"
                           f"&timeframe=1Day&start={start.isoformat()}&limit=10000{extra}")
                    if token:
                        url += f"&page_token={urllib.parse.quote(token)}"
                    r = self.api._req("GET", url) or {}
                    for sym, bars in (r.get("bars") or {}).items():
                        for b in bars:
                            day = dt.datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET).date()
                            out.setdefault(sym, {})[day.isoformat()] = {"o": float(b["o"]), "c": float(b["c"])}
                    token = r.get("next_page_token")
                    if not token:
                        break
        return out


def last_close(bars: dict, sym: str, day: str) -> float | None:
    days = [d for d in bars.get(sym, {}) if d <= day]
    return bars[sym][max(days)]["c"] if days else None


# ---------- state ----------

def topic_dir(cfg: dict, slug: str) -> Path:
    return cfg["root"] / "topics" / slug


def state_path(cfg: dict, slug: str) -> Path:
    return cfg["state"] / f"books-{slug}.json"


def load_state(cfg: dict, slug: str) -> dict:
    p = state_path(cfg, slug)
    st = json.loads(p.read_text()) if p.exists() else {}
    st.setdefault("books", {})
    st.setdefault("created", [])
    st.setdefault("verdicts", {})
    st.setdefault("checked", None)
    return st


def save_state(cfg: dict, slug: str, st: dict) -> None:
    for b in st["books"].values():
        b["trades"] = b["trades"][-300:]
        b["events"] = b["events"][-50:]
    state_path(cfg, slug).write_text(json.dumps(st, indent=1))


def event(b: dict, day: str, text: str, lines: list[str], bid: str) -> None:
    b["events"].append({"date": day, "text": text})
    lines.append(f"{bid}: {text}")


# ---------- checking specs ----------

def normalize(spec: dict) -> dict:
    return {"thesis": spec.get("thesis"), "status": spec.get("status", "active"),
            "targets": [{"symbol": str(t.get("symbol", "")).upper().strip(), "kind": t.get("kind"),
                         "weight": t.get("weight")} for t in (spec.get("targets") or [])],
            "kill": spec.get("kill") or []}


def spec_hash(spec: dict) -> str:
    n = normalize(spec)
    return hashlib.sha256(json.dumps([n["status"], n["targets"], n["kill"]], sort_keys=True).encode()).hexdigest()[:16]


def check_spec(spec, stem: str, s: dict) -> str | None:
    """Why a spec is invalid, or None."""
    if not isinstance(spec, dict):
        return "a spec must be a JSON object"
    if not re.fullmatch(r"B\d{3,}", stem) or spec.get("id") != stem:
        return f"id must match the file name and look like B001 (file {stem}.json)"
    if not re.fullmatch(r"T\d+", str(spec.get("thesis", ""))):
        return "thesis must be a thesis ID like T3"
    status = spec.get("status", "active")
    if status not in ("active", "close"):
        return "status must be active or close"
    targets = spec.get("targets") or []
    if not isinstance(targets, list) or (status == "active" and not targets):
        return "an active book needs a list of targets"
    long = short = opts = 0.0
    seen = set()
    for t in targets:
        if not isinstance(t, dict):
            return "each target must be an object with symbol, kind and weight"
        sym, kind, w = str(t.get("symbol", "")).upper().strip(), t.get("kind"), t.get("weight")
        if not sym or sym in seen:
            return f"missing or repeated symbol {sym!r}"
        seen.add(sym)
        if "/" in sym:
            return f"{sym}: crypto isn't supported in books yet"
        if kind not in KINDS:
            return f"{sym}: kind must be stock, etf or option"
        if (kind == "option") != bool(OCC.match(sym)):
            return f"{sym}: options need an OCC symbol like SPY271217C00700000, and OCC symbols need kind option"
        if not isinstance(w, (int, float)) or isinstance(w, bool) or w == 0:
            return f"{sym}: weight must be a non-zero number (a fraction of the book, e.g. 0.15)"
        if kind == "option":
            if w < 0:
                return f"{sym}: options can only be bought, not written"
            if w > s["max_option_each"] + 1e-9:
                return f"{sym}: one option can be at most {s['max_option_each']:.0%} of a book"
            opts += w
        if kind == "stock" and abs(w) > s["max_single_stock"] + 1e-9:
            return f"{sym}: one company can be at most {s['max_single_stock']:.0%} of a book (use more names or an ETF)"
        if w > 0:
            long += w
        else:
            short -= w
    if long > s["max_long"] + 1e-9:
        return f"long weights add to {long:.0%}; the limit is {s['max_long']:.0%} (no margin)"
    if short > s["max_short"] + 1e-9:
        return f"short weights add to {short:.0%}; the limit is {s['max_short']:.0%}"
    if opts > s["max_options_total"] + 1e-9:
        return f"options add to {opts:.0%}; the limit is {s['max_options_total']:.0%}"
    kill = spec.get("kill")
    if not isinstance(kill, list) or not kill:
        return "every book needs at least one kill rule"
    for r in kill:
        typ = r.get("if") if isinstance(r, dict) else None
        if typ not in RULES:
            return f"each kill rule needs \"if\": one of {', '.join(sorted(RULES))}"
        v = r.get("value")
        if typ == "after":
            try:
                dt.date.fromisoformat(str(v))
            except ValueError:
                return "an after rule needs a date like 2027-06-30"
        elif not isinstance(v, (int, float)) or isinstance(v, bool):
            return f"{typ} needs a numeric value"
        if typ in ("close_below", "close_above") and not r.get("symbol"):
            return f"{typ} needs a symbol"
        if typ == "nav_below" and not 0 < v < 1:
            return "nav_below is a fraction of the starting value, e.g. 0.7"
    return None


def accept(cfg: dict, slug: str, now: dt.datetime | None = None) -> list[str]:
    """Read books/*.json, queue valid new or changed books. Returns lines for the digest."""
    s = settings(cfg, slug)
    if not s:
        return []
    now = now or now_et()
    st = load_state(cfg, slug)
    lines: list[str] = []
    d = topic_dir(cfg, slug) / "books"
    files = sorted(d.glob("*.json")) if d.exists() else []
    verdicts = {}
    seen_ids = set()
    for p in files:
        stem = p.stem
        seen_ids.add(stem)
        try:
            spec = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            verdicts[stem] = f"rejected: not valid JSON ({e.msg}, line {e.lineno})"
            continue
        err = check_spec(spec, stem, s)
        b = st["books"].get(stem)
        if err:
            verdicts[stem] = f"rejected: {err}"
            if not b or b.get("last_rejection") != err:
                lines.append(f"{stem}: rejected: {err}")
            if b:
                b["last_rejection"] = err
            continue
        h = spec_hash(spec)
        if b and b.get("spec_hash") == h:
            verdicts[stem] = "unchanged"
            continue
        n = normalize(spec)
        day = now.date().isoformat()
        if b and b["status"] not in LIVE:
            verdicts[stem] = f"ignored: this book is {b['status']}; start a new book with a new id"
            continue
        if not b:
            if n["status"] == "close":
                verdicts[stem] = "ignored: a new book can't start closed"
                continue
            active = sum(1 for x in st["books"].values() if x["status"] in LIVE)
            recent = [c for c in st["created"] if now - dt.datetime.fromisoformat(c) < dt.timedelta(days=7)]
            if active >= s["max_books"]:
                verdicts[stem] = f"held: {active} books are open, the limit is {s['max_books']}; close one first"
                continue
            if len(recent) >= s["max_new_per_week"]:
                verdicts[stem] = (f"held: {len(recent)} new books in the last 7 days, the limit is "
                                  f"{s['max_new_per_week']}; it will open once the window allows")
                continue
            b = st["books"][stem] = {"thesis": n["thesis"], "status": "pending", "cash": float(s["start_cash"]),
                                     "positions": {}, "start": None, "trades": [], "history": [], "events": [],
                                     "pending": None, "last_eval": None, "created": now.isoformat()}
            st["created"].append(now.isoformat())
            why = "open"
        else:
            why = "close" if n["status"] == "close" else "rebalance"
        b.update(spec=n, spec_hash=h, thesis=n["thesis"])
        b.pop("last_rejection", None)
        if why == "close" and b["status"] == "pending" and not b["positions"]:
            b["status"], b["pending"] = "closed", None
            event(b, day, "closed before it opened", lines, stem)
            verdicts[stem] = "closed before it opened"
            continue
        b["pending"] = {"targets": [] if why == "close" else n["targets"], "decided_at": now.isoformat(), "why": why}
        if why == "close":
            b["status"] = "closing"
        verdicts[stem] = f"accepted ({why}): fills at the next market open"
        event(b, day, f"{why} queued for the next open", lines, stem)
    for bid, b in st["books"].items():
        if b["status"] in LIVE and bid not in seen_ids:
            verdicts[bid] = "spec file missing: the book is left as it is"
    st["verdicts"], st["checked"] = verdicts, now.isoformat()
    save_state(cfg, slug, st)
    write_md(cfg, slug, st, s)
    return lines


# ---------- filling, kill rules, expiry ----------

def nav_at(b: dict, day: str, bars: dict) -> float:
    total = b["cash"]
    for sym, q in b["positions"].items():
        p = last_close(bars, sym, day)
        total += q * (p or 0.0) * mult(sym)
    return total


def fill(b: dict, day: str, bars: dict, s: dict, lines: list[str], bid: str) -> bool:
    """Trade the book to its pending targets at this session's open. False if a price is missing."""
    pend = b["pending"]
    targets = {t["symbol"]: t["weight"] for t in pend["targets"]}
    syms = set(targets) | {k for k, q in b["positions"].items() if q}
    px = {}
    missing = []
    for sym in syms:
        bar = bars.get(sym, {}).get(day)
        (missing.append(sym) if not bar else px.__setitem__(sym, bar["o"]))
    if missing:
        pend["waits"] = pend.get("waits", 0) + 1
        if pend["waits"] >= 5:
            event(b, day, f"{pend['why']} dropped: no trades in {', '.join(sorted(missing))} for 5 sessions", lines, bid)
            b["pending"] = None
            if b["status"] == "pending":
                b["status"] = "rejected"
            elif b["status"] == "closing":
                b["status"] = "live"
        return False
    nav = b["cash"] + sum(q * px[k] * mult(k) for k, q in b["positions"].items())
    slip = s["slippage_bps"] / 1e4
    for sym in sorted(syms):
        m = mult(sym)
        want = targets.get(sym, 0.0) * nav / (px[sym] * m)
        want = math.floor(want) if m == 100 else round(want, 4)
        dq = want - b["positions"].get(sym, 0.0)
        if abs(dq) < 1e-9:
            continue
        price = px[sym] * (1 + slip if dq > 0 else 1 - slip)
        b["cash"] -= dq * price * m
        b["trades"].append({"date": day, "symbol": sym, "qty": round(dq, 4), "price": round(price, 4), "why": pend["why"]})
        if abs(want) < 1e-9:
            b["positions"].pop(sym, None)
        else:
            b["positions"][sym] = want
        if m == 100 and want == 0 and targets.get(sym, 0) > 0:
            event(b, day, f"{sym}: target too small for one contract, so none bought", lines, bid)
    if not b["start"]:
        b["start"] = {"date": day, "nav": round(nav, 2), "spy": bars["SPY"][day]["o"]}
    why = pend["why"]
    b["pending"] = None
    if why == "close" or why.startswith("kill"):
        b["status"] = "closed" if why == "close" else "killed"
        b["end"] = {"date": day, "nav": round(b["cash"], 2)}
        event(b, day, f"{'closed' if why == 'close' else 'sold out (' + why + ')'} at the open, "
                      f"value ${b['cash']:,.0f}", lines, bid)
    else:
        b["status"] = "live"
        event(b, day, f"{why} filled at the open", lines, bid)
    return True


def settle_expired(b: dict, day: str, bars: dict, lines: list[str], bid: str) -> None:
    for sym in [k for k in b["positions"] if OCC.match(k)]:
        root, expiry, cp, strike = occ(sym)
        if dt.date.fromisoformat(day) <= expiry:
            continue
        s = last_close(bars, root, expiry.isoformat())
        if s is None:
            continue
        value = max(0.0, s - strike) if cp == "C" else max(0.0, strike - s)
        q = b["positions"].pop(sym)
        b["cash"] += q * value * 100
        b["trades"].append({"date": day, "symbol": sym, "qty": -q, "price": round(value, 4), "why": "expired"})
        event(b, day, f"{sym} expired worth ${value:,.2f} a share ({root} closed at {s:,.2f})", lines, bid)


def broken_rule(b: dict, day: str, bars: dict, nav: float) -> str | None:
    for r in b["spec"]["kill"]:
        typ, v = r["if"], r["value"]
        if typ == "nav_below" and nav < v * b["start"]["nav"]:
            return f"value fell below {v:.0%} of start"
        if typ in ("close_below", "close_above"):
            c = bars.get(r["symbol"].upper(), {}).get(day, {}).get("c")
            if c is not None and ((typ == "close_below" and c < v) or (typ == "close_above" and c > v)):
                return f"{r['symbol'].upper()} closed at {c:,.2f}, {'below' if typ == 'close_below' else 'above'} {v:,.2f}"
        if typ == "after" and day > str(v):
            return f"past its end date {v}"
    return None


def update(cfg: dict, slug: str, market: Market | None = None, now: dt.datetime | None = None) -> list[str]:
    """Bring every open book up to date with the market. Returns lines for the digest."""
    s = settings(cfg, slug)
    if not s:
        return []
    st = load_state(cfg, slug)
    lines: list[str] = []
    open_books = {k: b for k, b in st["books"].items() if b["status"] in LIVE}
    if open_books:
        now = now or now_et()
        today = now.date()
        syms = {"SPY"}
        starts = []
        for b in open_books.values():
            syms |= set(b["positions"])
            if b["pending"]:
                syms |= {t["symbol"] for t in b["pending"]["targets"]}
                starts.append(dt.datetime.fromisoformat(b["pending"]["decided_at"]).date())
            syms |= {r["symbol"].upper() for r in b["spec"]["kill"] if r.get("symbol")}
            if b["last_eval"]:
                starts.append(dt.date.fromisoformat(b["last_eval"]))
        syms |= {occ(x)[0] for x in list(syms) if OCC.match(x)}
        start = min(starts or [today]) - dt.timedelta(days=7)
        market = market or Market(broker.client(s))
        bars = market.bars(sorted(syms), start)
        # Only finished sessions: a bar for today is still forming until well after the close.
        sessions = [d for d in sorted(bars.get("SPY", {})) if at(d, CLOSE) + dt.timedelta(minutes=20) <= now]
        for bid, b in open_books.items():
            for day in sessions:
                if b["last_eval"] and day <= b["last_eval"]:
                    continue
                if b["pending"] and at(day, OPEN) > dt.datetime.fromisoformat(b["pending"]["decided_at"]):
                    fill(b, day, bars, s, lines, bid)
                if b["status"] not in ("live", "closing"):
                    b["last_eval"] = day
                    if b["status"] != "pending":
                        break
                    continue
                settle_expired(b, day, bars, lines, bid)
                nav = nav_at(b, day, bars)
                b["marks"] = {k: last_close(bars, k, day) for k in b["positions"]}
                b["history"].append({"date": day, "nav": round(nav, 2), "spy": bars["SPY"][day]["c"]})
                if b["status"] == "live":
                    rule = broken_rule(b, day, bars, nav)
                    if rule:
                        b["pending"] = {"targets": [], "decided_at": at(day, CLOSE).isoformat(), "why": f"kill: {rule}"}
                        b["status"] = "closing"
                        event(b, day, f"kill rule hit ({rule}); sells at the next open", lines, bid)
                b["last_eval"] = day
    save_state(cfg, slug, st)
    write_md(cfg, slug, st, s)
    return lines


# ---------- BOOKS.md ----------

def write_md(cfg: dict, slug: str, st: dict, s: dict) -> None:
    def pct(a, b0):
        return f"{(a / b0 - 1) * 100:+.1f}%" if a and b0 else "-"

    rows, detail = [], []
    for bid in sorted(st["books"]):
        b = st["books"][bid]
        last = b["history"][-1] if b["history"] else None
        nav = (b.get("end") or {}).get("nav") or (last or {}).get("nav")
        spy_end = (last or {}).get("spy")
        st0 = b["start"] or {}
        ret = pct(nav, st0.get("nav"))
        spy = pct(spy_end, st0.get("spy"))
        vs = (f"{((nav / st0['nav']) - (spy_end / st0['spy'])) * 100:+.1f} pts"
              if nav and spy_end and st0.get("nav") else "-")
        rows.append(f"| {bid} | {b['thesis']} | {b['status']} | {st0.get('date', '-')} | "
                    f"{'$' + format(nav, ',.0f') if nav else '-'} | {ret} | {spy} | {vs} |")
        d = [f"## {bid} · {b['thesis']} · {b['status']}", ""]
        if b.get("spec"):
            d.append("Targets: " + ", ".join(f"{t['symbol']} {t['weight']:+.0%} ({t['kind']})" for t in b["spec"]["targets"]) or "none")
            d.append("Kill: " + "; ".join(f"{r['if']} {r.get('symbol', '')} {r['value']}".replace("  ", " ") for r in b["spec"]["kill"]))
        if b["pending"]:
            d.append(f"Queued: {b['pending']['why']}, written {b['pending']['decided_at'][:16].replace('T', ' ')}, fills at the next open")
        if b["positions"] and last:
            d += ["", "| Symbol | Qty | Last close | Value | Weight |", "|---|---|---|---|---|"]
            for sym, q in sorted(b["positions"].items()):
                c = b.get("marks", {}).get(sym)
                v = q * (c or 0) * mult(sym)
                d.append(f"| {sym} | {q:,.4g} | {c if c is not None else '-'} | {v:,.0f} | {v / last['nav'] * 100:.1f}% |")
            d.append(f"| cash | | | {b['cash']:,.0f} | {b['cash'] / last['nav'] * 100:.1f}% |")
        if b["events"]:
            d += ["", "Events:"] + [f"- {e['date']}: {e['text']}" for e in b["events"][-6:]]
        detail += d + [""]

    active = sum(1 for b in st["books"].values() if b["status"] in LIVE)
    now = now_et()
    recent = [c for c in st["created"] if now - dt.datetime.fromisoformat(c) < dt.timedelta(days=7)]
    L = ["# Books", "",
         "Written by the runner from real market prices (Alpaca market data) every cycle and after each of your runs. "
         "Read-only: edits are overwritten. Change a book by editing `books/<ID>.json`.", "",
         f"As of {now:%Y-%m-%d %H:%M %Z}. Open books: {active} of {s['max_books']}. "
         f"New in the last 7 days: {len(recent)} of {s['max_new_per_week']}. Each book starts at ${s['start_cash']:,.0f}.", "",
         "Rules: a change fills at the open of the first trading session after it was written, with "
         f"{s['slippage_bps']} bps slippage. Kill rules are checked on each session's close; a hit sells the "
         "book at the next open. Options settle at their intrinsic value after expiry. Dividends, splits and "
         "borrow fees are not modelled.", "",
         "| Book | Thesis | Status | Started | Value | Return | SPY | vs SPY |", "|---|---|---|---|---|---|---|---|"]
    L += rows or ["| none yet | | | | | | | |"]
    L += ["", "## Spec check" + (f" ({st['checked'][:16].replace('T', ' ')})" if st.get("checked") else ""), ""]
    L += [f"- {k}: {v}" for k, v in sorted(st["verdicts"].items())] or ["- no specs yet"]
    L += [""] + detail
    (topic_dir(cfg, slug) / "BOOKS.md").write_text("\n".join(L).rstrip() + "\n")


if __name__ == "__main__":
    import argparse
    import cycle

    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--update", action="store_true")
    g.add_argument("--accept", action="store_true")
    a = ap.parse_args()
    cfg = cycle.load_config()
    try:
        out = accept(cfg, a.slug) if a.accept else update(cfg, a.slug)
        print("\n".join(out) or "nothing new")
    except broker.BrokerError as e:
        sys.exit(f"books: {e}")
