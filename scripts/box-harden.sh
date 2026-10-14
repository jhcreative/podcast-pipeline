#!/usr/bin/env bash
# box-harden.sh — HOST-01 lane B: harden a fresh OVH VPS. Run it as:  scripts/box-harden.sh --no-docker <ipv4>
# Vendored whole from jhc-agency skills/infrastructure/ovhcloud/harden-ovh-box.sh at d2d7d46 (Orly · Oliver, 2026-10-12:
# --no-docker for this pipeline, which runs no containers; the gates count with grep -c, not echo | grep -q under
# pipefail). V-4: this repo carries its own copy. Refresh it from jhc-agency, never edit it here. The original follows.
#
# harden-ovh-box.sh — deploy-tailor-vps Routine 1 (the old deploy-hetzner steps 2a–2e + 3) for an OVHcloud VPS, as one
# idempotent command with a gate after every phase. HOST-01 ran these by hand on 2026-09-28;
# this is that run, made repeatable (deterministic scaffolding: agents enter on content only).
#
# What is different from Hetzner, and why each line below exists:
#   1. OVH's Ubuntu image logs in as `ubuntu` (key-only, passwordless sudo), not root. The deploy
#      user's authorized_keys is copied from /home/ubuntu/.ssh.
#   2. The image ships sshd_config.d/50-cloud-init.conf + 60-cloudimg-settings.conf. sshd takes the
#      FIRST value it reads and the drop-ins load before the main file, so a `sed` on sshd_config
#      alone is not authoritative. We write 10-jhc-hardening.conf, which sorts first, and gate on
#      `sshd -T` — the effective config, not the file we hoped wins.
#   3. A fresh image runs unattended-upgrades in its first minutes and holds the dpkg lock; every
#      apt-get here waits up to 10 min for it (DPkg::Lock::Timeout) instead of failing (2026-09-28).
#   4. OVH delivered Ubuntu 26.04 when 24.04 was ordered. Phase 0 refuses anything but 24.04;
#      fix it with `ovh-vps.py rebuild <service> --image "Ubuntu 24.04"`, not by pressing on.
#
# Usage:   harden-ovh-box.sh [--no-docker] <ipv4> [ssh-pubkey]      (default key ~/.ssh/id_ed25519.pub)
#   --no-docker  skip phase 3: for a box that runs no containers (the RockstarPodcast pipeline,
#                HOST-01 2026-10-12). Every dependency earns its place; an unused daemon is attack surface.
# Safe to re-run. It reboots only if apt left /var/run/reboot-required.
# NOT here: GHCR login and /srv/<slug> (first-deploy.md A2/A3), the OVH edge firewall
# (`ovh-vps.py firewall-web`), reverse DNS (`ovh-vps.py rdns`).
set -euo pipefail

DOCKER=1
if [ "${1:-}" = "--no-docker" ]; then DOCKER=0; shift; fi
IP="${1:?usage: harden-ovh-box.sh [--no-docker] <ipv4> [ssh-pubkey]}"
PUB="${2:-$HOME/.ssh/id_ed25519.pub}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
gate() { echo "GATE FAILED: $*" >&2; exit 1; }

# Which user gets us in? A fresh box: ubuntu. A re-run after this script: deploy (ubuntu still works).
if "${SSH[@]}" "ubuntu@$IP" true 2>/dev/null; then ADMIN="ubuntu@$IP"
elif "${SSH[@]}" "deploy@$IP" true 2>/dev/null; then ADMIN="deploy@$IP"
else gate "neither ubuntu@ nor deploy@$IP accepts key $(basename "$PUB") — was it passed to the rebuild?"; fi
echo "-- connecting as $ADMIN"

echo "-- 0: image"
os="$("${SSH[@]}" "$ADMIN" '. /etc/os-release && echo "$VERSION_ID"')"
[ "$os" = "24.04" ] || gate "Ubuntu $os, not 24.04 — run: ovh-vps.py rebuild <service> --image \"Ubuntu 24.04\""
echo "OK — Ubuntu 24.04"

echo "-- 2a/2b: deploy user"
"${SSH[@]}" "$ADMIN" 'set -euo pipefail
id deploy >/dev/null 2>&1 || sudo adduser --disabled-password --gecos "" deploy >/dev/null
sudo usermod -aG sudo deploy
sudo install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
src=/home/ubuntu/.ssh/authorized_keys; [ -f "$src" ] || src=/home/deploy/.ssh/authorized_keys
sudo install -m 600 -o deploy -g deploy "$src" /home/deploy/.ssh/authorized_keys.new
sudo mv /home/deploy/.ssh/authorized_keys.new /home/deploy/.ssh/authorized_keys
# deploy-hetzner lessons (old step 2a): NOPASSWD in /etc/sudoers itself (sudoers.d was unreliable over SSH on 24.04).
sudo grep -q "^deploy ALL=(ALL:ALL) NOPASSWD:ALL" /etc/sudoers || echo "deploy ALL=(ALL:ALL) NOPASSWD:ALL" | sudo tee -a /etc/sudoers >/dev/null
sudo visudo -cq'
[ "$("${SSH[@]}" "deploy@$IP" 'sudo -n whoami')" = root ] || gate "deploy@ cannot sudo without a password"
echo "OK — deploy@ logs in with the key and sudo -n works"

echo "-- 2c/2d: sshd, ufw, UTC, fail2ban"
"${SSH[@]}" "deploy@$IP" 'set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
sudo sed -i "s/^#\?PasswordAuthentication.*/PasswordAuthentication no/; s/^#\?PermitRootLogin.*/PermitRootLogin no/" /etc/ssh/sshd_config
printf "PasswordAuthentication no\nPermitRootLogin no\n" | sudo tee /etc/ssh/sshd_config.d/10-jhc-hardening.conf >/dev/null
sudo sshd -t && sudo systemctl restart ssh
for p in 22 80 443; do sudo ufw allow "$p" >/dev/null; done
sudo ufw --force enable >/dev/null
sudo timedatectl set-timezone UTC
sudo apt-get -o DPkg::Lock::Timeout=600 update -qq
sudo apt-get -o DPkg::Lock::Timeout=600 install -y -qq fail2ban >/dev/null
sudo systemctl enable --now fail2ban >/dev/null 2>&1'
v="$("${SSH[@]}" "deploy@$IP" 'sudo sshd -T | grep -E "^(passwordauthentication|permitrootlogin) " | sort | tr "\n" " "; sudo ufw status | head -1; timedatectl show -p Timezone --value; systemctl is-active fail2ban')"
# Count, never `echo | grep -q`: under pipefail grep -q can exit before echo finishes and read as a miss
# (the 2026-10-09 RSB 521; feedback_pipefail_grep_q).
has() { [ "$(printf '%s\n' "$v" | grep -c -- "$1")" -gt 0 ]; }
has "passwordauthentication no permitrootlogin no" || gate "sshd effective config: $v"
has "Status: active" || gate "ufw not active"
has "UTC" || gate "timezone not UTC"
has "^active$" || gate "fail2ban not active"
echo "OK — password + root login off (effective), ufw 22/80/443, UTC, fail2ban"

echo "-- 2e: upgrade (+ reboot only if required)"
if "${SSH[@]}" "deploy@$IP" 'sudo DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 -y -qq upgrade >/dev/null 2>&1; [ -f /var/run/reboot-required ]'; then
  "${SSH[@]}" "deploy@$IP" 'sudo systemd-run --on-active=2 systemctl reboot >/dev/null'
  sleep 20
  for _ in $(seq 1 30); do "${SSH[@]}" -o ConnectTimeout=4 "deploy@$IP" true 2>/dev/null && break; sleep 5; done
  "${SSH[@]}" "deploy@$IP" '[ ! -f /var/run/reboot-required ]' || gate "still reboot-required after reboot"
  echo "OK — upgraded and rebooted"
else
  echo "OK — upgraded, no reboot needed"
fi

if [ "$DOCKER" = 0 ]; then
  echo "-- 3: Docker skipped (--no-docker)"
  "${SSH[@]}" "deploy@$IP" '! command -v docker >/dev/null' || echo "NOTE — docker is present from an earlier run; --no-docker does not remove it"
  echo
  echo "HARDENED (no Docker) — $IP: deploy@ with key + sudo, root and password login off, ufw 22/80/443, UTC, fail2ban."
  echo "Next: ovh-vps.py firewall-web $IP · ovh-vps.py rdns $IP <name>"
  exit 0
fi

echo "-- 3: Docker + Compose"
"${SSH[@]}" "deploy@$IP" 'set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null; then
  sudo apt-get -o DPkg::Lock::Timeout=600 install -y -qq ca-certificates curl >/dev/null
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get -o DPkg::Lock::Timeout=600 update -qq
  sudo apt-get -o DPkg::Lock::Timeout=600 install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null 2>&1
fi
sudo usermod -aG docker deploy
sudo systemctl enable --now docker >/dev/null 2>&1'
# A fresh SSH session picks up the docker group; no newgrp.
"${SSH[@]}" "deploy@$IP" 'docker run --rm hello-world 2>/dev/null | grep -q "Hello from Docker"' || gate "docker run as deploy failed"
echo "OK — $("${SSH[@]}" "deploy@$IP" 'docker --version; docker compose version' | tr '\n' ' ')"

echo
echo "HARDENED — $IP is ready for first-deploy.md A2 (GHCR read-only login) and A3 (/srv/<slug>)."
echo "Next: ovh-vps.py firewall-web $IP · ovh-vps.py rdns $IP <domain> · then provision-site.sh"
