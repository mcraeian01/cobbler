#!/usr/bin/env python3
"""Alpaca paper accounts for live books: plan them, check them, and keep a table of what each holds.

Signing up, creating paper accounts and generating keys happen in Alpaca's dashboard, by the owner:
Alpaca has no public API for any of them. This tool does the rest:

  accounts.py plan N    reserve the next N account names across logins (per_login accounts each,
                        following login_pattern) in the accounts file, and print a checklist
  accounts.py check     test every account that has keys: the keys work, the number matches, the
                        balance and options level are right. Then rewrite the table.
  accounts.py table     rewrite the table only (the runner does this every cycle)

The accounts file (private, outside the vault and every sandbox) has one section per account:

  [awl]
  login = "you+awl@example.com"      # which Alpaca login it lives under
  number = "PA..."                    # filled in by check if left blank
  key_id = "PK..."                    # pasted by the owner
  secret_key = "..."
  use = true                          # false for a login's default account, not used for books
  added = "2026-11-11"

Settings come from [accounts] in config.toml: login_pattern (with {alias}), per_login, start_cash,
table (a path in the instance, kept private by release.py).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import broker  # noqa: E402
import live  # noqa: E402

FIELDS = ["login", "number", "key_id", "secret_key", "use", "added", "note"]

# Names for accounts, and for logins (a login takes the name of its first account). Each is used
# once, ever. Shoemaking words, all plain letters so they're valid in an email alias.
WORDS = """awl welt vamp heel sole insole eyelet lace tack shank toecap quarter tongue rand bevel burnish
rivet buckle thread needle bobbin thimble hammer anvil punch skiver edger gouge crimp stitch seam gusset
lining collar throat instep wedge cleat brogue oxford derby loafer clog mule sandal boot chukka moccasin
slipper galosh spat gaiter cordwain hide suede calf cork rubber wax polish dubbin tallow aglet heelplate
lapstone pegs bristle hob tread""".split()


def settings(cfg: dict) -> dict:
    a = {"per_login": 3, "start_cash": 1_000_000, "table": "_private/ACCOUNTS.md", "login_pattern": ""}
    a.update(cfg.get("accounts", {}))
    return a


def path(cfg: dict) -> Path:
    b = next(iter(cfg.get("books", {}).values()), {})
    return Path(b.get("accounts_file") or live.ACCOUNTS_FILE).expanduser()


def read(p: Path) -> dict[str, dict]:
    return tomllib.load(open(p, "rb")) if p.exists() else {}


def write(p: Path, accts: dict[str, dict]) -> None:
    """Rewrite the file, keeping its header comments. Only plain strings and booleans are stored."""
    head = []
    if p.exists():
        for line in p.read_text().splitlines():
            if line.startswith("["):
                break
            head.append(line)
    out = ["\n".join(head).rstrip(), ""]
    for name, a in accts.items():
        out.append(f"[{name}]")
        for k in FIELDS:
            if k in a:
                v = a[k]
                out.append(f"{k} = {'true' if v is True else 'false' if v is False else json.dumps(str(v))}")
        out.append("")
    p.write_text("\n".join(out).lstrip("\n"))
    p.chmod(0o600)


def number_of(name: str) -> int:
    m = re.search(r"(\d+)$", name)
    return int(m.group(1)) if m else 0


def order(accts: dict) -> list[str]:
    """Accounts in the order they were added."""
    return sorted(accts, key=lambda k: (accts[k].get("added") or "", number_of(k), k))


def alias_of(login: str) -> str:
    return login.split("@")[0].split("+")[-1]


def plan(cfg: dict, n: int, today: str | None = None) -> list[str]:
    s, p = settings(cfg), path(cfg)
    if "{alias}" not in s["login_pattern"]:
        raise SystemExit('set [accounts] login_pattern in config.toml, e.g. "you+{alias}@gmail.com"')
    accts = read(p)
    today = today or dt.date.today().isoformat()
    per_login: dict[str, int] = {}
    for k in order(accts):
        if accts[k].get("login"):
            per_login[accts[k]["login"]] = per_login.get(accts[k]["login"], 0) + 1
    logins = list(per_login)
    used = set(accts) | {alias_of(l) for l in per_login}
    words = iter(w for w in WORDS if w not in used)
    new = []
    open_logins = [l for l in logins if per_login[l] < s["per_login"]]
    while len(new) < n:
        try:
            name = next(words)
        except StopIteration:
            raise SystemExit("ran out of names: add more words to WORDS in bin/accounts.py")
        if not open_logins:
            login = s["login_pattern"].replace("{alias}", name)
            per_login[login] = 0
            open_logins.append(login)
        login = open_logins[0]
        accts[name] = {"login": login, "use": True, "added": today}
        per_login[login] += 1
        new.append((name, login))
        if per_login[login] >= s["per_login"]:
            open_logins.pop(0)
    write(p, accts)
    lines = [f"Reserved {n} account(s). In Alpaca's dashboard, for each login below:"]
    for login in dict.fromkeys(l for _, l in new):
        names = [nm for nm, l in new if l == login]
        fresh = login not in logins
        lines.append(f"\n{login}" + ("  (new login: sign up first)" if fresh else ""))
        for nm in names:
            lines.append(f"  - New Paper Account, named {nm}, balance ${s['start_cash']:,}; "
                         f"generate its API keys; paste them under [{nm}] in {p}")
    lines.append(f"\nThen run: python3 bin/accounts.py check")
    return lines


def check(cfg: dict) -> list[str]:
    s, p = settings(cfg), path(cfg)
    accts = read(p)
    side = p.with_suffix(".status.json")
    old = json.loads(side.read_text()) if side.exists() else {}
    lines = []
    for name, a in accts.items():
        first_check = not (old.get(name) or {}).get("checked")
        if a.get("use") is False:
            continue
        if not (a.get("key_id") and a.get("secret_key")):
            a["status"] = "waiting for keys"
            continue
        try:
            api = live.CLIENT(a["key_id"], a["secret_key"])
            acct = api.account()
        except broker.BrokerError as e:
            a["status"] = f"keys rejected ({e})"
            lines.append(f"{name}: keys rejected by Alpaca ({e})")
            continue
        num, eq = acct.get("account_number", ""), float(acct.get("equity", 0))
        problems = []
        if a.get("number") and a["number"] != num:
            problems.append(f"keys belong to {num}, not {a['number']}")
        if not a.get("number"):
            a["number"] = num
        if first_check and abs(eq - s["start_cash"]) > 0.01 * s["start_cash"]:
            problems.append(f"balance is ${eq:,.0f}, expected ${s['start_cash']:,}")
        if int(acct.get("options_trading_level") or 0) < 2:
            problems.append("options trading isn't enabled (level < 2)")
        if acct.get("status") != "ACTIVE":
            problems.append(f"status is {acct.get('status')}")
        if problems:
            a["status"] = "; ".join(problems)
            lines.append(f"{name}: " + "; ".join(problems))
        else:
            a["status"] = "ready"
            if first_check:
                a["checked"] = dt.date.today().isoformat()
                lines.append(f"{name}: ready ({num}, ${eq:,.0f})")
    status = {k: {"status": v.pop("status", None), "checked": v.pop("checked", None)} for k, v in accts.items()}
    write(p, accts)
    for k, v in status.items():
        if v["checked"] is None and old.get(k, {}).get("checked"):
            v["checked"] = old[k]["checked"]
    side.write_text(json.dumps(status, indent=1))
    side.chmod(0o600)
    table(cfg)
    return lines or ["no changes"]


def waiting_books(cfg: dict) -> list[str]:
    """Books whose spec is accepted in every other way but has no account yet."""
    out = []
    for slug in cfg.get("books", {}):
        stp = cfg["state"] / f"books-{slug}.json"
        if stp.exists():
            out += [bid for bid, v in json.loads(stp.read_text()).get("verdicts", {}).items()
                    if "no free Alpaca paper account" in v]
    return out


def needs(cfg: dict, today: str | None = None) -> list[str]:
    """Lines for the digest's NEEDS YOU: asks for one login's accounts at a time, only when needed."""
    s, p = settings(cfg), path(cfg)
    accts = read(p)
    side = p.with_suffix(".status.json")
    status = json.loads(side.read_text()) if side.exists() else {}
    out = []
    for name in order(accts):
        st = (status.get(name) or {}).get("status")
        if accts[name].get("key_id") and st not in (None, "ready", "waiting for keys"):
            out.append(f"ALPACA: {name}: {st}. Fix it in the keys file or the dashboard.")
    taken = set()
    for slug in cfg.get("books", {}):
        stp = cfg["state"] / f"books-{slug}.json"
        if stp.exists():
            taken |= {b.get("account") for b in json.loads(stp.read_text())["books"].values()}
    b = next(iter(cfg.get("books", {}).values()), {})
    free = [k for k in live.load_accounts({"accounts_file": str(p), **b}) if k not in taken]
    waiting = waiting_books(cfg)
    if not waiting and len(free) > 1:
        return out
    pending = [k for k in order(accts) if accts[k].get("use", True) is not False and not accts[k].get("key_id")]
    if not pending and s["login_pattern"]:
        plan(cfg, s["per_login"], today)
        accts = read(p)
        pending = [k for k in order(accts) if accts[k].get("use", True) is not False and not accts[k].get("key_id")]
    if not pending:
        return out
    login = accts[pending[0]]["login"]
    names = [k for k in pending if accts[k]["login"] == login]
    new_login = all(accts[k]["login"] != login or k in names for k in accts)
    why = (f"{', '.join(waiting)} {'is' if len(waiting) == 1 else 'are'} waiting for an account" if waiting
           else f"{len(free)} free account{'s' if len(free) != 1 else ''} left")
    how = (f"sign up as {login}, turn its default account into {names[0]} and create {', '.join(names[1:])}"
           if new_login and len(names) > 1 else f"in {login}, make sure {', '.join(names)} exist{'s' if len(names) == 1 else ''}")
    out.insert(0, f"ALPACA: {why}. {how[0].upper() + how[1:]}, all at $1M, and paste each one's keys into the keys file. "
                  "The runner checks them and opens waiting books by itself.")
    return out


def table(cfg: dict) -> Path:
    """The owner's register: every account, its login, and the book and thesis it holds."""
    s, p = settings(cfg), path(cfg)
    accts = read(p)
    side = p.with_suffix(".status.json")
    status = json.loads(side.read_text()) if side.exists() else {}
    holders = {}
    for slug in cfg.get("books", {}):
        stp = cfg["state"] / f"books-{slug}.json"
        if stp.exists():
            for bid, b in json.loads(stp.read_text())["books"].items():
                if b.get("account"):
                    holders[b["account"]] = (slug, bid, b)
    rows = []
    for name in order(accts):
        a = accts[name]
        if a.get("use") is False:
            rows.append(f"| {name} | {a.get('login', '-')} | {a.get('number', '-')} | not for books | | | | {a.get('note', '')} |")
            continue
        st = (status.get(name) or {}).get("status") or ("keys added, not checked" if a.get("key_id") else "waiting for keys")
        slug, bid, b = holders.get(name, (None, None, None))
        thesis = ""
        if b:
            tf = cfg["root"] / "topics" / slug / "theses" / f"{b['thesis']}.md"
            title = tf.read_text().split("\n", 1)[0].split("·", 1)[-1].strip() if tf.exists() else ""
            thesis = f"{b['thesis']} · {title}" if title else b["thesis"]
        book = f"{bid} ({b['status']})" if b else "free"
        rows.append(f"| {name} | {a.get('login', '-')} | {a.get('number', '-')} | {st} | {book} | {thesis} | "
                    f"{a.get('added', '')} | {a.get('note', '')} |")
    out = cfg["root"] / s["table"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join([
        "# Alpaca paper accounts", "",
        "Written by bin/accounts.py every cycle. Private: never released. Keys live only in "
        f"`{str(p).replace(str(Path.home()), '~')}`.", "",
        f"One account per book, never reused. Each login holds up to {s['per_login']} paper accounts.", "",
        "| Account | Login | Number | Status | Book | Thesis | Added | Note |", "|---|---|---|---|---|---|---|---|",
        *rows, ""]))
    return out


if __name__ == "__main__":
    import cycle
    cfg = cycle.load_config()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "table"
    if cmd == "plan":
        print("\n".join(plan(cfg, int(sys.argv[2]))))
    elif cmd == "check":
        print("\n".join(check(cfg)))
    else:
        print(f"wrote {table(cfg)}")
