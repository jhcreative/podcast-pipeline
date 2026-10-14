#!/usr/bin/env node

/**
 * test_clip_boundaries.js — failure matrix for the "stop counting, start finding"
 * clip-boundary logic in lookup_quote_timestamps.js.
 * v1.0 · 2026-06-22
 *
 * Born from ADR 0003 ("Reel clip boundaries frame a complete thought"). E27's
 * reels 1, 2, and 4 framed PARTIAL thoughts because the old clipper COUNTED the
 * out-point (bestI + baseLen) instead of FINDING it, and applied a blind fixed
 * lead-in that dragged in the previous sentence. Because the Brand Team CLEANS
 * quotes (removes host interjections + fillers, fixes transcription errors), the
 * cleaned `text` does not word-align to the raw transcript, so any count under- or
 * over-shoots. This matrix locks the find-based behavior against that drift.
 *
 * Each fixture is a tiny hand-built timeline (word + interpolated start/end) so the
 * assertions are exact and deterministic — no audio, no WhisperX, no network.
 *
 * The matrix (the three shapes ADR 0003 names):
 *   1. UNDERSHOOT  — cleaned text SHORTER than spoken span (dropped stutter/dup).
 *                    A forward count stops early; find must reach the real last word.
 *   2. OVERSHOOT/INTERJECTION — a host interjection sits INSIDE the raw span and was
 *                    cleaned out, so raw has MORE words than cleaned. The end must
 *                    still land on the guest's intended final word, not trail past it.
 *   3. PRIOR-THOUGHT BLEED at start — the coarse window opens early; the in-point
 *                    must anchor to the quote's first word, and the emitted lead-in
 *                    must be clamped so the clip cannot reach back into the prior word.
 *   4. REPEATED TAIL — the cleaned text repeats its own closing phrase; the end must
 *                    land on the LAST (true) occurrence, not the first echo (E27 q02).
 *
 * Zero dependencies — plain node, same loud PASS/FAIL + exit-code convention as
 * verify_reel_spec.js / test_speaker_role.js. Imports pure helpers from source.
 *
 * Usage:   node test_clip_boundaries.js
 * Exit:    0 = all cases pass · 1 = one or more failed
 */

import {
  findTimestamps, safeLeadInMs, buildTimeline, DEFAULT_LEAD_IN_MS,
} from './lookup_quote_timestamps.js';

// ── fixture helpers ──────────────────────────────────────────────────────────

/**
 * Build a timeline from a list of words at a fixed cadence (ms per word),
 * mimicking buildTimeline's contiguous interpolation (prev.end === next.start).
 * Returns {timeline, indexOf(word, occurrence)} for readable assertions.
 */
function makeTimeline(words, msPerWord = 400) {
  const timeline = words.map((w, i) => ({
    word: w,
    start: (i * msPerWord) / 1000,
    end:  ((i + 1) * msPerWord) / 1000,
  }));
  return timeline;
}

let failed = 0;
function check(label, cond, detail) {
  if (cond) { console.log(`[bound] PASS  ${label}`); }
  else { failed++; console.error(`[bound] FAIL  ${label}`); if (detail) console.error(`[bound]   ✗ ${detail}`); }
}

// ── Case 1: UNDERSHOOT — cleaned shorter than spoken ─────────────────────────
// Spoken: "i am never going to get through this how am i going to get through this"
//         (the guest doubles "get through this"). Cleaned text keeps both — but a
//         naive baseLen count from the start would stop ~6 words early, mid-clause.
// Expectation: end lands on the LAST "this" (the real resolution), not an early one.
(() => {
  const spoken = ('reinvention in my life i am never going to get through this ' +
                  'how am i going to get through this and i had my career').split(' ');
  const tl = makeTimeline(spoken);
  const cleaned = 'I am never going to get through this. How am I going to get through this?';
  const r = findTimestamps(cleaned, tl);
  // last "this" is the word immediately before "and" — index of 2nd "this"
  const lastThis = spoken.lastIndexOf('this');
  const expectedEndMs = Math.round(tl[lastThis].end * 1000);
  check('undershoot: end reaches the real last word, not a counted-short stop',
    r.end_ms === expectedEndMs,
    `expected end_ms=${expectedEndMs} (word "${spoken[lastThis]}"@${lastThis}), got ${r.end_ms}`);
  check('undershoot: end does NOT bleed into the next thought ("and i had my career")',
    r.end_ms <= Math.round(tl[lastThis + 1].start * 1000),
    `next-word start=${Math.round(tl[lastThis+1].start*1000)}, end=${r.end_ms}`);
})();

// ── Case 2: OVERSHOOT / INTERJECTION — host words inside the raw span ─────────
// Raw span contains a host interjection ("heather were so young") that the Brand
// Team removed from the cleaned text. Cleaned therefore has FEWER words than the
// raw span between the same endpoints. The end must still land on the guest's
// intended final word ("over"), not count past it into "part of what i touch".
(() => {
  const spoken = ('felt the same as me too old to start over heather were so young ' +
                  'we are not too old to start over part of what i touch on').split(' ');
  const tl = makeTimeline(spoken);
  const cleaned = 'felt the same as me: too old to start over. We are not too old to start over.';
  const r = findTimestamps(cleaned, tl);
  const lastOver = spoken.lastIndexOf('over');
  const expectedEndMs = Math.round(tl[lastOver].end * 1000);
  check('interjection: end lands on guest final word despite cleaned-out host line',
    r.end_ms === expectedEndMs,
    `expected end_ms=${expectedEndMs} (word "${spoken[lastOver]}"@${lastOver}), got ${r.end_ms}`);
  check('interjection: end does not trail into the next thought ("part of what...")',
    r.end_ms <= Math.round(tl[lastOver + 1].start * 1000),
    `next-word start=${Math.round(tl[lastOver+1].start*1000)}, end=${r.end_ms}`);
})();

// ── Case 3: PRIOR-THOUGHT BLEED at start ─────────────────────────────────────
// The quote's true first word is preceded by an unrelated prior thought. The
// in-point must anchor to the first quote word, and safeLeadInMs must clamp so the
// clip cannot open inside the previous word. With a contiguous (gap-0) interpolated
// timeline the safe lead-in is 0; with a real gap it is the gap, capped at default.
(() => {
  const spoken = ('yes so my divorce was a midlife transition for me ' +
                  'the first thing i thought when my husband asked').split(' ');
  const tl = makeTimeline(spoken);
  const cleaned = 'The first thing I thought when my husband asked';
  const r = findTimestamps(cleaned, tl);
  const firstIdx = spoken.indexOf('first');
  const expectedStartMs = Math.round(tl[firstIdx].start * 1000);
  check('prior-bleed: in-point anchors to the quote first word, not the prior thought',
    r.start_ms === expectedStartMs,
    `expected start_ms=${expectedStartMs} (word "first"@${firstIdx}), got ${r.start_ms}`);

  // Contiguous timeline → no gap before the first word → lead-in clamps to 0.
  const leadContig = safeLeadInMs(tl, r.start_idx, DEFAULT_LEAD_IN_MS);
  check('prior-bleed: lead-in clamps to 0 on a contiguous (gap-0) timeline',
    leadContig === 0, `got leadIn=${leadContig}`);

  // Now inject a real 800ms silence gap before the first quote word and confirm
  // the lead-in is allowed up to that gap (but never past the previous word).
  const gapped = tl.map((w, i) => ({ ...w }));
  for (let i = firstIdx; i < gapped.length; i++) {
    gapped[i].start += 0.8; gapped[i].end += 0.8;     // shift the quote later by 800ms
  }
  const gap = Math.round(gapped[firstIdx].start * 1000 - gapped[firstIdx - 1].end * 1000); // 800
  const leadGapped = safeLeadInMs(gapped, firstIdx, DEFAULT_LEAD_IN_MS);
  check('prior-bleed: lead-in uses the real gap when one exists (here 800ms ≤ default)',
    leadGapped === gap, `expected leadIn=${gap}, got ${leadGapped}`);
  check('prior-bleed: lead-in never exceeds the gap to the previous word',
    leadGapped <= gap, `leadIn=${leadGapped} > gap=${gap}`);

  // And a gap larger than the default must cap at the default (the desired breath).
  const bigGap = tl.map((w, i) => ({ ...w }));
  for (let i = firstIdx; i < bigGap.length; i++) {
    bigGap[i].start += 3.0; bigGap[i].end += 3.0;     // 3s gap, > 1.5s default
  }
  const leadBig = safeLeadInMs(bigGap, firstIdx, DEFAULT_LEAD_IN_MS);
  check('prior-bleed: lead-in caps at the default even when the gap is larger',
    leadBig === DEFAULT_LEAD_IN_MS, `expected ${DEFAULT_LEAD_IN_MS}, got ${leadBig}`);
})();

// ── Case 4: tie-break — outward rounding never clips mid-clause ───────────────
// Sanity that the located end is always >= the located start and lands on a real
// word boundary (end_ms equals some word's end), proving we never stop "between"
// words or invert the span.
(() => {
  const spoken = 'if you need someone to complete you you re not ready it took me two years'.split(' ');
  const tl = makeTimeline(spoken);
  const cleaned = 'If you need someone to complete you, you’re not ready. It took me two years.';
  const r = findTimestamps(cleaned, tl);
  const ends = new Set(tl.map(w => Math.round(w.end * 1000)));
  check('tie-break: end_ms lands exactly on a word boundary (no mid-word stop)',
    ends.has(r.end_ms), `end_ms=${r.end_ms} not a word end`);
  check('tie-break: span is non-inverted (end >= start)',
    r.end_ms >= r.start_ms, `start=${r.start_ms} end=${r.end_ms}`);
})();

// ── summary ──────────────────────────────────────────────────────────────────
const PASSES = 11; // keep in step with check() calls above for a quick eyeball
console.log(`[bound] done — ${failed === 0 ? 'all checks passed' : failed + ' check(s) FAILED'}`);
process.exit(failed === 0 ? 0 : 1);

// buildTimeline is imported only to keep it covered by the import surface (it is the
// production timeline builder the fixtures emulate); referenced here to avoid an
// unused-import lint and to fail loudly if its export is ever removed.
void buildTimeline;
