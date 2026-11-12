#!/usr/bin/env python3
"""Tests for the digest the runner assembles. Run: python3 tests/test_digest.py"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import cycle  # noqa: E402

FAILS = []


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


PITCH = """# Topic · Pitch

Status: active

## Delivered last run
What changed.

- Wrote the first draft.
  - A nested detail that shouldn't appear.
- Checked the numbers against primary data.

## Next step
- Score the prediction.
- Draft a second idea.
- Review the first.
- A fourth item.

## Ask
Tier: M
- Not a next step.
"""
ok(cycle.pitch_bullets(PITCH, "Delivered last run") == ["Wrote the first draft.", "Checked the numbers against primary data."],
   "top-level bullets only, from the right section")
nxt = cycle.pitch_bullets(PITCH, "Next step")
ok(len(nxt) == 4 and nxt[-1] == "…and 1 more" and "Not a next step." not in nxt, "long lists are capped and stop at the next section")
ok(cycle.pitch_bullets("- x " + "y" * 300, "Next step") == [], "a missing section gives nothing")
ok(len(cycle.pitch_bullets("## Next step\n- " + "y" * 300 + "\n", "Next step")[0]) <= 150, "long bullets are shortened")

tmp = Path(tempfile.mkdtemp())
(tmp / "topics/alpha").mkdir(parents=True)
(tmp / "topics/alpha/PITCH.md").write_text(PITCH)
(tmp / "_orchestrator/digests").mkdir(parents=True)
cfg = {"root": tmp}
run = {"topic": "alpha", "tier": "M", "num_turns": 30, "cap": 35, "outcome": "ok"}
prog = cycle.progress(cfg, [run])
ok(prog[0] == "ALPHA · M · 30/35 turns · ✅" and "Did:" in prog and "Next, if selected:" in prog, "progress shows measured turns, what was done, and what's next")
ok(any("earlier run" in x for x in cycle.progress(cfg, [{**run, "outcome": "hit_ceiling"}])), "a run that failed warns its pitch may be stale")
ok(cycle.progress(cfg, []) == ["No agents ran."], "no agents is said plainly")

plan = {"proposals": [], "selection": ["alpha · M · 2 credits: closest to done"], "not_picked": ["beta: not before Nov 10"], "needs_you": ["beta: pick a layout, blocking the draft"]}
d = cycle.build_digest(plan, prog, "Credits: 3/20 used this week.", ["alpha B001: open filled at the open"])
order = [d.index(x) for x in ("PROPOSALS", "SELECTION", "PROGRESS", "NEEDS YOU", "RUNNER")]
ok(order == sorted(order), "sections come in order: proposals, selection, progress, needs you, runner")
ok("- None." in d and "Not picked:\n- beta" in d and "Credits: 3/20" in d, "empty proposals say so; not-picked and credits are under selection")
ok("RUNNER" not in cycle.build_digest(plan, prog, "c", []), "the runner section appears only when it has something")
ok("NEEDS YOU" not in cycle.build_digest({**plan, "needs_you": []}, prog, "c", []), "needs you appears only when it has something")
u = cycle.build_digest({**plan, "needs_you": ["URGENT beta: key exposed"]}, prog, "c", [])
ok(u.startswith("🙋 NEEDS YOU"), "an urgent item leads the digest")
ok(cycle.pitch_bullets("## Next step\n- see `books/B001.json`\n", "Next step") == ["see books/B001.json"], "backticks are stripped for phones")
ok(any("⚠️ hit_ceiling" in x for x in cycle.progress(cfg, [{**run, "outcome": "hit_ceiling"}])), "a failed run shows a warning and its outcome")
big = "x\n" * 5000
ok(len(cycle.fit(big).encode()) < 4096 and "cut short" in cycle.fit(big), "a long digest stays under ntfy's attachment limit")
ok(cycle.fit("short") == "short", "a short digest is untouched")
ok(len(cycle.fit("é\n" * 3000).encode()) < 4096, "the cut is safe for multi-byte text")
cycle.save_digest(cfg, "morning\n")
cycle.save_digest(cfg, "evening\n")
text = next((tmp / "_orchestrator/digests").glob("*.md")).read_text()
ok("morning" in text and "evening" in text, "two cycles in one day both survive")
shutil.rmtree(tmp)

print(f"\n{'All passed' if not FAILS else str(len(FAILS)) + ' failed'}")
sys.exit(1 if FAILS else 0)
