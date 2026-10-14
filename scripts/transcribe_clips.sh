#!/bin/bash
#
# transcribe_clips.sh — precise WhisperX large-v3 + word alignment, per quote clip
#
# Smart pipeline stage 3 of 4 (v1.6.2):
#   Stage 1: transcribe_fast.sh (base model, no align — for selection)
#   Stage 2: extract_quote_clips.sh (cut 5 audio clips)
#   Stage 3: THIS SCRIPT — large-v3 + word alignment ONLY on the 5 small clips
#   Stage 4: inject_word_timestamps.js (merge into quotes.json)
#
# Runs WhisperX large-v3 with word-level alignment on each clip in <episode>/clips/.
#
# CLIP-01c: the batch size and the longest clip come from the box's MEASURED limits file (box-limits.env, written by
# measure_clip_limit.sh, CLIP-01b), never from here. Batch size is what makes memory grow with clip length: at batch 8
# a 175 s clip peaked at ~6.1 GB and E13 died of OOM on the 4 GB box five times; at batch 2 the peak is flat across
# 30-180 s (~4.1-4.4 GB on the 8 GB box). No limits file, no run (exit 4); a clip over the limit, no run (exit 5).
# A quote clip is normally under a minute and a half (the reel ceiling); a long_form quote can run to ~170 s.
#
# Diarization is SKIPPED in this stage — we already know the speaker for each
# quote (recorded in quotes.json during Brand Team selection).
#
# Usage:
#   transcribe_clips.sh <episode_folder_path>
#
# Exit codes:
#   0 success
#   1 invalid args / missing dependencies
#   2 no clips found or clips_manifest.json missing
#   3 whisperx failed
#   4 no usable limits file (box-limits.env): measure first, never guess
#   5 a clip is longer than the box's CLIP_MAX_SECONDS: WhisperX is never started

set -euo pipefail

EPISODE_FOLDER="${1:-}"

if [[ -z "$EPISODE_FOLDER" ]]; then
  echo "ERROR: Usage: $0 <episode_folder_path>" >&2
  exit 1
fi

CLIPS_DIR="$EPISODE_FOLDER/clips"
MANIFEST="$CLIPS_DIR/clips_manifest.json"
OUTPUT_DIR="$EPISODE_FOLDER/clips/whisperx"

if [[ ! -f "$MANIFEST" ]]; then
  echo "ERROR: clips_manifest.json not found at $MANIFEST. Run extract_quote_clips.sh first." >&2
  exit 2
fi

command -v jq >/dev/null 2>&1 || { echo "ERROR: jq not found." >&2; exit 1; }

VENV_PATH="${VENV_PATH:-$HOME/RockstarPodcast/scripts/whisperx_env}"
if [[ ! -d "$VENV_PATH" ]]; then
  echo "ERROR: WhisperX venv not found at $VENV_PATH" >&2
  exit 1
fi

# HF token still needed (whisperx loads alignment models from HF)
ENV_FILE="$HOME/RockstarPodcast/scripts/.env"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

mkdir -p "$OUTPUT_DIR"

CLIP_COUNT=$(jq '.clips | length' "$MANIFEST")
if [[ "$CLIP_COUNT" -eq 0 ]]; then
  echo "ERROR: No clips in manifest." >&2
  exit 2
fi

# CLIP-01c: the box's measured limits. Read as data (never sourced: it's a file on disk, not a script).
LIMITS_FILE="${STATE_DIR:-/opt/rockstar-pipeline/state}/box-limits.env"
# The test suite's fixture only: BOX_LIMITS_FILE counts with BOX_LIMITS_TEST=1, so a stray variable can't redirect a run.
if [[ "${BOX_LIMITS_TEST:-}" == "1" && -n "${BOX_LIMITS_FILE:-}" ]]; then LIMITS_FILE="$BOX_LIMITS_FILE"; fi
# One awk, no pipe: under pipefail a `sed | tail` on a missing file ends the script with exit 1, not 4.
limit_value() { awk -F= -v k="$1" '$1 == k && $2 ~ /^[0-9]+$/ { v = $2 } END { print v }' "$LIMITS_FILE"; }
CLIP_MAX_SECONDS=""
CLIP_BATCH_SIZE=""
if [[ -r "$LIMITS_FILE" ]]; then
  CLIP_MAX_SECONDS=$(limit_value CLIP_MAX_SECONDS)
  CLIP_BATCH_SIZE=$(limit_value CLIP_BATCH_SIZE)
fi
if [[ -z "$CLIP_MAX_SECONDS" || -z "$CLIP_BATCH_SIZE" || "$CLIP_MAX_SECONDS" -le 0 || "$CLIP_BATCH_SIZE" -le 0 ]]; then
  echo "ERROR: no usable CLIP_MAX_SECONDS / CLIP_BATCH_SIZE in $LIMITS_FILE. Measure the box first:" >&2
  echo "       scripts/measure_clip_limit.sh --measure, then --write --reserve-mb 600 (CLIP-01b)" >&2
  exit 4
fi
OVER=$(jq -r --argjson max "$CLIP_MAX_SECONDS" \
  '.clips[] | select(((.clip_end_ms - .clip_start_ms) / 1000) > $max)
   | "quote \(.quote_index): clip \((.clip_end_ms - .clip_start_ms) / 1000) s"' "$MANIFEST")
if [[ -n "$OVER" ]]; then
  echo "ERROR: over the box's CLIP_MAX_SECONDS=$CLIP_MAX_SECONDS ($LIMITS_FILE); WhisperX not started:" >&2
  echo "$OVER" >&2
  exit 5
fi
echo "INFO: box limits: clips up to ${CLIP_MAX_SECONDS}s, batch ${CLIP_BATCH_SIZE} ($LIMITS_FILE)"

echo "INFO: Transcribing $CLIP_COUNT clip(s) with WhisperX large-v3 + word alignment."
echo "INFO: Expect ~1-2 min per clip on the box at batch ${CLIP_BATCH_SIZE}."

source "$VENV_PATH/bin/activate"

FAILED=0
for IDX in $(seq 0 $((CLIP_COUNT - 1))); do
  CLIP_FILENAME=$(jq -r ".clips[$IDX].clip_filename" "$MANIFEST")
  CLIP_PATH="$CLIPS_DIR/$CLIP_FILENAME"
  QUOTE_INDEX=$(jq -r ".clips[$IDX].quote_index" "$MANIFEST")

  if [[ ! -f "$CLIP_PATH" ]]; then
    echo "WARN: Clip missing: $CLIP_PATH — skipping"
    FAILED=$((FAILED + 1))
    continue
  fi

  echo "  [$QUOTE_INDEX] $CLIP_FILENAME..."

  WORK_SUBDIR="$OUTPUT_DIR/.work_${QUOTE_INDEX}"
  rm -rf "$WORK_SUBDIR"
  mkdir -p "$WORK_SUBDIR"

  WHISPERX_CMD=(
    whisperx
    "$CLIP_PATH"
    --model large-v3
    --language en
    --output_dir "$WORK_SUBDIR"
    --output_format json
    --compute_type int8
    --batch_size "$CLIP_BATCH_SIZE"
  )

  if ! "${WHISPERX_CMD[@]}" >> "$OUTPUT_DIR/whisperx_clips.log" 2>&1; then
    echo "ERROR: WhisperX failed on $CLIP_FILENAME. Check $OUTPUT_DIR/whisperx_clips.log" >&2
    FAILED=$((FAILED + 1))
    continue
  fi

  # WhisperX writes to <work_subdir>/<basename>.json. Find it.
  CLIP_BASENAME="${CLIP_FILENAME%.m4a}"
  PRODUCED_JSON="$WORK_SUBDIR/$CLIP_BASENAME.json"
  if [[ ! -f "$PRODUCED_JSON" ]]; then
    # WhisperX sometimes uses .wav internally
    PRODUCED_JSON=$(find "$WORK_SUBDIR" -name "*.json" | head -n 1)
  fi

  if [[ -z "$PRODUCED_JSON" || ! -f "$PRODUCED_JSON" ]]; then
    echo "ERROR: No JSON produced for $CLIP_FILENAME" >&2
    FAILED=$((FAILED + 1))
    rm -rf "$WORK_SUBDIR"
    continue
  fi

  # Move to canonical location: clips/whisperx/quote_NN.json
  TARGET_JSON=$(printf "$OUTPUT_DIR/quote_%02d.json" "$QUOTE_INDEX")
  mv "$PRODUCED_JSON" "$TARGET_JSON"
  rm -rf "$WORK_SUBDIR"
done

deactivate

if [[ "$FAILED" -gt 0 ]]; then
  echo "ERROR: $FAILED clip(s) failed. PM should surface a card to John." >&2
  exit 3
fi

echo ""
echo "SUCCESS: All $CLIP_COUNT clip transcripts written to $OUTPUT_DIR/"
echo "  - quote_NN.json (one per quote, with word-level timestamps in clip-local time)"
echo ""
echo "NEXT: Run inject_word_timestamps.js to merge per-clip words into quotes.json"
echo "      (it will translate clip-local timestamps back to absolute episode time using clips_manifest.json)"
exit 0
