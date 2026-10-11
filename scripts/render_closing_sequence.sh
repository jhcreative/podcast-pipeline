#!/usr/bin/env bash
# render_closing_sequence.sh
# One-time pre-render of the shared reel closing sequence.
# Run once; output lands in BrandAssets/reel_closing_sequence.mp4.
# Re-run whenever the closing stills change.
#
# Required stills in BrandAssets/closing/:
#   close_01.jpg  — base frame (microphone + LISTEN ON, no platform icons)
#   close_02.jpg  — + Apple Podcasts
#   close_03.jpg  — + Audible/Amazon
#   close_04.jpg  — + Spotify (final hold frame)
#
# Timing: 0.5s × 3 frames + 4.0s final hold = 5.5s total.
# Audio: uses BrandAssets/audio/opener.m4a if present; otherwise synthesizes 80Hz hum.

set -euo pipefail

BRAND_ASSETS="${BRAND_ASSETS_DIR:-$HOME/RockstarPodcast/BrandAssets}"
CLOSING_DIR="$BRAND_ASSETS/closing"
OUTPUT="$BRAND_ASSETS/reel_closing_sequence.mp4"
AUDIO_ASSET="$BRAND_ASSETS/audio/opener.m4a"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

W=1080
H=1920
FPS=30
ABRATE="192k"
AR=48000
CRF=20

echo "[closing] checking stills..."
for f in close_01.jpg close_02.jpg close_03.jpg close_04.jpg; do
  if [[ ! -f "$CLOSING_DIR/$f" ]]; then
    echo "[closing] ERROR: missing $CLOSING_DIR/$f"
    echo "  Place the 4 closing stills in $CLOSING_DIR/ and re-run."
    exit 1
  fi
done

# ffmpeg-full has drawtext/libfreetype (keg-only, not on PATH)
FFMPEG="${FFMPEG_BIN:-/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg}"

# Check ffmpeg
if [[ ! -x "$FFMPEG" ]]; then
  echo "[closing] ERROR: ffmpeg not found at $FFMPEG"
  echo "  Install: brew install ffmpeg-full"
  exit 1
fi

# ── Video: 4 stills with snap cuts ────────────────────────────────────────────
# Frame durations: 0.5s, 0.5s, 0.5s, 1.5s
echo "[closing] rendering video from stills..."
# JPEG stills decode as full-range yuvj420p. in_range/out_range on each scale CONVERTS
# pixel levels full→limited; the -color_range/-colorspace flags below then tag the stream
# truthfully. Tagging without converting ships washed levels — and the untagged version
# of this asset (yuvj420p/bt470bg) was part of the Instagram silent-rejection profile.
$FFMPEG -y \
  -loop 1 -framerate $FPS -t 0.5 -i "$CLOSING_DIR/close_01.jpg" \
  -loop 1 -framerate $FPS -t 0.5 -i "$CLOSING_DIR/close_02.jpg" \
  -loop 1 -framerate $FPS -t 0.5 -i "$CLOSING_DIR/close_03.jpg" \
  -loop 1 -framerate $FPS -t 4.0 -i "$CLOSING_DIR/close_04.jpg" \
  -filter_complex \
    "[0:v]scale=${W}:${H}:force_original_aspect_ratio=increase:in_range=full:out_range=tv,crop=${W}:${H},format=yuv420p,setsar=1[v0]; \
     [1:v]scale=${W}:${H}:force_original_aspect_ratio=increase:in_range=full:out_range=tv,crop=${W}:${H},format=yuv420p,setsar=1[v1]; \
     [2:v]scale=${W}:${H}:force_original_aspect_ratio=increase:in_range=full:out_range=tv,crop=${W}:${H},format=yuv420p,setsar=1[v2]; \
     [3:v]scale=${W}:${H}:force_original_aspect_ratio=increase:in_range=full:out_range=tv,crop=${W}:${H},format=yuv420p,setsar=1[v3]; \
     [v0][v1][v2][v3]concat=n=4:v=1:a=0[outv]" \
  -map "[outv]" \
  -c:v libx264 -preset medium -crf $CRF \
  -profile:v high -level:v 4.0 \
  -pix_fmt yuv420p -color_range tv -colorspace bt709 -color_primaries bt709 -color_trc bt709 \
  -x264-params "colorprim=bt709:transfer=bt709:colormatrix=bt709" \
  -r $FPS \
  "$TMP/closing_video.mp4"

# ── Audio: 5.5s hum (real asset or synthesized) ────────────────────────────────
echo "[closing] generating audio..."
if [[ -f "$AUDIO_ASSET" ]]; then
  echo "[closing] using real opener asset: $AUDIO_ASSET"
  $FFMPEG -y -i "$AUDIO_ASSET" \
    -t 5.5 \
    -c:a aac -b:a $ABRATE -ar $AR -ac 2 \
    "$TMP/closing_audio.m4a"
else
  echo "[closing] BrandAssets/audio/opener.m4a not found — synthesizing 80Hz placeholder hum"
  $FFMPEG -y \
    -f lavfi -i "sine=frequency=80:sample_rate=${AR}" \
    -t 5.5 \
    -af "afade=t=in:st=0:d=0.2,afade=t=out:st=5.3:d=0.2" \
    -c:a aac -b:a $ABRATE -ar $AR -ac 2 \
    "$TMP/closing_audio.m4a"
fi

# ── Mux video + audio ──────────────────────────────────────────────────────────
echo "[closing] muxing..."
# +faststart puts the moov atom before mdat. This asset is a concat INPUT (re-encoded
# downstream) so faststart isn't strictly load-bearing here, but every mp4 this pipeline
# writes is held to the same Instagram-streamable spec — no untagged stragglers.
$FFMPEG -y \
  -i "$TMP/closing_video.mp4" \
  -i "$TMP/closing_audio.m4a" \
  -c:v copy -c:a aac -b:a $ABRATE -ar $AR -ac 2 \
  -movflags +faststart \
  -shortest \
  "$OUTPUT"

# Derive ffprobe from $FFMPEG so this works on the server (Linux) too, not just
# the macOS homebrew path.
FFPROBE="${FFMPEG%ffmpeg}ffprobe"
DURATION=$("$FFPROBE" -v error -show_entries format=duration -of csv=p=0 "$OUTPUT" 2>/dev/null || echo "?")
echo "[closing] done: $OUTPUT (${DURATION}s)"
echo "[closing] place closing stills in $CLOSING_DIR/ and re-run to update."
