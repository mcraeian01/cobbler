#!/usr/bin/env python3
"""Tests for bin/seal.py with a fake ots client. Run: python3 tests/test_seal.py"""
import datetime as dt
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import seal  # noqa: E402

FAILS = []


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


tmp = Path(tempfile.mkdtemp())
topic = tmp / "root/topics/inv"
(topic / "books").mkdir(parents=True)
(tmp / "state").mkdir()
fake_ots = tmp / "ots"
fake_ots.write_text('#!/bin/sh\n'
                    'if [ "$1" = stamp ]; then echo pending > "$2.ots"; fi\n'
                    'if [ "$1" = upgrade ]; then if [ -f "$(dirname "$2")/CONFIRM" ]; then echo bitcoin > "$2"; echo "Success! Timestamp complete"; else echo Pending; fi; fi\n')
fake_ots.chmod(0o755)
cfg = {"root": tmp / "root", "state": tmp / "state",
       "seal": {"inv": {"files": ["THESES.md", "books/*.json"], "ots": str(fake_ots)}}}
rec = tmp / "root/_records/inv"
t0 = dt.datetime(2026, 11, 6, 9, 50, tzinfo=dt.timezone.utc)

theses = """# Theses

Intro text.

### T1 · Rates fall
Status: live
Argument one.
#### Evidence
- for

### T2 · Oil is cheap
Status: draft
Argument two.
"""
parts = seal.split_theses(theses)
ok(set(parts) == {"theses/T1.md", "theses/T2.md"}, "THESES.md splits into one item per thesis")
ok("#### Evidence" in parts["theses/T1.md"] and "Oil" not in parts["theses/T1.md"], "a section keeps its sub-headings and stops at the next thesis")

(topic / "THESES.md").write_text(theses)
(topic / "books/B001.json").write_text(json.dumps({"id": "B001", "thesis": "T1"}))
(topic / "books/B002.json").write_text(json.dumps({"id": "B002", "thesis": "T2"}))
lines = seal.seal(cfg, "inv", now=t0)
ms = list((rec / "manifests").glob("*.manifest"))
ok(len(ms) == 1 and (rec / "manifests" / (ms[0].name + ".ots")).exists(), "first seal writes a manifest and its timestamp")
ok(any("4 new or changed" in x for x in lines), "two theses and two books are sealed")
h = hashlib.sha256(parts["theses/T1.md"].encode()).hexdigest()
ok(f"{h}  theses/T1.md" in ms[0].read_text() and (rec / "objects" / h).exists(), "each item's hash is listed and its exact text kept")
ok(seal.seal(cfg, "inv", now=t0 + dt.timedelta(hours=1)) == [], "nothing new: no new manifest")

(topic / "THESES.md").write_text(theses.replace("Argument one.", "Argument one, sharpened."))
(topic / "books/B002.json").unlink()
seal.seal(cfg, "inv", now=t0 + dt.timedelta(days=1))
ms = sorted((rec / "manifests").glob("*.manifest"))
ok(len(ms) == 2 and "books/B002.json" in ms[1].read_text(), "a deleted book stays in every later manifest, so the field can't shrink")

(rec / "manifests/CONFIRM").write_text("")
ok(any("2 seal timestamp" in x for x in seal.seal(cfg, "inv", now=t0 + dt.timedelta(days=2))), "pending timestamps are upgraded once confirmed")
ok(not any("timestamp" in x for x in seal.seal(cfg, "inv", now=t0 + dt.timedelta(days=3))), "confirmed ones aren't upgraded again")

(tmp / "state/books-inv.json").write_text(json.dumps({"books": {"B001": {
    "start": {"date": "2026-11-09", "nav": 1_000_000, "spy": 500},
    "history": [{"date": "2027-11-08", "nav": 1_650_000, "spy": 560}], "end": None}}}))
out = seal.reveal(cfg, "inv", "B001")
files = sorted(p.name for p in (out / "files").iterdir())
ok(files == ["B001.v1.json", "T1.v1.md", "T1.v2.md"], "reveal holds only B001 and its thesis, every sealed version")
ok("Oil" not in "".join((out / "files" / f).read_text() for f in files), "nothing from the other thesis is revealed")
ok(hashlib.sha256((out / "files/T1.v1.md").read_bytes()).hexdigest() == h, "the revealed first version hashes to what was sealed")
ok(all((out / "proofs" / (m.name)).exists() for m in ms[:1]) and list((out / "proofs").glob("*.ots")), "the proving manifests and their .ots files are included")
readme = (out / "README.md").read_text()
ok("2 book spec(s) and 2 thesis(es)" in readme, "the README states the size of the field without revealing it")
ok("+65.0%" in readme and "SPY over the same period +12.0%" in readme, "the README shows the simulated result")

shutil.rmtree(tmp)
print(f"\n{'All passed' if not FAILS else str(len(FAILS)) + ' failed'}")
sys.exit(1 if FAILS else 0)
