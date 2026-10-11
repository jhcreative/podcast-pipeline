#!/bin/bash
#
# extract_quote_clips.sh — cut audio clips for each selected quote
#
# Smart pipeline stage 2 of 4 (v1.6.2):
#   Stage 1: transcribe_fast.sh (fast Whisper base + diarize, no word align)
#   Stage 2: THIS SCRIPT — cut 5 quote audio clips from raw episode audio
#   Stage 3: transcribe_clips.sh (large-v3 + word align on the 5 clips only)
#   Stage 4: inject_word_timestamps.js (merge word data into quotes.json)
#
# Reads quotes.json (each quote has transcript_start_ms / transcript_end_ms).
# For each quote: ffmpeg cuts the audio clip [start-leadIn, end+tail].
# Writes per-quote clip files to <episode_folder>/clips/.
# Also writes <episode_folder>/clips/clips_manifest.json so the next stage
# knows which clip corresponds to which quote and the absolute clip-start
# offset (needed for translating clip-local word timestamps back to absolute
# episode timestamps).
#
# Usage:
#   extract_quote_clips.sh <episode_folder_path> <audio_filename>
#
# Args:
#   episode_folder_path — absolute path to Episodes/E{N}-{slug}/
#   audio_filename      — name of the audio file inside the folder (e.g. E24-Rosalind.m4a)
#
# Exit codes:
#   0 success
#   1 invalid arguments / missing files
#   2 quotes.json missing or invalid
#   3 ffmpeg failure

set -euo pipefail

EPISODE_FOLDER="${1:-}"
AUDIO_FILENAME="${2:-}"

if [[ -z "$EPISODE_FOLDER" || -z "$AUDIO_FILENAME" ]]; then
  echo "ERROR: Usage: $0 <episode_folder_path> <audio_filename>" >&2
  exit 1
fi

QUOTES_JSON="$EPISODE_FOLDER/quotes.json"
AUDIO_PATH="$EPISODE_FOLDER/$AUDIO_FILENAME"
CLIPS_DIR="$EPISODE_FOLDER/clips"

if [[ ! -f "$QUOTES_JSON" ]]; then
  echo "ERROR: quotes.json not found at $QUOTES_JSON. Run Brand Team quote selection first." >&2
  exit 2
fi

if [[ ! -f "$AUDIO_PATH" ]]; then
  echo "ERROR: Audio file not found at $AUDIO_PATH" >&2
  exit 1
fi

command -v ffmpeg >/dev/null 2>&1 || { echo "ERROR: ffmpeg not found. brew install ffmpeg" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "ERROR: jq not found. brew install jq" >&2; exit 1; }

# Lead-in and tail (ms). Lead-in gives audio context before quote begins
# being spoken (text on screen first); tail = natural breath after last word.
LEAD_IN_MS=1500
TAIL_MS=1000

mkdir -p "$CLIPS_DIR"

# Pre-materialize source audio locally to avoid Drive File Provider suspension
# during ffmpeg seekable reads (5 clip cuts = 5 seek+read operations on the
# same source file; if it's stream-mode, the first one suspends ffmpeg and the
# rest never run). See transcribe_fast.sh for the same pattern and the E25
# 2026-05-20 incident that motivated it. Output clips still get written back
# to the Drive CLIPS_DIR — sequential writes don't trigger File Provider stalls.
SAFE_SLUG=$(basename "$EPISODE_FOLDER" | tr -c 'a-zA-Z0-9_-' '_')
LOCAL_WORK_DIR="$HOME/RockstarPodcast/.tmp/extract_quote_clips_${SAFE_SLUG}_$$"
mkdir -p "$LOCAL_WORK_DIR"
LOCAL_AUDIO="$LOCAL_WORK_DIR/$(basename "$AUDIO_PATH")"
echo "INFO: Copying source audio from Episode folder to local work dir (avoids Drive stream-mode suspension)..."
# macOS doesn't ship GNU `timeout`; if cp ever hangs, manual `pkill cp` is the escape hatch.
if ! cp "$AUDIO_PATH" "$LOCAL_AUDIO"; then
  echo "ERROR: cp failed on $AUDIO_PATH" >&2
  rm -rf "$LOCAL_WORK_DIR"
  exit 1
fi
AUDIO_PATH="$LOCAL_AUDIO"
trap 'rm -rf "$LOCAL_WORK_DIR"' EXIT

# Read quotes from quotes.json. For each quote: extract clip.
QUOTE_COUNT=$(jq '.quotes | length' "$QUOTES_JSON")
if [[ "$QUOTE_COUNT" -eq 0 ]]; then
  echo "ERROR: No quotes in $QUOTES_JSON" >&2
  exit 2
fi

echo "INFO: Extracting $QUOTE_COUNT clip(s) from $AUDIO_FILENAME..."

# Build manifest as we go
MANIFEST="$CLIPS_DIR/clips_manifest.json"
echo '{ "clips": [' > "$MANIFEST"

FIRST=1
for IDX in $(seq 0 $((QUOTE_COUNT - 1))); do
  QUOTE_INDEX=$(jq -r ".quotes[$IDX].index" "$QUOTES_JSON")
  TOPIC_SLUG=$(jq -r ".quotes[$IDX].topic_slug" "$QUOTES_JSON")
  START_MS=$(jq -r ".quotes[$IDX].transcript_start_ms" "$QUOTES_JSON")
  END_MS=$(jq -r ".quotes[$IDX].transcript_end_ms" "$QUOTES_JSON")

  # Fallback: index defaults to IDX+1 if field is null/missing
  if [[ "$QUOTE_INDEX" == "null" || -z "$QUOTE_INDEX" ]]; then
    QUOTE_INDEX=$((IDX + 1))
  fi

  # Fallback: topic_slug derived from reel_hook or text first 3 words
  if [[ "$TOPIC_SLUG" == "null" || -z "$TOPIC_SLUG" ]]; then
    RAW_TEXT=$(jq -r "(.quotes[$IDX].reel_hook // .quotes[$IDX].text // \"clip\")" "$QUOTES_JSON")
    TOPIC_SLUG=$(echo "$RAW_TEXT" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | cut -c1-24 | sed 's/-*$//')
  fi

  # Skip quotes with 0 timestamps — lookup_quote_timestamps.js should run first
  if [[ "$START_MS" == "null" || "$END_MS" == "null" || \
        ( "$START_MS" == "0" && "$END_MS" == "0" ) ]]; then
    echo "WARNING: quote $QUOTE_INDEX has no timestamps (start=$START_MS end=$END_MS) — skipping clip" >&2
    continue
  fi

  # Compute clip boundaries
  CLIP_START_MS=$((START_MS - LEAD_IN_MS))
  if [[ "$CLIP_START_MS" -lt 0 ]]; then CLIP_START_MS=0; fi
  CLIP_END_MS=$((END_MS + TAIL_MS))

  # Convert to seconds for ffmpeg
  CLIP_START_SEC=$(echo "scale=3; $CLIP_START_MS / 1000" | bc)
  CLIP_DURATION_SEC=$(echo "scale=3; ($CLIP_END_MS - $CLIP_START_MS) / 1000" | bc)

  # File name with zero-padded index
  CLIP_NAME=$(printf "quote_%02d_%s.m4a" "$QUOTE_INDEX" "$TOPIC_SLUG")
  CLIP_PATH="$CLIPS_DIR/$CLIP_NAME"

  echo "  [$QUOTE_INDEX] $TOPIC_SLUG: ${CLIP_START_SEC}s for ${CLIP_DURATION_SEC}s → $CLIP_NAME"

  ffmpeg -y -loglevel error \
    -ss "$CLIP_START_SEC" -t "$CLIP_DURATION_SEC" \
    -i "$AUDIO_PATH" \
    -c:a aac -b:a 192k -ar 48000 -ac 2 \
    "$CLIP_PATH" || { echo "ERROR: ffmpeg failed on quote $QUOTE_INDEX" >&2; exit 3; }

  # Append to manifest
  if [[ "$FIRST" -eq 0 ]]; then echo "," >> "$MANIFEST"; fi
  FIRST=0
  cat >> "$MANIFEST" <<JSON
    {
      "quote_index": $QUOTE_INDEX,
      "topic_slug": "$TOPIC_SLUG",
      "clip_filename": "$CLIP_NAME",
      "clip_start_ms": $CLIP_START_MS,
      "clip_end_ms": $CLIP_END_MS,
      "lead_in_ms": $LEAD_IN_MS,
      "tail_ms": $TAIL_MS,
      "quote_transcript_start_ms": $START_MS,
      "quote_transcript_end_ms": $END_MS
    }
JSON
done

echo '  ]' >> "$MANIFEST"
echo "}" >> "$MANIFEST"

# Pretty-print the manifest
jq '.' "$MANIFEST" > "$MANIFEST.tmp" && mv "$MANIFEST.tmp" "$MANIFEST"

echo ""
echo "SUCCESS: $QUOTE_COUNT clips written to $CLIPS_DIR/"
echo "Manifest: $MANIFEST"
echo ""
echo "NEXT: Run transcribe_clips.sh to get precise word timestamps for each clip."
exit 0
