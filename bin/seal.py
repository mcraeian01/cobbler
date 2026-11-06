#!/usr/bin/env python3
"""Seal: a private, timestamped record of a topic's files, with selective reveal later.

For topics listed under [seal.<slug>] in config.toml. Every cycle the runner fingerprints the
topic's chosen files (for the investor: each thesis in THESES.md separately, and books/*.json),
keeps a copy of every version, and stamps the full list with OpenTimestamps, which anchors it in
the Bitcoin blockchain. Nothing is published. The record stays in this instance.

Later, `seal.py SLUG --reveal B007` builds a folder to share: that book's spec and its thesis
exactly as first sealed, the stamped lists that prove they existed before the book filled, and
how to check it. The lists also carry every other item's fingerprint, which proves how many books
and theses ran without revealing what any of them said.

Theses are split by heading: each section whose heading starts with its ID ("### T3 · ...") is
fingerprinted on its own, so one thesis can be revealed without the rest.

By hand:
  seal.py SLUG                    seal now (also upgrades pending timestamps)
  seal.py SLUG --reveal B007      build reveal-B007/ in the records folder
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish  # noqa: E402  (ots_bin)

HEADING = re.compile(r"^(#{1,6})\s+(T\d+)\b")


def settings(cfg: dict, slug: str) -> dict | None:
    return cfg.get("seal", {}).get(slug)


def rec_dir(cfg: dict, slug: str, s: dict) -> Path:
    return cfg["root"] / s["dir"] if s.get("dir") else cfg["root"] / "_records" / slug


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def split_theses(text: str) -> dict[str, str]:
    """{'theses/T3.md': section}: each heading that starts with a thesis ID, up to the next heading
    at the same or a higher level."""
    lines = text.split("\n")
    heads = [(i, len(m.group(1)), m.group(2)) for i, l in enumerate(lines) if (m := HEADING.match(l))]
    levels = [(i, len(m.group(1))) for i, l in enumerate(lines) if (m := re.match(r"^(#{1,6})\s", l))]
    out = {}
    for i, lvl, tid in heads:
        end = next((j for j, L in levels if j > i and L <= lvl), len(lines))
        out.setdefault(f"theses/{tid}.md", "\n".join(lines[i:end]).rstrip("\n") + "\n")
    return out


def items(cfg: dict, slug: str, s: dict) -> dict[str, bytes]:
    topic = cfg["root"] / "topics" / slug
    out: dict[str, bytes] = {}
    for pattern in s.get("files", []):
        for src in sorted(topic.glob(pattern)):
            if not src.is_file():
                continue
            rel = str(src.relative_to(topic))
            if rel == "THESES.md" and s.get("split_theses", True):
                out.update({k: v.encode() for k, v in split_theses(src.read_text()).items()})
            else:
                out[rel] = src.read_bytes()
    return out


def seal(cfg: dict, slug: str, now: dt.datetime | None = None) -> list[str]:
    s = settings(cfg, slug)
    if not s:
        return []
    now = now or dt.datetime.now(dt.timezone.utc)
    rec = rec_dir(cfg, slug, s)
    (rec / "objects").mkdir(parents=True, exist_ok=True)
    (rec / "manifests").mkdir(exist_ok=True)
    cur = items(cfg, slug, s)
    hashes = {rel: sha(b) for rel, b in cur.items()}
    for rel, b in cur.items():
        obj = rec / "objects" / hashes[rel]
        if not obj.exists():
            obj.write_bytes(b)
    latest_p = rec / "latest.json"
    latest = json.loads(latest_p.read_text()) if latest_p.exists() else {}
    lines = []
    # Keep every item ever sealed in the list, so a deleted book still counts in the field.
    merged = {**latest, **hashes}
    if hashes and merged != latest:
        changed = sum(1 for k, v in hashes.items() if latest.get(k) != v)
        name = now.strftime("%Y-%m-%dT%H%M%SZ")
        m = rec / "manifests" / f"{name}.manifest"
        m.write_text(f"# Cobbler seal {now.isoformat(timespec='seconds')} · topic {slug}\n" +
                     "".join(f"{h}  {rel}\n" for rel, h in sorted(merged.items())))
        ots = publish.ots_bin(s)
        if ots:
            r = subprocess.run([ots, "stamp", str(m)], capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                lines.append(f"seal: timestamp failed ({(r.stderr or r.stdout).strip()[-120:]})")
        else:
            lines.append("seal: no ots client found; sealed without a timestamp")
        latest_p.write_text(json.dumps(merged, indent=1, sort_keys=True))
        lines.append(f"sealed {changed} new or changed item(s), {len(merged)} in the record")
    n = upgrade(rec, s)
    if n:
        lines.append(f"{n} seal timestamp(s) confirmed in the Bitcoin blockchain")
    return lines


def upgrade(rec: Path, s: dict) -> int:
    ots = publish.ots_bin(s)
    if not ots:
        return 0
    done_p = rec / "confirmed.txt"
    done = set(done_p.read_text().split()) if done_p.exists() else set()
    n = 0
    for f in sorted((rec / "manifests").glob("*.ots")):
        if f.name in done:
            continue
        r = subprocess.run([ots, "upgrade", str(f)], capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).lower()
        if r.returncode == 0 and ("success" in out or "complete" in out):
            done.add(f.name)
            n += 1
        for bak in f.parent.glob(f.name + ".bak"):
            bak.unlink()
    if done:
        done_p.write_text("\n".join(sorted(done)) + "\n")
    return n


def manifests(rec: Path) -> list[tuple[str, dict[str, str]]]:
    out = []
    for m in sorted((rec / "manifests").glob("*.manifest")):
        entries = {}
        for line in m.read_text().splitlines():
            if line and not line.startswith("#"):
                h, rel = line.split("  ", 1)
                entries[rel] = h
        out.append((m.name, entries))
    return out


def reveal(cfg: dict, slug: str, book: str) -> Path:
    """Build a shareable folder proving one book and its thesis were sealed, and when."""
    s = settings(cfg, slug) or {}
    rec = rec_dir(cfg, slug, s)
    ms = manifests(rec)
    spec_rel = f"books/{book}.json"

    def versions(rel):
        seen, out = set(), []
        for name, entries in ms:
            h = entries.get(rel)
            if h and h not in seen:
                seen.add(h)
                out.append((h, name))
        return out

    spec_v = versions(spec_rel)
    if not spec_v:
        raise SystemExit(f"{book} was never sealed")
    tid = json.loads((rec / "objects" / spec_v[0][0]).read_text()).get("thesis")
    thesis_v = versions(f"theses/{tid}.md")
    out = rec / f"reveal-{book}"
    shutil.rmtree(out, ignore_errors=True)
    (out / "files").mkdir(parents=True)
    (out / "proofs").mkdir()
    used = set()
    rows = []
    for rel, vs in ((f"theses/{tid}.md", thesis_v), (spec_rel, spec_v)):
        for n, (h, mname) in enumerate(vs, 1):
            fname = f"{Path(rel).stem}.v{n}{Path(rel).suffix}"
            shutil.copy(rec / "objects" / h, out / "files" / fname)
            used.add(mname)
            rows.append(f"| `files/{fname}` | `{h[:16]}…` | `proofs/{mname}` |")
    for mname in sorted(used):
        shutil.copy(rec / "manifests" / mname, out / "proofs" / mname)
        if (rec / "manifests" / (mname + ".ots")).exists():
            shutil.copy(rec / "manifests" / (mname + ".ots"), out / "proofs" / (mname + ".ots"))
    every = set().union(*(e.keys() for _, e in ms))
    n_books = sum(1 for r in every if r.startswith("books/"))
    n_theses = sum(1 for r in every if r.startswith("theses/"))
    result = ""
    stp = cfg["state"] / f"books-{slug}.json"
    if stp.exists():
        b = json.loads(stp.read_text())["books"].get(book)
        if b and b.get("start"):
            last = (b.get("end") or (b["history"][-1] if b["history"] else {})) or {}
            nav = last.get("nav")
            spy = b["history"][-1]["spy"] if b["history"] else None
            if nav:
                result = (f"\n## Result (simulated)\nOpened {b['start']['date']} at ${b['start']['nav']:,.0f}; "
                          f"latest value ${nav:,.0f} ({(nav / b['start']['nav'] - 1) * 100:+.1f}%)"
                          + (f"; SPY over the same period {(spy / b['start']['spy'] - 1) * 100:+.1f}%" if spy else "")
                          + ". Books are simulated at real market prices (Alpaca market data): no real money or orders.\n")
    first = ms[0][0][:10] if ms else "-"
    (out / "README.md").write_text(f"""# {book} · thesis {tid}

This folder proves that thesis {tid} and book {book} were written down, word for word, before the dates shown, without revealing anything else in the record.
{result}
## What's here
| File | SHA-256 | First sealed in |
|---|---|---|
{chr(10).join(rows)}

The first version of each file is the one that counts: it was sealed before the book first traded.

## How to check it
1. Hash a file: `sha256sum files/{Path(f'theses/{tid}.md').stem}.v1.md`.
2. Find that hash in the manifest listed next to it in `proofs/`.
3. Verify the manifest's `.ots` file at [opentimestamps.org](https://opentimestamps.org) (drop in both the manifest and its `.ots`), or with `ots verify proofs/<manifest>.ots`. That proves the manifest, and so every hash in it, existed by the time of the Bitcoin block it was anchored in.

## The rest of the field
The manifests also list the fingerprint of every other item in the record: {n_books} book spec(s) and {n_theses} thesis(es) across all seals since {first}. Their contents aren't revealed, but the timestamps fix how many there were and when, so the field this book was picked from can't be quietly shrunk after the fact.
""")
    return out


if __name__ == "__main__":
    import argparse
    import cycle

    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--reveal", metavar="BOOK")
    a = ap.parse_args()
    cfg = cycle.load_config()
    if a.reveal:
        print(f"wrote {reveal(cfg, a.slug, a.reveal)}")
    else:
        print("\n".join(seal(cfg, a.slug)) or "nothing new")
