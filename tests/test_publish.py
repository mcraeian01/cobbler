#!/usr/bin/env python3
"""Tests for bin/publish.py with a local bare repo and a fake ots client. Run: python3 tests/test_publish.py"""
import datetime as dt
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import publish  # noqa: E402

FAILS = []


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


tmp = Path(tempfile.mkdtemp())
remote = tmp / "remote.git"
subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
topic = tmp / "root/topics/inv"
(topic / "books").mkdir(parents=True)
(tmp / "state").mkdir()
fake_ots = tmp / "ots"
fake_ots.write_text('#!/bin/sh\n'
                    'if [ "$1" = stamp ]; then echo pending > "$2.ots"; echo "Submitting"; fi\n'
                    'if [ "$1" = upgrade ]; then if [ -f "$(dirname "$2")/../CONFIRM" ]; then echo bitcoin > "$2"; echo "Success! Timestamp complete"; else echo "Pending"; fi; fi\n')
fake_ots.chmod(0o755)
cfg = {"root": tmp / "root", "state": tmp / "state",
       "publish": {"inv": {"repo": "me/inv", "remote": str(remote), "files": ["THESES.md", "BOOKS.md", "books/*.json"],
                           "ots": str(fake_ots)}}}
clone = lambda: (shutil.rmtree(tmp / "check", ignore_errors=True),
                 subprocess.run(["git", "clone", "-q", str(remote), str(tmp / "check")], check=True), tmp / "check")[2]
t0 = dt.datetime(2026, 11, 6, 9, 50, tzinfo=dt.timezone.utc)

(topic / "THESES.md").write_text("# Theses\n## T1\nRates fall.\n")
(topic / "books/B001.json").write_text('{"id": "B001", "thesis": "T1"}\n')
(topic / "BOOKS.md").write_text("# Books\n")
(topic / "STATE.md").write_text("private notes\n")
lines = publish.publish(cfg, "inv", now=t0)
c = clone()
ok(any("published 3 file" in x for x in lines), "first publish sends the three chosen files")
ok((c / "THESES.md").exists() and (c / "books/B001.json").exists() and not (c / "STATE.md").exists(), "only the chosen files go out, never STATE.md")
man = list((c / "stamps").glob("*.manifest"))
ok(len(man) == 1 and (c / "stamps" / (man[0].name + ".ots")).exists(), "a manifest and its timestamp are published")
import hashlib
h = hashlib.sha256((c / "THESES.md").read_bytes()).hexdigest()
ok(f"{h}  THESES.md" in man[0].read_text(), "the manifest holds each file's SHA-256")
ok("How to check a claim" in (c / "README.md").read_text(), "a README explains how to verify")

ok(publish.publish(cfg, "inv", now=t0 + dt.timedelta(hours=1)) == [], "nothing new means no commit")

(topic / "books/B001.json").unlink()
(topic / "books/B002.json").write_text('{"id": "B002", "thesis": "T2"}\n')
publish.publish(cfg, "inv", now=t0 + dt.timedelta(hours=2))
c = clone()
ok((c / "books/B001.json").exists() and (c / "books/B002.json").exists(), "append only: a removed spec stays published")
ok(len(list((c / "stamps").glob("*.manifest"))) == 2, "each publish adds its own stamped manifest")

os.environ["ALPACA_SECRET_KEY"] = "s3cr3t-value-123"
(topic / "THESES.md").write_text("# Theses\nleaked s3cr3t-value-123 here\n")
lines = publish.publish(cfg, "inv", now=t0 + dt.timedelta(hours=3))
c = clone()
ok(lines and "blocked" in lines[0] and "ALPACA_SECRET_KEY" in lines[0], "a file holding an env secret blocks the publish")
ok("leaked" not in (c / "THESES.md").read_text(), "and nothing from it reached the public repo")
(topic / "THESES.md").write_text("# Theses\nsee sk-ant-oat01-abcdefghijkl\n")
ok("Claude token" in publish.publish(cfg, "inv", now=t0 + dt.timedelta(hours=4))[0], "a token pattern blocks it too")
(topic / "THESES.md").write_text(f"# Theses\npath {Path.home()}/x\n")
ok("home folder" in publish.publish(cfg, "inv", now=t0 + dt.timedelta(hours=5))[0], "a home path blocks it too")
(topic / "THESES.md").write_text("# Theses\n## T1\nRates fall.\n")

(tmp / "state/publish/inv/CONFIRM").write_text("")
lines = publish.publish(cfg, "inv", now=t0 + dt.timedelta(hours=6))
c = clone()
log = subprocess.run(["git", "log", "--format=%s", "-1"], cwd=c, capture_output=True, text=True).stdout
ok("confirmed in the Bitcoin blockchain" in log, "pending timestamps are upgraded and committed once confirmed")
ok(all(f.read_text().strip() == "bitcoin" for f in (c / "stamps").glob("*.ots")), "every stamp now carries the confirmation")
ok(publish.publish(cfg, "inv", now=t0 + dt.timedelta(hours=7)) == [], "a confirmed stamp isn't upgraded again")

shutil.rmtree(tmp)
print(f"\n{'All passed' if not FAILS else str(len(FAILS)) + ' failed'}")
sys.exit(1 if FAILS else 0)
