#!/usr/bin/env python3
"""Tests for bin/release.py's privacy checks. Run: python3 tests/test_release.py"""
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import release  # noqa: E402

FAILS = []


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


ledger = "# Ledger\n\nAppend only.\n\n```\n## YYYY-MM-DD HH:MM · cycle\nCredits at start: n/pool\n```\n\nA note after the example.\n\n## 2026-11-05 04:46 · daily cycle\nGranted: secret-topic M (2)\n"
out = release.blank_seed("_orchestrator/LEDGER.md", ledger)
ok("A note after the example." in out and "## YYYY-MM-DD" in out, "a seed keeps its header, example block and notes")
ok("secret-topic" not in out and "2026-11-05" not in out, "a seed loses every real entry")
policy = "| Topic | Priority |\n|---|---|\n| investor | 3 |\n| security | 3 |\n\n## Fairness\n"
out = release.blank_seed("_orchestrator/POLICY.md", policy)
ok("investor" not in out and "| | |" in out and "## Fairness" in out, "policy priorities are emptied, the rest kept")

ok(release.leaks("see sk-ant-oat01-abcdefghijk", []) == ["a Claude token"], "a token pattern is reported")
ok(release.leaks("see sk-ant-oat01-abcdefghijk", ["a Claude token"]) == [], "a reviewed exception allows a fake one")
os.environ["SOME_SECRET_KEY"] = "real-value-9f8e7d6c"
ok("the real value of SOME_SECRET_KEY" in release.leaks("x real-value-9f8e7d6c y", ["a Claude token"]), "a real secret value can never be allowed")
both = release.leaks("fake sk-ant-oat01-abcdefghijk and real-value-9f8e7d6c", ["a Claude token"])
ok(both == ["the real value of SOME_SECRET_KEY"], "an allowed example can't hide a real secret in the same file")
ok("a home folder path" in release.leaks(f"{Path.home()}/notes", []), "home paths are reported")
host = socket.gethostname()
ok(len(host) < 4 or "this machine's hostname" in release.leaks(f"runs on {host}", []), "the hostname is reported")
ok(release.matches("topics/investor/THESES.md", release.ALWAYS_PRIVATE) and release.matches("_records/x/objects/ab", release.ALWAYS_PRIVATE)
   and release.matches("private.toml", release.ALWAYS_PRIVATE) and release.matches("CLAUDE.md", release.ALWAYS_PRIVATE),
   "topics, records, the register and CLAUDE.md are always private")
ok(not release.matches("config.toml", release.ENGINE) and release.matches("bin/cycle.py", release.ENGINE), "config.toml isn't engine; the runner is")

print(f"\n{'All passed' if not FAILS else str(len(FAILS)) + ' failed'}")
sys.exit(1 if FAILS else 0)
