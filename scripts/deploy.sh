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

set -euo pipefail

DEST="/opt/rockstar-pipeline"
# The checkout this script lives in (SHARE-01: it used to be hard-coded to ~/RockstarPodcast).
LOCAL_ROOT="$(CDPATH= cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

# Explicit file list — never a blind rsync of the repo. A directory sync would
# ship local-only material (episode archives, carry-forward notes, test files)
# and hide what actually changed. Every deployable earns its line.
FILES=(
  "db.py"
  "anthropic_client.py"
  "run_ledger.py"        # OBS-01: anthropic_client, both jobs and the worker import it
  "content_gates.py"
  "thank_you.py"
  "calendar_client.py"   # CAL-OFF / BLOG-02 F-1: was unmanaged, the box kept the May copy
  "guest_form.py"        # BIO-01
  "summary_norm.py"      # R1-02 / R1-04
  "host_identity.py"         # REEL-09: the one host rule (summary_norm, episode_files, content_gates import it)
  "episode_files.py"     # R1-01
  # BLOG-02 (Oliver's 02c blocker #1): publish_finalize imports all three at startup — a
  # missing one crashes the worker on import, not just the blog step.
  "blog_post.py"
  "blog_publish.py"
  "tailor_client.py"
  "drive_client.py"
  "gmail_client.py"
  "publisher_email.py"
  "workspace.py"
  "drive_changes.py"
  "poll_drive_changes.py"
  "webhook_service.py"
  "webhook_auth.py"      # SEC-01: who may call the webhook service (webhook_service imports it)
  "jobs/round_1.py"
  "jobs/publish_finalize.py"
  "scripts/render_reel.js"
  "scripts/render_closing_sequence.sh"
  "scripts/verify_reel_spec.js"
  "scripts/extract_quote_clips.sh"
  "scripts/lookup_quote_timestamps.js"
  # DEPLOY-01 (2026-07-21): these three are invoked by the pipeline but were never
  # in this list. align_episode.py did not exist on the server AT ALL, which is why
  # ADR 0003 / ADR 0004 never ran in production; detect_boundaries.js and
  # inject_word_timestamps.js happened to match local, but nothing enforced that —
  # an edit to either would have sat on the laptop while --check still reported green.
  "scripts/align_episode.py"
  "scripts/detect_boundaries.js"
  "scripts/inject_word_timestamps.js"
  "scripts/transcribe_fast.sh"
  "scripts/transcribe_clips.sh"
  "scripts/transcribe_episode.sh"
  "scripts/deploy.sh"
  "scripts/ledger_summary.py"   # OBS-01: one episode's cost, cache and steps, read on the box
  "prompts/brand_team_lisa.md"
  "prompts/brand_team_marcus.md"
  "prompts/quote_card_captions.md"   # local tool only, but anthropic_client.py
                                     # is deployed and _load_prompt()s it — keep
                                     # the pair together (4th FILES-gap class)
  "prompts/seo_captions.md"
  "prompts/thank_you.md"
  "prompts/title.md"
  # PROMPT-01 Part 1 (2026-10-10): loaded by anthropic_client.py, never in this list — the box ran
  # whatever copies were placed some other way, and --check couldn't see them. check_manifest.py now does.
  "prompts/apple_summary.md"
  "prompts/faq.md"
  "prompts/brand_team_robert.md"
  "prompts/brand_team_dr_joe.md"
  # Same class, found by check_manifest.py: the worker itself and the modules every Google client imports.
  "worker.py"
  "google_auth.py"
  "jobs/__init__.py"
  "tools/rerun_brand_team.py"
  "tools/reprocess_episode.py"
  "tools/create_thank_you_draft.py"
  "watches/renew_watches.py"
  # CARD-05 (ADR-0015): Phase 2 builds the quote cards on the box, in vendored IBM Plex.
  "jobs/card_step.py"
  "quote_cards/card_format.py"
  "quote_cards/card_upload.py"
  "quote_cards/cover_backfill.py"
  "quote_cards/cover_format.py"
  "quote_cards/cover_image.py"
  "quote_cards/cover_titles.py"
  "quote_cards/make_captions.py"
  "quote_cards/make_cover_titles.py"
  "quote_cards/plex_fonts.py"
  "quote_cards/quote_source.py"
  "quote_cards/render_cards.py"
  "quote_cards/render_covers.py"
  "quote_cards/background.png"
  "prompts/cover_titles.md"
  "BrandAssets/reel_background_host.jpg"
  "BrandAssets/fonts/ibm-plex/IBMPlexSans-Regular.woff2"
  "BrandAssets/fonts/ibm-plex/IBMPlexSans-Medium.woff2"
  "BrandAssets/fonts/ibm-plex/IBMPlexSans-SemiBold.woff2"
  "BrandAssets/fonts/ibm-plex/IBMPlexSans-Bold.woff2"
  "BrandAssets/fonts/ibm-plex/IBMPlexSansCondensed-SemiBold.woff2"
  "BrandAssets/fonts/ibm-plex/IBMPlexSansCondensed-Bold.woff2"
  "BrandAssets/fonts/ibm-plex/LICENSE.txt"
)

# Files deploy.sh did not manage until now. Their box copies were placed some other way and may hold an
# edit made on the box, so a deploy never overwrites one unless it is absent, identical, or a version this
# repo committed (scripts/box_gate.sh; PROMPT-01 scope 3, PP-11). A copy edited on the box stops the deploy
# until it is resolved by hand (scp it down, diff, decide with Heather for a prompt). Local edits after the
# first deploy pass: the box then holds a committed version.
NEWLY_MANAGED=(
  "prompts/apple_summary.md"
  "prompts/faq.md"
  "prompts/brand_team_robert.md"
  "prompts/brand_team_dr_joe.md"
  "worker.py"
  "google_auth.py"
  "jobs/__init__.py"
)

REBUILD_CLOSING=0
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --rebuild-closing) REBUILD_CLOSING=1 ;;
    --check)           CHECK_ONLY=1 ;;
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

# Every FILES entry must be tracked by git: the clean-tree check above can't see an untracked file, and its local
# bytes would ship (re-review of PR #38, finding 4).
for f in "${FILES[@]}"; do
  git -C "$LOCAL_ROOT" ls-files --error-unmatch "$f" >/dev/null 2>&1 || fail "$f is in FILES but not tracked by git"
done

# The box's address: scripts/box.conf (gitignored), via scripts/box.sh. A deploy honours NO override — not SERVER,
# not BOX_SERVER_OVERRIDE (that one is the card scripts' rehearsal hook only): box.conf alone names the target.
unset BOX_SERVER_OVERRIDE
builtin source "$LOCAL_ROOT/scripts/box.sh"

cd "$LOCAL_ROOT"

# The manifest must name everything the box runs. A gap blocks both deploy and --check (PROMPT-01, DEPLOY-01).
python3 -I scripts/check_manifest.py --root "$LOCAL_ROOT" || fail "deploy manifest is incomplete (see MISSING above)"

# Reach the box once before asking it anything. An unreachable box must read as unreachable, never as "absent on
# server" (2026-10-11: a placeholder box.conf made --check report every file MISSING).
# Every ssh/scp call times out instead of hanging: on connect, and on a box that stops answering mid-run (Toby, PR #39;
# the v1.0.9 ssh hang). The probe adds BatchMode, so a password or host-key prompt fails it rather than waiting (Orly).
SSH_OPTS=(-o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=2)
ssh "${SSH_OPTS[@]}" -o BatchMode=yes "$SERVER" true \
  || fail "cannot reach $SERVER over ssh — check scripts/box.conf and your ssh key (nothing was checked or shipped)"

if [[ "$CHECK_ONLY" -eq 0 ]]; then
  bash scripts/box_gate.sh "$SERVER" "$DEST" "${NEWLY_MANAGED[@]}" || fail "box gate stopped the deploy (see above)"
fi

NEED_RESTART=0
MISMATCH=0

# scp does not create directories; a first deploy of a new folder (quote_cards/,
# BrandAssets/fonts/ibm-plex/ for CARD-05) would otherwise fail on its first file.
if [[ "$CHECK_ONLY" -eq 0 ]]; then
  dirs=$(for f in "${FILES[@]}"; do d=$(dirname "$f"); [[ "$d" == "." ]] || echo "$DEST/$d"; done | sort -u | tr '\n' ' ')
  ssh "${SSH_OPTS[@]}" "$SERVER" "mkdir -p $dirs" || fail "could not create directories on the server"
fi

for f in "${FILES[@]}"; do
  [[ -f "$f" ]] || fail "local file missing: $f"

  if [[ "$CHECK_ONLY" -eq 0 ]]; then
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

if [[ "$CHECK_ONLY" -eq 0 && "$REBUILD_CLOSING" -eq 1 ]]; then
  echo "[deploy] rebuilding closing asset on server..."
  ssh "${SSH_OPTS[@]}" "$SERVER" "cd $DEST && BRAND_ASSETS_DIR=$DEST/BrandAssets FFMPEG_BIN=\$(command -v ffmpeg) bash scripts/render_closing_sequence.sh" \
    || fail "server-side closing asset rebuild failed"
fi

if [[ "$CHECK_ONLY" -eq 0 && "$NEED_RESTART" -eq 1 ]]; then
  # Python is imported once at service startup — deploying a .py without this
  # restart means the server keeps running the OLD code while the filesystem
  # shows the new. That silent gap is exactly how the E26 incident happened.
  # BOTH long-running services import the shared modules (db, drive_client,
  # gmail_client, anthropic_client), so any .py deploy restarts both — webhook
  # was previously left running stale code because only the worker was bounced.
  echo "[deploy] .py deployed — restarting rockstar-worker + rockstar-webhook..."
  ssh "${SSH_OPTS[@]}" "$SERVER" "systemctl restart rockstar-worker rockstar-webhook && systemctl is-active rockstar-worker rockstar-webhook" \
    || fail "a service did not come back active after restart"
  echo "[deploy] rockstar-worker + rockstar-webhook active"
fi

# local==git==deploy: git was proved clean and equal to origin/main BEFORE any ssh (SHARE-01), and the checksums
# above proved server == local.
echo "[deploy] local==git==deploy OK (git proved before ssh; checksums above)"
