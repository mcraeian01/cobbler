#!/usr/bin/env python3
"""Release engine changes from this instance to the public template, carefully.

Work happens here, in the instance. Now and then the owner asks for a release: this copies only
the engine (an allow-list of paths) into a working copy of the public template, resets seed files
to their blank headers, and refuses if anything matches the private register. Nothing is pushed
unless asked.

The private register is `private.toml` at the instance root. It is never released. It lists:
  [private] paths   never released, even if an engine pattern matches them
  [private] words   words that must not appear in released files (case-insensitive, whole word)
  [allow]           per-file exceptions, e.g. LICENSE may name the copyright holder
Secrets from the env file, key and token patterns, home paths and the hostname are always checked.

  release.py              stage and scan; show what would change in the template; change nothing
  release.py --commit -m "message"   ...then copy into the template working copy and commit
  release.py --push       push the template's committed releases to GitHub
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import publish  # noqa: E402  (secrets_in)

# What the engine is. Anything not matched here never leaves the instance.
ENGINE = [
    "bin/*", "tests/*", "_global/*", "_template/*", "_template/outputs/.gitkeep", "examples/**",
    "deploy/*", "docs/*", "video/*", "video/src/*", "setup.sh", "new-topic.sh", "README.md",
    "LICENSE", "config.example.toml", ".gitignore", "topics/.gitkeep",
    "_orchestrator/ORCHESTRATOR_PROMPT.md", "_orchestrator/ORCHESTRATOR_PROTOCOL.md",
    "_orchestrator/WAKE_MESSAGE.md", "_orchestrator/digests/.gitkeep",
]
# Released as blank seeds: the header only, never this instance's entries.
SEEDS = ["_orchestrator/LEDGER.md", "_orchestrator/PROPOSALS.md", "_orchestrator/FEEDBACK.md",
         "_orchestrator/POLICY.md"]
ALWAYS_PRIVATE = ["topics/*/**", "_records/**", "config.toml", "private.toml", "CLAUDE.md", "share/**",
                  ".claude/**", "_orchestrator/USAGE.md", "_orchestrator/digests/*.md", "_inbox/**",
                  "_private/**"]


def run(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def load_register() -> dict:
    p = ROOT / "private.toml"
    if not p.exists():
        sys.exit("private.toml is missing: the release has nothing to check against. Create it first.")
    return tomllib.load(open(p, "rb"))


def matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pat) or (pat.endswith("/**") and path.startswith(pat[:-3] + "/"))
               for pat in patterns)


def leaks(text: str, allowed: list[str]) -> list[str]:
    """Every secret-like thing in text. Pattern hits can be allowed per file by name (e.g. a test's
    fake key); real values from the env file, home paths and the hostname never can."""
    import os
    import socket
    found = [what for pat, what in publish.SECRET_PATTERNS if pat.search(text) and what not in allowed]
    for k, v in os.environ.items():
        if re.search(r"TOKEN|KEY|SECRET|NTFY|PASSWORD", k) and len(v) >= 8 and v in text:
            found.append(f"the real value of {k}")
    if str(Path.home()) + "/" in text:
        found.append("a home folder path")
    host = socket.gethostname()
    if len(host) >= 4 and re.search(rf"\b{re.escape(host)}\b", text, re.I):
        found.append("this machine's hostname")
    return found


def blank_seed(path: str, text: str) -> str:
    """The seed with this instance's entries removed: everything from the first `## ` heading
    outside a code block (ledger cycles, proposals, feedback per topic), and the priority rows."""
    if path == "_orchestrator/POLICY.md":
        return re.sub(r"(\| Topic \| Priority \|\n\|---\|---\|\n)(\|.*\|\n)+", r"\1| | |\n", text)
    out, fenced = [], False
    for line in text.split("\n"):
        if line.startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            break
        out.append(line)
    return "\n".join(out).rstrip("\n") + "\n"


def discovered_private() -> list[str]:
    """Private words found automatically, so nobody has to remember to register them: every
    thesis title in this instance's topics, and every Alpaca account number and login."""
    found = []
    for f in ROOT.glob("topics/*/theses/*.md"):
        head = f.read_text().split("\n", 1)[0]
        if "·" in head:
            title = head.split("·", 1)[1].strip()
            if len(title) >= 12:
                found.append(title)
    try:
        import live
        acc = live.ACCOUNTS_FILE
        if acc.exists():
            for a in tomllib.load(open(acc, "rb")).values():
                if isinstance(a, dict):
                    found += [v for k, v in a.items() if k in ("number", "login") and isinstance(v, str) and len(v) >= 6]
    except Exception:
        pass
    return found


def stage(reg: dict) -> tuple[Path, list[str], list[str]]:
    """Copy the committed engine into a temp dir. Returns (dir, files, problems)."""
    private = ALWAYS_PRIVATE + reg.get("private", {}).get("paths", [])
    tracked = run(["git", "ls-files"], cwd=ROOT).stdout.split()
    dirty = run(["git", "status", "--porcelain"], cwd=ROOT).stdout
    out = Path(tempfile.mkdtemp(prefix="cobbler-release-"))
    files, problems = [], []
    if any(l[3:].strip() for l in dirty.splitlines() if matches(l[3:].strip(), ENGINE + list(SEEDS))):
        problems.append("engine files have uncommitted changes: commit them first (a release ships committed work only)")
    words = [w for w in reg.get("private", {}).get("words", []) if w.strip()] + discovered_private()
    allow = reg.get("allow", {})
    for f in tracked:
        if matches(f, private) or not (matches(f, ENGINE) or f in SEEDS):
            continue
        data = run(["git", "show", f"HEAD:{f}"], cwd=ROOT).stdout if not f.endswith((".mp4", ".gif")) else None
        dst = out / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        if data is None:
            dst.write_bytes(subprocess.run(["git", "show", f"HEAD:{f}"], cwd=ROOT, capture_output=True, check=True).stdout)
        else:
            if f in SEEDS:
                data = blank_seed(f, data)
            dst.write_text(data)
            ok_words = [w.lower() for w in allow.get(f, [])]
            for w in words:
                if w.lower() in ok_words:
                    continue
                for n, line in enumerate(data.splitlines(), 1):
                    if re.search(rf"(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])", line, re.I):
                        problems.append(f"{f}:{n}: private word '{w}': {line.strip()[:100]}")
            problems += [f"{f}: contains {what}" for what in leaks(data, allow.get(f, []))]
        files.append(f)
    return out, files, problems


def template_dir(reg: dict) -> Path:
    return Path(reg.get("release", {}).get("template", "~/workspace/cobbler")).expanduser()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("-m", "--message")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    reg = load_register()
    tpl = template_dir(reg)
    if a.push:
        r = run(["git", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
                 "push", "origin", "main"], cwd=tpl, check=False)
        print(r.stdout + r.stderr or "pushed")
        sys.exit(r.returncode)

    staged, files, problems = stage(reg)
    try:
        run(["git", "pull", "-q", "--ff-only"], cwd=tpl, check=False)
        released = set(run(["git", "ls-files"], cwd=tpl).stdout.split())
        added = sorted(set(files) - released)
        removed = sorted(f for f in released - set(files) if matches(f, ENGINE) or f in SEEDS)
        changed = sorted(f for f in set(files) & released if (staged / f).read_bytes() != (tpl / f).read_bytes())
        print(f"Engine files: {len(files)}. Against the template at {tpl}:")
        for label, group in (("new", added), ("changed", changed), ("removed", removed)):
            for f in group:
                print(f"  {label:8} {f}")
        if not (added or changed or removed):
            print("  nothing to release")
        if problems:
            print("\nBlocked: fix these first (or add a reviewed exception to private.toml [allow]):")
            for p in problems:
                print(f"  {p}")
            sys.exit(1)
        if not a.commit:
            print("\nScan clean. Nothing was changed. Run with --commit -m \"...\" to commit this release to the template.")
            return
        if not a.message:
            sys.exit("--commit needs -m \"message\"")
        if run(["git", "status", "--porcelain"], cwd=tpl).stdout.strip():
            sys.exit(f"{tpl} has uncommitted changes; sort those out first")
        for f in added + changed:
            (tpl / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staged / f, tpl / f)
        for f in removed:
            (tpl / f).unlink()
        run(["git", "add", "-A"], cwd=tpl)
        run(["git", "commit", "-q", "-m", a.message], cwd=tpl)
        print(f"\nCommitted to {tpl}: {run(['git', 'log', '--oneline', '-1'], cwd=tpl).stdout.strip()}. Not pushed.")
    finally:
        shutil.rmtree(staged, ignore_errors=True)


if __name__ == "__main__":
    main()
