#!/usr/bin/env bash
# clip_measure_driver.sh — runs ON THE BOX as the transient unit clip-measure (CLIP-01b AC 1).
# scripts/measure_clip_limit.sh copies it to $DIR with the clip and measure_one_clip.py, stops the idle worker, and
# starts this with ExecStopPost=systemctl start rockstar-worker, so the worker comes back however this ends.
#
# Why the worker is stopped (Toby, CLIP-01b review): a job arriving mid-run would put the worker's WhisperX beside a
# measuring one. A global OOM could then kill the worker's, and CLIP-01a would rightly fail that episode for good.
# Stopped, the worker leaves new jobs waiting in jobs.db; they run when it restarts.
#
# Each run is its own unit (clip-measure-run-<batch>-<seconds>):
#   MemoryMax = available at idle minus CAP_MARGIN_MB (webhook, journald and sshd keep their room), swap off (swap is
#   not room), OOMPolicy=continue (a run that doesn't fit is killed inside its unit and reported),
#   OOMScoreAdjust=1000 (if the box ever runs out globally, the measurement goes first), RuntimeMaxSec (a hung run ends).
set -uo pipefail

DIR="${1:?usage: clip_measure_driver.sh <dir> <app> <worker_user> <runs>}"
APP="$2"
WORKER_USER="$3"
RUNS="$4"                      # "batch:seconds batch:seconds ..."
VENV="$APP/scripts/whisperx_env"
EVIDENCE="$DIR/evidence.jsonl"
MEMINFO="${MEMINFO:-/proc/meminfo}"   # a test points this at a fake
CAP_MARGIN_MB=300
RUN_MAX_SEC=1800
: > "$EVIDENCE"

total=$(awk '/MemTotal/{print int($2/1024)}' "$MEMINFO")
avail=$(awk '/MemAvailable/{print int($2/1024)}' "$MEMINFO")
swap=$(awk '/SwapTotal/{print int($2/1024)}' "$MEMINFO")
cap=$((avail - CAP_MARGIN_MB))
echo "{\"box\": {\"mem_total_mb\": $total, \"mem_available_mb\": $avail, \"swap_mb\": $swap, \"cap_mb\": $cap, \"measured_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\", \"cpus\": $(nproc)}}" | tee -a "$EVIDENCE"

for run in $RUNS; do
  batch="${run%%:*}"; seconds="${run##*:}"
  if pgrep -f '[w]hisperx' >/dev/null; then
    echo "STOPPED: WhisperX is already running." | tee -a "$DIR/run.log"
    exit 3
  fi
  clip="$DIR/clip_${seconds}.m4a"
  [[ -f "$clip" ]] || ffmpeg -nostdin -loglevel error -y -t "$seconds" -i "$DIR/source.m4a" -c copy "$clip"
  chown "$WORKER_USER" "$clip"
  out="$DIR/out_${batch}_${seconds}"; mkdir -p "$out"; chown "$WORKER_USER" "$out"
  line=$(systemd-run --unit="clip-measure-run-${batch}-${seconds}" --wait --pipe --collect --quiet \
           -p User="$WORKER_USER" -p Group="$WORKER_USER" \
           -p MemoryMax="${cap}M" -p MemorySwapMax=0 -p OOMPolicy=continue -p OOMScoreAdjust=1000 \
           -p RuntimeMaxSec="$RUN_MAX_SEC" \
           -p EnvironmentFile=-"$APP/.env" -p EnvironmentFile=-"$APP/scripts/.env" \
           -p WorkingDirectory="$DIR" \
           "$VENV/bin/python" "$DIR/measure_one_clip.py" "$clip" "$seconds" "$batch" "$VENV/bin/whisperx" "$out" \
         2>>"$DIR/run.log" | grep '^{' | tail -1)
  # The whole unit killed before the wrapper could report (OOM, or RuntimeMaxSec): never a pass.
  [[ -n "$line" ]] || line="{\"seconds\": $seconds, \"batch\": $batch, \"peak_mb\": null, \"exit\": \"killed\", \"oom\": null, \"wall_s\": null}"
  echo "$line" | tee -a "$EVIDENCE"
done
echo "DONE" | tee -a "$DIR/run.log"
