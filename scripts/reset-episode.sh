#!/usr/bin/env bash
# reset-episode.sh — reset an episode in Drive for a re-run from scratch (the E13 reset, any episode). John runs it.
#
#   scripts/reset-episode.sh E6 --check     # every check, nothing changed: what it would do, and whether it can
#   scripts/reset-episode.sh E6             # the reset
#
# Needs the episode's freeze (scripts/freeze-episode.sh). rockstar/tools/drive_reset.py does the Drive work by the frozen ids
# and picks its own targets; no Drive file name crosses ssh or reaches a shell (Toby's review, 2026-10-12).
#   1. The recording (the one <EP>-*.m4a in the folder) is copied to ~/RockstarPodcast/ (gitignored), md5 checked
#      against the freeze, before anything changes. You re-drop it into the inbox to start the re-run.
#   2. The pipeline's outputs and the folder's audio copy go to Drive's trash (30 days to undo).
#   3. reels/ and cards/ are QUARANTINED, not trashed: moved into <EP folder>/_DO-NOT-POST/<date>-<EP>-redo/ with a
#      README, out of the posting path and kept as evidence (QUOTE-03 Q2).
# Keeps: the episode's images, the folder, the schedule entry, the blog record, any Tailor draft, the ledger.
# It stops, changing nothing, if anything differs from the freeze, a name is duplicated, or a Drive change could start
# Phase 2 on this episode. Not here (a person does it): delete any Gmail thank-you DRAFT for the guest before the re-run.
set -euo pipefail

EP="${1:?usage: reset-episode.sh E6 [--check]}"
[[ "$EP" =~ ^E[0-9]+$ ]] || { echo "usage: reset-episode.sh E6 [--check]"; exit 2; }
CHECK=""
[[ "${2:-}" == "--check" ]] && CHECK="--check"
ROOT="$(CDPATH= cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/box.sh"
APP="${BOX_APP_OVERRIDE:-/opt/rockstar-pipeline}"
FREEZE="${FREEZE_ROOT:-$HOME/RockstarPodcast-evaldata/freeze}/$EP"
INV="$FREEZE/drive/inventory.json"
MAC_AUDIO_DIR="${MAC_AUDIO_DIR:-$HOME/RockstarPodcast}"
DAY="$(date -u +%Y-%m-%d)"
LABEL="$DAY-$EP-redo"   # made once here, passed as a fixed word, checked against its pattern on the box
OUT="$FREEZE-reset-$DAY${CHECK:+-check}"
REMOTE="/tmp/reset-$EP-$(date -u +%Y%m%dT%H%M%SZ)"

[[ -f "$INV" ]] || { echo "RESET STOP: no frozen inventory at $INV: run scripts/freeze-episode.sh $EP first" >&2; exit 2; }
[[ -e "$OUT" ]] && { echo "RESET STOP: $OUT exists: this already ran (move it aside to redo)" >&2; exit 2; }

# The plan, read here from the frozen inventory (no Drive call). Its names are only printed, never run.
plan="$(python3 -I "$ROOT/rockstar/tools/drive_reset.py" plan "$INV")" || { echo "$plan" >&2; exit 2; }
field() { python3 -I -c 'import json,sys; v=json.loads(sys.argv[1]); [v := v[k] for k in sys.argv[2:]]; print(v)' "$plan" "$@"; }
AUDIO_NAME="$(field audio name)"
[[ "$AUDIO_NAME" =~ ^E[0-9]+-[A-Za-z0-9._-]+\.m4a$ ]] || { echo "RESET STOP: the recording's name isn't a plain file name" >&2; exit 2; }
LOCAL_AUDIO="$MAC_AUDIO_DIR/$AUDIO_NAME"
[[ -z "$CHECK" && -e "$LOCAL_AUDIO" ]] && { echo "RESET STOP: $LOCAL_AUDIO already exists: not overwriting it" >&2; exit 2; }

mkdir -p "$OUT"
exec > >(tee "$OUT/reset-run.txt") 2>&1
echo "== $EP reset (UTC $(date -u +%H:%M))${CHECK:+  --check: nothing will change}"
python3 -I "$ROOT/rockstar/tools/drive_reset.py" describe "$INV" "$LOCAL_AUDIO" "$LABEL" "$FREEZE/jobs-rows.json"

box() { ssh "$SERVER" "cd $APP && set -a && . ./.env && set +a && PYTHONPATH=$APP venv/bin/python $REMOTE/drive_reset.py $*"; }
trap 'ssh "$SERVER" "rm -rf $REMOTE" || true' EXIT
ssh "$SERVER" "mkdir -m 700 $REMOTE"
scp -q "$ROOT/rockstar/tools/drive_reset.py" "$INV" "$SERVER:$REMOTE/"

echo "== checks (every target against the freeze, and whether Phase 2 could start)"
box reset "$REMOTE/inventory.json" "$LABEL" --check
if [[ -n "$CHECK" ]]; then
  echo "== --check passed: nothing changed. To reset: scripts/reset-episode.sh $EP"
  exit 0
fi

echo "== 1/2 the recording -> $LOCAL_AUDIO"
box fetch-audio "$REMOTE/inventory.json" "$REMOTE/recording.m4a"
scp -q "$SERVER:$REMOTE/recording.m4a" "$LOCAL_AUDIO.part"
want="$(field audio md5)"
got="$(md5 -q "$LOCAL_AUDIO.part" 2>/dev/null || md5sum "$LOCAL_AUDIO.part" | cut -d' ' -f1)"
if [[ "$got" != "$want" ]]; then
  echo "RESET STOP: the Mac copy's md5 $got != frozen $want: nothing changed in Drive" >&2
  rm -f "$LOCAL_AUDIO.part"; exit 2
fi
mv "$LOCAL_AUDIO.part" "$LOCAL_AUDIO"
echo "on the Mac: $LOCAL_AUDIO, md5 matches the freeze"

echo "== 2/2 trash the outputs and the audio copy, then quarantine reels/ and cards/ (re-checked first)"
box reset "$REMOTE/inventory.json" "$LABEL"
echo "done. Undo: Drive Trash -> restore (30 days); move _DO-NOT-POST/<label>/* back. The ids are in $INV."
echo "before the re-run: delete any Gmail thank-you DRAFT for the guest. To start it: drop $LOCAL_AUDIO into recordings_inbox/."
echo "Phase 2: $(python3 -I "$ROOT/rockstar/tools/drive_reset.py" phase2 "$FREEZE/jobs-rows.json" "$EP" "$INV")"
echo "   by hand, on the box: cd /opt/rockstar-pipeline && set -a && source .env && set +a && venv/bin/python -m rockstar.tools.reprocess_episode --episode ${EP#E} --apple <url> --spotify <url>"
