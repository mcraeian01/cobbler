#!/bin/sh
# Read-only security posture of this computer, for a Cobbler security topic.
#
# Run it on your other machines. It reads settings, changes nothing, needs no root, and opens
# no connection to anything. It writes one small report, which reaches the runner through
# whatever already syncs the folder. The runner holds no key to this machine.
#
# Install a COPY outside the synced folder and run that:
#   cp bin/posture.sh ~/.local/bin/cobbler-posture
# Never schedule the synced file itself: then anything able to write to the folder could run
# code on this machine. Re-copy by hand, after reading what changed.
#
# Usage: posture.sh NAME OUTDIR     e.g. cobbler-posture mac ~/path/to/your/instance/_inbox/machines
set -u
PATH=/usr/sbin:/usr/bin:/sbin:/bin:/usr/libexec:$PATH

name=${1:?usage: posture.sh NAME OUTDIR}
out=${2:?usage: posture.sh NAME OUTDIR}
case "$name" in
  *[!a-z0-9-]*) echo "NAME must be lowercase letters, digits and hyphens" >&2; exit 1 ;;
esac
[ -d "$out" ] || { echo "no such folder: $out" >&2; exit 1; }

disk_encryption=unknown firewall=unknown auto_updates=unknown gatekeeper=n/a sip=n/a
remote_login=unknown pending_updates=unknown listening_tcp=unknown

case "$(uname -s)" in
Darwin)
  os="macOS $(sw_vers -productVersion 2>/dev/null)"
  case "$(fdesetup status 2>/dev/null)" in
    *"is On"*) disk_encryption=on ;; *"is Off"*) disk_encryption=off ;;
  esac
  case "$(/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null)" in
    *enabled*) firewall=on ;; *disabled*) firewall=off ;;
  esac
  case "$(defaults read /Library/Preferences/com.apple.SoftwareUpdate AutomaticCheckEnabled 2>/dev/null)" in
    1) auto_updates=on ;; 0) auto_updates=off ;;
  esac
  case "$(spctl --status 2>/dev/null)" in
    *enabled*) gatekeeper=on ;; *disabled*) gatekeeper=off ;; *) gatekeeper=unknown ;;
  esac
  case "$(csrutil status 2>/dev/null)" in
    *enabled*) sip=on ;; *disabled*) sip=off ;; *) sip=unknown ;;
  esac
  if launchctl print system/com.openssh.sshd >/dev/null 2>&1; then remote_login=on; else remote_login=off; fi
  pending_updates=$(softwareupdate -l 2>/dev/null | grep -c 'Label:')
  listening_tcp=$(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | awk 'NR>1 && $9 !~ /^(127\.|\[::1\])/ {n=split($9,a,":"); print a[n]}' | sort -un | paste -sd, -)
  ;;
Linux)
  os=$(. /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-Linux}")
  if lsblk -rno TYPE 2>/dev/null | grep -q crypt; then disk_encryption=on
  elif command -v lsblk >/dev/null; then disk_encryption=off; fi
  # The config, not the service: ufw.service reads "active" even while disabled.
  if [ -r /etc/ufw/ufw.conf ]; then
    if grep -q '^ENABLED=yes' /etc/ufw/ufw.conf; then firewall=on; else firewall=off; fi
  elif systemctl is-active -q firewalld 2>/dev/null; then firewall=on
  elif command -v systemctl >/dev/null; then firewall=off; fi
  if systemctl is-enabled -q unattended-upgrades 2>/dev/null; then auto_updates=on
  elif command -v systemctl >/dev/null; then auto_updates=off; fi
  if systemctl is-active -q ssh 2>/dev/null || systemctl is-active -q sshd 2>/dev/null; then remote_login=on
  elif command -v systemctl >/dev/null; then remote_login=off; fi
  command -v apt >/dev/null && pending_updates=$(apt list --upgradable 2>/dev/null | grep -c -- '-security')
  listening_tcp=$(ss -Htln 2>/dev/null | awk '$4 !~ /^(127\.|\[::1\])/ {n=split($4,a,":"); print a[n]}' | sort -un | paste -sd, -)
  ;;
*)
  os=$(uname -s)
  ;;
esac

tmp="$out/.$name.tmp"
{
  echo "# Machine posture: $name"
  echo
  echo "- collected: $(date +%Y-%m-%d)"
  echo "- os: $os"
  echo "- disk_encryption: $disk_encryption"
  echo "- firewall: $firewall"
  echo "- auto_updates: $auto_updates"
  echo "- gatekeeper: $gatekeeper"
  echo "- sip: $sip"
  echo "- remote_login: $remote_login"
  echo "- pending_updates: $pending_updates"
  echo "- listening_tcp: ${listening_tcp:-none}"
} > "$tmp" && mv "$tmp" "$out/$name.md"
echo "wrote $out/$name.md"
