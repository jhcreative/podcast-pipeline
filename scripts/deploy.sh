#!/usr/bin/env bash
# deploy.sh — deterministic deploy + parity check for the Rockstar pipeline.
#
# The server (/opt/rockstar-pipeline) is NOT a git repo; deploys are file pushes.
# This script exists because "local == git == deploy" was assumed, not checked:
# the Instagram encoder fix sat committed locally for hours while the server
# kept rendering rejected reels (the E26 incident, 2026-06-03).
#
# What it does, in order:
#   1. scp every file in FILES to the server
#   2. sha256-compare local vs server for each — abort loudly on any mismatch
#   3. restart rockstar-worker IF any .py file was deployed (the long-running
#      worker imports Python at startup; Node scripts hot-load per invocation)
#   0. BEFORE any ssh: refuse unless on main, clean, equal to origin/main, and every FILES entry tracked by git
#   4. report local==git==deploy (git was proved in step 0; the checksums in step 2)
#
# Usage:
#   bash scripts/deploy.sh                    # deploy + verify
#   bash scripts/deploy.sh --rebuild-closing  # also regenerate the closing asset server-side
#   bash scripts/deploy.sh --check            # verify checksums only, deploy nothing
#   bash scripts/deploy.sh --list             # print every file a deploy ships (from git), touch nothing
#   bash scripts/deploy.sh --new-box [--check] # the same, to scripts/box-new.conf (HOST-01: the OVH box before its
#                                             # cutover; its services are installed but not enabled, so nothing starts)

set -euo pipefail

DEST="/opt/rockstar-pipeline"
# The checkout this script lives in (SHARE-01: it used to be hard-coded to ~/RockstarPodcast).
LOCAL_ROOT="$(CDPATH= cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

# Explicit file list — never a blind rsync of the repo. A directory sync would
# ship local-only material (episode archives, carry-forward notes, test files)
# and hide what actually changed. Every deployable earns its line.
FILES=(
  # The shell scripts the pipeline runs, and the brand assets it reads. The code and prompts are added from git below.
  # (DEPLOY-01, 2026-07-21: align_episode.py had never reached the server, so ADR 0003/0004 never ran in production —
  # the history behind shipping from git: docs/carry-forward/2026-10-13-remodel.md, subject "deploy".)
  "scripts/render_closing_sequence.sh"
  "scripts/extract_quote_clips.sh"
  "scripts/align_episode.py"
  "scripts/transcribe_fast.sh"
  "scripts/transcribe_clips.sh"
  "scripts/transcribe_episode.sh"
  "scripts/deploy.sh"
  # CARD-05 (ADR-0015) and HOST-01: the cards' fonts, the reel's theme, beat grid, colours and closing stills (the
  # closing video is built from the stills on the box: deploy.sh --rebuild-closing).
  "BrandAssets/reel_background_host.jpg"
  "BrandAssets/audio/theme.mp3"
  "BrandAssets/audio/theme_bpm.json"
  "BrandAssets/brand_colors.json"
  "BrandAssets/host_bio.md"            # Heather's published bio, for an episode with no guest (content_gates.host_bio)
  "BrandAssets/closing/close_01.jpg"
  "BrandAssets/closing/close_02.jpg"
  "BrandAssets/closing/close_03.jpg"
  "BrandAssets/closing/close_04.jpg"
  "BrandAssets/fonts/ibm-plex/IBMPlexSans-SemiBold.ttf"   # the reel's captions and opener (render_reel.js PRIMARY_FONT)
  "BrandAssets/fonts/ibm-plex/IBMPlexSans-Regular.woff2"
  "BrandAssets/fonts/ibm-plex/IBMPlexSans-Medium.woff2"
  "BrandAssets/fonts/ibm-plex/IBMPlexSans-SemiBold.woff2"
  "BrandAssets/fonts/ibm-plex/IBMPlexSans-Bold.woff2"
  "BrandAssets/fonts/ibm-plex/IBMPlexSansCondensed-SemiBold.woff2"
  "BrandAssets/fonts/ibm-plex/IBMPlexSansCondensed-Bold.woff2"
  "BrandAssets/fonts/ibm-plex/LICENSE.txt"
)

# The code ships whole (2026-10-14 remodel): every tracked module of the rockstar package, every reel script and every
# prompt, read from git. A new module or prompt can't be left off a list (PROMPT-01: four prompts and five modules
# never shipped), so scripts/check_manifest.py, which caught that after the fact, is retired.
# Mac-only tools stay off the box (Orly, the remodel's cold review): the sandbox harness deletes the folders it's given
# and spends the API key; the export, the hand blog write and the Dr. Joe re-run are laptop work.
MAC_ONLY='^rockstar/tools/(sandbox_run|share_export|publish_blog_post|run_dr_joe)\.py$'
LIST_OK=1
code_list=$(git -C "$LOCAL_ROOT" ls-files 'rockstar/*.py' 'rockstar/cards/background.png' 'reels/*.js' 'prompts/*.md' \
  2>/dev/null) || LIST_OK=0
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  [[ "$f" =~ ^reels/(test_[^/]*|[^/]*\.test)\.m?js$ || "$f" =~ $MAC_ONLY ]] && continue
  FILES+=("$f")
done <<<"$code_list"

# The list's own checks, run after the git gates and before any ssh (and by --list):
check_list() {
  (( LIST_OK )) || { echo "[deploy] FAIL: git ls-files failed — nothing was listed or shipped" >&2; exit 1; }
  # A path reaches a root shell on the box (mkdir, install, sha256sum): plain names only (Orly, S2).
  for f in "${FILES[@]}"; do
    [[ "$f" =~ ^[A-Za-z0-9._/-]+$ ]] || { echo "[deploy] FAIL: refusing to ship an unusual path: '$f'" >&2; exit 1; }
  done
  # A list without the package would ship no code and still report parity: it must hold the service and its auth.
  # (A loop, not `printf | grep -q`: under pipefail grep -q's early exit can fail the pipe — the 2026-10-09 RSB 521.)
  local must f found
  for must in rockstar/__init__.py rockstar/service/worker.py rockstar/service/webhook_auth.py; do
    found=0
    for f in "${FILES[@]}"; do [[ "$f" == "$must" ]] && found=1; done
    (( found )) || { echo "[deploy] FAIL: $must is not in the list" >&2; exit 1; }
  done
}

# Every shipped file passes the box gate first: a box copy is overwritten only if it is absent, identical, or a
# version this repo committed (scripts/box_gate.sh; PROMPT-01 scope 3, PP-11). A copy edited on the box stops the
# deploy until it is resolved by hand (scp it down, diff, decide with Heather for a prompt).

REBUILD_CLOSING=0
CHECK_ONLY=0
BOX_WHICH=""
for arg in "$@"; do
  case "$arg" in
    --rebuild-closing) REBUILD_CLOSING=1 ;;
    --check)           CHECK_ONLY=1 ;;
    --new-box)         BOX_WHICH=__box_new__ ;;   # HOST-01: the OVH box before its cutover (scripts/box-new.conf)
    --list)            check_list; printf '%s\n' "${FILES[@]}"; exit 0 ;;   # what would ship; nothing else runs
    *) echo "[deploy] unknown flag: $arg"; exit 2 ;;
  esac
done

fail() { echo "[deploy] FAIL: $*" >&2; exit 1; }

# Only `main` is deployed. Any checkout works (a clone anywhere, SHARE-01), but a worktree on an unmerged branch
# never ships: what the guard checks is what goes out, and what goes out is main (PR #29 finding 7, kept portable).
branch="$(git -C "$LOCAL_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
[[ "$branch" == "main" ]] || fail "deploy only from main — $LOCAL_ROOT is on '${branch:-no git branch}'"
# ...and only a main that is clean and IS origin/main, checked before any ssh (cold review of PR #38, Oliver: a stale or
# dirty clone on main used to ship its files and restart the services, and only then exit 3).
[[ -z "$(git -C "$LOCAL_ROOT" status --porcelain --untracked-files=no)" ]] \
  || fail "uncommitted changes in $LOCAL_ROOT — commit or stash them; the box gets what git has"
git -C "$LOCAL_ROOT" fetch -q origin main || fail "could not fetch origin/main to compare — deploy stopped"
[[ "$(git -C "$LOCAL_ROOT" rev-parse HEAD)" == "$(git -C "$LOCAL_ROOT" rev-parse origin/main)" ]] \
  || fail "local main is not origin/main (pull or push first) — the box gets exactly what GitHub has"

check_list

# Every FILES entry must be tracked by git: the clean-tree check above can't see an untracked file, and its local
# bytes would ship (re-review of PR #38, finding 4).
for f in "${FILES[@]}"; do
  git -C "$LOCAL_ROOT" ls-files --error-unmatch "$f" >/dev/null 2>&1 || fail "$f is in FILES but not tracked by git"
done

# The box's address: scripts/box.conf (gitignored), via scripts/box.sh. A deploy honours NO override — not SERVER,
# not BOX_SERVER_OVERRIDE (that one is the card scripts' rehearsal hook only): box.conf alone names the target.
# The one choice is this script's own flag: --new-box reads scripts/box-new.conf instead (HOST-01), passed to box.sh
# as an argument, never through the environment.
unset BOX_SERVER_OVERRIDE
builtin source "$LOCAL_ROOT/scripts/box.sh" "$BOX_WHICH"
echo "[deploy] target: $SERVER ($( [[ -n "$BOX_WHICH" ]] && echo scripts/box-new.conf || echo scripts/box.conf ))"

cd "$LOCAL_ROOT"

# The local gate (DOD-1): lint, format, every Python and Node test, every module imports. A deploy (not --check,
# which ships nothing) refuses to run without it green.
if [[ "$CHECK_ONLY" -eq 0 ]]; then
  bash "$LOCAL_ROOT/scripts/check.sh" || fail "the local gate (scripts/check.sh) is not green — nothing was shipped"
fi

# Reach the box once before asking it anything. An unreachable box must read as unreachable, never as "absent on
# server" (2026-10-11: a placeholder box.conf made --check report every file MISSING).
# Every ssh/scp call times out instead of hanging: on connect, and on a box that stops answering mid-run (Toby, PR #39;
# the v1.0.9 ssh hang). The probe adds BatchMode, so a password or host-key prompt fails it rather than waiting (Orly).
SSH_OPTS=(-o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=2)
ssh "${SSH_OPTS[@]}" -o BatchMode=yes "$SERVER" true \
  || fail "cannot reach $SERVER over ssh — check scripts/box.conf and your ssh key (nothing was checked or shipped)"

if [[ "$CHECK_ONLY" -eq 0 ]]; then
  bash scripts/box_gate.sh "$SERVER" "$DEST" "${FILES[@]}" || fail "box gate stopped the deploy (see above)"
fi

NEED_RESTART=0
MISMATCH=0

# HOST-01: the OVH box allows no root login (box-harden.sh). As root (the Hetzner box), everything below is unchanged.
# As any other user, files are staged in a private folder and installed with sudo, owned by root as on the old box, and
# box commands run through `sudo -n` (passwordless, from box-harden.sh; a prompt fails instead of waiting).
SUDO=""
if [[ "${SERVER%%@*}" != "root" ]]; then SUDO="sudo -n"; fi

# scp does not create directories; a first deploy of a new folder (quote_cards/,
# BrandAssets/fonts/ibm-plex/ for CARD-05) would otherwise fail on its first file.
if [[ "$CHECK_ONLY" -eq 0 ]]; then
  dirs=$(for f in "${FILES[@]}"; do d=$(dirname "$f"); [[ "$d" == "." ]] || echo "$DEST/$d"; done | sort -u | tr '\n' ' ')
  ssh "${SSH_OPTS[@]}" "$SERVER" "$SUDO mkdir -p $dirs" || fail "could not create directories on the server"
fi

if [[ "$CHECK_ONLY" -eq 0 && -n "$SUDO" ]]; then
  # One tar over ssh into a private staging folder, then sudo install each file in place (owner root, the file's mode).
  # The checksum loop below then proves the installed bytes, file by file, exactly as for a root deploy.
  STAGE="/tmp/deploy-stage-$$"
  for f in "${FILES[@]}"; do [[ -f "$f" ]] || fail "local file missing: $f"; done
  # The stage folder is private (0700); the files keep their own modes (tar -p), never the umask's (Toby: a umask of
  # 077 had made every installed file root:root 0600, unreadable to the worker).
  # COPYFILE_DISABLE: macOS tar otherwise ships ._ AppleDouble files for every file with an xattr (one landed on the box).
  COPYFILE_DISABLE=1 tar -cf - "${FILES[@]}" | ssh "${SSH_OPTS[@]}" "$SERVER" "set -e; rm -rf $STAGE; mkdir -m 700 $STAGE; tar -C $STAGE -xpf -
    cd $STAGE; find . -type f | while read -r f; do sudo -n install -D -o root -g root -m \$(stat -c %a \"\$f\") \"\$f\" \"$DEST/\${f#./}\"; done
    rm -rf $STAGE" || fail "staged install failed — the box may be partially updated and nothing was restarted; run the deploy again"
  for f in "${FILES[@]}"; do [[ "$f" == *.py ]] && NEED_RESTART=1; done
fi

for f in "${FILES[@]}"; do
  [[ -f "$f" ]] || fail "local file missing: $f"

  if [[ "$CHECK_ONLY" -eq 0 && -z "$SUDO" ]]; then
    scp -q "${SSH_OPTS[@]}" "$f" "$SERVER:$DEST/$f" || fail "scp failed for $f"
    [[ "$f" == *.py ]] && NEED_RESTART=1
  fi

  # Parity is proven by bytes, not by "the scp didn't error".
  #
  # The `|| true` is load-bearing (2026-07-21). This script runs under
  # `set -euo pipefail`. When a file is ABSENT on the server, sha256sum exits
  # non-zero, pipefail propagates that through the pipeline, and set -e killed the
  # whole script MID-LOOP — silently, with the last line on screen being a tidy
  # column of OK. That is how `--check` reported 17 clean files and never told
  # anyone it had abandoned the remaining 15. A verifier that stops verifying
  # without saying so is worse than no verifier: it manufactures false confidence,
  # which is the exact E26 failure this script was written to prevent. A missing
  # file is now a REPORTED result, not a fatal one.
  local_sum=$(shasum -a 256 "$f" | awk '{print $1}')
  # The remote side always exits 0: nothing for an absent file, UNREADABLE for one that exists but can't be summed.
  # So a non-zero exit is ssh itself, a lost connection, and the run stops there rather than counting the rest as
  # MISSING. In a deploy, files up to here were already copied and nothing was restarted (Toby, PR #39).
  if [[ "$CHECK_ONLY" -eq 1 ]]; then lost="the check is incomplete, not a mismatch"
  else lost="deploy incomplete — the box is partially updated and nothing was restarted; run the deploy again"; fi
  remote_out=$(ssh "${SSH_OPTS[@]}" "$SERVER" \
    "if [ -e '$DEST/$f' ]; then sha256sum '$DEST/$f' 2>/dev/null || echo UNREADABLE; fi") \
    || fail "lost ssh to $SERVER at $f — $lost"
  remote_sum=$(awk '{print $1}' <<<"$remote_out")
  if [[ "$remote_sum" == UNREADABLE ]]; then
    echo "[deploy] UNREADABLE $f  (present on server, but it couldn't be summed)"
    MISMATCH=1
  elif [[ -z "$remote_sum" ]]; then
    echo "[deploy] MISSING $f  (absent on server)"
    MISMATCH=1
  elif [[ "$local_sum" == "$remote_sum" ]]; then
    echo "[deploy] OK   $f"
  else
    echo "[deploy] DIFF $f  (local ${local_sum:0:12}… vs server ${remote_sum:0:12}…)"
    MISMATCH=1
  fi
done

[[ "$MISMATCH" -eq 1 ]] && fail "checksum mismatch — server does not match local"

# HOST-01: the reels need production's ffmpeg 8.0.x (Ubuntu 26.04's 8.0.1 on the old box; the pinned 8.0.1 build on
# the VPS-2). A swap back to 24.04's 6.1 would render quietly differently: say the version, and stop on anything else.
ffv=$(ssh "${SSH_OPTS[@]}" "$SERVER" "ffmpeg -hide_banner -version 2>/dev/null | head -1") || ffv=""
echo "[deploy] box ffmpeg: ${ffv:-none}"
[[ "$ffv" =~ ^ffmpeg\ version\ n?8\.0\. ]] || fail "the box's ffmpeg is not 8.0.x — reels would render on another version"

if [[ "$CHECK_ONLY" -eq 0 && "$REBUILD_CLOSING" -eq 1 ]]; then
  echo "[deploy] rebuilding closing asset on server..."
  ssh "${SSH_OPTS[@]}" "$SERVER" "cd $DEST && $SUDO env BRAND_ASSETS_DIR=$DEST/BrandAssets FFMPEG_BIN=\$(command -v ffmpeg) bash scripts/render_closing_sequence.sh" \
    || fail "server-side closing asset rebuild failed"
fi

if [[ "$CHECK_ONLY" -eq 0 && "$NEED_RESTART" -eq 1 ]]; then
  # Python is imported once at service startup — deploying a .py without this
  # restart means the server keeps running the OLD code while the filesystem
  # shows the new. That silent gap is exactly how the E26 incident happened.
  # BOTH long-running services import the shared modules (db, drive_client,
  # gmail_client, anthropic_client), so any .py deploy restarts both — webhook
  # was previously left running stale code because only the worker was bounced.
  # HOST-01: only a box whose services are enabled is restarted. A box before its cutover has them installed but not
  # enabled, and starting them there would put two boxes on the same Drive and Gmail feeds. Nothing is started there.
  # Restart only a worker that is running. Enabled but stopped (paused for a measurement, or failed) is said, not
  # started; not enabled (a box before its cutover) starts nothing. Anything else (a lost ssh) fails the deploy.
  wstate=$(ssh "${SSH_OPTS[@]}" "$SERVER" "echo \$(systemctl is-active rockstar-worker) \$(systemctl is-enabled rockstar-worker)") \
    || fail "lost ssh reading the worker's state — new .py files are on the box and NOTHING was restarted; run the deploy again"
  case "$wstate" in
    "active "*)
      echo "[deploy] .py deployed — restarting rockstar-worker + rockstar-webhook..."
      ssh "${SSH_OPTS[@]}" "$SERVER" "$SUDO systemctl restart rockstar-worker rockstar-webhook && systemctl is-active rockstar-worker rockstar-webhook" \
        || fail "a service did not come back active after restart"
      echo "[deploy] rockstar-worker + rockstar-webhook active" ;;
    *" disabled")
      echo "[deploy] .py deployed — rockstar-worker is not enabled on this box (before its cutover): nothing started" ;;
    *" enabled")
      # The webhook imports the same shared modules: restart it if it's running (try-restart starts nothing).
      ssh "${SSH_OPTS[@]}" "$SERVER" "$SUDO systemctl try-restart rockstar-webhook" \
        || fail "could not restart rockstar-webhook (the worker was left stopped)"
      echo "[deploy] .py deployed — rockstar-worker is enabled but ${wstate%% *} (paused or failed): NOT started; the new"
      echo "[deploy] code runs when it next starts. rockstar-webhook restarted if it was running."
      echo "[deploy] To start the worker now: ssh to the box, systemctl restart rockstar-worker" ;;
    *) fail "unexpected worker state '$wstate' — new .py files are on the box and NOTHING was restarted" ;;
  esac
fi

# local==git==deploy: git was proved clean and equal to origin/main BEFORE any ssh (SHARE-01), and the checksums
# above proved server == local.
echo "[deploy] local==git==deploy OK (git proved before ssh; checksums above)"
