"""Hostile-repo tests for bin/scanner.py: what must never reach the agent, and what a failure
must look like. Needs network for OSV. Run: python3 tests/test_scanner.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

wt = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())
repo, ws = tmp / "victim", tmp / "ws"
repo.mkdir()
(ws / "topics/sec/outputs/patches").mkdir(parents=True)

FAKE = {
    "aws": "AKIA" + "IOSFODNN7EXAMPLE",
    "gh": "ghp_" + "a1B2" * 9,
    "ant": "sk-ant-" + "api03-abcdefghijklmnopqrstuv",
    "assign": "hunter2hunter2hunter2",
    "envval": "ENVFILEVALUE123456",
    "keybody": "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC",
}
(repo / "app.py").write_text(
    f'AWS = "{FAKE["aws"]}"\nGH = "{FAKE["gh"]}"\nANT = "{FAKE["ant"]}"\n'
    f'password = "{FAKE["assign"]}"\nsafe_line = 1\n')
(repo / ".env").write_text(f"X={FAKE['envval']}\n")
(repo / ".env.example").write_text("X=\n")
(repo / "cert.txt").write_text(
    f"-----BEGIN RSA PRIVATE KEY-----\n{FAKE['keybody']}\n-----END RSA PRIVATE KEY-----\n")
os.symlink("/etc/passwd", repo / "link")
(repo / "blob.bin").write_bytes(b"\0" * 100)
(repo / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": {
    "": {"name": "x"},
    "node_modules/lodash": {"version": "4.17.15"},
    "node_modules/a/node_modules/@b/c": {"version": "1.0.0", "dev": True}}}))
(repo / "pyproject.toml").write_text('[project]\nname="x"\ndependencies=["PyYAML>=6"]\n')
(repo / "bad").mkdir()
(repo / "bad/package-lock.json").write_text("{nope")
(repo / "untracked.txt").write_text("should never appear")
(repo / "docs.md").write_text("Scrub for `-----BEGIN " + "PRIVATE KEY-----` before you push.\n")
(repo / "gcp.json").write_text('{"private_key": "-----BEGIN ' + 'PRIVATE KEY-----\\nMIIEvQIBADANBg\\n"}\n')
(repo / "node_modules").mkdir()
(repo / "node_modules/README.md").write_text('secret: "examplevalue12345"\n')
env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, env={**os.environ, **env})
g("init", "-q")
g("add", "--", "app.py", ".env", ".env.example", "cert.txt", "link", "blob.bin", "package-lock.json", "pyproject.toml", "bad", "docs.md", "gcp.json", "node_modules/README.md")
g("commit", "-qm", "x")

secretfile = tmp / "secretfile"
secretfile.write_text("x")
secretfile.chmod(0o644)
cfgp = tmp / "config.toml"
cfgp.write_text(f"""
[workspace]
root = "{ws}"
[runner]
state_dir = "{tmp}/state"
[agents]
allowed_tools = "Read,Glob,Grep,Edit,Write,Bash(ls *),Bash(mkdir *)"
disallowed_tools = "Agent"
[scanners.sec]
repos = ["{repo}", "{tmp}/does-not-exist"]
host = true
sensitive_paths = ["{secretfile}"]
""")
os.environ["AGENTS_CONFIG"] = str(cfgp)
sys.path.insert(0, str(wt / "bin"))
import cycle
import scanner

cfg = cycle.load_config()
checks = []
def ok(name, cond):
    checks.append(cond)
    print(("PASS " if cond else "FAIL ") + name)

scanner.scan(cfg, "sec")
report = (ws / "topics/sec/SCAN.md").read_text()
snap = scanner.code_dir(cfg, "sec") / "victim"
everything = report + "".join(p.read_text(errors="replace") for p in snap.rglob("*") if p.is_file())
ok("snapshot is not empty", (snap / "app.py").exists() and (snap / "cert.txt").exists())
for label, value in FAKE.items():
    ok(f"secret value absent from snapshot and report: {label}", value not in everything)
ok("prose mention of a key marker isn't a finding", "docs.md" not in report)
ok("key embedded in JSON is caught", "victim/gcp.json:1: private-key" in report and "MIIEvQ" not in (snap / "gcp.json").read_text())
ok("vendored example isn't a finding", "node_modules/README.md" not in report)
ok("vendored example is still redacted", "examplevalue12345" not in (snap / "node_modules/README.md").read_text())
ok("safe line kept", "safe_line = 1" in (snap / "app.py").read_text())
ok(".env withheld", not (snap / ".env").exists())
ok(".env.example kept", (snap / ".env.example").exists())
ok("symlink gone", not (snap / "link").is_symlink() and not (snap / "link").exists())
ok("binary dropped", not (snap / "blob.bin").exists())
ok("untracked excluded", not (snap / "untracked.txt").exists())
ok("no .git in snapshot", not (snap / ".git").exists())
ok("secret locations reported", "victim/app.py:1: aws-access-key" in report and "victim/.env: sensitive file" in report)
ok("real advisory found", "lodash 4.17.15" in report and "GHSA-" in report and "Fixed in" in report)
ok("nested scoped package parsed", any(p["name"] == "@b/c" for p in scanner.read_packages("v", snap)[0]))
ok("unlocked deps declared as unchecked", "no lockfile" in report and "PyYAML" in report)
ok("corrupt lockfile reported", "could not parse" in report)
ok("missing repo is a coverage problem", "does-not-exist: not scanned" in report)
ok("loose file mode reported", "mode 644" in report)
facts = "\n".join(scanner.fact_lines(cfg))
ok("alert in facts", "SCAN ALERT" in facts and "1 coverage problems" in facts)
print(facts)

# Tools: the web can't get back in, whatever [agents] says.
a, d = scanner.sealed_tools({"allowed_tools": "Read,WebSearch,WebFetch,Bash(ls *)", "disallowed_tools": "Agent"})
ok("web tools stripped", a == "Read,Bash(ls *)" and "WebFetch" in d and "WebSearch" in d)

# Patches: good, bad, misnamed. None may change anything.
pdir = ws / "topics/sec/outputs/patches"
(pdir / "victim--good.diff").write_text(
    "--- a/app.py\n+++ b/app.py\n@@ -5 +5 @@\n-safe_line = 1\n+safe_line = 2\n")
(pdir / "victim--bad.diff").write_text(
    "--- a/app.py\n+++ b/app.py\n@@ -5 +5 @@\n-not_there = 1\n+x = 2\n")
(pdir / "nonsense.diff").write_text("x")
(pdir / "victim--escape.diff").write_text(
    "--- a/../../outside\n+++ b/../../outside\n@@ -1 +1 @@\n-a\n+b\n")
v = scanner.check_patches(cfg, "sec")
print(v)
ok("good patch applies", v["victim--good.diff"] == "applies cleanly")
ok("bad patch fails", v["victim--bad.diff"].startswith("does not apply"))
ok("misnamed rejected", v["nonsense.diff"].startswith("rejected"))
ok("escape refused", v["victim--escape.diff"].startswith("does not apply"))
ok("check changes nothing", "safe_line = 1" in (snap / "app.py").read_text())

# Baseline: a failed run keeps alerts; a finished run clears them; a new one re-raises.
first = scanner.after_run(cfg, "sec", "error")
ok("patch verdicts reach the digest", any("victim--good.diff: applies cleanly" in n for n in first))
scanner.scan(cfg, "sec")
ok("failed run keeps alert", "SCAN ALERT" in "\n".join(scanner.fact_lines(cfg)))
notes = scanner.after_run(cfg, "sec", "ok")
ok("second after_run is quiet", scanner.after_run(cfg, "sec", "ok") == [])
scanner.scan(cfg, "sec")
ok("finished run clears alert", "no scan alert" in "\n".join(scanner.fact_lines(cfg)))
(repo / "new.py").write_text(f'K = "{FAKE["gh"]}"\n')
g("add", "new.py")
g("commit", "-qm", "y")
scanner.scan(cfg, "sec")
ok("new secret re-raises alert", "SCAN ALERT, 1 new" in "\n".join(scanner.fact_lines(cfg)))

# Outage: must read as a failure, never as clean.
real = scanner.OSV
scanner.OSV = "http://127.0.0.1:9"
shutil.rmtree(scanner.state_dir(cfg, "sec") / "code")
scanner.scan(cfg, "sec")
report = (ws / "topics/sec/SCAN.md").read_text()
ok("OSV outage is a coverage problem", "OSV check failed" in report)
scanner.OSV = real
rows = [l for l in report.splitlines() if l.startswith("| 20")]
ok("cadence history grows, newest first", len(rows) >= 4)

shutil.rmtree(tmp)
print(f"\n{sum(checks)}/{len(checks)} passed")
sys.exit(0 if all(checks) else 1)
