#!/usr/bin/env bash
# measure_clip_limit.sh — how long a quote clip the box can transcribe, measured on the box (CLIP-01b). Run on the Mac, by John.
#
#   scripts/measure_clip_limit.sh --start               # copy a speech clip up, pause the idle worker, start measuring (~30-45 min)
#   scripts/measure_clip_limit.sh --fetch               # is it done? save the evidence here, print the proposed limit
#   scripts/measure_clip_limit.sh --stop                # stop measuring now; the worker restarts
#   scripts/measure_clip_limit.sh --write --reserve-mb N   # write state/box-limits.env from the saved evidence, then clean up
#
# The measuring runs on the box as the transient unit clip-measure, so a dropped ssh doesn't stop it. Every run is
# WhisperX exactly as transcribe_clips.sh runs it, as the worker's user, in its own capped unit (see
# clip_measure_driver.sh). The WORKER IS STOPPED for the window, so no episode's WhisperX shares memory with a
# measuring one; new jobs wait in jobs.db, and the worker restarts when clip-measure ends, however it ends
# (ExecStopPost). --start refuses unless the queue is empty and nothing is running.
#
# Every mode saves its full output under ~/RockstarPodcast-evaldata/box-limits/ (the 2026-10-12 manual rule).
set -euo pipefail

# --new-box: the OVH box before its cutover (scripts/box-new.conf, HOST-01). Anywhere in the arguments.
BOX_WHICH=""
for a in "$@"; do [[ "$a" == "--new-box" ]] && BOX_WHICH=__box_new__; done
_args=(); for a in "$@"; do [[ "$a" == "--new-box" ]] || _args+=("$a"); done; set -- ${_args[@]+"${_args[@]}"}
source "$(dirname "$0")/box.sh" "$BOX_WHICH"   # SERVER from scripts/box.conf, or box-new.conf (SHARE-01, HOST-01)
ROOT="$(CDPATH= cd "$(dirname "$0")/.." && pwd)"
APP="${BOX_APP_OVERRIDE:-/opt/rockstar-pipeline}"   # the rehearsal test points these at a fake box
WORKER_USER="rockstar"            # systemd/rockstar-worker.service User=
DIR="${BOX_DIR_OVERRIDE:-/var/tmp/clip-measure}"
UNIT="clip-measure"
# batch 2 (the candidate, flat with length on the Mac) at five lengths; batch 8 (today's) at 30 s, as E6 ran and passed.
RUNS="2:30 2:60 2:90 2:120 2:180 8:30"
# Real speech: three minutes of a recorded episode (E24), cut on the Mac. Override with CLIP_SOURCE=<file.m4a>.
SOURCE="${CLIP_SOURCE:-$ROOT/Episodes/E24-generations-endocannabinoid-system/E24-Rosalind.m4a}"
SAVE="$HOME/RockstarPodcast-evaldata/box-limits${BOX_WHICH:+-newbox}"   # each box's evidence apart: --write never mixes them
SSH=(ssh -o ConnectTimeout=15 -o ServerAliveInterval=30 "$SERVER")
# As root (the Hetzner box) a box command runs as written; as deploy@ (the OVH box, no root login) through sudo -n.
box() { if [[ "${SERVER%%@*}" == root ]]; then "${SSH[@]}" "$1"; else "${SSH[@]}" "sudo -n bash -c $(printf '%q' "$1")"; fi; }
OWNER="${SERVER%%@*}"   # who owns the scratch folder, so scp (as that user) can write into it
mkdir -p "$SAVE"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"

case "${1:-}" in
--start)
  [[ -f "$SOURCE" ]] || { echo "no speech source at $SOURCE (set CLIP_SOURCE)"; exit 2; }
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  ffmpeg -nostdin -loglevel error -y -ss 600 -t 180 -i "$SOURCE" -c copy "$tmp/source.m4a"
  box "set -e
    if systemctl is-active --quiet $UNIT; then echo 'REFUSED: clip-measure is already running (--fetch to check)'; exit 1; fi
    q=0   # no jobs.db yet (a box before its cutover): no queue
    [ ! -e $APP/state/jobs.db ] || q=\$($APP/venv/bin/python -B -c \"import sqlite3; c=sqlite3.connect('file:$APP/state/jobs.db?mode=ro', uri=True); print(c.execute(\\\"select count(*) from jobs where status in ('pending','running')\\\").fetchone()[0])\")
    [ \"\$q\" = 0 ] || { echo \"REFUSED: \$q pipeline job(s) waiting or running; measure when the queue is empty\"; exit 1; }
    if pgrep -f 'bin/[w]hisperx' >/dev/null; then echo 'REFUSED: WhisperX is running'; exit 1; fi
    free_kb=\$(df -k /var/tmp | awk 'NR==2{print \$4}')
    [ \"\$free_kb\" -ge 4194304 ] || { echo 'REFUSED: under 4 GB free on /var/tmp (a cache miss downloads large-v3, 2.9 GB)'; exit 1; }
    echo \"model cache ($WORKER_USER): \$(du -sh ~$WORKER_USER/.cache/huggingface 2>/dev/null | cut -f1 || echo none)\"
    rm -rf $DIR && mkdir -p $DIR && chmod 755 $DIR && chown $OWNER $DIR" | tee "$SAVE/$stamp-start.txt"
  scp -q -o ConnectTimeout=15 "$tmp/source.m4a" "$ROOT/rockstar/tools/measure_one_clip.py" "$ROOT/scripts/clip_measure_driver.sh" "$SERVER:$DIR/"
  # The worker stops only now, with everything in place, and comes back when clip-measure ends (or fails to start).
  # Only a worker that was running is paused and brought back: on a box before its cutover it is off and stays off
  # (HOST-01: starting it there would put two boxes on the same feeds).
  box "set -e
    chmod a+r $DIR/*
    if systemctl is-active --quiet rockstar-worker; then
      systemctl stop rockstar-worker
      systemd-run --unit=$UNIT --collect --quiet -p ExecStopPost='/bin/systemctl start rockstar-worker' \\
        bash $DIR/clip_measure_driver.sh $DIR $APP $WORKER_USER '$RUNS' \\
        || { systemctl start rockstar-worker; echo 'clip-measure did not start; worker restarted'; exit 1; }
      echo \"started $UNIT at \$(date -u +%H:%MZ) UTC: runs $RUNS; rockstar-worker paused until it ends\"
    else
      systemd-run --unit=$UNIT --collect --quiet bash $DIR/clip_measure_driver.sh $DIR $APP $WORKER_USER '$RUNS'
      echo \"started $UNIT at \$(date -u +%H:%MZ) UTC: runs $RUNS; rockstar-worker was not running and stays off\"
    fi" | tee -a "$SAVE/$stamp-start.txt"
  echo "About 30-45 minutes. Then: scripts/measure_clip_limit.sh --fetch   (to abandon it: --stop)"
  ;;
--fetch)
  state="$(box "systemctl is-active $UNIT 2>/dev/null || true")"
  box "cat $DIR/evidence.jsonl 2>/dev/null; echo '--- run.log'; cat $DIR/run.log 2>/dev/null" > "$SAVE/$stamp-fetch.txt"
  grep '^{' "$SAVE/$stamp-fetch.txt" > "$SAVE/$stamp-evidence.jsonl" || true
  worker="$(box "systemctl is-active rockstar-worker 2>/dev/null || true")"
  echo "unit: ${state:-gone}   worker: $worker   saved: $SAVE/$stamp-fetch.txt"
  grep -E '^(STOPPED|DONE)' "$SAVE/$stamp-fetch.txt" || echo "(still measuring: $(grep -c '"seconds"' "$SAVE/$stamp-evidence.jsonl" || true) of $(wc -w <<<"$RUNS" | tr -d ' ') runs in)"
  if [[ -s "$SAVE/$stamp-evidence.jsonl" ]] && grep -q '"seconds"' "$SAVE/$stamp-evidence.jsonl"; then
    python3 -I "$ROOT/rockstar/tools/clip_limit.py" "$SAVE/$stamp-evidence.jsonl" --reserve-mb "${RESERVE_MB:-600}" | tee -a "$SAVE/$stamp-fetch.txt"
    echo "(reserve ${RESERVE_MB:-600} MB is Oliver's proposal, Q4: RESERVE_MB=N to see another)"
  fi
  ;;
--stop)
  box "systemctl stop '$UNIT' '$UNIT-run-*' 2>/dev/null || true
    if systemctl is-enabled --quiet rockstar-worker; then systemctl start rockstar-worker; fi   # never on a box before its cutover
    echo \"clip-measure stopped; rockstar-worker: \$(systemctl is-active rockstar-worker)\"" | tee "$SAVE/$stamp-stop.txt"
  ;;
--write)
  [[ "${2:-}" == "--reserve-mb" && -n "${3:-}" ]] || { echo "usage: $0 --write --reserve-mb N"; exit 2; }
  evidence="$(ls -t "$SAVE"/*-evidence.jsonl 2>/dev/null | head -1)"
  [[ -n "$evidence" ]] && grep -q '"seconds"' "$evidence" || { echo "no evidence yet: --fetch after the run finishes"; exit 2; }
  grep -q '^DONE' "${evidence%-evidence.jsonl}-fetch.txt" || { echo "REFUSED: $evidence is from an unfinished run"; exit 2; }
  python3 -I "$ROOT/rockstar/tools/clip_limit.py" "$evidence" --reserve-mb "$3" --env > "$SAVE/$stamp-box-limits.env"
  cat "$SAVE/$stamp-box-limits.env"
  scp -q -o ConnectTimeout=15 "$SAVE/$stamp-box-limits.env" "$SERVER:$DIR/box-limits.env"
  scp -q -o ConnectTimeout=15 "$evidence" "$SERVER:$DIR/box-limits-evidence.jsonl"
  box "set -e
    install -o $WORKER_USER -g $WORKER_USER -m 0640 $DIR/box-limits.env $APP/state/box-limits.env
    install -o $WORKER_USER -g $WORKER_USER -m 0640 $DIR/box-limits-evidence.jsonl $APP/state/box-limits-evidence.jsonl
    rm -rf $DIR   # the speech clip and every run's output: nothing of it stays behind
    echo \"written: $APP/state/box-limits.env; $DIR removed\"" | tee "$SAVE/$stamp-write.txt"
  ;;
*)
  sed -n '2,7p' "$0"; exit 2
  ;;
esac
