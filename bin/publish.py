#!/usr/bin/env python3
"""Publish a topic's chosen files to a public git repo, with independent timestamps.

For topics listed under [publish.<slug>] in config.toml. After each cycle the runner copies the
topic's chosen files (for the investor: THESES.md, BOOKS.md and books/*.json) into a public repo,
commits, and pushes. Each publish also writes a manifest of every file's SHA-256 and stamps it with
OpenTimestamps, which anchors it in the Bitcoin blockchain. Anyone can then check that a thesis
existed, word for word, before the book that bets on it was filled.

Rules that make the record worth trusting:
  - append only: a file that disappears from the topic stays published (git history shows all)
  - nothing goes out if it contains a secret from the env file, a key, a token, a home path
    or the machine's hostname
  - the push uses a deploy key for that one repo, kept with the runner, outside every sandbox

By hand:
  publish.py SLUG            publish now (also upgrades pending timestamps)
  publish.py SLUG --dry-run  copy and check into the local clone, commit and push nothing
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

SECRET_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"), "a Claude token"),
    (re.compile(r"\bPK[A-Z0-9]{16,}\b"), "an Alpaca key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"), "a GitHub token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"ntfy\.sh/[A-Za-z0-9_-]{6,}"), "an ntfy topic"),
]


def settings(cfg: dict, slug: str) -> dict | None:
    return cfg.get("publish", {}).get(slug)


def base_dir(cfg: dict) -> Path:
    return cfg["state"] / "publish"


def ots_bin(p: dict) -> str | None:
    if "ots" in p:
        return p["ots"] or None
    local = Path.home() / ".local/opt/ots/bin/ots"
    return str(local) if local.exists() else shutil.which("ots")


def git(args: list[str], cwd: Path, env: dict, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=check, capture_output=True, text=True)


def git_env(cfg: dict, p: dict) -> dict:
    b = base_dir(cfg)
    key = Path(p.get("ssh_key") or b / "deploy_key")
    hosts = Path(p.get("known_hosts") or b / "known_hosts")
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TZ")}
    env["GIT_SSH_COMMAND"] = (f"ssh -i {key} -o IdentitiesOnly=yes -o UserKnownHostsFile={hosts} "
                              "-o StrictHostKeyChecking=yes -o BatchMode=yes")
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def secrets_in(text: str) -> str | None:
    for pat, what in SECRET_PATTERNS:
        if pat.search(text):
            return what
    for k, v in os.environ.items():
        if re.search(r"TOKEN|KEY|SECRET|NTFY|PASSWORD", k) and len(v) >= 8 and v in text:
            return f"the value of {k}"
    home = str(Path.home())
    if home + "/" in text:
        return "a home folder path"
    host = socket.gethostname()
    if len(host) >= 4 and re.search(rf"\b{re.escape(host)}\b", text, re.I):
        return "this machine's hostname"
    return None


def readme(p: dict, slug: str) -> str:
    return f"""# {p.get('title', slug.replace('-', ' ').title() + ' · public record')}

Written and maintained by an AI agent running in [Cobbler](https://github.com/mcraeian01/cobbler). This repo is its public, timestamped record. Nothing here is investment advice.

## What's here
- `THESES.md`: every investment thesis, with the date it was written, its argument, kill criteria and a dated record of evidence.
- `books/<ID>.json`: one simulated portfolio ("book") per thesis: target weights and kill rules.
- `BOOKS.md`: every book's value and return against the S&P 500 (SPY), refreshed each cycle.
- `stamps/`: a manifest of every file's SHA-256 at each publish, with an OpenTimestamps proof (`.ots`) that anchors it in the Bitcoin blockchain.

## The rules
- **Books are simulated with real prices.** Each book starts at the same value and trades at real market prices (Alpaca market data), filled at the open of the first trading session *after* its spec was written. No real money and no real orders.
- **The thesis comes first.** A book's spec is published here before its first fill, and its thesis is in `THESES.md` before that.
- **Nothing is deleted.** Losing and closed books stay; the git history is the full record.

## How to check a claim
1. Find the commit where a thesis or book first appeared. GitHub records when each push arrived.
2. For independent proof, take the manifest in `stamps/` from that publish: it lists the SHA-256 of every file. Check the file's hash (`sha256sum THESES.md` at that commit) against the manifest. Then verify the manifest's `.ots` file at [opentimestamps.org](https://opentimestamps.org) or with `ots verify`. That proves the manifest, and every file in it, existed by the time of the Bitcoin block it was anchored in.
"""


def publish(cfg: dict, slug: str, now: dt.datetime | None = None, dry: bool = False) -> list[str]:
    p = settings(cfg, slug)
    if not p:
        return []
    now = now or dt.datetime.now(dt.timezone.utc)
    work = base_dir(cfg) / slug
    env = git_env(cfg, p)
    remote = p.get("remote") or f"git@github.com:{p['repo']}.git"
    key = Path(p.get("ssh_key") or base_dir(cfg) / "deploy_key")
    if not p.get("remote") and not key.exists():
        return [f"publishing to {p['repo']} isn't set up yet: run bin/publish-setup.sh {p['repo']}"]
    if not (work / ".git").exists():
        work.mkdir(parents=True, exist_ok=True)
        git(["init", "-q", "-b", "main"], work, env)
        git(["remote", "add", "origin", remote], work, env)
        git(["config", "user.name", p.get("author_name", "Cobbler publisher")], work, env)
        git(["config", "user.email", p.get("author_email", "cobbler-publisher@users.noreply.github.com")], work, env)
    if git(["fetch", "-q", "origin"], work, env, check=False).returncode == 0 and \
            git(["rev-parse", "-q", "--verify", "origin/main"], work, env, check=False).returncode == 0:
        git(["reset", "-q", "--hard", "origin/main"], work, env)

    topic = cfg["root"] / "topics" / slug
    copied = []
    for pattern in p.get("files", []):
        for src in sorted(topic.glob(pattern)):
            if not src.is_file():
                continue
            rel = src.relative_to(topic)
            text = src.read_text(errors="replace")
            hit = secrets_in(text)
            if hit:
                git(["reset", "-q", "--hard"], work, env, check=False)
                git(["clean", "-qfd"], work, env, check=False)
                return [f"publish blocked: {rel} contains {hit}; nothing was published"]
            dst = work / rel
            if not dst.exists() or dst.read_text(errors="replace") != text:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(text)
                copied.append(str(rel))
    lines = []
    if copied:
        (work / "README.md").write_text(readme(p, slug))
        stamps = work / "stamps"
        stamps.mkdir(exist_ok=True)
        files = sorted(f for f in work.rglob("*") if f.is_file() and ".git" not in f.parts and "stamps" not in f.parts)
        name = now.strftime("%Y-%m-%dT%H%M%SZ")
        manifest = stamps / f"{name}.manifest"
        manifest.write_text(f"# Cobbler publish {now.isoformat(timespec='seconds')}\n" +
                            "".join(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(work)}\n" for f in files))
        ots = ots_bin(p)
        if ots and not dry:
            r = subprocess.run([ots, "stamp", str(manifest)], capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                lines.append(f"timestamp failed ({(r.stderr or r.stdout).strip()[-120:]}); published without one")
        elif not ots:
            lines.append("no ots client found; published without a timestamp")
    upgraded = 0 if dry else upgrade(work, p)
    if dry:
        return lines + [f"dry run: would publish {', '.join(copied) or 'nothing new'}"]
    if not copied and not upgraded:
        return lines
    git(["add", "-A"], work, env)
    msg = (f"{now:%Y-%m-%d %H:%M} UTC: {', '.join(copied)}" if copied else
           f"{upgraded} timestamp(s) confirmed in the Bitcoin blockchain")
    git(["commit", "-q", "-m", msg], work, env)
    r = git(["push", "-q", "origin", "main"], work, env, check=False)
    if r.returncode != 0:
        return lines + [f"publish committed locally but the push failed: {r.stderr.strip()[-200:]}"]
    if copied:
        lines.append(f"published {len(copied)} file(s) to {p.get('repo') or remote}")
    return lines


def upgrade(work: Path, p: dict) -> int:
    """Ask the calendars for Bitcoin confirmations of pending stamps. Returns how many completed."""
    ots = ots_bin(p)
    stamps = work / "stamps"
    if not ots or not stamps.exists():
        return 0
    done_file = work / ".git" / "cobbler-complete-stamps"
    done = set(done_file.read_text().split()) if done_file.exists() else set()
    n = 0
    for f in sorted(stamps.glob("*.ots")):
        if f.name in done:
            continue
        r = subprocess.run([ots, "upgrade", str(f)], capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).lower()
        if r.returncode == 0 and ("success" in out or "complete" in out):
            done.add(f.name)
            n += 1
        for bak in stamps.glob(f.name + ".bak"):
            bak.unlink()
    done_file.write_text("\n".join(sorted(done)) + "\n")
    return n


if __name__ == "__main__":
    import argparse
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cycle

    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    for line in publish(cycle.load_config(), a.slug, dry=a.dry_run) or ["nothing new"]:
        print(line)
