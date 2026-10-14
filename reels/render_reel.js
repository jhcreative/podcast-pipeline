#!/usr/bin/env node

/**
 * render_reel.js — Rockstar Podcast Reel Generation Pipeline
 * v0.3 · 2026-05-17
 *
 * Full spec: ~/RockstarPodcast/reels/render_reel_PRD.md
 *
 * Reel structure per quote:
 *   [2s opening title card] → [quote clip + captions] → [3.6s closing sequence]
 *
 * Opening title positions are visual estimates for the bottom purple band —
 * tune RENDER_CONFIG.opening.numberY and titleY after first live render.
 *
 * Opening/closing audio is SILENCE; the theme music (beat-snapped) is the real
 * audio layer for both, applied by mixMusicTrack().
 *
 * Quick start:
 *   cd ~/RockstarPodcast/scripts
 *   node render_reel.js \
 *     --quotes-json ~/path/to/quotes.json \
 *     --audio ~/path/to/raw.m4a \
 *     --background ~/path/to/reel_background.png \
 *     --output-dir ./test_output \
 *     --dry-run --verbose
 *
 * Pre-render the shared closing sequence once before first use:
 *   bash render_closing_sequence.sh
 */

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { execSync, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

// Portable __dirname (import.meta.dirname needs Node ≥20.11; the server's
// Node version is not guaranteed).
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));

// ============================================================
// CONFIGURATION
// ============================================================

const HOME = os.homedir();
const DEFAULT_BRAND_ASSETS_DIR = process.env.BRAND_ASSETS_DIR
  || path.join(HOME, 'RockstarPodcast', 'BrandAssets');

// ffmpeg-full has drawtext/libfreetype; keg-only so not on PATH
const FFMPEG_BIN = process.env.FFMPEG_BIN
  || '/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg';
// The render's scratch files: a fresh folder inside the job's own work dir (the output dir's parent), made in main().
// It used to be one fixed folder, $HOME/RockstarPodcast/scripts/.tmp, shared by every render on the machine and
// deleted at the end of each; a second render meanwhile lost its files mid-run ("moov atom not found", sandbox
// 2026-10-14). Inside the work dir it is on disk, as workspace.py requires (the box's /tmp is a 1.9 GB tmpfs).
let TMP_DIR = null;

const RENDER_CONFIG = {
  width: 1080,
  height: 1920,
  fps: 30,
  audioBitrate: '192k',
  audioSampleRate: 48000,
  audioChannels: 2,
  videoCrf: 20,
  videoPreset: 'medium',
  loudnessTargetLufs: -14,
  leadInMs: 1500,
  tailMs: 500,
  minReelCount: 3,
  maxReelCount: 5,
  openingDurationSec: 2.5,
  closingDurationSec: 5.5,   // 0.5s × 3 frames + 4.0s final hold
  // Opening card: E-number + episode title in the purple bottom band.
  // Band observed at y≈1320 from top (h-600). numberY/titleY are top-of-text.
  // Move both DOWN by reducing the subtracted value (h-520 → y=1400, lower than h-600).
  opening: {
    numberFontSize: 84,
    numberY: 'h-520',      // ≈ y=1400, ~80px into the band
    titleFontSize: 60,
    titleY: 'h-400',       // ≈ y=1520, below E-number with gap
    titleCharsPerLine: 20,
  },
  // Theme music — ducked under speech, full during opening card and closing sequence.
  music: {
    baseVolume: 0.8,           // volume during opening/closing (0–1)
    fadeDurationSec: 0.5,      // seconds to fade out/in around speech
    finalFadeDurationSec: 0.5, // seconds to fade out at reel end
    beatSnapEnabled: true,     // snap closing sequence length to bar boundary (needs theme_bpm.json)
    minLastFrameHoldSec: 4.0,  // minimum hold time on final closing still; drives bar selection
  },
  // Captions — accumulating word-by-word karaoke when word timestamps exist.
  // Words build up line-by-line within each 3-line page (top-justified), then wipe.
  // bandTop: fixed top edge of caption block. Text grows downward from here.
  captions: {
    fontSize: 64,
    charsPerLine: 26,
    linesPerPage: 4,
    bandTop: 1460,   // fixed top of caption block; tune if band shifts
  },
};

// ============================================================
// CLI ARGS
// ============================================================

function parseArgs(argv) {
  const args = {
    quotesJson: null,
    audio: null,
    background: null,
    outputDir: null,
    brandAssetsDir: DEFAULT_BRAND_ASSETS_DIR,
    music: null,
    reel: 'all',
    dryRun: false,
    verbose: false,
  };

  for (let i = 2; i < argv.length; i++) {
    const arg = argv[i];
    const next = argv[i + 1];
    if (arg === '--quotes-json')        { args.quotesJson = next; i++; }
    else if (arg === '--audio')         { args.audio = next; i++; }
    else if (arg === '--background')    { args.background = next; i++; }
    else if (arg === '--output-dir')    { args.outputDir = next; i++; }
    else if (arg === '--brand-assets-dir') { args.brandAssetsDir = next; i++; }
    else if (arg === '--music')         { args.music = next; i++; }
    else if (arg === '--reel')          { args.reel = next; i++; }
    else if (arg === '--dry-run')       { args.dryRun = true; }
    else if (arg === '--verbose')       { args.verbose = true; }
    else if (arg === '--help' || arg === '-h') { printHelp(); process.exit(0); }
    else { console.error(`Unknown argument: ${arg}`); process.exit(5); }
  }

  for (const required of ['quotesJson', 'audio', 'background', 'outputDir']) {
    if (!args[required]) {
      console.error(`Missing required argument: --${required.replace(/([A-Z])/g, '-$1').toLowerCase()}`);
      printHelp();
      process.exit(5);
    }
  }

  return args;
}

function printHelp() {
  console.log(`
Usage: node render_reel.js --quotes-json PATH --audio PATH --background PATH --output-dir PATH [options]

Required:
  --quotes-json PATH        Per-episode quotes.json (canonical quote list with timestamps)
  --audio PATH              raw.m4a (trimmed source audio)
  --background PATH         reel_background.png (9:16 background image, episode-specific)
  --output-dir PATH         Where to write rendered .mp4 files

Options:
  --brand-assets-dir PATH   Override BrandAssets location (default: ~/RockstarPodcast/BrandAssets)
  --music PATH              Theme music .mp3/.m4a; auto-detected from BrandAssets/audio/theme.mp3 if omitted
  --reel <01-05|all>        Render single Reel or all (default: all)
  --dry-run                 Validate inputs and log commands without executing
  --verbose                 Detailed step logs + ffmpeg stderr
  --help, -h                Show this message

Exit codes:
  0  success
  1  missing input file/asset
  2  ffmpeg failure
  4  output validation failure
  5  argument or quotes.json schema error

Full spec: ~/RockstarPodcast/reels/render_reel_PRD.md
`);
}

// ============================================================
// LOGGING
// ============================================================

let VERBOSE = false;
function log(msg)   { console.log(`[render] ${msg}`); }
function vlog(msg)  { if (VERBOSE) console.log(`[render:verbose] ${msg}`); }
function warn(msg)  { console.warn(`[render:warn] ${msg}`); }
function error(msg) { console.error(`[render:error] ${msg}`); }

// ============================================================
// FFMPEG RUNNER
// ============================================================

function runFfmpeg(cmd, label) {
  vlog(`ffmpeg [${label}]: ${cmd.join(' ')}`);
  const result = spawnSync(cmd[0], cmd.slice(1), {
    stdio: VERBOSE ? 'inherit' : ['ignore', 'ignore', 'pipe'],
  });
  if (result.status !== 0) {
    error(`ffmpeg ${label} failed (exit ${result.status})`);
    if (result.stderr) error(result.stderr.toString());
    process.exit(2);
  }
}

// ============================================================
// VALIDATION
// ============================================================

// The reel's one face: IBM Plex Sans SemiBold, the cards' own font (CARD-05), SIL OFL. John, 2026-10-12: "we can change
// typewriter font to plex that we are doing in cards". It replaced American Typewriter, a macOS system font whose licence
// doesn't cover rendering on a server. TTF because ffmpeg's drawtext reads it in every build; it is the cards' pinned
// WOFF2, decompressed (fonts/ibm-plex/README.md).
const PRIMARY_FONT = path.join('fonts', 'ibm-plex', 'IBMPlexSans-SemiBold.ttf');

function resolvePrimaryFont(brandAssetsDir) {
  const p = path.join(brandAssetsDir, PRIMARY_FONT);
  return fs.existsSync(p) ? p : null;
}

function resolveHostBackground(brandAssetsDir) {
  for (const ext of ['jpg', 'jpeg', 'png', 'webp']) {
    const p = path.join(brandAssetsDir, `reel_background_host.${ext}`);
    if (fs.existsSync(p)) return p;
  }
  return null;
}

function loadBpmData(brandAssetsDir) {
  const p = path.join(brandAssetsDir, 'audio', 'theme_bpm.json');
  if (!fs.existsSync(p)) return null;
  try {
    const d = JSON.parse(fs.readFileSync(p, 'utf8'));
    if (!d.bpm || !d.beat_period_sec || !d.beat_offset_sec) return null;
    return d;
  } catch { return null; }
}

// Snap the reel end to the nearest 4-beat bar boundary after (contentEndSec + minimum closing).
// Returns the absolute time (seconds from reel start) at which the snap point falls.
function findBeatSnapPoint(bpmData, contentEndSec) {
  const { beat_period_sec, beat_offset_sec } = bpmData;
  const barPeriod = 4 * beat_period_sec;
  const minSnap = contentEndSec + 1.5 + RENDER_CONFIG.music.minLastFrameHoldSec; // 3×0.5s stills + min hold
  const n = Math.ceil((minSnap - beat_offset_sec) / barPeriod);
  return beat_offset_sec + n * barPeriod;
}

function resolveThemeMusic(brandAssetsDir) {
  for (const ext of ['mp3', 'm4a', 'wav']) {
    const p = path.join(brandAssetsDir, 'audio', `theme.${ext}`);
    if (fs.existsSync(p)) return p;
  }
  return null;
}

function validateInputs(args) {
  const missing = [];

  for (const [key, p] of Object.entries({
    'quotes.json': args.quotesJson,
    'audio': args.audio,
    'background': args.background,
  })) {
    if (!fs.existsSync(p)) missing.push(`${key}: ${p}`);
  }

  const primaryFont = resolvePrimaryFont(args.brandAssetsDir);
  const brandAssets = {
    primaryFont,
    brandColors:    path.join(args.brandAssetsDir, 'brand_colors.json'),
    closingSequence: path.join(args.brandAssetsDir, 'reel_closing_sequence.mp4'),
  };

  if (!primaryFont) {
    missing.push(
      `BrandAssets.primaryFont: ${path.join(args.brandAssetsDir, PRIMARY_FONT)} not found`
    );
  }
  for (const [key, p] of Object.entries(brandAssets)) {
    if (key === 'primaryFont') continue;
    if (!p || !fs.existsSync(p)) missing.push(`BrandAssets.${key}: ${p}`);
  }

  if (missing.length > 0) {
    error('Missing required inputs:');
    missing.forEach(m => error(`  - ${m}`));
    error('See ~/RockstarPodcast/BrandAssets/README.md for required brand files.');
    process.exit(1);
  }

  try {
    execSync(`${FFMPEG_BIN} -version`, { encoding: 'utf8' });
    vlog('ffmpeg: OK');
  } catch {
    error('ffmpeg not found on PATH. Install: brew install ffmpeg');
    process.exit(1);
  }

  let brandColors;
  try {
    brandColors = JSON.parse(fs.readFileSync(brandAssets.brandColors, 'utf8'));
    if (!brandColors.caption_text_color) throw new Error('caption_text_color required');
    if (!brandColors.caption_font_family) throw new Error('caption_font_family required (e.g. "American Typewriter")');
  } catch (e) {
    error(`brand_colors.json invalid: ${e.message}`);
    process.exit(5);
  }

  return { brandAssets, brandColors };
}

// ============================================================
// LOAD QUOTES.JSON
// ============================================================

function loadQuotes(quotesJsonPath) {
  let data;
  try {
    data = JSON.parse(fs.readFileSync(quotesJsonPath, 'utf8'));
  } catch (e) {
    error(`Cannot parse quotes.json: ${e.message}`);
    process.exit(5);
  }

  if (!data.quotes || !Array.isArray(data.quotes)) {
    error('quotes.json missing required field: quotes[]');
    process.exit(5);
  }

  const count = data.quotes.length;
  if (count < RENDER_CONFIG.minReelCount || count > RENDER_CONFIG.maxReelCount) {
    warn(`quotes[] length ${count} outside expected range ${RENDER_CONFIG.minReelCount}–${RENDER_CONFIG.maxReelCount}; proceeding anyway`);
  }

  for (const q of data.quotes) {
    if (typeof q.index !== 'number')               { error('Quote missing index'); process.exit(5); }
    if (typeof q.transcript_start_ms !== 'number') { error(`Quote ${q.index} missing transcript_start_ms`); process.exit(5); }
    if (typeof q.transcript_end_ms !== 'number')   { error(`Quote ${q.index} missing transcript_end_ms`); process.exit(5); }
    if (!q.topic_slug)                             { error(`Quote ${q.index} missing topic_slug`); process.exit(5); }
    if (!q.quote_text_clean)                       { error(`Quote ${q.index} missing quote_text_clean`); process.exit(5); }
    if (!Array.isArray(q.words)) q.words = [];
    // audio_segments (ADR 0004) is OPTIONAL — older quotes.json files predate it. When
    // absent we synthesize the single full-window segment in the render loop (from
    // transcript_start/end + leadIn/tail), which makes extractAudioClip's 1-segment
    // path equal the pre-ADR-0004 single cut. words_timeline defaults to 'absolute'.
    if (q.audio_segments != null && !Array.isArray(q.audio_segments)) {
      error(`Quote ${q.index} audio_segments must be an array`); process.exit(5);
    }
  }

  log(`Loaded ${count} quotes for ${data.episode?.slug ?? 'unknown episode'}`);
  return data;
}

// ============================================================
// OPENER AUDIO
// ============================================================

/**
 * Build the opening card's audio track: SILENCE of the right length.
 *
 * WHY SILENCE AND NOT A TONE (2026-07-21, John): the theme music is the opening's
 * audio — it is laid over the assembled reel by mixMusicTrack() at
 * music.baseVolume, beat-snapped, and it has always been the design. This segment
 * only needs *an audio stream to exist* so the concat has matching streams across
 * the opening / clip / closing parts.
 *
 * It used to synthesize an 80 Hz sine for that structural role, and to first look
 * for a `BrandAssets/audio/opener.m4a` "HBO-style sting" that was never produced
 * and is not wanted. Two problems with that: the mix is
 * `amix=inputs=2:normalize=0`, so the tone was SUMMED under the music rather than
 * replaced — every reel carried 2.5s of low-end rumble nothing asked for — and the
 * absent-asset warning fired on every single render, training us to ignore a
 * real-looking signal.
 *
 * Silence fills the structural role honestly. The music, the card, the timing and
 * the beat-snap are untouched.
 */
function generateOpenerAudio(brandAssetsDir, durationSec, outputPath, dryRun) {
  const cmd = [
    FFMPEG_BIN, '-y',
    '-f', 'lavfi',
    '-i', `anullsrc=r=${RENDER_CONFIG.audioSampleRate}:cl=stereo`,
    '-t', String(durationSec),
    '-c:a', 'aac', '-b:a', RENDER_CONFIG.audioBitrate,
    '-ar', String(RENDER_CONFIG.audioSampleRate), '-ac', String(RENDER_CONFIG.audioChannels),
    outputPath,
  ];

  vlog(`opener audio (${durationSec}s silence; music is the real opening layer): ${cmd.join(' ')}`);
  if (!dryRun) runFfmpeg(cmd, 'opener audio');
}

// ============================================================
// OPENING TITLE SEGMENT
// ============================================================

// Wrap text to max charsPerLine, returning array of line strings.
function wrapText(text, charsPerLine) {
  const words = String(text).replace(/\r/g, '').split(/\s+/).filter(Boolean);
  const lines = [];
  let curr = '';
  for (const w of words) {
    if (!curr) { curr = w; }
    else if (curr.length + 1 + w.length <= charsPerLine) { curr += ' ' + w; }
    else { lines.push(curr); curr = w; }
  }
  if (curr) lines.push(curr);
  return lines;
}

// Escape special chars for ffmpeg drawtext text= values.
function escapeDrawtext(str) {
  return String(str)
    .replace(/\r/g,  '')     // strip carriage returns (WhisperX \r\n artifact renders as □)
    .replace(/\\/g, '\\\\')
    .replace(/'/g,  "\\'")
    .replace(/:/g,  '\\:')
    .replace(/%/g,  '%%');
}

function renderOpeningSegment(backgroundPath, episode, brandAssetsDir, openerAudioPath, outputPath, dryRun) {
  const primaryFont = resolvePrimaryFont(brandAssetsDir);
  const cfg = RENDER_CONFIG.opening;

  // Write text to temp files — avoids all ffmpeg filter escaping issues with
  // spaces, colons, and other special characters in episode titles.
  const numberFile = path.join(TMP_DIR, 'opening_number.txt');
  if (!dryRun) {
    fs.writeFileSync(numberFile, `E${episode.number}`, 'utf8');
  }

  const dtNumber = [
    `fontfile=${primaryFont}`,
    `textfile=${numberFile}`,
    `fontsize=${cfg.numberFontSize}`,
    `fontcolor=white`,
    `borderw=3`,
    `bordercolor=black`,
    `x=(w-text_w)/2`,
    `y=${cfg.numberY}`,
  ].join(':');

  // Resolve title y-base from expression like 'h-400' or plain number
  const titleYExpr = String(cfg.titleY);
  const titleYMatch = titleYExpr.match(/^h-(\d+)$/);
  const titleYBase = titleYMatch
    ? RENDER_CONFIG.height - parseInt(titleYMatch[1])
    : parseInt(titleYExpr);
  const titleLineH = cfg.titleFontSize + 10;

  const titleLines = wrapText(episode.title || '', cfg.titleCharsPerLine);
  const dtTitleFilters = titleLines.map((lineText, li) => {
    const lineFile = path.join(TMP_DIR, `opening_title_${li}.txt`);
    if (!dryRun) fs.writeFileSync(lineFile, lineText, 'utf8');
    return `drawtext=${[
      `fontfile=${primaryFont}`,
      `textfile=${lineFile}`,
      `fontsize=${cfg.titleFontSize}`,
      `fontcolor=white`,
      `borderw=2`,
      `bordercolor=black`,
      `x=(w-text_w)/2`,
      `y=${titleYBase + li * titleLineH}`,
    ].join(':')}`;
  });

  const vf = [
    `scale=${RENDER_CONFIG.width}:${RENDER_CONFIG.height}:force_original_aspect_ratio=increase`,
    `crop=${RENDER_CONFIG.width}:${RENDER_CONFIG.height}`,
    `drawtext=${dtNumber}`,
    ...dtTitleFilters,
    // JPEG backgrounds decode as full-range yuvj420p. The output color flags below only
    // TAG the stream — this scale pass actually CONVERTS pixel levels full→limited so the
    // tag is true. Without it, Instagram-compliant metadata sits on full-range pixels
    // (washed blacks / crushed whites on players that honor the tag).
    'scale=in_range=full:out_range=tv',
    'format=yuv420p',
  ].join(',');

  const cmd = [
    FFMPEG_BIN, '-y',
    '-loop', '1', '-framerate', String(RENDER_CONFIG.fps), '-i', backgroundPath,
    '-i', openerAudioPath,
    '-vf', vf,
    '-t', String(RENDER_CONFIG.openingDurationSec),
    '-c:v', 'libx264', '-preset', RENDER_CONFIG.videoPreset, '-crf', String(RENDER_CONFIG.videoCrf),
    '-profile:v', 'high', '-level:v', '4.0',
    '-pix_fmt', 'yuv420p', '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
    // This ffmpeg build does not pass -color_primaries/-color_trc through to the x264
    // VUI (verified: ffprobe showed transfer/primaries "unknown"). x264-params writes
    // them into the bitstream directly; the flags above still tag the container.
    '-x264-params', 'colorprim=bt709:transfer=bt709:colormatrix=bt709',
    '-r', String(RENDER_CONFIG.fps),
    '-c:a', 'aac', '-b:a', RENDER_CONFIG.audioBitrate,
    '-ar', String(RENDER_CONFIG.audioSampleRate), '-ac', String(RENDER_CONFIG.audioChannels),
    '-shortest',
    outputPath,
  ];

  vlog(`opening segment: ${cmd.join(' ')}`);
  if (!dryRun) runFfmpeg(cmd, 'opening segment');
}

// ============================================================
// AUDIO CLIP EXTRACTION
// ============================================================

// Short crossfade applied at every stitch between kept sub-spans (ADR 0004). 30ms is
// long enough to ramp through a sample-level discontinuity (no click/pop) yet far too
// short to be heard as a dip in continuous speech. Cuts are also placed by lookup at
// the kept words' own boundaries (near silence), so the fade rarely spans live voice.
const STITCH_CROSSFADE_MS = 30;

/**
 * Build the clip audio from the per-quote KEPT sub-spans (q.audio_segments, ADR 0004)
 * and concatenate them click-free, then loudnorm ONCE over the whole assembly.
 *
 * Single-segment back-compat: when audioSegments has exactly one span this is byte-for
 * -byte the OLD single -ss/-to cut + loudnorm path (no crossfade, no concat graph),
 * so quotes with no excision render identically to before ADR 0004.
 *
 * WHY loudnorm LAST, not per-segment: loudnorm is a per-input normalization; running
 * it on each sub-span separately would set different gains and the levels would JUMP at
 * each stitch (an audible step even with a crossfade). Concatenating the raw spans
 * first, then normalizing the single result, keeps one consistent gain across stitches.
 *
 * Returns the assembled clip's intended duration in seconds (sum of span lengths minus
 * the crossfade overlaps) — the caller still PROBES the written file for the real
 * duration (loudnorm/codec buffering shifts it a hair), but this is the dry-run value.
 */
function extractAudioClip(inputPath, audioSegments, outputPath, dryRun) {
  const loudnorm = `loudnorm=I=${RENDER_CONFIG.loudnessTargetLufs}:LRA=11:TP=-1.5`;
  const xfadeSec = STITCH_CROSSFADE_MS / 1000;

  // Intended duration: sum of spans, less one crossfade overlap per stitch.
  const sumSec = audioSegments.reduce((s, seg) => s + (seg.end_ms - seg.start_ms) / 1000, 0);
  const intendedSec = sumSec - Math.max(0, audioSegments.length - 1) * xfadeSec;

  if (audioSegments.length === 1) {
    // ── Back-compat single-cut path (identical to pre-ADR-0004) ────────────────
    const clipStart = Math.max(0, audioSegments[0].start_ms / 1000);
    const clipEnd   = audioSegments[0].end_ms / 1000;
    const cmd = [
      FFMPEG_BIN, '-y',
      '-i', inputPath,
      '-ss', clipStart.toFixed(3),
      '-to', clipEnd.toFixed(3),
      '-af', loudnorm,
      '-c:a', 'aac',
      '-b:a', RENDER_CONFIG.audioBitrate,
      '-ar', String(RENDER_CONFIG.audioSampleRate),
      '-ac', String(RENDER_CONFIG.audioChannels),
      outputPath,
    ];
    vlog(`audio clip (1 segment): ${clipStart.toFixed(3)}s → ${clipEnd.toFixed(3)}s`);
    if (!dryRun) runFfmpeg(cmd, 'audio clip');
    return intendedSec;
  }

  // ── Multi-segment excision path (ADR 0004) ───────────────────────────────────
  // One -ss/-to input per kept sub-span, all resampled to the project rate so the
  // acrossfade graph is uniform, then chained acrossfade stitches (click-free), then
  // a single loudnorm over the whole assembly.
  const inputs = [];
  const labels = [];
  audioSegments.forEach((seg, i) => {
    const s = Math.max(0, seg.start_ms / 1000).toFixed(3);
    const e = (seg.end_ms / 1000).toFixed(3);
    inputs.push('-ss', s, '-to', e, '-i', inputPath);
    labels.push(`[${i}:a]`);
  });

  // Normalize every segment to the project sample rate/layout before crossfading so
  // acrossfade never has to reconcile formats mid-graph.
  const pre = audioSegments.map((_, i) =>
    `[${i}:a]aformat=sample_rates=${RENDER_CONFIG.audioSampleRate}:channel_layouts=stereo,asetpts=PTS-STARTPTS[s${i}]`
  );

  // Chain acrossfade: s0 ⊕ s1 → x1, x1 ⊕ s2 → x2, … (triangular curve, default, is
  // a constant-power ramp that removes the splice discontinuity without a level dip).
  const chain = [];
  let prev = 's0';
  for (let i = 1; i < audioSegments.length; i++) {
    const out = (i === audioSegments.length - 1) ? 'xfade_out' : `x${i}`;
    chain.push(`[${prev}][s${i}]acrossfade=d=${xfadeSec.toFixed(3)}:c1=tri:c2=tri[${out}]`);
    prev = out;
  }
  const filterComplex = [...pre, ...chain, `[xfade_out]${loudnorm}[aout]`].join(';');

  const cmd = [
    FFMPEG_BIN, '-y',
    ...inputs,
    '-filter_complex', filterComplex,
    '-map', '[aout]',
    '-c:a', 'aac',
    '-b:a', RENDER_CONFIG.audioBitrate,
    '-ar', String(RENDER_CONFIG.audioSampleRate),
    '-ac', String(RENDER_CONFIG.audioChannels),
    outputPath,
  ];

  vlog(`audio clip (${audioSegments.length} segments, ${STITCH_CROSSFADE_MS}ms stitch): ` +
    audioSegments.map(s => `${(s.start_ms/1000).toFixed(2)}-${(s.end_ms/1000).toFixed(2)}`).join(' | ') +
    ` → intended ${intendedSec.toFixed(2)}s`);
  if (!dryRun) runFfmpeg(cmd, 'audio clip (excision concat)');
  return intendedSec;
}

// ============================================================
// CAPTION PAGES — drawtext-based, no libass required
// ============================================================

// Split text into lines by character budget, group into pages.
function buildLineLevelPages(text, charsPerLine, linesPerPage) {
  const words = text.split(/\s+/).filter(Boolean);
  const lines = [];
  let current = '';
  for (const word of words) {
    if (!current) {
      current = word;
    } else if (current.length + 1 + word.length <= charsPerLine) {
      current += ' ' + word;
    } else {
      lines.push(current);
      current = word;
    }
  }
  if (current) lines.push(current);

  const pages = [];
  for (let i = 0; i < lines.length; i += linesPerPage) {
    pages.push(lines.slice(i, i + linesPerPage).join('\n'));
  }
  return pages;
}

// Group word-timing objects into pages by character budget.
// Returns array of { startSec, endSec, text }.
function buildKaraokePages(wordTimings, clipStartMs, charsPerLine, linesPerPage) {
  const lines = [];
  let currentLine = [], currentLen = 0;
  for (const w of wordTimings) {
    const wl = w.word.length;
    if (!currentLen) {
      currentLine.push(w); currentLen = wl;
    } else if (currentLen + 1 + wl <= charsPerLine) {
      currentLine.push(w); currentLen += 1 + wl;
    } else {
      lines.push(currentLine);
      currentLine = [w]; currentLen = wl;
    }
  }
  if (currentLine.length) lines.push(currentLine);

  const pages = [];
  for (let i = 0; i < lines.length; i += linesPerPage) {
    const pageLines = lines.slice(i, i + linesPerPage);
    const allWords  = pageLines.flat();
    pages.push({
      startSec: (allWords[0].start_ms - clipStartMs) / 1000,
      endSec:   (allWords[allWords.length - 1].end_ms - clipStartMs) / 1000,
      text:     pageLines.map(l => l.map(w => w.word).join(' ')).join('\n'),
    });
  }
  return pages;
}

// Words accumulate word-by-word within each linesPerPage-line page, then wipe.
// Each entry shows all words spoken so far in the current page block.
// The block is bottom-anchored so text grows upward as new lines appear.
function buildAccumulatingWordPages(wordTimings, clipStartMs, charsPerLine, linesPerPage) {
  // Step 1: group words into lines by char budget
  const lines = [];
  let curLine = [], curLen = 0;
  for (const w of wordTimings) {
    const wl = w.word.length;
    if (!curLen) {
      curLine.push(w); curLen = wl;
    } else if (curLen + 1 + wl <= charsPerLine) {
      curLine.push(w); curLen += 1 + wl;
    } else {
      lines.push(curLine);
      curLine = [w]; curLen = wl;
    }
  }
  if (curLine.length) lines.push(curLine);

  // Step 2: group lines into pages, generate one state per word
  const result = [];
  for (let pageStart = 0; pageStart < lines.length; pageStart += linesPerPage) {
    const pageLines = lines.slice(pageStart, pageStart + linesPerPage);
    const pageStartIdx = result.length;

    for (let li = 0; li < pageLines.length; li++) {
      let partialLine = [];
      for (const word of pageLines[li]) {
        partialLine.push(word);

        // Text = all complete lines before this + partial current line
        const textLines = [];
        for (let prev = 0; prev < li; prev++) {
          textLines.push(pageLines[prev].map(w => w.word).join(' '));
        }
        textLines.push(partialLine.map(w => w.word).join(' '));

        result.push({
          startSec: (word.start_ms - clipStartMs) / 1000,
          endSec:   (word.end_ms   - clipStartMs) / 1000,
          text:     textLines.join('\n'),
        });
      }
    }

    // Make transitions gapless WITHIN this page: each entry's endSec = next entry's startSec.
    // This eliminates the blank-frame blink between consecutive words.
    // The last entry of the page keeps its natural endSec so the wipe gap before
    // the next page is preserved (brief text-free pause between pages).
    const pageEndIdx = result.length;
    for (let i = pageStartIdx; i < pageEndIdx - 1; i++) {
      result[i].endSec = result[i + 1].startSec;
    }
  }
  return result;
}

// Build caption pages [{text, startSec, endSec}] from a quote.
function buildCaptionPages(quote, clipStartMs, clipDurationSec) {
  const cfg       = RENDER_CONFIG.captions;
  const leadInSec = RENDER_CONFIG.leadInMs / 1000;
  const tailSec   = RENDER_CONFIG.tailMs   / 1000;

  if (quote.words.length > 0) {
    const pages = buildAccumulatingWordPages(
      quote.words, clipStartMs, cfg.charsPerLine, cfg.linesPerPage
    );
    vlog(`  caption mode: accumulating word-by-word (${pages.length} states, ${quote.words.length} words)`);
    return pages;
  } else {
    // Fallback: line-level, timed over speech window only (no word timestamps).
    const texts = buildLineLevelPages(quote.quote_text_clean, cfg.charsPerLine, 3);
    vlog(`  caption mode: line-level fallback — ${texts.length} page(s)`);
    const speechDurationSec = clipDurationSec - leadInSec - tailSec;
    const secPerPage = speechDurationSec / texts.length;
    return texts.map((text, i) => ({
      text,
      startSec: leadInSec + i * secPerPage,
      endSec:   leadInSec + (i + 1) * secPerPage,
    }));
  }
}

// Write per-line text files and return a chained drawtext filter string.
// One drawtext filter per line — ffmpeg renders \n in textfile= as □, so we
// never put newlines in text files; instead we offset each line's y manually.
function buildDrawtextFilter(pages, fontPath, brandColors, tmpDir, reelNum, dryRun) {
  const cfg     = RENDER_CONFIG.captions;
  const color   = brandColors.caption_text_color || '#FFFFFF';
  const outline = brandColors.caption_text_outline_color || '#000000';
  const lineH   = cfg.fontSize + 10;

  const filters = [];
  pages.forEach((page, i) => {
    const pageLines = page.text.replace(/\r/g, '').split('\n').filter(l => l.trim().length > 0);
    vlog(`  page ${i + 1}: ${page.startSec.toFixed(2)}s–${page.endSec.toFixed(2)}s`);
    pageLines.forEach((lineText, li) => {
      const txtFile = path.join(tmpDir, `cap_${reelNum}_p${i}_l${li}.txt`);
      if (!dryRun) fs.writeFileSync(txtFile, lineText, 'utf8');
      const opts = [
        `fontfile=${fontPath}`,
        `textfile=${txtFile}`,
        `fontsize=${cfg.fontSize}`,
        `fontcolor=${color}`,
        `borderw=3`,
        `bordercolor=${outline}`,
        `x=(w-text_w)/2`,
        `y=${cfg.bandTop + li * lineH}`,
        // Half-open [start, end): the END bound is EXCLUSIVE (REEL-07). `buildAccumulatingWordPages`
        // snaps each entry's endSec to the NEXT entry's startSec to kill the blank-frame blink, so an
        // inclusive end makes the shared instant satisfy BOTH states — and accumulating captions make
        // state i a strict prefix of i+1, so the shorter text paints offset over the longer one. One
        // frame per boundary, measured in published reels. The page's LAST entry keeps its natural
        // endSec and is followed by the wipe gap, so losing its final frame costs nothing.
        `enable=gte(t\\,${page.startSec.toFixed(3)})*lt(t\\,${page.endSec.toFixed(3)})`,
      ].join(':');
      filters.push(`drawtext=${opts}`);
    });
  });
  return filters.join(',');
}

// ============================================================
// QUOTE BODY VIDEO (background + audio + drawtext captions)
// ============================================================

function composeReelBody(backgroundPath, audioPath, captionFilter, outputPath, dryRun) {
  const vf = [
    `scale=${RENDER_CONFIG.width}:${RENDER_CONFIG.height}:force_original_aspect_ratio=increase`,
    `crop=${RENDER_CONFIG.width}:${RENDER_CONFIG.height}`,
    captionFilter,
    // Convert full-range JPEG pixels to limited range so the tv/bt709 tags below are
    // true — tagging alone does not convert (see opening segment note).
    'scale=in_range=full:out_range=tv',
    'format=yuv420p',
  ].join(',');

  const cmd = [
    FFMPEG_BIN, '-y',
    '-loop', '1', '-framerate', String(RENDER_CONFIG.fps), '-i', backgroundPath,
    '-i', audioPath,
    '-vf', vf,
    '-c:v', 'libx264', '-preset', RENDER_CONFIG.videoPreset, '-crf', String(RENDER_CONFIG.videoCrf),
    '-profile:v', 'high', '-level:v', '4.0',
    '-pix_fmt', 'yuv420p', '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
    // This ffmpeg build does not pass -color_primaries/-color_trc through to the x264
    // VUI (verified: ffprobe showed transfer/primaries "unknown"). x264-params writes
    // them into the bitstream directly; the flags above still tag the container.
    '-x264-params', 'colorprim=bt709:transfer=bt709:colormatrix=bt709',
    '-c:a', 'aac', '-b:a', RENDER_CONFIG.audioBitrate,
    '-ar', String(RENDER_CONFIG.audioSampleRate), '-ac', String(RENDER_CONFIG.audioChannels),
    '-shortest', '-r', String(RENDER_CONFIG.fps),
    outputPath,
  ];

  vlog(`compose body: ${cmd.join(' ')}`);
  if (!dryRun) runFfmpeg(cmd, 'compose body');
}

// ============================================================
// MUSIC MIXING
// ============================================================

// Build a piecewise-linear volume envelope for the theme music:
//   full during opening card → fade out as speech starts → silent under speech
//   → fade back in at tail silence start → full during closing → final fade-out.
function buildMusicVolumeExpr(speechStartSec, speechEndSec, reelDurationSec) {
  const { baseVolume: base, fadeDurationSec: fade, finalFadeDurationSec: tail } = RENDER_CONFIG.music;
  const foStart   = Math.max(0, speechStartSec - fade).toFixed(3); // fade-out begins
  const sStart    = speechStartSec.toFixed(3);
  const sEnd      = speechEndSec.toFixed(3);
  const fiEnd     = (speechEndSec + fade).toFixed(3);              // fade-in complete
  const tfStart   = Math.max(0, reelDurationSec - tail).toFixed(3);// final fade starts

  return (
    `if(lt(t,${foStart}),` +
      `${base},` +
    `if(lt(t,${sStart}),` +
      `${base}*(1-(t-${foStart})/${fade}),` +
    `if(lt(t,${sEnd}),` +
      `0,` +
    `if(lt(t,${fiEnd}),` +
      `${base}*(t-${sEnd})/${fade},` +
    `if(lt(t,${tfStart}),` +
      `${base},` +
      `max(0,${base}*(1-(t-${tfStart})/${tail}))` +
    `)))))`
  );
}

// Mix theme music into the assembled reel. Video stream is stream-copied; only
// the audio is reprocessed. Music is trimmed to reel duration and gets the
// volume envelope applied before mixing with speech audio (normalize=0 keeps
// relative levels set by the envelope — no auto-normalization).
function mixMusicTrack(assembledPath, musicPath, speechStartSec, speechEndSec, reelDurationSec, outputPath, dryRun) {
  const volExpr = buildMusicVolumeExpr(speechStartSec, speechEndSec, reelDurationSec);
  const dur = reelDurationSec.toFixed(3);

  const filterComplex =
    `[1:a]atrim=duration=${dur},asetpts=PTS-STARTPTS,` +
    `volume='${volExpr}':eval=frame[music];` +
    `[0:a][music]amix=inputs=2:normalize=0[aout]`;

  const cmd = [
    FFMPEG_BIN, '-y',
    '-i', assembledPath,
    '-i', musicPath,
    '-filter_complex', filterComplex,
    '-map', '0:v',
    '-map', '[aout]',
    '-c:v', 'copy',
    '-c:a', 'aac', '-b:a', RENDER_CONFIG.audioBitrate,
    '-ar', String(RENDER_CONFIG.audioSampleRate), '-ac', String(RENDER_CONFIG.audioChannels),
    '-movflags', '+faststart',
    outputPath,
  ];

  vlog(`music mix: speech ${speechStartSec.toFixed(2)}s–${speechEndSec.toFixed(2)}s, reel ${reelDurationSec.toFixed(2)}s`);
  if (!dryRun) runFfmpeg(cmd, 'music mix');
}

// ============================================================
// DYNAMIC CLOSING SEGMENT (beat-snapped, per-reel)
// ============================================================

// Render a beat-snapped closing segment from the 4 closing stills.
// First 3 stills hold 0.5s each; final still holds (durationSec - 1.5)s.
// Audio: SILENCE trimmed to durationSec. The music mix step lays the real music
// layer over the assembled reel, so this segment only needs an audio stream to
// exist for the concat. It used to synthesize an 80 Hz sine for that structural
// role — and because the mix is amix=normalize=0, that tone was summed UNDER the
// music rather than replaced, putting low-end rumble in every closing. Silence
// does the structural job without contaminating the mix.
function renderClosingSegment(closingStillsDir, durationSec, outputPath, dryRun) {
  const { width: W, height: H, fps: FPS, videoCrf: CRF, videoPreset,
          audioBitrate, audioSampleRate, audioChannels } = RENDER_CONFIG;
  const lastHold  = Math.max(RENDER_CONFIG.music.minLastFrameHoldSec, durationSec - 1.5);

  // Range conversion happens per-input (before concat) so all four stills enter the
  // concat already limited-range — converting after concat would be equivalent here,
  // but per-input keeps every concat input uniform by construction.
  const scaleStr = s =>
    `[${s}:v]scale=${W}:${H}:force_original_aspect_ratio=increase:in_range=full:out_range=tv,crop=${W}:${H},format=yuv420p,setsar=1[v${s}]`;

  const filterComplex = [
    scaleStr(0), scaleStr(1), scaleStr(2), scaleStr(3),
    `[v0][v1][v2][v3]concat=n=4:v=1:a=0[outv]`,
    `[4:a]atrim=duration=${durationSec.toFixed(3)}[outa]`,
  ].join(';');

  const cmd = [
    FFMPEG_BIN, '-y',
    '-loop', '1', '-framerate', String(FPS), '-t', '0.5',               '-i', path.join(closingStillsDir, 'close_01.jpg'),
    '-loop', '1', '-framerate', String(FPS), '-t', '0.5',               '-i', path.join(closingStillsDir, 'close_02.jpg'),
    '-loop', '1', '-framerate', String(FPS), '-t', '0.5',               '-i', path.join(closingStillsDir, 'close_03.jpg'),
    '-loop', '1', '-framerate', String(FPS), '-t', lastHold.toFixed(3), '-i', path.join(closingStillsDir, 'close_04.jpg'),
    '-f', 'lavfi', '-i', `anullsrc=r=${audioSampleRate}:cl=stereo`,
    '-filter_complex', filterComplex,
    '-map', '[outv]', '-map', '[outa]',
    '-c:v', 'libx264', '-preset', videoPreset, '-crf', String(CRF),
    '-profile:v', 'high', '-level:v', '4.0',
    '-pix_fmt', 'yuv420p', '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
    // This ffmpeg build does not pass -color_primaries/-color_trc through to the x264
    // VUI (verified: ffprobe showed transfer/primaries "unknown"). x264-params writes
    // them into the bitstream directly; the flags above still tag the container.
    '-x264-params', 'colorprim=bt709:transfer=bt709:colormatrix=bt709',
    '-r', String(FPS),
    '-c:a', 'aac', '-b:a', audioBitrate, '-ar', String(audioSampleRate), '-ac', String(audioChannels),
    outputPath,
  ];

  vlog(`closing segment: ${durationSec.toFixed(2)}s total, last still ${lastHold.toFixed(2)}s`);
  if (!dryRun) runFfmpeg(cmd, 'closing segment');
}

// ============================================================
// 3-PART CONCAT: opening + body + closing
// ============================================================

function concatAllSegments(openingPath, bodyPath, closingPath, finalPath, dryRun) {
  const { width: W, height: H } = RENDER_CONFIG;
  const cmd = [
    FFMPEG_BIN, '-y',
    '-i', openingPath,
    '-i', bodyPath,
    '-i', closingPath,
    '-filter_complex',
      // Segments are already limited-range yuv420p (converted at encode) — no range
      // params here. Re-applying in_range=full would crush levels a second time.
      `[0:v]scale=${W}:${H},setsar=1[v0];` +
      `[1:v]scale=${W}:${H},setsar=1[v1];` +
      `[2:v]scale=${W}:${H},setsar=1[v2];` +
      `[v0][0:a][v1][1:a][v2][2:a]concat=n=3:v=1:a=1[cv][outa];` +
      // Force an exact 30/1 CFR timeline after the splice. Concat preserves the
      // segments' slightly-irregular PTS (loop/image inputs + AAC frame durations),
      // which shipped as avg_frame_rate=37560/1253 (~29.976) — a VFR smell Instagram
      // rejects. fps=30 regenerates timestamps; -r below is belt-and-suspenders.
      `[cv]fps=${RENDER_CONFIG.fps},format=yuv420p[outv]`,
    '-map', '[outv]', '-map', '[outa]',
    '-c:v', 'libx264', '-preset', RENDER_CONFIG.videoPreset, '-crf', String(RENDER_CONFIG.videoCrf),
    '-profile:v', 'high', '-level:v', '4.0',
    '-pix_fmt', 'yuv420p', '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
    // This ffmpeg build does not pass -color_primaries/-color_trc through to the x264
    // VUI (verified: ffprobe showed transfer/primaries "unknown"). x264-params writes
    // them into the bitstream directly; the flags above still tag the container.
    '-x264-params', 'colorprim=bt709:transfer=bt709:colormatrix=bt709',
    '-r', String(RENDER_CONFIG.fps),
    '-c:a', 'aac', '-b:a', RENDER_CONFIG.audioBitrate,
    '-ar', String(RENDER_CONFIG.audioSampleRate), '-ac', String(RENDER_CONFIG.audioChannels),
    '-movflags', '+faststart',
    finalPath,
  ];

  vlog(`3-part concat: ${cmd.join(' ')}`);
  if (!dryRun) runFfmpeg(cmd, '3-part concat');
}

function getVideoDuration(videoPath) {
  const ffprobe = FFMPEG_BIN.replace(/ffmpeg$/, 'ffprobe');
  const out = execSync(
    `"${ffprobe}" -v error -show_entries format=duration -of csv=p=0 "${videoPath}"`,
    { encoding: 'utf8' }
  ).trim();
  return parseFloat(out);
}

// ============================================================
// OUTPUT VALIDATION
// ============================================================

function validateOutput(outputPath) {
  if (!fs.existsSync(outputPath)) {
    error(`Output not created: ${outputPath}`);
    process.exit(4);
  }
  if (fs.statSync(outputPath).size === 0) {
    error(`Output is zero bytes: ${outputPath}`);
    process.exit(4);
  }

  try {
    const out = execSync(
      `${FFMPEG_BIN.replace(/ffmpeg$/, 'ffprobe')} -v error -show_entries format=duration -of csv=p=0 "${outputPath}"`,
      { encoding: 'utf8' }
    ).trim();
    const duration = parseFloat(out);
    if (isNaN(duration) || duration < 5 || duration > 120) {
      error(`Output duration suspicious (${duration}s): ${outputPath}`);
      process.exit(4);
    }
    vlog(`Output OK: ${outputPath} (${duration.toFixed(1)}s)`);
  } catch (e) {
    warn(`Could not validate duration of ${outputPath}: ${e.message}`);
  }
}

// ============================================================
// MAIN
// ============================================================

function main() {
  const args = parseArgs(process.argv);
  VERBOSE = args.verbose;

  log(`Starting render. quotes.json: ${args.quotesJson}`);
  if (args.dryRun) log('DRY RUN mode — no files will be written');

  const { brandAssets, brandColors } = validateInputs(args);
  const quotesData = loadQuotes(args.quotesJson);

  // Resolve theme music (CLI flag takes priority; fall back to BrandAssets/audio/theme.*)
  if (!args.music) args.music = resolveThemeMusic(args.brandAssetsDir);
  if (args.music) log(`Theme music: ${args.music}`);
  else warn('No theme music — rendering without. Use --music PATH or place BrandAssets/audio/theme.mp3');

  fs.mkdirSync(args.outputDir, { recursive: true });
  TMP_DIR = fs.mkdtempSync(path.join(path.dirname(path.resolve(args.outputDir)), '.render-tmp-'));

  // ── Reel selection — honor the Brand Team's editorial verdict (REEL-06) ──────
  //
  // Each quote carries `reel_candidate` from the Brand Team. Until 2026-07-21 this
  // line was `? quotesData.quotes` — every quote, unfiltered — so the verdict was
  // written to quotes.json, shipped to Drive, and read by nothing. E28 q05 was set
  // false with Lisa's reasoning recorded ("needs visual support that works better in
  // blog/highlights context than as a standalone Reel clip; recommend Heather review
  // before any paid promotion use") and was published as a standalone Reel anyway.
  //
  // ABSENT MEANS RENDER: only an explicit `false` excludes, so older quotes.json
  // files that predate the field are unaffected.
  //
  // An explicit `--reel NN` deliberately BYPASSES the filter: a human naming one reel
  // is overriding on purpose, and the tool should obey and say so. The flag governs
  // automated selection, not a person's explicit instruction.
  const isExcluded = q => q.reel_candidate === false;

  let reelsToRender;
  if (args.reel === 'all') {
    const excluded = quotesData.quotes.filter(isExcluded);
    reelsToRender = quotesData.quotes.filter(q => !isExcluded(q));
    // Report every exclusion with the editor's own reasoning. A filter that silently
    // drops work is precisely the failure mode this story exists to remove.
    for (const q of excluded) {
      const idx = String(q.index).padStart(2, '0');
      log(`Skipping reel_${idx} (${q.topic_slug || q.topic || 'quote'}) — reel_candidate: false`);
      if (q.lisa_note) vlog(`  reel_${idx} lisa_note: ${q.lisa_note}`);
    }
  } else {
    reelsToRender = quotesData.quotes.filter(
      q => String(q.index).padStart(2, '0') === args.reel.padStart(2, '0')
    );
    if (reelsToRender.some(isExcluded)) {
      warn(`--reel ${args.reel} is marked reel_candidate: false — rendering anyway because it was named explicitly.`);
    }
  }

  if (reelsToRender.length === 0) {
    error(`No reels matched --reel ${args.reel}`);
    process.exit(5);
  }

  // Range check on what we are ACTUALLY rendering. The check at load time (see
  // validateQuotes) runs on the full quote list, so an episode where the editor
  // excluded several quotes could fall below the floor without anyone being told.
  if (args.reel === 'all' && reelsToRender.length < RENDER_CONFIG.minReelCount) {
    warn(
      `only ${reelsToRender.length} reel(s) after applying reel_candidate ` +
      `(minimum is ${RENDER_CONFIG.minReelCount}) — proceeding, but this episode is short on reels.`
    );
  }

  // Resolve host background if any quote in this run is from the host.
  let hostBackgroundPath = null;
  // REEL-08: a quote that carries its own background_path (its speaker's image) never needs the host default.
  if (reelsToRender.some(q => q.speaker_role === 'host' && !q.background_path)) {
    hostBackgroundPath = resolveHostBackground(args.brandAssetsDir);
    if (!hostBackgroundPath) {
      error('One or more quotes have speaker_role "host" but no reel_background_host.jpg/.png found in BrandAssets');
      process.exit(1);
    }
    log(`Host background: ${hostBackgroundPath}`);
  }

  // Load BPM data for beat-snap (optional — falls back to pre-rendered closing if absent).
  let bpmData = null;
  if (args.music && RENDER_CONFIG.music.beatSnapEnabled) {
    bpmData = loadBpmData(args.brandAssetsDir);
    if (bpmData) log(`Beat snap: ${bpmData.bpm} BPM, bar ${bpmData.bar_period_sec.toFixed(3)}s, offset ${bpmData.beat_offset_sec.toFixed(3)}s`);
    else warn('theme_bpm.json not found — closing sequence will use pre-rendered asset. Run analyze_theme_music.sh to enable beat-snap.');
  }

  // Generate opener audio once — reused for every reel's opening segment.
  const openerAudioPath = path.join(TMP_DIR, 'opener_audio.m4a');
  generateOpenerAudio(args.brandAssetsDir, RENDER_CONFIG.openingDurationSec, openerAudioPath, args.dryRun);

  for (const quote of reelsToRender) {
    const reelNum  = String(quote.index).padStart(2, '0');
    log(`Rendering reel_${reelNum} (${quote.topic})...`);

    const backgroundPath = quote.background_path
      || (quote.speaker_role === 'host' ? hostBackgroundPath : args.background);
    if (quote.background_path && !fs.existsSync(quote.background_path)) {
      error(`background_path for ${quote.id || 'a quote'} does not exist: ${quote.background_path}`);
    }

    const openingPath    = path.join(TMP_DIR, `opening_${reelNum}.mp4`);
    const clipPath       = path.join(TMP_DIR, `clip_${reelNum}.m4a`);
    const bodyPath       = path.join(TMP_DIR, `body_${reelNum}.mp4`);
    // CLIP-01c, Toby's condition A: a long_form quote the CODE kept (no reason from Lisa) waits for a person's yes. Its
    // reel is rendered (it's a real test of the 3-minute render) but named so it can't pass for an approved one.
    const pendingTag     = quote.long_form_pending === true ? 'PENDING-APPROVAL_' : '';
    const finalPath      = path.join(args.outputDir, `reel_${reelNum}_${pendingTag}${quote.topic_slug}.mp4`);
    // When music is active, concat writes to a temp file; music mix writes to finalPath.
    const assembledPath  = args.music ? path.join(TMP_DIR, `assembled_${reelNum}.mp4`) : finalPath;

    renderOpeningSegment(
      backgroundPath, quotesData.episode,
      args.brandAssetsDir, openerAudioPath, openingPath, args.dryRun
    );

    // Per-quote clamped lead-in from lookup (ADR 0003); fall back to the global
    // default so older quotes.json files (no lead_in_ms field) still render.
    const leadInMs = typeof quote.lead_in_ms === 'number' ? quote.lead_in_ms : RENDER_CONFIG.leadInMs;

    // Resolve the KEPT sub-spans (ADR 0004). When lookup emitted audio_segments we
    // use them verbatim; otherwise synthesize the single full-window segment so the
    // clip is the old single cut [start-leadIn, end+tail] — full back-compat.
    const windowStartMs = Math.max(0, quote.transcript_start_ms - leadInMs);
    const windowEndMs   = quote.transcript_end_ms + RENDER_CONFIG.tailMs;
    const audioSegments = (Array.isArray(quote.audio_segments) && quote.audio_segments.length > 0)
      ? quote.audio_segments
      : [{ start_ms: windowStartMs, end_ms: windowEndMs }];

    const intendedClipSec = extractAudioClip(args.audio, audioSegments, clipPath, args.dryRun);

    // The clip's own timeline. With excision the captions[] are remapped to LOCAL
    // (base 0) by lookup (quote.words_timeline === 'local'); without it they stay in
    // absolute episode ms and the base is the window start (pre-ADR-0004 behavior).
    const captionsLocal  = quote.words_timeline === 'local';
    const clipStartMs    = captionsLocal ? 0 : windowStartMs;
    // True clip duration drives caption fallback timing, beat-snap, and the music
    // envelope. Probe the written file (loudnorm/codec buffering shifts it); fall back
    // to the intended duration in dry-run. This is where excision SHRINKS the reel.
    const clipDurationSec = args.dryRun
      ? intendedClipSec
      : getVideoDuration(clipPath);
    const captionPages   = buildCaptionPages(quote, clipStartMs, clipDurationSec);
    const primaryFont    = resolvePrimaryFont(args.brandAssetsDir);
    const captionFilter  = buildDrawtextFilter(captionPages, primaryFont, brandColors, TMP_DIR, reelNum, args.dryRun);

    composeReelBody(backgroundPath, clipPath, captionFilter, bodyPath, args.dryRun);

    // Beat-snap: render closing at the nearest bar boundary after content ends.
    // Falls back to the pre-rendered closing sequence if no BPM data.
    let closingPath = brandAssets.closingSequence;
    if (bpmData) {
      const contentEndSec = RENDER_CONFIG.openingDurationSec + clipDurationSec;
      const snapSec       = findBeatSnapPoint(bpmData, contentEndSec);
      const closingDurSec = snapSec - contentEndSec;
      const lastHoldSec   = closingDurSec - 1.5;
      vlog(`beat snap: content ends ${contentEndSec.toFixed(2)}s → bar snap ${snapSec.toFixed(2)}s (closing ${closingDurSec.toFixed(2)}s, last hold ${lastHoldSec.toFixed(2)}s)`);
      closingPath = path.join(TMP_DIR, `closing_${reelNum}.mp4`);
      renderClosingSegment(path.join(args.brandAssetsDir, 'closing'), closingDurSec, closingPath, args.dryRun);
    }

    concatAllSegments(openingPath, bodyPath, closingPath, assembledPath, args.dryRun);

    if (args.music) {
      // Speech window in reel time: opening card + lead-in → transcript_end (tail is silence, so music fades in there).
      // Uses the SAME per-quote leadInMs as the audio cut + caption pages above, so the music envelope stays aligned.
      const speechStartSec  = RENDER_CONFIG.openingDurationSec + leadInMs / 1000;
      const speechEndSec    = RENDER_CONFIG.openingDurationSec + clipDurationSec - RENDER_CONFIG.tailMs / 1000;
      // Probe the actual assembled duration — loudnorm and codec buffering make it
      // slightly longer than the sum of config values, so the envelope must use truth.
      const reelDurationSec = args.dryRun
        ? RENDER_CONFIG.openingDurationSec + clipDurationSec + RENDER_CONFIG.closingDurationSec
        : getVideoDuration(assembledPath);
      mixMusicTrack(assembledPath, args.music, speechStartSec, speechEndSec, reelDurationSec, finalPath, args.dryRun);
    }

    if (!args.dryRun) {
      validateOutput(finalPath);
      // Instagram compliance gate — asserts the full encoding spec (yuv420p,
      // limited range, bt709, exact 30fps CFR, faststart moov, ≤level 4.2).
      // A reel that fails spec must fail the render loudly, not ship and be
      // silently rejected on John's phone (the E26 incident).
      // CLIP-01c: a long_form quote's reel may run past 90 s (Phase 1 sets long_form only with Lisa's reason).
      const verify = spawnSync('node',
        [path.join(SCRIPT_DIR, 'verify_reel_spec.js'), ...(quote.long_form === true ? ['--long-form'] : []), finalPath],
        { stdio: 'inherit' });
      if (verify.status !== 0) {
        error(`Reel failed Instagram spec verification: ${finalPath}`);
        process.exit(4);
      }
    }
    log(`  ✓ ${finalPath}`);
  }

  if (!args.dryRun && !VERBOSE) {
    try { fs.rmSync(TMP_DIR, { recursive: true, force: true }); } catch {}
  }

  log(`Done. ${reelsToRender.length}/${reelsToRender.length} Reels rendered to ${args.outputDir}`);
}

// Run the CLI only when this file IS the entry point, so `import`ing the pure caption
// builders (REEL-07's test) does not kick off a render. argv[1] is resolved AND passed
// through realpath before comparison: a relative path or a symlinked one is a different
// string from `import.meta.url` otherwise, and the guard would silently never match.
const isMain = (() => {
  const entry = process.argv[1];
  if (!entry) return false;
  try {
    return import.meta.url === pathToFileURL(fs.realpathSync(path.resolve(entry))).href;
  } catch {
    return false;
  }
})();

if (isMain) main();

// Exported for tests only (REEL-07). These are pure functions; the CLI path above is unchanged.
export { buildAccumulatingWordPages, buildCaptionPages, buildDrawtextFilter };
