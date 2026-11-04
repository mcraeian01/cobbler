#!/usr/bin/env bash
# One-time setup for Cobbler on a Linux machine that stays on overnight.
# Checks prerequisites, writes the private env file, and installs the systemd timers
# using the times in config.toml's [schedule]. Safe to re-run after changing the schedule.
#
# Usage: ./setup.sh            check, write env, install timers
#        ./setup.sh --check    only check prerequisites and show the timers it would install
set -euo pipefail

root="$(cd "$(dirname "$0")" && pwd)"
envdir="$HOME/.config/agent-runner"
envfile="$envdir/env"
unitdir="$HOME/.config/systemd/user"
check_only=false
[[ "${1:-}" == "--check" ]] && check_only=true

ok()   { printf '  ok    %s\n' "$1"; }
bad()  { printf '  MISSING %s\n' "$1"; missing=1; }
missing=0

echo "Checking prerequisites"
[[ "$(uname -s)" == "Linux" ]] && ok "Linux" || bad "Linux (bubblewrap and systemd are Linux-only; see README → Mac and Windows)"
command -v claude >/dev/null && ok "Claude Code: $(claude --version 2>/dev/null | head -1)" \
  || bad "Claude Code CLI: curl -fsSL https://claude.ai/install.sh | bash"
command -v bwrap >/dev/null && ok "bubblewrap" || bad "bubblewrap: sudo apt install bubblewrap"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null \
  && ok "Python $(python3 -c 'import platform; print(platform.python_version())')" || bad "Python 3.11+"
command -v systemctl >/dev/null && ok "systemd" || bad "systemd"
if command -v loginctl >/dev/null && [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" == "yes" ]]; then
  ok "lingering (timers fire while logged out)"
else
  echo "  note  lingering is off, so timers only fire while you're logged in. Fix: sudo loginctl enable-linger $USER"
fi
[[ -f "$root/config.toml" ]] || cp "$root/config.example.toml" "$root/config.toml"
(( missing )) && { echo "Install what's missing, then re-run."; exit 1; }

# Times come from config.toml so the timers and the runner's cycle count always agree.
read -r daily_time weekly_day weekly_time < <(python3 - "$root/config.toml" <<'PY'
import sys, tomllib
s = tomllib.load(open(sys.argv[1], "rb")).get("schedule", {})
days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
print(s.get("daily_time", "04:40"), days[s.get("weekly_weekday", 2)], s.get("weekly_time", "20:10"))
PY
)

render() {
  sed -e "s|@ROOT@|$root|g" -e "s|@DAILY_TIME@|$daily_time|g" \
      -e "s|@WEEKLY_DAY@|$weekly_day|g" -e "s|@WEEKLY_TIME@|$weekly_time|g" "$root/deploy/$1"
}

echo "Timers (local time, $(timedatectl show -p Timezone --value 2>/dev/null || echo system zone)):"
echo "  daily   every day at $daily_time"
echo "  weekly  $weekly_day at $weekly_time (should be the evening before your weekly usage reset)"
$check_only && { echo "Check only: nothing installed."; exit 0; }

# The agents and orchestrator call you by the name in config.toml ([workspace] owner).
owner="$(python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb")).get("workspace", {}).get("owner", ""))' "$root/config.toml")"
if [[ -z "$owner" ]]; then
  read -rp "Your first name (the agents and digests will use it): " owner
  [[ "$owner" =~ ^[A-Za-z][A-Za-z\ \'-]*$ ]] || { echo "Letters, spaces, apostrophes or hyphens only."; exit 1; }
  python3 - "$root/config.toml" "$owner" <<'PY'
import re, sys
p, name = sys.argv[1], sys.argv[2]
s = open(p).read()
s, n = re.subn(r'(?m)^owner = ""', 'owner = "%s"' % name.replace('"', ''), s, count=1)
if not n:
    s = s.replace("[workspace]\n", '[workspace]\nowner = "%s"\n' % name, 1)
open(p, "w").write(s)
PY
  ok "config.toml: the agents will call you $owner"
else
  ok "owner: $owner"
fi

# Private env file: token and ntfy topic. Never inside the workspace.
mkdir -p "$envdir" && chmod 700 "$envdir"
if [[ -f "$envfile" ]] && ! grep -q 'paste-token-here' "$envfile"; then
  ok "env file exists at $envfile (left as is)"
else
  echo
  echo "Run 'claude setup-token' in another terminal, then paste the token here (input hidden)."
  read -rsp "Token: " token; echo
  [[ "$token" == sk-ant-* ]] || { echo "That doesn't look like a Claude token."; exit 1; }
  default_topic="https://ntfy.sh/cobbler-$(python3 -c 'import secrets; print(secrets.token_hex(12))')"
  read -rp "ntfy URL for the digest [$default_topic]: " ntfy
  umask 077
  printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\nNTFY_URL=%s\n' "$token" "${ntfy:-$default_topic}" > "$envfile"
  chmod 600 "$envfile"
  ok "wrote $envfile. Subscribe to ${ntfy:-$default_topic} in the ntfy phone app."
fi

mkdir -p "$unitdir"
for u in agents-cycle@.service agents-daily.timer agents-weekly.timer; do
  render "$u" > "$unitdir/$u"
done
systemctl --user daemon-reload
systemctl --user enable --now agents-daily.timer agents-weekly.timer
echo
systemctl --user list-timers 'agents-*' --no-pager
echo
echo "Done. Next: ./new-topic.sh <slug> \"<Title>\", fill in its CHARTER.md, then try"
echo "  set -a; . $envfile; set +a; bin/cycle.py --only <slug> --tier S"
