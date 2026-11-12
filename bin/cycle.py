#!/usr/bin/env python3
"""Agent workspace runner.

One cycle:
  1. Run the orchestrator (claude -p) with read access to every topic. It returns
     a JSON plan: which topics to wake, at what tier, with what wake message, plus the digest fields.
  2. Validate the plan against config.toml (tiers, max agents, weekly pool).
  3. Run each woken topic agent (claude -p) inside a sandbox that only sees its own
     folder and _global/, with a hard --max-turns ceiling.
  4. Record actual usage, append a usage summary for the orchestrator, send the digest.

Usage:
  cycle.py                     normal cycle
  cycle.py --mode weekly       larger end-of-week cycle (orchestrator is told)
  cycle.py --only SLUG --tier S   wake one topic by hand, skipping the orchestrator
  cycle.py --dry-run           print what would run, call nothing
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("AGENTS_CONFIG", HERE.parent / "config.toml"))
sys.path.insert(0, str(HERE))
import broker  # noqa: E402  (bin/broker.py: paper-trading step for topics listed under [brokers])
import books  # noqa: E402  (bin/books.py: simulated books, one per thesis, for topics under [books])
import scanner  # noqa: E402  (bin/scanner.py: scan step for topics listed under [scanners])
import publish  # noqa: E402  (bin/publish.py: public, timestamped record for topics under [publish])
import seal  # noqa: E402  (bin/seal.py: private, timestamped record for topics under [seal])
import accounts  # noqa: E402  (bin/accounts.py: register of Alpaca paper accounts for live books)

RUN_ENV = {"PATH", "LANG", "TERM", "TZ", "HOME", "CLAUDE_CODE_OAUTH_TOKEN"}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "wakes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "tier": {"type": "string"},
                    "reason": {"type": "string", "enum": ["selected", "starved"]},
                    "message": {"type": "string"},
                },
                "required": ["topic", "tier", "reason", "message"],
            },
        },
        # The digest's parts, assembled by the runner once the agents have finished.
        "proposals": {"type": "array", "items": {"type": "string"}},
        "selection": {"type": "array", "items": {"type": "string"}},
        "not_picked": {"type": "array", "items": {"type": "string"}},
        "needs_you": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["wakes", "proposals", "selection", "not_picked", "needs_you"],
}


# ---------- config and state ----------

def load_config() -> dict:
    with open(CONFIG_PATH, "rb") as f:
        cfg = tomllib.load(f)
    # Root defaults to the folder holding bin/, so a copied workspace runs without edits.
    cfg["root"] = Path(cfg.get("workspace", {}).get("root") or HERE.parent).expanduser()
    cfg["state"] = Path(cfg["runner"]["state_dir"]).expanduser()
    cfg["state"].mkdir(parents=True, exist_ok=True)
    return cfg


def owner(cfg: dict) -> str:
    """The person the agents work for, from [workspace] owner in config.toml."""
    return cfg.get("workspace", {}).get("owner") or "the owner"


def now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def week_start(cfg: dict, t: dt.datetime) -> dt.datetime:
    """Most recent weekly reset at or before t (config: weekday 0=Mon, hh:mm local)."""
    wd = cfg["budget"]["weekly_reset_weekday"]
    hh, mm = map(int, cfg["budget"]["weekly_reset_time"].split(":"))
    candidate = t.replace(hour=hh, minute=mm, second=0, microsecond=0)
    candidate -= dt.timedelta(days=(t.weekday() - wd) % 7)
    if candidate > t:
        candidate -= dt.timedelta(days=7)
    return candidate


def cycles_left(cfg: dict, t: dt.datetime) -> int:
    """Scheduled cycles from now until the next weekly reset, counting the one running now."""
    sched = cfg.get("schedule", {})
    reset = (week_start(cfg, t) + dt.timedelta(days=7)).replace(tzinfo=None)
    t = t.replace(tzinfo=None)  # wall-clock comparisons, like the systemd timers
    slots = []
    if "daily_time" in sched:
        slots.append((None, sched["daily_time"]))
    if "weekly_time" in sched:
        slots.append((sched.get("weekly_weekday"), sched["weekly_time"]))
    n, d = 1, t.date()
    while d <= reset.date():
        for weekday, hhmm in slots:
            if weekday is not None and d.weekday() != weekday:
                continue
            hh, mm = map(int, hhmm.split(":"))
            if t < dt.datetime.combine(d, dt.time(hh, mm)) < reset:
                n += 1
        d += dt.timedelta(days=1)
    return n


def agent_spend_cap(cfg: dict, runs: list[dict], mode: str) -> int:
    """Credits topic agents may use this cycle. Reserves the orchestrator's cost for every
    cycle left, then splits the rest evenly. A weekly cycle spends whatever would expire."""
    left = cfg["budget"]["weekly_credits"] - credits_used_this_week(cfg, runs)
    n = cycles_left(cfg, now())
    for_agents = max(0, left - n * cfg["budget"]["orchestrator_credits"])
    return for_agents if mode == "weekly" else math.ceil(for_agents / n)


def read_runs(cfg: dict) -> list[dict]:
    p = cfg["state"] / "runs.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def append_run(cfg: dict, rec: dict) -> None:
    with open(cfg["state"] / "runs.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")


def topics(cfg: dict) -> list[str]:
    d = cfg["root"] / "topics"
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.exists() else []


def credits_used_this_week(cfg: dict, runs: list[dict]) -> int:
    ws = week_start(cfg, now())
    return sum(r.get("credits", 0) for r in runs if dt.datetime.fromisoformat(r["ts"]) >= ws)


def facts_for_orchestrator(cfg: dict, runs: list[dict], mode: str) -> str:
    """Deterministic numbers the orchestrator should not have to work out itself."""
    t = now()
    ws = week_start(cfg, t)
    pool = cfg["budget"]["weekly_credits"]
    used = credits_used_this_week(cfg, runs)
    lines = [
        "RUNNER FACTS (computed by the runner, trust these numbers)",
        f"Now: {t:%Y-%m-%d %H:%M %Z}. Cycle mode: {mode}.",
        f"Week started {ws:%a %Y-%m-%d %H:%M}. Credits used {used} of {pool}; {pool - used} left.",
        f"Scheduled cycles left before the reset, including this one: {cycles_left(cfg, t)}.",
        f"Agent spend cap this cycle: {agent_spend_cap(cfg, runs, mode)} credits "
        "(the runner drops grants beyond it).",
        f"Max agents this cycle: {cfg['budget']['max_agents_per_cycle']}.",
        "Tiers: " + ", ".join(f"{k} = {v['credits']} credits / {v['turns']} turns"
                               for k, v in cfg["tiers"].items()) + ".",
        "Last wake and last-run outcome per topic:",
    ]
    for slug in topics(cfg):
        mine = [r for r in runs if r.get("topic") == slug]
        if mine:
            last = mine[-1]
            age = t - dt.datetime.fromisoformat(last["ts"])
            lines.append(
                f"- {slug}: {age.days}d {age.seconds // 3600}h ago, tier {last['tier']}, "
                f"turns {last.get('num_turns')}/{last.get('cap')}, outcome {last.get('outcome')}"
            )
        else:
            lines.append(f"- {slug}: never woken")
    return "\n".join(lines + scanner.fact_lines(cfg))


# ---------- running claude ----------

def claude_bin() -> str:
    b = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
    if not b:
        sys.exit("claude not found on PATH (set CLAUDE_BIN)")
    return b


def sandbox_prefix(cfg: dict, binds_rw: list[tuple[Path, str]], binds_ro: list[tuple[Path, str]],
                   config_dir: Path, chdir: str) -> list[str]:
    """bubblewrap command that exposes only the given paths, plus system dirs and the claude binary."""
    real_bin = Path(claude_bin()).resolve()
    # Mirror the host's /lib64 link: usr/lib on Arch, usr/lib64 on Ubuntu/Debian.
    # The ELF loader lives there; point it wrong and nothing can exec in the sandbox.
    lib64 = os.readlink("/lib64") if os.path.islink("/lib64") else "usr/lib64"
    cmd = [
        "bwrap", "--die-with-parent", "--unshare-all", "--share-net",
        "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
        "--symlink", lib64, "/lib64", "--symlink", "usr/bin", "/sbin",
        "--ro-bind", "/etc", "/etc",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        # Arch with systemd-resolved points /etc/resolv.conf into /run; without this, DNS fails.
        "--ro-bind-try", "/run/systemd/resolve", "/run/systemd/resolve",
        "--tmpfs", "/home",
        "--ro-bind", str(real_bin.parent), str(real_bin.parent),
        "--bind", str(config_dir), "/home/agent/.claude",
        "--setenv", "HOME", "/home/agent",
        "--setenv", "CLAUDE_CONFIG_DIR", "/home/agent/.claude",
    ]
    for src, dst in binds_ro:
        cmd += ["--ro-bind", str(src), dst]
    for src, dst in binds_rw:
        cmd += ["--bind", str(src), dst]
    cmd += ["--chdir", chdir]
    return cmd


def run_claude(cfg: dict, *, name: str, prompt: str, model: str, max_turns: int,
               allowed: str, disallowed: str, binds_rw, binds_ro, chdir_real: Path,
               chdir_box: str, schema: dict | None, dry: bool) -> dict:
    config_dir = cfg["state"] / "claude-config" / name
    config_dir.mkdir(parents=True, exist_ok=True)
    sandboxed = cfg["runner"]["isolation"] == "bwrap"
    bin_path = str(Path(claude_bin()).resolve()) if sandboxed else claude_bin()

    args = [
        bin_path, "-p", prompt,
        "--output-format", "json",
        "--model", model,
        "--max-turns", str(max_turns),
        "--permission-mode", "acceptEdits",
        "--permission-prompts", "none",
        "--allowedTools", allowed,
        "--disallowedTools", disallowed,
        "--no-session-persistence",
    ]
    if schema:
        args += ["--json-schema", json.dumps(schema)]

    # Pass only what Claude needs. Everything else in the env file (broker keys, ntfy topic)
    # stays with the runner and never enters a run.
    env = {k: v for k, v in os.environ.items() if k in RUN_ENV or k.startswith("LC_")}
    if sandboxed:
        cmd = sandbox_prefix(cfg, binds_rw, binds_ro, config_dir, chdir_box) + args
        cwd = None
    else:
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        cmd, cwd = args, chdir_real

    if dry:
        print(f"\n[dry-run] {name}: max_turns={max_turns} model={model}")
        print("  " + " ".join(a if len(a) < 80 else a[:77] + "..." for a in cmd))
        return {"dry_run": True, "num_turns": 0, "is_error": False, "subtype": "dry_run"}

    timeout = cfg["runner"]["run_timeout_minutes"] * 60
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"is_error": True, "subtype": "timeout", "result": f"killed after {timeout}s"}
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"is_error": True, "subtype": "bad_output",
                "result": (p.stdout or p.stderr)[-2000:], "exit": p.returncode}


def outcome_of(res: dict) -> str:
    if res.get("dry_run"):
        return "dry_run"
    sub = res.get("subtype", "")
    if "max_turns" in sub:
        return "hit_ceiling"
    if not res.get("is_error"):
        return "ok"   # a finished run's text is the agent's own words ("limit order", "Fed rate"), not a status
    if sub in ("timeout", "bad_output"):
        return sub
    text = str(res.get("result", "")).lower()
    if re.search(r"usage limit|rate limit|limit reached|out of (extra )?usage|\b429\b", text):
        return "usage_limited"
    return "error"


# ---------- the two kinds of run ----------

def orchestrate(cfg: dict, runs: list[dict], mode: str, dry: bool) -> dict:
    root = cfg["root"]
    prompt = (root / "_orchestrator" / "ORCHESTRATOR_PROMPT.md").read_text().replace("{{OWNER}}", owner(cfg))
    prompt += "\n\n" + facts_for_orchestrator(cfg, runs, mode)
    res = run_claude(
        cfg, name="orchestrator", prompt=prompt,
        model=cfg["models"]["orchestrator"],
        max_turns=cfg["runner"]["orchestrator_max_turns"],
        allowed="Read,Glob,Grep,Edit,Write",
        disallowed="Bash,WebFetch,WebSearch,Agent",
        binds_rw=[(root / "_orchestrator", "/work/_orchestrator")],
        binds_ro=[(root / "_global", "/work/_global"), (root / "topics", "/work/topics"),
                  (root / "_template", "/work/_template")],
        chdir_real=root, chdir_box="/work", schema=PLAN_SCHEMA, dry=dry,
    )
    if dry:
        return {"wakes": [], "proposals": [], "selection": ["(dry run: orchestrator not called)"],
                "not_picked": [], "needs_you": []}
    append_run(cfg, {"ts": now().isoformat(), "topic": "_orchestrator", "tier": "-",
                     "credits": cfg["budget"]["orchestrator_credits"],
                     "cap": cfg["runner"]["orchestrator_max_turns"],
                     "num_turns": res.get("num_turns"), "cost_usd": res.get("total_cost_usd"),
                     "usage": res.get("usage"), "outcome": outcome_of(res)})
    plan = res.get("structured_output")
    if not plan:
        raise RuntimeError(f"orchestrator returned no plan: {outcome_of(res)} {str(res.get('result'))[:500]}")
    return plan


def validate(cfg: dict, plan: dict, runs: list[dict],
             cap: int | None = None) -> tuple[list[dict], list[str]]:
    tiers = cfg["tiers"]
    known = set(topics(cfg))
    left = cfg["budget"]["weekly_credits"] - credits_used_this_week(cfg, runs)
    kept, notes, seen, spent = [], [], set(), 0
    for w in plan.get("wakes", []):
        slug, tier = w.get("topic"), w.get("tier")
        if slug not in known or slug in seen:
            notes.append(f"dropped wake for unknown or duplicate topic '{slug}'")
            continue
        if tier not in tiers:
            notes.append(f"dropped {slug}: unknown tier '{tier}'")
            continue
        if scanner.scan_cfg(cfg, slug) and cfg["runner"]["isolation"] != "bwrap":
            notes.append(f"dropped {slug}: a scanning topic only runs in the sandbox")
            continue
        if len(kept) >= cfg["budget"]["max_agents_per_cycle"]:
            notes.append(f"dropped {slug}: over max agents per cycle")
            continue
        if tiers[tier]["credits"] > left:
            notes.append(f"dropped {slug}: {tier} needs {tiers[tier]['credits']} credits, {left} left")
            continue
        if cap is not None and spent + tiers[tier]["credits"] > cap:
            notes.append(f"dropped {slug}: {tier} would pass this cycle's cap of {cap} credits")
            continue
        words = len(w.get("message", "").split())
        if words > cfg["runner"]["wake_message_max_words"]:
            notes.append(f"{slug}: wake message was {words} words, trimmed")
            w["message"] = " ".join(w["message"].split()[: cfg["runner"]["wake_message_max_words"]])
        left -= tiers[tier]["credits"]
        spent += tiers[tier]["credits"]
        seen.add(slug)
        kept.append(w)
    return kept, notes


def wake(cfg: dict, w: dict, dry: bool) -> dict:
    root, slug, tier = cfg["root"], w["topic"], w["tier"]
    t = cfg["tiers"][tier]
    buffer = cfg["runner"]["turn_ceiling_buffer"]
    sandboxed = cfg["runner"]["isolation"] == "bwrap"
    global_path = "/work/_global" if sandboxed else str(root / "_global")
    prompt = (root / "_global" / "AGENT_PROMPT.md").read_text()
    prompt = prompt.replace("{{TOPIC}}", slug).replace("{{GLOBAL}}", global_path).replace("{{OWNER}}", owner(cfg))
    prompt += "\n\n" + w["message"].strip()
    trades = slug in cfg.get("brokers", {}) and not dry
    has_books = slug in cfg.get("books", {}) and not dry
    broker_notes = []
    if trades:  # fresh account view for the agent; a broker outage never blocks the run
        try:
            broker.snapshot(cfg, slug)
        except Exception as e:
            broker_notes.append(f"snapshot failed: {e}")
    scans = scanner.scan_cfg(cfg, slug) is not None
    allowed, disallowed = cfg["agents"]["allowed_tools"], cfg["agents"]["disallowed_tools"]
    binds_ro = [(root / "_global", "/work/_global")]
    if scans:  # it reads the code, so it gets no web tools: nothing it reads can leave
        allowed, disallowed = scanner.sealed_tools(cfg["agents"])
        binds_ro.append((scanner.code_dir(cfg, slug), scanner.BOX_CODE))
    res = run_claude(
        cfg, name=f"topic-{slug}", prompt=prompt,
        model=cfg["models"]["agent"], max_turns=t["turns"] + buffer,
        allowed=allowed, disallowed=disallowed,
        binds_rw=[(root / "topics" / slug, "/work/topic")],
        binds_ro=binds_ro,
        chdir_real=root / "topics" / slug, chdir_box="/work/topic", schema=None, dry=dry,
    )
    out = outcome_of(res)
    if has_books:  # check the agent's book specs, queue changes, refresh BOOKS.md
        try:
            broker_notes += books.accept(cfg, slug)
            broker_notes += books.update(cfg, slug)
        except Exception as e:
            broker_notes.append(f"books failed, nothing queued: {e}")
    if trades:  # place what the agent asked for, checked against config.toml, then refresh
        try:
            broker_notes += broker.execute(cfg, slug)
            broker.snapshot(cfg, slug)
        except Exception as e:
            broker_notes.append(f"execute failed, nothing placed: {e}")
    scan_notes = []
    if scans and not dry:  # a scan problem never fails the cycle
        try:
            scan_notes = scanner.after_run(cfg, slug, out)
        except Exception as e:
            scan_notes = [f"patch check failed: {e}"]
    rec = {"ts": now().isoformat(), "topic": slug, "tier": tier, "reason": w["reason"],
           "credits": t["credits"], "cap": t["turns"], "num_turns": res.get("num_turns"),
           "cost_usd": res.get("total_cost_usd"), "usage": res.get("usage"), "outcome": out,
           "broker": broker_notes, "scanner": scan_notes}
    if not dry:
        append_run(cfg, rec)
    if out in ("hit_ceiling", "timeout", "error", "bad_output") and not dry:
        log = root / "topics" / slug / "LOG.md"
        with open(log, "a") as f:
            f.write(f"\n## {now():%Y-%m-%d %H:%M}\nRunner: run ended with '{out}' "
                    f"after {res.get('num_turns')} turns (cap {t['turns']}). "
                    "State may be partly written. Check STATE.md before continuing.\n")
    return rec


# ---------- reporting ----------

def write_usage_summary(cfg: dict, runs: list[dict]) -> None:
    """Human- and orchestrator-readable usage, kept inside the workspace."""
    ws = week_start(cfg, now())
    week = [r for r in runs if dt.datetime.fromisoformat(r["ts"]) >= ws]
    lines = ["# Usage (written by the runner)", "",
             f"Week from {ws:%a %Y-%m-%d %H:%M}. Credits used: "
             f"{sum(r.get('credits', 0) for r in week)} of {cfg['budget']['weekly_credits']}.", "",
             "| When | Topic | Tier | Turns / cap | Est. cost | Outcome |", "|---|---|---|---|---|---|"]
    for r in reversed(week[-40:]):
        cost = f"${r['cost_usd']:.2f}" if isinstance(r.get("cost_usd"), (int, float)) else "-"
        lines.append(f"| {r['ts'][:16].replace('T', ' ')} | {r['topic']} | {r['tier']} | "
                     f"{r.get('num_turns')}/{r.get('cap')} | {cost} | {r.get('outcome')} |")
    lines += ["", "Est. cost is Claude Code's client-side estimate. On a subscription it is a "
              "relative measure of how much of your allowance a run used, not a bill."]
    (cfg["root"] / "_orchestrator" / "USAGE.md").write_text("\n".join(lines) + "\n")


def pitch_bullets(text: str, heading: str, limit: int = 3, width: int = 150) -> list[str]:
    """The top-level bullets under a `## heading` in a PITCH.md, shortened for a phone."""
    m = re.search(rf"^## {re.escape(heading)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    items = [l[2:].replace("`", "").strip() for l in (m.group(1) if m else "").splitlines()
             if l.startswith("- ") and l[2:].strip()]
    short = [i if len(i) <= width else i[: width - 1].rstrip() + "…" for i in items]
    return short[:limit] + ([f"…and {len(short) - limit} more"] if len(short) > limit else [])


def progress(cfg: dict, results: list[dict]) -> list[str]:
    """What each agent that ran says it did and wants next, read from its fresh pitch."""
    out = []
    for r in results:
        out.append(f"{r['topic'].upper()} · {r['tier']} · {r.get('num_turns')}/{r.get('cap')} turns · "
                   + ("✅" if r["outcome"] == "ok" else f"⚠️ {r['outcome']}"))
        p = cfg["root"] / "topics" / r["topic"] / "PITCH.md"
        text = p.read_text() if p.exists() else ""
        if r["outcome"] != "ok":
            out.append("The run didn't finish cleanly, so this pitch may be from an earlier run.")
        out += ["Did:"] + [f"- {b}" for b in pitch_bullets(text, "Delivered last run") or ["nothing reported"]]
        out += ["Next, if selected:"] + [f"- {b}" for b in pitch_bullets(text, "Next step") or ["nothing proposed"]]
        out.append("")
    return out[:-1] if out else ["No agents ran."]


def build_digest(plan: dict, prog: list[str], credits: str, runner: list[str]) -> str:
    def section(label, lines):
        return [label, *lines, ""]

    bullets = lambda key: [f"- {x}" for x in plan.get(key, [])]
    needs = section("🙋 NEEDS YOU", bullets("needs_you")) if plan.get("needs_you") else []
    urgent = any(x.upper().startswith("URGENT") for x in plan.get("needs_you", []))
    picked = bullets("selection") or ["- Nobody this cycle."]
    if plan.get("not_picked"):
        picked += ["Not picked:"] + bullets("not_picked")
    parts = (needs if urgent else []) + section("💡 PROPOSALS", bullets("proposals") or ["- None."]) \
        + section("🎯 SELECTION", picked + [credits]) + section("🔨 PROGRESS", prog) \
        + ([] if urgent else needs) + (section("⚙️ RUNNER", [f"- {n}" for n in runner]) if runner else [])
    return "\n".join(parts).rstrip() + "\n"


def fit(body: str, limit: int = 3900) -> str:
    """ntfy turns a message over 4,096 bytes into a file attachment, so stay under it."""
    if len(body.encode()) <= limit:
        return body
    cut = body.encode()[:limit].decode(errors="ignore").rsplit("\n", 1)[0]
    return cut + "\n…cut short. The full digest is in _orchestrator/digests/."


def save_digest(cfg: dict, body: str) -> None:
    """Append, so a morning and an evening cycle on the same day both survive."""
    p = cfg["root"] / "_orchestrator" / "digests" / f"{now():%Y-%m-%d}.md"
    with open(p, "a") as f:
        f.write(("\n---\n\n" if p.exists() and p.stat().st_size else "") + f"{now():%H:%M}\n\n{body}")


def notify(cfg: dict, title: str, body: str, dry: bool) -> None:
    url = cfg.get("notify", {}).get("ntfy_url") or os.environ.get("NTFY_URL")
    if dry or not url:
        print(f"\n--- {title} ---\n{body}")
        return
    req = urllib.request.Request(url, data=fit(body).encode(), headers={"Title": title})
    token = os.environ.get("NTFY_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:  # never let a failed notification fail the cycle
        print(f"notify failed: {e}", file=sys.stderr)


def maybe_commit(cfg: dict, msg: str, dry: bool) -> None:
    if dry or not cfg["workspace"].get("git_commit"):
        return
    root = cfg["root"]
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=False)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", msg], check=False)
    if cfg["workspace"].get("git_push"):
        subprocess.run(["git", "-C", str(root), "push", "-q"], check=False)


# ---------- main ----------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="daily", choices=["daily", "weekly"])
    ap.add_argument("--only", help="wake one topic by hand, skipping the orchestrator")
    ap.add_argument("--tier", default="S")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cfg = load_config()
    lock = open(cfg["state"] / "cycle.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("another cycle is running")

    scan_failures = []
    if not a.dry_run:  # before the orchestrator, so it sees any alert; costs no model usage
        for slug in cfg.get("scanners", {}):
            if a.only in (None, slug):
                try:
                    scanner.scan(cfg, slug)
                except Exception as e:
                    scan_failures.append(f"{slug} scan failed, SCAN.md is stale: {e}")

    runs = read_runs(cfg)
    # Books move with the market every cycle, whether or not their topic is woken:
    # queued changes fill, kill rules are checked, BOOKS.md is refreshed before anyone reads it.
    book_notes = []
    live_books = [k for k, b in cfg.get("books", {}).items() if b.get("mode") == "alpaca"]
    if live_books and not a.dry_run:
        # New keys pasted in since last cycle become usable, and a book waiting for an account
        # gets one now, so its orders go in before the open.
        try:
            book_notes += [f"accounts: {line}" for line in accounts.check(cfg) if line != "no changes"]
        except Exception as e:
            book_notes.append(f"accounts check failed: {e}")
        for slug in live_books:
            try:
                book_notes += [f"{slug} {line}" for line in books.accept(cfg, slug)
                               if "waiting for a free Alpaca" not in line]
            except Exception as e:
                book_notes.append(f"{slug} books: accept failed: {e}")
    for slug in ([] if a.dry_run else cfg.get("books", {})):
        try:
            book_notes += [f"{slug} {line}" for line in books.update(cfg, slug)]
        except Exception as e:
            book_notes.append(f"{slug} books: update failed: {e}")
    cap = None if a.only else agent_spend_cap(cfg, runs, a.mode)  # before the orchestrator's own charge
    if a.only:
        plan = {"wakes": [{"topic": a.only, "tier": a.tier, "reason": "selected",
                           "message": f"WAKE · {a.only} · {now():%Y-%m-%d %H:%M}\n"
                                      f"Allocation: {a.tier} · manual run by {owner(cfg)}\n"
                                      "From owner: none\nContext: none"}],
                "proposals": [], "selection": [f"{a.only.upper()} · {a.tier}: manual run"],
                "not_picked": [], "needs_you": []}
    else:
        try:
            plan = orchestrate(cfg, runs, a.mode, a.dry_run)
        except Exception as e:
            notify(cfg, "Agents: orchestrator failed", str(e)[:1500], a.dry_run)
            raise
        runs = read_runs(cfg)

    wakes, notes = validate(cfg, plan, runs, cap)
    notes += scan_failures
    results = []
    for w in wakes:
        rec = wake(cfg, w, a.dry_run)
        results.append(rec)
        if rec["outcome"] == "usage_limited":
            notes.append("usage limit reached; remaining wakes skipped")
            break

    # Publish and seal after the agents, so a book accepted tonight is timestamped before it fills.
    for slug in ([] if a.dry_run else cfg.get("publish", {})):
        try:
            book_notes += [f"{slug} {line}" for line in publish.publish(cfg, slug)]
        except Exception as e:
            book_notes.append(f"{slug} publish failed: {e}")
    if not a.dry_run and any(b.get("mode") == "alpaca" for b in cfg.get("books", {}).values()):
        try:
            accounts.table(cfg)   # the owner's register of accounts and what each holds
        except Exception as e:
            book_notes.append(f"accounts table failed: {e}")
    for slug in ([] if a.dry_run else cfg.get("seal", {})):
        try:
            book_notes += [f"{slug} {line}" for line in seal.seal(cfg, slug)]
        except Exception as e:
            book_notes.append(f"{slug} seal failed: {e}")

    runs = read_runs(cfg)
    if not a.dry_run:
        write_usage_summary(cfg, runs)
    runner = [f"{r['topic'].upper()}: {line}" for r in results for line in r.get("broker", [])]
    runner += [re.sub(r"^\S+", lambda m: m.group().upper(), n) for n in book_notes]
    runner += [f"{r['topic'].upper()}: {line}" for r in results for line in r.get("scanner", [])]
    runner += notes
    used = credits_used_this_week(cfg, runs)
    credits = f"Credits: {used}/{cfg['budget']['weekly_credits']} used this week."
    if live_books and not a.dry_run:
        try:
            plan["needs_you"] = list(plan.get("needs_you", [])) + accounts.needs(cfg)
        except Exception as e:
            runner.append(f"accounts: couldn't work out what's needed ({e})")
    digest = build_digest(plan, progress(cfg, results), credits, runner)
    if not a.dry_run:
        save_digest(cfg, digest)
    notify(cfg, f"Cobbler · {now():%a %d %b}", digest, a.dry_run)
    maybe_commit(cfg, f"cycle {now():%Y-%m-%d %H:%M} ({a.mode})", a.dry_run)


if __name__ == "__main__":
    main()
