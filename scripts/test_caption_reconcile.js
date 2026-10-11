#!/usr/bin/env node
/**
 * test_caption_reconcile.js — locks reconcileCleanedWords() against the E27 caption
 * regression shapes. v1.0 · 2026-06-22 (Jim)
 *
 * The renderer reveals captions word-by-word from quote.words[] {word,start_ms,
 * end_ms}. Those word STRINGS must be the BRAND-CLEANED tokens (no fillers, no host
 * interjection, "blow up"→"glow up"), but timed on the speaker's REAL aligned word
 * timing. reconcileCleanedWords does that by LCS-aligning cleaned tokens to the raw
 * aligned word stream within the quote's located window.
 *
 * Fixtures are tiny hand-built timelines (word + real start/end) so assertions are
 * exact and deterministic — no audio, no WhisperX, no network. Same loud PASS/FAIL
 * + exit-code convention as test_clip_boundaries.js / verify_reel_spec.js.
 *
 * Cases (the shapes ADR 0003 / John's E27 review named):
 *   1. PREFIX SUBSET — cleaned text is a clean prefix of the spoken span (q01):
 *      every cleaned token is matched to its real timing; the trailing spoken
 *      words ("I was 48, 49...") never appear in words[].
 *   2. DROPPED CLAUSE + REPEATED TAIL — a whole clause is cleaned out and the tail
 *      phrase repeats (q02 "...too old to start over"): the OPENING lands on the
 *      first copy, the CLOSING snaps to the LAST copy across the dropped clause.
 *   3. TRANSCRIPTION FIX — "blow"→"glow": the fixed word is shown (cleaned) and
 *      interpolated in time (no raw match), neighbors keep real timing.
 *   4. FULL-THOUGHT SPAN — the audio is cut to the full quote `text`; reconciling
 *      that full text fills the whole window so the LAST caption word reaches the
 *      window end. Sourcing the shorter reel_hook instead stops the captions far
 *      short and leaves a blank purple band (the exact E27 regression John found).
 *
 * Usage:  node test_caption_reconcile.js     Exit: 0 all pass · 1 any fail
 */

import {
  reconcileCleanedWords, buildTimelineFromWords,
  smoothCadence, CADENCE_CAP_MS, MAX_LEAD_MS,
} from './lookup_quote_timestamps.js';

let failures = 0;
function check(name, cond) {
  console.log(`[recon] ${cond ? 'PASS' : 'FAIL'}  ${name}`);
  if (!cond) failures++;
}

// Helper: build a timeline (seconds) from {word,start,end} list, then reconcile.
function reconcile(cleaned, rawWords) {
  const timeline = buildTimelineFromWords(rawWords);
  return reconcileCleanedWords(cleaned, timeline, 0, timeline.length - 1);
}

// ── Case 1: PREFIX SUBSET ────────────────────────────────────────────────────
{
  const raw = [
    { word: 'And', start: 0.0, end: 0.2 }, { word: 'the', start: 0.2, end: 0.4 },
    { word: 'first', start: 0.4, end: 0.7 }, { word: 'thing', start: 0.7, end: 1.0 },
    { word: 'I', start: 1.0, end: 1.1 }, { word: 'said', start: 1.1, end: 1.5 },
    { word: 'I', start: 2.0, end: 2.1 }, { word: 'was', start: 2.1, end: 2.4 },
    { word: 'forty', start: 2.4, end: 2.9 }, // trailing spoken words NOT in cleaned
  ];
  const out = reconcile('The first thing I said.', raw);
  const seq = out.map(w => w.word).join(' ');
  check('prefix: caption text is the cleaned tokens only', seq === 'The first thing I said.');
  check('prefix: "forty" (spoken-but-cleaned-out) absent', !seq.toLowerCase().includes('forty'));
  check('prefix: "first" carries its REAL timing (400ms)', out[1].start_ms === 400 && out[1].end_ms === 700);
  let mono = true; for (let i = 1; i < out.length; i++) if (out[i].start_ms < out[i - 1].start_ms) mono = false;
  check('prefix: timings monotonic', mono);
}

// ── Case 2: DROPPED CLAUSE + REPEATED TAIL ───────────────────────────────────
{
  // Spoken: "we are not old. <dropped clause>. we are not too old to start over"
  const raw = [
    { word: 'We', start: 0.0, end: 0.2 }, { word: 'are', start: 0.2, end: 0.4 },
    { word: 'not', start: 0.4, end: 0.6 }, { word: 'old', start: 0.6, end: 0.9 },
    // dropped clause:
    { word: 'and', start: 1.0, end: 1.2 }, { word: 'I', start: 1.2, end: 1.3 },
    { word: 'began', start: 1.3, end: 1.7 }, { word: 'focusing', start: 1.7, end: 2.3 },
    // true closing (the repeated tail):
    { word: 'we', start: 5.0, end: 5.2 }, { word: 'are', start: 5.2, end: 5.4 },
    { word: 'not', start: 5.4, end: 5.6 }, { word: 'too', start: 5.6, end: 5.8 },
    { word: 'old', start: 5.8, end: 6.0 }, { word: 'to', start: 6.0, end: 6.1 },
    { word: 'start', start: 6.1, end: 6.4 }, { word: 'over', start: 6.4, end: 6.8 },
  ];
  // cleaned keeps only "We are not too old to start over" (the closing form)
  const out = reconcile('We are not too old to start over.', raw);
  const last = out[out.length - 1];
  check('repeated-tail: closing "over." snaps to the LAST occurrence (~6.8s)',
    last.start_ms >= 6000);
  // "too" only exists in the closing, so it pins there
  const tooW = out.find(w => w.word.toLowerCase().replace(/[^a-z]/g, '') === 'too');
  check('repeated-tail: "too" pinned to the closing region (≥5.5s)', tooW && tooW.start_ms >= 5500);
  let mono = true; for (let i = 1; i < out.length; i++) if (out[i].start_ms < out[i - 1].start_ms) mono = false;
  check('repeated-tail: timings monotonic across the dropped clause', mono);
  check('repeated-tail: no dropped-clause word leaked into captions',
    !out.map(w => w.word).join(' ').toLowerCase().match(/began|focusing/));
}

// ── Case 3: TRANSCRIPTION FIX (blow→glow) ────────────────────────────────────
{
  const raw = [
    { word: 'my', start: 0.0, end: 0.2 }, { word: 'own', start: 0.2, end: 0.5 },
    { word: 'blow', start: 0.5, end: 0.9 }, { word: 'up', start: 0.9, end: 1.2 },
    { word: 'now', start: 1.2, end: 1.5 },
  ];
  const out = reconcile('my own glow up now', raw);
  const seq = out.map(w => w.word).join(' ');
  check('fix: caption shows corrected "glow", not spoken "blow"',
    seq === 'my own glow up now' && !seq.includes('blow'));
  const glow = out.find(w => w.word === 'glow');
  // glow is unmatched → interpolated between "own"(end .5) and "up"(start .9)
  check('fix: "glow" interpolated between its neighbors (0.5–0.9s)',
    glow.start_ms >= 500 && glow.end_ms <= 900);
  check('fix: "up" keeps its real timing (900–1200ms)',
    out.find(w => w.word === 'up').start_ms === 900);
}

// ── Case 4: FULL-THOUGHT SPAN (the E27 blank-band regression) ────────────────
// The audio clip is cut to the full quote `text`. If the karaoke is sourced from
// the shorter reel_hook, the captions run out partway and leave a blank band for
// the rest of the audio. This case proves that reconciling the FULL text fills
// the whole located window: the LAST caption word must land at/near the window's
// real end, not far short of it (which is what the hook produced).
{
  // Spoken thought (the full `text`), 10 words across ~5s.
  const raw = [
    { word: 'I', start: 0.0, end: 0.2 }, { word: 'am', start: 0.2, end: 0.5 },
    { word: 'too', start: 0.5, end: 0.8 }, { word: 'old', start: 0.8, end: 1.1 },
    { word: 'for', start: 1.1, end: 1.3 }, { word: 'this', start: 1.3, end: 1.7 },
    // the hook would STOP here; the audio (and text) continue:
    { word: 'how', start: 3.0, end: 3.3 }, { word: 'am', start: 3.3, end: 3.5 },
    { word: 'I', start: 3.5, end: 3.6 }, { word: 'getting', start: 3.6, end: 4.0 },
    { word: 'through', start: 4.0, end: 4.6 },
  ];
  const fullText = 'I am too old for this. How am I getting through?';
  const hookText = 'I am too old for this.';
  const winEndMs = Math.round(raw[raw.length - 1].end * 1000); // 4600
  const fromFull = reconcile(fullText, raw);
  const fromHook = reconcile(hookText, raw);
  const fullLastEnd = fromFull[fromFull.length - 1].end_ms;
  const hookLastEnd = fromHook[fromHook.length - 1].end_ms;
  check('full-thought: last caption word reaches the window end (no blank band)',
    fullLastEnd >= winEndMs - 100);
  check('full-thought: the SHORT hook would stop far short (proves the regression)',
    hookLastEnd < winEndMs - 1000);
  check('full-thought: caption includes the words after the hook ("through")',
    fromFull.map(w => w.word).join(' ').toLowerCase().includes('through'));
  let mono = true; for (let i = 1; i < fromFull.length; i++) if (fromFull[i].start_ms < fromFull[i - 1].start_ms) mono = false;
  check('full-thought: timings monotonic', mono);
}

// ── Case 5: CADENCE CAP — moderate gap closed, first/last/order preserved ────
// The accumulating karaoke must not FREEZE while the audio plays through a cleaned-
// out span. smoothCadence eases later words earlier to cap the reveal stall, under
// hard constraints: first word never moves, last word never pushed past its onset,
// monotonic, and a word never leads its own audio by more than MAX_LEAD_MS.
{
  // words[] shape: {word,start_ms,end_ms}. A clean 2.0s moderate gap (short filler
  // cleaned out) sits between "lighter." and "A" — well under MAX_LEAD_MS so it
  // should fully close to CADENCE_CAP_MS.
  const words = [
    { word: '90',       start_ms: 0,    end_ms: 200 },
    { word: 'pounds',   start_ms: 200,  end_ms: 600 },
    { word: 'lighter.', start_ms: 600,  end_ms: 1000 },
    { word: 'A',        start_ms: 3000, end_ms: 3100 }, // 2.4s reveal gap after "lighter."
    { word: 'lot',      start_ms: 3100, end_ms: 3400 },
    { word: 'stronger.',start_ms: 3400, end_ms: 3900 },
  ];
  const out = smoothCadence(words);
  check('cap: first word never moves', out[0].start_ms === 0);
  check('cap: order/words unchanged', out.map(w => w.word).join(' ') === '90 pounds lighter. A lot stronger.');
  let mono = true; for (let i = 1; i < out.length; i++) if (out[i].start_ms < out[i - 1].start_ms) mono = false;
  check('cap: reveals monotonic', mono);
  // "A" originally 2.4s after "lighter." (1000→3000); cap closes it to ≤ CADENCE_CAP_MS
  const aGap = out[3].start_ms - out[2].start_ms;
  check(`cap: moderate gap closed to ≤ cap (${aGap}ms ≤ ${CADENCE_CAP_MS})`, aGap <= CADENCE_CAP_MS + 1);
  // "A" (real onset 3000) may not be revealed more than MAX_LEAD_MS early
  check('cap: word never leads its own audio by more than MAX_LEAD_MS',
    out[3].start_ms >= 3000 - MAX_LEAD_MS);
  // dense tail ("A lot stronger." already <cap apart) left essentially as-is relative to each other
  check('cap: last word never pushed past its real onset', out[5].start_ms <= 3400);
}

// ── Case 6: TRUE EXCISION — large gap only shaved, next sentence NOT dragged up ─
// A multi-second cleaned-out clause/host-interjection (q02/q04 shape). Capping must
// NOT collapse it — that would race the closing sentence seconds ahead of its audio.
// It may shrink by at most MAX_LEAD_MS; the rest is honest residual.
{
  const words = [
    { word: 'not',    start_ms: 0,     end_ms: 200 },
    { word: 'ready.', start_ms: 200,   end_ms: 600 },
    // 13s excision (cleaned-out clause still in the audio):
    { word: 'It',     start_ms: 13600, end_ms: 13700 },
    { word: 'took',   start_ms: 13700, end_ms: 13900 },
  ];
  const out = smoothCadence(words);
  const bigGap = out[2].start_ms - out[1].start_ms;
  check('excision: large gap NOT collapsed (residual remains)', bigGap > 1000 + CADENCE_CAP_MS);
  check('excision: large gap shaved by exactly MAX_LEAD_MS (honesty bound)',
    out[2].start_ms === 13600 - MAX_LEAD_MS);
  check('excision: post-excision word still reveals close to its real audio',
    out[2].start_ms >= 13600 - MAX_LEAD_MS);
  let mono = true; for (let i = 1; i < out.length; i++) if (out[i].start_ms < out[i - 1].start_ms) mono = false;
  check('excision: monotonic', mono);
}

console.log(`[recon] ${failures === 0 ? 'done — all checks passed' : failures + ' check(s) FAILED'}`);
process.exit(failures === 0 ? 0 : 1);
