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
# Each clip is ~30-60s, so 5 clips = ~5 min of audio total. Even with large-v3
# + alignment, this completes in ~2-3 min (vs. ~20-30 min for the full episode).
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

echo "INFO: Transcribing $CLIP_COUNT clip(s) with WhisperX large-v3 + word alignment."
echo "INFO: Estimated runtime: ~2-3 min total (vs. 20-30 min for the legacy full-episode pass)."

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
    --batch_size 8
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
