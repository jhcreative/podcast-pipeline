#!/usr/bin/env node

/**
 * verify_reel_spec.js — Instagram Reel compliance gate
 * v1.0 · 2026-06-03
 *
 * Asserts that a rendered reel .mp4 meets the encoding spec Instagram actually
 * accepts. Born from a real incident: E26's reels were silently rejected on
 * mobile (no error on draft OR share) because the encoder inherited full-range
 * yuvj420p / bt470bg from JPEG backgrounds, wrote moov after mdat, and produced
 * a not-quite-30fps timeline. Nothing in the pipeline checked, so bad files
 * shipped to Drive looking like success (PQ-5: honest systems).
 *
 * Usage:
 *   node verify_reel_spec.js reel_01.mp4 [--long-form reel_02.mp4 ...]
 *
 *   --long-form marks the ONE file after it as a long_form quote's reel (CLIP-01c): it may run to
 *   longFormMaxDurationSec instead of maxDurationSec. Every other file is a standard reel.
 *
 * Exit codes: 0 = all files pass · 1 = one or more assertions failed ·
 *             2 = usage / file missing / ffprobe unavailable
 *
 * Hooked in two places (defense in depth):
 *   - render_reel.js: right after each reel's validateOutput()
 *   - rockstar/jobs/publish_finalize.py: Step 11.5, before anything uploads to Drive
 *
 * Hand-rolled, zero dependencies: ffprobe (already required by the pipeline)
 * plus ~40 lines of MP4 atom walking. We own this gate; it has no opinions we
 * didn't put there.
 */

import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

// Same resolution rule as render_reel.js: FFMPEG_BIN env wins, macOS homebrew
// keg path is the fallback; ffprobe lives next to ffmpeg in both worlds.
const FFMPEG_BIN = process.env.FFMPEG_BIN
  || '/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg';
const FFPROBE_BIN = FFMPEG_BIN.replace(/ffmpeg$/, 'ffprobe');

// The spec, in one place. Every value here maps to a documented Instagram
// requirement or to a defect observed in the rejected E26-era files.
const SPEC = {
  width: 1080,
  height: 1920,             // 9:16, full-frame Reels canvas
  pixFmt: 'yuv420p',        // rejected files were yuvj420p (full-range JPEG inheritance)
  colorRange: 'tv',         // rejected files were 'pc'
  colorSpace: 'bt709',      // rejected files were bt470bg
  colorTransfer: 'bt709',   // rejected files were 'unknown'
  colorPrimaries: 'bt709',  // rejected files were 'unknown'
  frameRate: '30/1',        // exact string — rejected files averaged 37560/1253 (~29.976, VFR smell)
  maxLevel: 42,             // H.264 level ≤4.2 (we pin 4.0; headroom is deliberate)
  audioCodec: 'aac',
  audioSampleRate: '48000',
  audioChannels: 2,
  minDurationSec: 5,
  // THE reel length caps (CLIP-01c, John 2026-10-12): rockstar/gates/clip_limits.py reads both from here to set the quote ceilings.
  maxDurationSec: 90,             // a standard reel: the Reels-tab cap (render_reel_PRD 3g)
  longFormMaxDurationSec: 180,    // a long_form quote's reel, "only if it's really a good quote": under 3 minutes
};

function ffprobeJson(file) {
  const out = execFileSync(
    FFPROBE_BIN,
    ['-v', 'error', '-show_streams', '-show_format', '-of', 'json', file],
    { encoding: 'utf8' }
  );
  return JSON.parse(out);
}

/**
 * Walk top-level MP4 atoms and assert moov appears before mdat.
 *
 * Why this matters: without `-movflags +faststart`, ffmpeg writes the moov atom
 * (the index) at the END of the file. Instagram's uploader streams the file and
 * wants the index up front; the rejected files had order ftyp,free,mdat,moov.
 *
 * Why only the first 64 KB: a faststart file has moov within the first few KB.
 * If we hit mdat (megabytes of media data) before seeing moov, moov can only be
 * after it — that is the failure, and we can say so without reading the rest.
 * Handles the 64-bit extended-size header (size==1 → next 8 bytes) so a >4 GB
 * mdat doesn't desync the walk.
 */
function checkMoovBeforeMdat(file) {
  const fd = fs.openSync(file, 'r');
  try {
    const fileSize = fs.fstatSync(fd).size;
    let pos = 0;
    while (pos + 16 <= fileSize && pos < 64 * 1024) {
      const hdr = Buffer.alloc(16);
      fs.readSync(fd, hdr, 0, 16, pos);
      let size = hdr.readUInt32BE(0);
      const type = hdr.toString('latin1', 4, 8);
      if (type === 'moov') return { ok: true };
      if (type === 'mdat') return { ok: false, detail: `mdat at byte ${pos}, moov not yet seen` };
      if (size === 1) {
        // 64-bit atom: real size is in the 8 bytes after the type tag.
        size = Number(hdr.readBigUInt64BE(8));
      } else if (size === 0) {
        // size 0 = "atom extends to EOF" — legal only for a final mdat.
        return { ok: false, detail: `${type} atom extends to EOF before moov` };
      }
      if (size < 8) return { ok: false, detail: `corrupt atom size ${size} at byte ${pos}` };
      pos += size;
    }
    return { ok: false, detail: 'no moov atom in first 64KB (and no mdat either — corrupt?)' };
  } finally {
    fs.closeSync(fd);
  }
}

function verifyFile(file, longForm = false) {
  const failures = [];
  const assert = (cond, label, actual) => {
    if (!cond) failures.push(`${label} (got: ${actual})`);
  };

  if (!fs.existsSync(file)) return [`file does not exist`];

  let probe;
  try {
    probe = ffprobeJson(file);
  } catch (e) {
    return [`ffprobe failed: ${e.message}`];
  }

  const v = (probe.streams || []).find(s => s.codec_type === 'video');
  const a = (probe.streams || []).find(s => s.codec_type === 'audio');

  if (!v) {
    failures.push('no video stream');
  } else {
    assert(v.codec_name === 'h264', 'video codec must be h264', v.codec_name);
    assert(v.pix_fmt === SPEC.pixFmt, `pix_fmt must be ${SPEC.pixFmt}`, v.pix_fmt);
    assert(v.color_range === SPEC.colorRange, `color_range must be ${SPEC.colorRange}`, v.color_range);
    assert(v.color_space === SPEC.colorSpace, `color_space must be ${SPEC.colorSpace}`, v.color_space);
    assert(v.color_transfer === SPEC.colorTransfer, `color_transfer must be ${SPEC.colorTransfer}`, v.color_transfer);
    assert(v.color_primaries === SPEC.colorPrimaries, `color_primaries must be ${SPEC.colorPrimaries}`, v.color_primaries);
    assert(v.width === SPEC.width && v.height === SPEC.height,
      `dimensions must be ${SPEC.width}x${SPEC.height}`, `${v.width}x${v.height}`);
    // SAR unset ("N/A"/undefined) is acceptable only if it defaults square; we
    // require the explicit 1:1 our setsar=1 writes — anything else distorts DAR.
    assert(v.sample_aspect_ratio === '1:1', 'SAR must be 1:1', v.sample_aspect_ratio);
    // Exact string compare on BOTH rates: avg_frame_rate is computed from real
    // timestamps, so it catches VFR drift that r_frame_rate (nominal) hides.
    assert(v.r_frame_rate === SPEC.frameRate, `r_frame_rate must be ${SPEC.frameRate}`, v.r_frame_rate);
    assert(v.avg_frame_rate === SPEC.frameRate, `avg_frame_rate must be exactly ${SPEC.frameRate}`, v.avg_frame_rate);
    // ffprobe reports level as int ×10 (40 = 4.0).
    assert(Number(v.level) <= SPEC.maxLevel, `h264 level must be ≤${SPEC.maxLevel / 10}`, v.level);
  }

  if (!a) {
    failures.push('no audio stream');
  } else {
    assert(a.codec_name === SPEC.audioCodec, `audio codec must be ${SPEC.audioCodec}`, a.codec_name);
    assert(a.sample_rate === SPEC.audioSampleRate, `audio sample rate must be ${SPEC.audioSampleRate}`, a.sample_rate);
    assert(a.channels === SPEC.audioChannels, `audio must be ${SPEC.audioChannels}ch`, a.channels);
  }

  const duration = parseFloat(probe.format?.duration);
  const maxSec = longForm ? SPEC.longFormMaxDurationSec : SPEC.maxDurationSec;
  assert(!isNaN(duration) && duration >= SPEC.minDurationSec && duration <= maxSec,
    `duration must be ${SPEC.minDurationSec}–${maxSec}s${longForm ? ' (long_form)' : ''}`, `${duration}s`);

  const moov = checkMoovBeforeMdat(file);
  assert(moov.ok, 'moov atom must precede mdat (faststart)', moov.detail || 'ok');

  return failures;
}

function parseArgs(argv) {
  const files = [];
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--long-form') files.push({ file: argv[++i], longForm: true });
    else files.push({ file: argv[i], longForm: false });
  }
  return files;
}

function main() {
  const files = parseArgs(process.argv.slice(2));
  if (files.length === 0 || files.some(f => !f.file)) {
    console.error('usage: verify_reel_spec.js <reel.mp4> [...]');
    process.exit(2);
  }
  if (!fs.existsSync(FFPROBE_BIN)) {
    console.error(`[verify] ffprobe not found at ${FFPROBE_BIN} (set FFMPEG_BIN)`);
    process.exit(2);
  }

  let anyFailed = false;
  for (const { file, longForm } of files) {
    const failures = verifyFile(file, longForm);
    if (failures.length === 0) {
      console.log(`[verify] PASS ${file}`);
    } else {
      anyFailed = true;
      console.error(`[verify] FAIL ${file}`);
      for (const f of failures) console.error(`[verify]   ✗ ${f}`);
    }
  }

  // Loud, binary outcome: a reel that fails spec must fail the JOB. The old
  // pipeline's sin was shipping non-compliant files with a success face on.
  process.exit(anyFailed ? 1 : 0);
}

main();
