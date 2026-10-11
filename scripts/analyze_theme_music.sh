#!/usr/bin/env bash
# analyze_theme_music.sh
# One-time BPM + beat-offset analysis of BrandAssets/audio/theme.mp3.
# Writes BrandAssets/audio/theme_bpm.json consumed by render_reel.js.
#
# Re-run whenever the theme music file changes.
# Requires: aubio (brew install aubio)
#
# Output JSON fields:
#   bpm              — beats per minute (span-based, most stable)
#   beat_period_sec  — seconds per beat
#   beat_offset_sec  — timestamp of first detected beat in the file
#   bar_period_sec   — seconds per 4-beat bar (4/4 assumed)
#   beat_count       — total beats detected
#   analyzed_file    — filename of the analyzed track
#   analyzed_at      — ISO date of analysis

set -euo pipefail

BRAND_ASSETS="${BRAND_ASSETS_DIR:-$(CDPATH= cd "$(dirname "$0")/.." && pwd)/BrandAssets}"  # this checkout
THEME_FILE="$BRAND_ASSETS/audio/theme.mp3"
OUT="$BRAND_ASSETS/audio/theme_bpm.json"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

if [[ ! -f "$THEME_FILE" ]]; then
  echo "[analyze] ERROR: $THEME_FILE not found"
  echo "  Place theme music at BrandAssets/audio/theme.mp3 and re-run."
  exit 1
fi

if ! command -v aubio &>/dev/null; then
  echo "[analyze] ERROR: aubio not found. Install: brew install aubio"
  exit 1
fi

echo "[analyze] Detecting beats in $THEME_FILE..."
aubio beat -i "$THEME_FILE" 2>/dev/null > "$TMP"

BEAT_COUNT=$(wc -l < "$TMP" | tr -d ' ')
echo "[analyze] Found $BEAT_COUNT beats — computing BPM..."

node - "$TMP" "$BEAT_COUNT" "$OUT" "$(basename "$THEME_FILE")" <<'EOF'
const fs = require('fs');
const [, , beatsFile, beatCount, outFile, analyzedFile] = process.argv;
const beats = fs.readFileSync(beatsFile, 'utf8')
  .trim().split('\n').map(Number).filter(n => !isNaN(n) && n > 0);
if (beats.length < 10) { console.error('[analyze] Too few beats detected — check audio file'); process.exit(1); }
const bpm = (beats.length - 1) * 60 / (beats[beats.length - 1] - beats[0]);
const period = 60 / bpm;
const data = {
  bpm:             parseFloat(bpm.toFixed(3)),
  beat_period_sec: parseFloat(period.toFixed(6)),
  beat_offset_sec: parseFloat(beats[0].toFixed(6)),
  bar_period_sec:  parseFloat((4 * period).toFixed(6)),
  beat_count:      beats.length,
  analyzed_file:   analyzedFile,
  analyzed_at:     new Date().toISOString().split('T')[0],
};
fs.writeFileSync(outFile, JSON.stringify(data, null, 2));
console.log(`[analyze] BPM: ${data.bpm}  bar: ${data.bar_period_sec}s  first beat: ${data.beat_offset_sec}s`);
console.log(`[analyze] Written: ${outFile}`);
EOF
