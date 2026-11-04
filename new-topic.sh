#!/usr/bin/env bash
# Seed a new topic from the template.
# Usage: ./new-topic.sh <slug> "<Title>"
# Example: ./new-topic.sh garden-plan "Garden plan"
set -euo pipefail

slug="${1:?usage: ./new-topic.sh <slug> \"<Title>\"}"
title="${2:-$slug}"
root="$(cd "$(dirname "$0")" && pwd)"
dest="$root/topics/$slug"
today="$(date +%Y-%m-%d)"

if [[ -e "$dest" ]]; then
  echo "Topic '$slug' already exists at $dest" >&2
  exit 1
fi

mkdir -p "$root/topics"
cp -R "$root/_template" "$dest"

for f in "$dest"/*.md; do
  sed -i.bak -e "s/{{TOPIC_TITLE}}/$title/g" -e "s/{{DATE}}/$today/g" "$f"
  rm -f "$f.bak"
done

echo "Created topics/$slug"
echo "Next: fill in topics/$slug/CHARTER.md and set its priority in _orchestrator/POLICY.md."
echo "The next cycle picks it up. To run it now: bin/cycle.py --only $slug --tier S"
