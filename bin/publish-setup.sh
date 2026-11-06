#!/usr/bin/env bash
# One-time setup for publishing a topic's record to a public GitHub repo (see bin/publish.py).
# Creates the repo, a deploy key that can write to that repo only, and a pinned GitHub host key.
# Usage: bin/publish-setup.sh <owner>/<repo> ["description"]
# Needs the GitHub CLI, logged in (gh auth login). Run it yourself: it makes a public repo.
set -euo pipefail
repo="${1:?usage: bin/publish-setup.sh <owner>/<repo> [\"description\"]}"
desc="${2:-Investment theses written by an AI agent and timestamped before any money went in. Part of Cobbler.}"
root="$(cd "$(dirname "$0")/.." && pwd)"
state="$(python3 -c 'import sys, tomllib, pathlib; c = tomllib.load(open(sys.argv[1], "rb")); print(pathlib.Path(c["runner"]["state_dir"]).expanduser())' "$root/config.toml")"
dir="$state/publish"
mkdir -p "$dir" && chmod 700 "$dir"

if gh repo view "$repo" >/dev/null 2>&1; then
  echo "  ok    $repo exists"
else
  gh repo create "$repo" --public --description "$desc" >/dev/null && echo "  ok    created public repo $repo"
fi

if [[ ! -f "$dir/deploy_key" ]]; then
  ssh-keygen -q -t ed25519 -N "" -C "cobbler-publisher ($repo only)" -f "$dir/deploy_key"
fi
if gh repo deploy-key list --repo "$repo" 2>/dev/null | grep -q "Cobbler publisher"; then
  echo "  ok    deploy key already on $repo"
else
  gh repo deploy-key add "$dir/deploy_key.pub" --repo "$repo" --allow-write --title "Cobbler publisher" >/dev/null
  echo "  ok    deploy key added (write access to $repo only)"
fi

# Pin GitHub's host key, checked against the fingerprint GitHub publishes over HTTPS.
want="$(gh api meta --jq .ssh_key_fingerprints.SHA256_ED25519)"
ssh-keyscan -t ed25519 github.com 2>/dev/null > "$dir/known_hosts.new"
got="$(ssh-keygen -lf "$dir/known_hosts.new" | awk '{print $2}' | sed 's/^SHA256://')"
[[ -n "$want" && "$got" == "$want" ]] || { rm -f "$dir/known_hosts.new"; echo "GitHub host key didn't match its published fingerprint; stopping." >&2; exit 1; }
mv "$dir/known_hosts.new" "$dir/known_hosts" && echo "  ok    GitHub host key pinned (SHA256:$got)"

GIT_SSH_COMMAND="ssh -i $dir/deploy_key -o IdentitiesOnly=yes -o UserKnownHostsFile=$dir/known_hosts -o StrictHostKeyChecking=yes -o BatchMode=yes" \
  git ls-remote "git@github.com:$repo.git" >/dev/null && echo "  ok    the deploy key can reach $repo"
echo "Done. Add [publish.<topic>] with repo = \"$repo\" to config.toml (see config.example.toml)."
