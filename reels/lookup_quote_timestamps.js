#!/usr/bin/env node
/**
 * lookup_quote_timestamps.js — match Brand Team quotes to WhisperX segment timestamps.
 *
 * Brand Team members set transcript_start_ms / transcript_end_ms to 0 because
 * they work from plain text.  This script fills them in by fuzzy-matching each
 * quote's text against the segment-level WhisperX transcript.
 *
 * Also adds:
 *   - index        (1-based integer, parsed from id like "q01" → 1)
 *   - topic_slug   (3-word kebab from reel_hook or text, for clip filenames)
 *
 * Algorithm:
 *   1. Build a flat word timeline from transcript segments, distributing each
 *      segment's duration linearly across its words (no word-level alignment
 *      needed — transcript_fast pass gives segment-level timing only).
 *   2. For each quote, slide a window of ≈ quoteWords length over the timeline.
 *   3. Score each window by token overlap (significant words, 3+ chars).
 *   4. Assign start_ms / end_ms from the best-scoring window.
 *   5. Skip update if best score < threshold (leaves timestamps at 0, logged).
 *
 * Usage:
 *   node lookup_quote_timestamps.js \
 *     --quotes        /path/to/quotes.json       (Brand Team output with 0 timestamps)
 *     --transcript    /path/to/transcript_fast.json
 *     [--out          /path/to/quotes.json]       (defaults to --quotes, in-place)
 *     [--min-score    0.5]                        (overlap threshold, default 0.5)
 */

import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import process from 'node:process';

// True only when this file is run directly (`node lookup_quote_timestamps.js …`),
// false when it is imported (e.g. by the speaker-role unit test). Lets the pure
// helpers be imported without the CLI/file-I/O side effects firing.
const isEntryPoint = process.argv[1] === fileURLToPath(import.meta.url);

// ── CLI parsing ─────────────────────────────────────────────────────────────

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i++) {
    if (argv[i].startsWith('--')) {
      args[argv[i].slice(2)] = argv[i + 1];
      i++;
    }
  }
  return args;
}

const args = parseArgs(process.argv);
const quotesPath   = args.quotes;
const transcriptPath = args.transcript;
// Optional: explicit path to the word-aligned transcript (align_episode.py output).
// When present (or when --transcript itself already carries a top-level words[]),
// the boundary stage uses REAL word timing instead of interpolated segment timing.
const alignedPath  = args.aligned;
const outPath      = args.out || quotesPath;
const minScore     = parseFloat(args['min-score'] || '0.5');

// Mirrors RENDER_CONFIG.leadInMs in render_reel.js. The desired breath before
// the first word; safeLeadInMs() clamps it down per-quote so the clip can never
// open inside the previous thought (ADR 0003). If render's default changes, keep
// these in step — the per-quote value lookup emits overrides render's default,
// but quotes without the field fall back to render's constant.
const DEFAULT_LEAD_IN_MS = 1500;

// Mirrors RENDER_CONFIG.tailMs in render_reel.js. The breath after the last word.
// Used here so buildAudioSegments takes the complement over the SAME padded window
// [start-leadIn, end+tail] that render's clip cut uses — keeping the audio-segment
// math and render's window identical. If render's tail default changes, keep in step.
const RENDER_TAIL_MS = 500;

if (isEntryPoint && (!quotesPath || !transcriptPath)) {
  console.error(
    'Usage: node lookup_quote_timestamps.js --quotes <path> --transcript <path> [--out <path>] [--min-score 0.5]'
  );
  process.exit(1);
}

// ── text helpers ─────────────────────────────────────────────────────────────

const STOPWORDS = new Set([
  'the','a','an','and','or','but','in','on','at','to','for','of','with',
  'is','was','are','were','be','been','it','its','this','that','we','i',
  'you','he','she','they','do','have','has','had','not','so','if','when',
  'can','just','get','like','all','more','up','out','go','my','our',
]);

function normalize(text) {
  return (text || '')
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function significantWords(text) {
  return normalize(text)
    .split(' ')
    .filter(w => w.length >= 3 && !STOPWORDS.has(w));
}

/** Token overlap: what fraction of quoteWords appear in windowWords. */
function overlapScore(quoteWords, windowWords) {
  if (quoteWords.length === 0) return 0;
  const winSet = new Set(windowWords);
  let hits = 0;
  for (const w of quoteWords) if (winSet.has(w)) hits++;
  return hits / quoteWords.length;
}

// ── speaker_role derivation ────────────────────────────────────────────────────

/**
 * Known host name aliases, normalized (lowercased, hyphens→spaces, whitespace
 * collapsed). Single source of truth — add a host alias here, nowhere else.
 *
 * The host is Heather Reilly Hiemstra. The Brand Team emits her sometimes as the
 * bare first name "Heather" and sometimes by full name (with or without the
 * hyphen in "Reilly-Hiemstra"), so all three spellings live here.
 */
const HOST_ALIASES = new Set([
  'heather',
  'heather reilly hiemstra',
]);

/**
 * Normalize a speaker name for alias comparison: lowercase, turn hyphens into
 * spaces so "Reilly-Hiemstra" and "Reilly Hiemstra" collapse to one form, strip
 * any remaining punctuation, and squeeze whitespace.
 */
function normalizeSpeakerName(name) {
  return (name || '')
    .toLowerCase()
    .replace(/-/g, ' ')          // hyphen is a word separator here, not a delimiter to drop
    .replace(/[^a-z0-9\s]/g, ' ') // drop dots/commas etc. ("Dr. Joe" → "dr joe")
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * Decide whether a quote's speaker is the host or a guest.
 *
 * WHAT: returns 'host' | 'guest'. render_reel.js uses this to pick the background
 * image (host background vs. guest background).
 *
 * WHY this is not a substring match (the E27 trap): the host (Heather Reilly
 * Hiemstra) and a guest (Lorna Vashti-Mercer) share the first name "Heather".
 * The old rule was `speaker.includes('heather') ? 'host' : 'guest'`, which tagged
 * EVERY one of the guest's quotes as host — E27's reels rendered with the host
 * background over a guest's words. First-name collisions are inevitable across a
 * podcast's guest roster, so the discriminator must be "is this name a KNOWN host
 * alias," not "does this name contain the host's first name."
 *
 * The rule: a name is the host only if, after normalization, it matches a
 * HOST_ALIAS exactly. Bare "Heather" matches (back-compat with the Brand Team's
 * shorthand); "Heather Reilly Hiemstra"/"...-Hiemstra" match; but "Heather
 * <anything-else>" carries non-host name tokens and is therefore a guest.
 *
 * AC1: an explicit role already on the quote is authoritative and never recomputed.
 */
function deriveSpeakerRole(speaker, existingRole) {
  if (existingRole) return existingRole;
  const name = normalizeSpeakerName(speaker);
  return HOST_ALIASES.has(name) ? 'host' : 'guest';
}

// ── topic_slug derivation ─────────────────────────────────────────────────────

const SLUG_STOPS = new Set([
  'the','a','an','and','or','but','in','on','at','to','for','of','with',
  'is','was','are','were','be','its','this','that','we','i','you','he','she',
  'your','my','our','can','its','so','if','when','not','do','have','has',
]);

function deriveTopicSlug(q) {
  const source = q.reel_hook || q.text || '';
  const words = normalize(source)
    .split(' ')
    .filter(w => w.length >= 2 && !SLUG_STOPS.has(w))
    .slice(0, 3);
  return words.length > 0 ? words.join('-') : 'clip';
}

// ── word timeline builder ─────────────────────────────────────────────────────

/**
 * Build a flat array of {word, start, end} from WhisperX segment-level JSON.
 * Each segment's duration is distributed linearly across its words (no word
 * alignment available in the fast pass — this gives ~0.5-1s precision which
 * is sufficient for clip extraction with the lead-in/tail buffers the script adds).
 *
 * THIS IS THE INTERPOLATED FLOOR (ADR 0003). Because every word in a segment is
 * spread evenly, consecutive words are CONTIGUOUS (prev.end === next.start) — there
 * are NO inter-word gaps, so safeLeadInMs can never prove a breath and the per-quote
 * lead-in clamps to 0 for every quote. That is the exact symptom we flagged on E27.
 * The fix is to feed REAL word timing instead (buildTimelineFromWords below); this
 * interpolated builder stays as the fallback for older episodes that have only a
 * segment-level transcript_fast.json and no aligned pass.
 */
function buildTimeline(segments) {
  const timeline = [];
  for (const seg of segments) {
    const words = normalize(seg.text).split(' ').filter(w => w.length > 0);
    if (words.length === 0) continue;
    const dur = (seg.end - seg.start);
    const perWord = dur / words.length;
    for (let i = 0; i < words.length; i++) {
      timeline.push({
        word: words[i],
        start: seg.start + i * perWord,
        end:   seg.start + (i + 1) * perWord,
      });
    }
  }
  return timeline;
}

/**
 * Build the flat timeline from REAL word-level timing (the align_episode.py output,
 * transcript_aligned.json). Each entry already has a measured start/end from
 * wav2vec2 forced alignment, so consecutive words carry REAL gaps — silence the
 * boundary clamp can see. We only normalize the word token to match the matcher's
 * vocabulary (lowercase, punctuation stripped); timings pass through untouched.
 *
 * WHY this is the right input for the find-don't-count logic (ADR 0003): every rule
 * downstream (head/tail anchor location, the outward tie-break, safeLeadInMs's
 * "prove a breath before the first word") is only as good as the gaps in the
 * timeline. Interpolated timing has none; aligned timing has the real ones. Same
 * algorithm, sharper input — nothing in findTimestamps changes.
 *
 * A normalized word can tokenize to MULTIPLE matcher tokens (e.g. "X" stays "x",
 * but a hyphenated/garbled token could split). We keep the word's own [start,end]
 * span and split it evenly across its sub-tokens so the flat timeline stays 1:1
 * with the matcher's tokenization — this is a micro-interpolation INSIDE one real
 * word (sub-100ms), not the segment-wide guess buildTimeline makes.
 */
function buildTimelineFromWords(words) {
  const timeline = [];
  for (const w of words) {
    const toks = normalize(w.word).split(' ').filter(t => t.length > 0);
    if (toks.length === 0) continue;
    const start = w.start;
    const end = w.end;
    const per = (end - start) / toks.length;
    for (let i = 0; i < toks.length; i++) {
      timeline.push({
        word: toks[i],
        start: start + i * per,
        end:   start + (i + 1) * per,
      });
    }
  }
  return timeline;
}

// ── timestamp lookup ──────────────────────────────────────────────────────────
//
// WHAT THIS SOLVES — the count-vs-find trap, and the cleaned-vs-raw drift.
// ADR 0003 ("Reel clip boundaries frame a complete thought").
//
// The Brand Team CLEANS each quote: host interjections are removed, fillers are
// dropped, transcription errors are fixed. So the editorial `text` does NOT
// word-align to the raw transcript. Two failure shapes follow:
//
//   • UNDERSHOOT — the cleaned text is SHORTER than what was actually spoken
//     across the span (e.g. cleaned drops a stutter or a doubled clause). If you
//     locate the start and then walk forward exactly `baseLen` cleaned words
//     (the OLD `endI = bestI + baseLen - 1`), you stop BEFORE the real last word.
//     The thought is guillotined mid-clause (E27 reels 1 & 4).
//
//   • OVERSHOOT — a host interjection sat INSIDE the raw span and was cleaned
//     out, so the raw span has MORE words than the cleaned text between the same
//     endpoints. A forward count then sails past the guest's actual final word
//     into the next breath (E27 reel 2's trailing tail).
//
// The defence is to STOP COUNTING and START FINDING: anchor both ends to the
// LOCATED position of the quote's own boundary words in the timeline, tolerating
// any number of extra/removed words in between. A short tail anchor (the last few
// significant words of the cleaned text) pins the out-point to the real last word
// regardless of drift. A short head anchor pins the in-point.
//
// THE TIMELINE FLOOR: word timestamps here are interpolated (segment duration
// spread evenly across the segment's words — see buildTimeline). They are guesses
// good to ~0.5–1s, not real onsets. Every rule below therefore rounds OUTWARD at
// the final boundary (ADR tie-break: complete over crisp — keep the whole word
// plus a little breath, never clip mid-clause) and clamps against thought
// boundaries so the imprecision can only ever ADD a little tail, never eat a word.

const HEAD_ANCHOR_LEN = 4; // significant words used to pin the in-point
const TAIL_ANCHOR_LEN = 4; // significant words used to pin the out-point

// ── caption cadence cap (the cleaned-text-over-uncleaned-audio defence) ─────────
//
// WHAT: the maximum time the accumulating karaoke caption may sit on the SAME text
// while the audio keeps playing. Above this, the next caption word is eased earlier
// so the on-screen text keeps advancing instead of reading as FROZEN.
//
// WHY this exists — the cleaned-text / uncleaned-audio gap (ADR 0003 family):
// captions reveal the BRAND-CLEANED tokens on the speaker's REAL onsets (via
// reconcileCleanedWords' LCS match). But the rendered audio is ONE continuous cut
// from transcript_start → transcript_end (render_reel.extractAudioClip), so it still
// contains every word the Brand Team CLEANED OUT — fillers, repeats, self-corrections,
// and q02's removed HOST interjection. Across a cleaned-out span there is no caption
// word to reveal, so the accumulated text stalls until the next matched onset → a
// visible mid-thought freeze (the polish John flagged on E27 01/02/04).
//
// CADENCE_CAP_MS (1000ms): the perceptual stall threshold. Below ~1s the eye reads a
// gap as natural breath; above it, the karaoke reads as stuck. So no two consecutive
// reveals may sit more than this far apart when we can honestly close the gap.
//
// MAX_LEAD_MS (2500ms): the honesty bound. A word may lead its OWN real audio onset by
// at most this much. This fully bridges natural-cadence gaps and SHORT cleaned fillers
// (where the next word genuinely arrives a beat later) but structurally REFUSES to drag
// a whole cleaned-out clause or host interjection forward — pulling "It took me two
// years" 12s ahead of its audio (q04) or the post-interjection "No, no, no" 8s ahead
// (q02) would trade a freeze for a worse lie: text racing seconds ahead of the speaker.
// Those true multi-second excisions are left as residual and only fully erased by the
// separate mid-quote audio-excision capability (not built here). See smoothCadence.
const CADENCE_CAP_MS = 1000;
const MAX_LEAD_MS    = 2500;

/** First `n` significant tokens of a text, in order (dups kept — order matters). */
function leadSignificant(text, n) {
  return significantWords(text).slice(0, n);
}
/** Last `n` significant tokens of a text, in order. */
function tailSignificant(text, n) {
  const sig = significantWords(text);
  return sig.slice(Math.max(0, sig.length - n));
}

/**
 * Find the index in `timeline` where the ordered token sequence `seq` first
 * appears at or after `fromIdx`, allowing up to `maxGap` non-matching words
 * BETWEEN consecutive matches (this absorbs cleaned-out interjections/fillers
 * and the occasional transcription mismatch). Returns {startIdx, endIdx} of the
 * first and last matched timeline word, or null if not found.
 *
 * WHY a gap-tolerant subsequence match and not an exact contiguous match: the
 * cleaned tail "...too old to start over" may be spoken as "...too old to, you
 * know, start over" — exact match fails, the thought is real. Tolerating gaps
 * finds the true anchor; bounding the gap keeps it from matching a coincidental
 * scatter of the same words elsewhere.
 */
function locateSequence(timeline, seq, fromIdx, maxGap, searchLimit) {
  if (seq.length === 0) return null;
  const hardStop = searchLimit == null ? timeline.length : Math.min(timeline.length, fromIdx + searchLimit);
  for (let i = fromIdx; i < hardStop; i++) {
    if (timeline[i].word !== seq[0]) continue;
    // try to extend the match from here
    let s = 0, gap = 0, last = i;
    for (let j = i; j < timeline.length && s < seq.length; j++) {
      if (timeline[j].word === seq[s]) { last = j; s++; gap = 0; }
      else if (s > 0) { gap++; if (gap > maxGap) break; }
    }
    if (s === seq.length) return { startIdx: i, endIdx: last };
  }
  return null;
}

/**
 * Find the best position in `timeline` for `quoteText`.
 *
 * Returns { start_ms, end_ms, score, start_idx, end_idx } where the *_idx are
 * timeline word positions for the located first/last significant word. main()
 * uses start_idx to compute a clamped per-quote lead-in.
 */
function findTimestamps(quoteText, timeline) {
  const qSig = significantWords(quoteText);
  if (qSig.length === 0) return { start_ms: 0, end_ms: 0, score: 0, start_idx: -1, end_idx: -1 };

  // ── Stage 1: coarse locate (unchanged in spirit — robust to cleaning drift) ──
  // A windowed token-overlap scan finds the rough REGION of the quote. We keep
  // this because it tolerates heavy cleaning: it scores on set overlap, not
  // position, so it lands in the right neighbourhood even when words moved or
  // dropped. We only use it to seed the precise anchor search below.
  const baseLen = normalize(quoteText).split(' ').filter(w => w).length;
  const slack   = Math.max(3, Math.floor(baseLen * 0.3));
  const winSize = baseLen + slack;

  let bestScore = 0;
  let bestI     = 0;
  for (let i = 0; i <= timeline.length - 1; i++) {
    const win     = timeline.slice(i, i + winSize);
    const winSig  = win.map(w => w.word).filter(w => w.length >= 3 && !STOPWORDS.has(w));
    const score   = overlapScore(qSig, winSig);
    if (score > bestScore) { bestScore = score; bestI = i; }
  }

  if (bestScore < minScore) {
    return { start_ms: 0, end_ms: 0, score: bestScore, start_idx: -1, end_idx: -1 };
  }

  // ── Stage 2: FIND the in-point (first significant word of the thought) ───────
  // The coarse window often opens a few words EARLY (its overlap is maximised by
  // a window straddling the prior sentence's tail — this is exactly E27 reel 1's
  // "opens on the previous sentence" bug). So we search a small zone bracketing
  // bestI for the quote's head anchor and anchor the start to its FIRST word.
  const head = leadSignificant(quoteText, HEAD_ANCHOR_LEN);
  // Search from a little before bestI (coarse start can also land slightly late)
  // through the front third of the window; head words must appear near the open.
  const headFrom = Math.max(0, bestI - slack);
  const headHit  = locateSequence(timeline, head, headFrom, /*maxGap*/ 6, /*limit*/ winSize + slack);
  const startIdx = headHit ? headHit.startIdx : bestI;

  // ── Stage 3: FIND the out-point (last significant word of the thought) ───────
  // Never `bestI + baseLen`. We locate the cleaned tail anchor and pin the end to
  // the LAST matched word. We search FORWARD from the in-point so we can't match
  // an earlier coincidental occurrence, and we prefer the occurrence whose end is
  // nearest the expected end (start + spoken length) so a phrase the guest repeats
  // (e.g. q02's doubled "too old to start over") resolves to the RIGHT one — the
  // one that actually closes the thought, not the first echo.
  const tail = tailSignificant(quoteText, TAIL_ANCHOR_LEN);
  // The cleaned text can REPEAT its own tail phrase (E27 q02 says "...too old to
  // start over. ... We are not too old to start over." — the anchor occurs twice).
  // The TRUE final word is the LAST such occurrence, because cleaned text is read
  // left-to-right: whatever closes the quote is the last time the tail is spoken.
  // So among candidate tail matches we take the LAST one within a bounded span —
  // this also satisfies the ADR tie-break (round OUTWARD: prefer the later, whole
  // landing over an earlier echo). The bound stops a far-future echo elsewhere in
  // the episode from hijacking the end: the raw span may run longer than the
  // cleaned word count (interjections), so we allow up to ~2× baseLen of slack but
  // no further.
  const tailLimit = startIdx + baseLen * 2 + slack;
  let endIdx = -1;
  {
    let from = startIdx;
    while (from < timeline.length) {
      const hit = locateSequence(timeline, tail, from, /*maxGap*/ 6, /*limit*/ (tailLimit - from) + 1);
      if (!hit || hit.endIdx > tailLimit) break;
      endIdx = hit.endIdx;          // keep the LAST in-bounds occurrence
      from = hit.startIdx + 1;      // advance to look for a still-later one
    }
  }
  // Fallback: if the tail anchor couldn't be found (heavy mismatch), fail SAFE by
  // rounding OUTWARD — extend to the END of the segment containing the coarse end,
  // never the bare counted word. Better a small tail than a clipped clause.
  if (endIdx < 0) {
    endIdx = Math.min(bestI + baseLen - 1, timeline.length - 1);
  }
  if (endIdx < startIdx) endIdx = Math.min(startIdx + baseLen - 1, timeline.length - 1);

  // ── Absorb the cleaned tail's trailing STOPWORDS ─────────────────────────────
  // The anchor pins endIdx to the last SIGNIFICANT word of the cleaned text, but a
  // thought commonly ends on a stopword: "...get through [this]?", "...you're not
  // [ready].", "...over [with]." The last significant word is "through"/"not"/"over"
  // and stopping there would guillotine the real final word. So we walk forward
  // past endIdx while the next timeline word matches the cleaned text's own
  // trailing tokens (the literal words after its last significant word). This is
  // the ADR's outward-rounding tie-break made concrete: we only ever ADD the
  // thought's own closing words, never reach into the next thought.
  const cleanedTokens = normalize(quoteText).split(' ').filter(Boolean);
  let trailing = [];
  for (let k = cleanedTokens.length - 1; k >= 0; k--) {
    const w = cleanedTokens[k];
    if (w.length >= 3 && !STOPWORDS.has(w)) break; // hit the last significant word
    trailing.unshift(w);
  }
  for (const t of trailing) {
    const cand = timeline[endIdx + 1];
    if (cand && cand.word === t) endIdx++;
    else break; // stop the moment the transcript diverges — never over-extend
  }

  // ── Stage 4: trailing guard — never cross into the NEXT thought ──────────────
  // The out-point may not extend past the onset of the thought AFTER the located
  // final word. With only interpolated timing, the sharpest "next thought" signal
  // we have is the start of the next timeline word; we let the natural word end
  // stand (rounding outward by the half-word the interpolation already gives us)
  // but cap it at the next word's start so we never bleed a following word in.
  let endMs = Math.round(timeline[endIdx].end * 1000);
  const nextWord = timeline[endIdx + 1];
  if (nextWord) {
    const guardMs = Math.round(nextWord.start * 1000);
    if (endMs > guardMs) endMs = guardMs; // shouldn't happen with clean interpolation, but guard anyway
  }

  return {
    start_ms:  Math.round(timeline[startIdx].start * 1000),
    end_ms:    endMs,
    score:     bestScore,
    start_idx: startIdx,
    end_idx:   endIdx,
  };
}

// ── cleaned-text → word-timing reconciliation ──────────────────────────────────
//
// WHAT THIS SOLVES — the caption karaoke must reveal the BRAND-CLEANED text
// word-by-word, on the speaker's REAL timing. Two texts are in play and they do
// NOT word-align (same reason the boundary logic above can't count):
//
//   • the RAW aligned transcript (transcript_aligned.json) — every word actually
//     spoken, with a measured [start,end], INCLUDING fillers, a host interjection
//     inside q02, and transcription errors ("blow up" for "glow up");
//   • the CLEANED caption text (reel_hook / quote_text_clean) — what the Brand
//     Team approved to show on screen: fillers removed, the host interjection
//     removed, "blow up"→"glow up" fixed.
//
// We must show the CLEANED tokens but time them from the RAW words. So we align
// the cleaned token stream against the raw word stream WITHIN this quote's located
// window [startIdx..endIdx] (the same window the boundary finder pinned), matching
// greedily and tolerating raw words the cleaning dropped:
//
//   – a cleaned token that EQUALS the next raw word (normalized) takes that raw
//     word's real [start_ms,end_ms]. Skipped raw words in between are the cleaned-
//     out fillers/interjection — we step over them, never emit them as captions.
//   – a cleaned token with NO raw match (a fix like "glow" for spoken "blow", or
//     an added connective) is INTERPOLATED: it borrows time evenly between the
//     previous matched word's end and the next matched word's start. Sub-second,
//     and it never reaches outside the window, so a cleaned-only word can never
//     drag in a dropped interjection's timing.
//
// Output shape mirrors the approved E23/E24/E25 words[] exactly: {word, start_ms,
// end_ms} in absolute ms, the `word` carrying its display punctuation so the
// karaoke reads the way it did on the approved reels. The ONLY difference from
// those episodes is the source of the `word` STRING: there it was raw spoken text;
// here it is the brand-cleaned token, which is the regression John flagged.

/** Normalize a single token for matching: lowercase, strip non-alphanumerics. */
function normToken(t) {
  return (t || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

/**
 * Reconcile a cleaned caption text to real word timings using the raw aligned
 * `timeline`, restricted to [startIdx..endIdx] (inclusive). Returns an array of
 * {word, start_ms, end_ms} where `word` is the cleaned DISPLAY token and the
 * timing is the speaker's real timing (matched) or interpolated (cleaned-only).
 *
 * startIdx/endIdx come straight from findTimestamps — the located span of THIS
 * thought — so fillers/interjections OUTSIDE the thought are structurally
 * unreachable, and ones INSIDE are stepped over by the greedy matcher.
 */
function reconcileCleanedWords(cleanedText, timeline, startIdx, endIdx) {
  // Display tokens keep their original punctuation/casing (E23 shape: "though,").
  // Strip a leading "—"/dash token the brand text sometimes uses as a separator.
  const display = String(cleanedText)
    .replace(/\r/g, '')
    .split(/\s+/)
    .filter(Boolean);
  if (display.length === 0) return [];

  // Raw spoken words for this thought only, with real ms timings.
  const lo = Math.max(0, startIdx);
  const hi = Math.min(timeline.length - 1, endIdx);
  const raw = [];
  for (let i = lo; i <= hi; i++) {
    raw.push({
      norm:     timeline[i].word,                       // timeline words are already normalized
      start_ms: Math.round(timeline[i].start * 1000),
      end_ms:   Math.round(timeline[i].end   * 1000),
    });
  }
  if (raw.length === 0) return [];

  // ── Pass 1: LCS alignment of cleaned → raw ───────────────────────────────────
  // The brand cleaning removes WHOLE CLAUSES, not just stray fillers — q02 drops
  // "...a lot stronger. And I began to focus on longevity and reconnecting with my
  // life's purpose. And what I discovered..." (~30 raw words) between "lighter."
  // and "We are not too old to start over." A greedy left-to-right matcher gets
  // pulled onto the wrong copy of a repeated common word ("I", "came", "to") and
  // the whole timeline shears.
  //
  // So we compute the LONGEST COMMON SUBSEQUENCE between the cleaned tokens and the
  // raw window tokens (matching on normalized form). LCS is the GLOBALLY optimal
  // monotonic alignment: it keeps as many cleaned words as possible matched to raw
  // words IN ORDER, which is exactly the shape of "cleaned text = raw with whole
  // clauses removed and a couple of words fixed." This is the same family of
  // algorithm a diff tool uses, and it naturally:
  //   – lands the opening "I came through a three-year divorce" on the FIRST copy,
  //   – lands the closing "We are not too old to start over" on the LAST copy,
  //   – leaves "glow" (spoken "blow") unmatched, to be interpolated in pass 2.
  // Each matched cleaned token takes its raw word's real [start_ms,end_ms].
  const cleanNorm = display.map(normToken);
  const rawNorm   = raw.map(r => r.norm);
  const N = cleanNorm.length, M = rawNorm.length;
  // dp[i][j] = LCS length of cleanNorm[i..] and rawNorm[j..]
  const dp = Array.from({ length: N + 1 }, () => new Int32Array(M + 1));
  for (let i = N - 1; i >= 0; i--) {
    for (let j = M - 1; j >= 0; j--) {
      dp[i][j] = (cleanNorm[i] && cleanNorm[i] === rawNorm[j])
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out = display.map(w => ({ word: w, start_ms: null, end_ms: null }));
  let i = 0, j = 0;
  while (i < N && j < M) {
    if (cleanNorm[i] && cleanNorm[i] === rawNorm[j]) {
      out[i].start_ms = raw[j].start_ms;
      out[i].end_ms   = raw[j].end_ms;
      i++; j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      i++; // cleaned token has no raw counterpart here (a fix/added word) → pass 2
    } else {
      j++; // raw word was cleaned out (filler/interjection/dropped clause) → skip
    }
  }

  // ── Pass 2: interpolate the unmatched cleaned tokens ─────────────────────────
  // Anchors: window start for a leading run, window end for a trailing run, and
  // the surrounding matched words for an interior run. Distribute the gap evenly
  // so consecutive cleaned-only words read left-to-right and never overlap.
  const winStartMs = raw[0].start_ms;
  const winEndMs   = raw[raw.length - 1].end_ms;
  let p = 0;
  while (p < out.length) {
    if (out[p].start_ms !== null) { p++; continue; }
    // run of unmatched tokens [p..q)
    let q = p;
    while (q < out.length && out[q].start_ms === null) q++;
    const prevEnd   = p > 0 ? out[p - 1].end_ms : winStartMs;
    const nextStart = q < out.length ? out[q].start_ms : winEndMs;
    const span      = Math.max(0, nextStart - prevEnd);
    const n         = q - p;
    const per       = n > 0 ? span / n : 0;
    for (let k = 0; k < n; k++) {
      out[p + k].start_ms = Math.round(prevEnd + k * per);
      out[p + k].end_ms   = Math.round(prevEnd + (k + 1) * per);
    }
    p = q;
  }

  // Guard: monotonic, non-degenerate spans (a zero-length span renders as a flash).
  for (let k = 0; k < out.length; k++) {
    if (out[k].end_ms <= out[k].start_ms) out[k].end_ms = out[k].start_ms + 40;
    if (k > 0 && out[k].start_ms < out[k - 1].end_ms) out[k].start_ms = out[k - 1].end_ms;
    if (out[k].end_ms <= out[k].start_ms) out[k].end_ms = out[k].start_ms + 40;
  }

  return out;
}

/**
 * Smooth the caption reveal cadence so the accumulating karaoke never reads as
 * FROZEN while the speaker is mid-thought, WITHOUT showing cleaned-out text and
 * WITHOUT touching the audio or the thought boundaries.
 *
 * Input/output: the {word,start_ms,end_ms}[] from reconcileCleanedWords. Only the
 * REVEAL timing (start_ms, and end_ms kept consistent) is adjusted; the `word`
 * strings — the brand-cleaned display tokens — are never changed, so no filler or
 * host interjection can ever appear. This runs AFTER reconciliation so it operates
 * purely on the already-cleaned token stream.
 *
 * THE RULE (see CADENCE_CAP_MS / MAX_LEAD_MS): walk left→right. Each word may reveal
 * at most CADENCE_CAP_MS after the previous (already-smoothed) reveal — when the real
 * onsets leave a larger gap, ease this word EARLIER to close it. Two bounds keep it
 * honest and within ADR 0003:
 *   • a word is NEVER moved LATER (we take the min of its real onset and the cap), so
 *     the LAST word can never be pushed past transcript_end_ms, and dense passages
 *     (onsets already < cap apart) are left exactly as-is — only large gaps get bridged;
 *   • a word may lead its OWN real onset by at most MAX_LEAD_MS, so a genuine multi-
 *     second excision (cleaned clause / host interjection) is only shaved by MAX_LEAD_MS,
 *     never collapsed — we refuse to race the closing sentence seconds ahead of its audio.
 * The FIRST word is never touched (loop starts at i=1): its real onset is the opening
 * breath/lead-in the boundary logic preserved (ADR 0003), and pulling it earlier would
 * drag the prior thought's tail into the open. Order is never changed (monotonic by
 * construction: each newStart ≥ the previous newStart).
 *
 * end_ms follows start_ms by the word's original spoken duration, then is clamped so
 * spans stay monotonic and non-degenerate (a zero/negative span renders as a flash).
 */
function smoothCadence(words, capMs = CADENCE_CAP_MS, maxLeadMs = MAX_LEAD_MS) {
  if (!Array.isArray(words) || words.length === 0) return words;
  const realStart = words.map(w => w.start_ms);
  const dur       = words.map(w => Math.max(0, w.end_ms - w.start_ms));
  const ns        = realStart.slice();

  for (let i = 1; i < words.length; i++) {
    // Default: don't move LATER than the real onset (never over-advance the reveal,
    // never push the last word past transcript_end). Cap the stall to capMs.
    let cand = Math.min(realStart[i], ns[i - 1] + capMs);
    // Honesty bound: never lead this word's OWN real onset by more than maxLeadMs.
    // This is what leaves a true excision (q02 host interjection, q04 cut clause) as
    // residual instead of dragging the next sentence forward.
    if (cand < realStart[i] - maxLeadMs) cand = realStart[i] - maxLeadMs;
    // Monotonic guard: never earlier than the previous reveal.
    if (cand < ns[i - 1]) cand = ns[i - 1];
    ns[i] = Math.round(cand);
  }

  // Rebuild end_ms from the smoothed starts, preserving each word's spoken duration
  // where it fits, then enforce monotonic, non-degenerate spans.
  const out = words.map((w, i) => ({ word: w.word, start_ms: ns[i], end_ms: ns[i] + dur[i] }));
  for (let i = 0; i < out.length; i++) {
    // A page renders each word until the next word's reveal, so end_ms is mostly a
    // floor; keep it from overrunning the next reveal and from collapsing to zero.
    const nextStart = i + 1 < out.length ? out[i + 1].start_ms : Infinity;
    if (out[i].end_ms > nextStart) out[i].end_ms = nextStart;
    if (out[i].end_ms <= out[i].start_ms) out[i].end_ms = out[i].start_ms + 40;
  }
  return out;
}

/**
 * Compute a SAFE lead-in (ms) for a quote whose first spoken word is at
 * `startIdx` in `timeline`. render_reel.js backs the clip start up by this much
 * to give the speaker a natural breath before the first word.
 *
 * WHY this lives in lookup, not render: render uses the lead-in in THREE coupled
 * places — the audio `-ss`, the caption-page timing, and the music speech-window.
 * Clamping inside only one of them desyncs the others. Emitting a single
 * per-quote `lead_in_ms` keeps every downstream calculation reading one coherent
 * value (render falls back to its global default when the field is absent, so old
 * quotes.json files still render).
 *
 * The clamp: the lead-in may never reach back past the END of the PREVIOUS word.
 * Crossing it would drag the tail of the prior thought into the open (E27 reel 1).
 * We back up at most `defaultLeadInMs`, and never past the gap to the previous
 * word — whichever is smaller. (Interpolated timing means "previous word end" is
 * itself a guess, so this is a floor, not a guarantee of silence; it does
 * structurally prevent opening a full word early, which was the actual bug.)
 */
function safeLeadInMs(timeline, startIdx, defaultLeadInMs) {
  if (startIdx <= 0) return 0; // first word of the episode: no room to back up
  const startMs = timeline[startIdx].start * 1000;
  const prevEndMs = timeline[startIdx - 1].end * 1000;
  const gap = Math.max(0, startMs - prevEndMs);
  // Allow up to the default, but not so much that we cross the previous word.
  // The +0 floor and Math.min mean: never negative, never past the prior word.
  return Math.round(Math.min(defaultLeadInMs, gap));
}

// ── mid-quote audio excision (ADR 0004) ─────────────────────────────────────────
//
// WHAT THIS SOLVES — cleaned-out speech still riding in the clip audio.
// ADR 0003 made the captions reveal the BRAND-CLEANED text on real word timing, and
// smoothCadence bridges SMALL cleaned-out gaps by easing the next caption in early.
// But the audio is still ONE continuous cut [start,end], so any large span the brand
// team cleaned out of the TEXT (a host interjection, a dropped clause) keeps PLAYING
// with no caption — the residual holds John flagged on E27 q02 (~6–8s host
// interjection) and q04 (~10–12s cut clause). smoothCadence deliberately refuses to
// drag the next sentence forward across those, so they survive as a frozen caption
// over live audio. The honest fix (ADR 0004) is to remove that AUDIO.
//
// THE SEAM (Toby's contract): the intelligence lives HERE — lookup already owns the
// cleaned-vs-raw LCS alignment (reconcileCleanedWords). Per quote we additionally
// emit two fields and render_reel.js stays dumb:
//   • audio_segments — the ordered KEPT sub-spans in ORIGINAL episode ms (the
//     complement of the excised runs within the padded clip window). render
//     concatenates these with a short click-free crossfade instead of one -ss/-to cut.
//   • words[] — already REMAPPED onto the post-excision continuous local timeline
//     (each word shifted earlier by the total excised duration that precedes it), so
//     the captions stay synced to the now-shorter audio and the big holds vanish.
//
// THE THRESHOLD — EXCISE_MIN_MS (2000ms). We excise a contiguous run of RAW
// (cleaned-out, LCS-unmatched) words only when its duration exceeds this. WHY 2.0s:
//   • It is the perceptual line between "natural breath / dropped filler" and
//     "structural dead air." Below it, smoothCadence already keeps the caption moving
//     and the listener reads the pause as cadence.
//   • Removing EVERY cleaned-out filler ("um", a repeated "and", "I navigated a lot
//     of it") makes speech sound chopped and unnatural — those are KEPT on purpose.
//   • On E27 it cleanly separates the targets from the keepers: the two real
//     excisions measure 8.1s (q02 host interjection) and 11.8s (q04 cut clause),
//     while the largest KEEPER run is 1.5s (q02 "I navigated a lot of it"). A 2.0s
//     cut sits in a wide empty band between them — there is no run anywhere near the
//     boundary, so the threshold is not finely balanced on this episode.
// It is a named, tunable constant exactly because future episodes may want a
// different line; this is the ADR's "named threshold."
const EXCISE_MIN_MS = 2000;

/**
 * Build the matched/unmatched map of the RAW words in [startIdx..endIdx] against the
 * cleaned text, using the SAME LCS alignment reconcileCleanedWords uses for captions.
 * Returns a boolean[] parallel to the raw window: matched[k] === true iff raw word k
 * corresponds to a cleaned (kept) token. The unmatched runs are the cleaned-out
 * speech — fillers, repeats, host interjections, dropped clauses.
 *
 * WHY share the LCS and not re-derive: the caption stream and the excision must agree
 * on exactly which raw words are "kept." If they used different alignments, a word
 * could be captioned but its audio excised (or vice-versa) — a desync by construction.
 * One alignment, two consumers.
 */
function alignCleanedToRaw(cleanedText, timeline, startIdx, endIdx) {
  const cleanNorm = String(cleanedText)
    .replace(/\r/g, '')
    .split(/\s+/)
    .filter(Boolean)
    .map(normToken);
  const lo = Math.max(0, startIdx);
  const hi = Math.min(timeline.length - 1, endIdx);
  const raw = [];
  for (let i = lo; i <= hi; i++) {
    raw.push({
      norm:     timeline[i].word,
      start_ms: Math.round(timeline[i].start * 1000),
      end_ms:   Math.round(timeline[i].end   * 1000),
    });
  }
  const N = cleanNorm.length, M = raw.length;
  const matched = new Array(M).fill(false);
  if (N === 0 || M === 0) return { raw, matched };

  // dp[i][j] = LCS length of cleanNorm[i..] and raw[j..] — identical recurrence to
  // reconcileCleanedWords, so the two stay in lock-step.
  const dp = Array.from({ length: N + 1 }, () => new Int32Array(M + 1));
  for (let i = N - 1; i >= 0; i--) {
    for (let j = M - 1; j >= 0; j--) {
      dp[i][j] = (cleanNorm[i] && cleanNorm[i] === raw[j].norm)
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  let i = 0, j = 0;
  while (i < N && j < M) {
    if (cleanNorm[i] && cleanNorm[i] === raw[j].norm) { matched[j] = true; i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { i++; }
    else { j++; }
  }
  return { raw, matched };
}

/**
 * Identify the spans to EXCISE: contiguous runs of unmatched (cleaned-out) raw words
 * whose duration ≥ minMs. Returns an array of {start_ms, end_ms, dur_ms, text} in
 * ORIGINAL episode ms, in order. Empty when nothing exceeds the threshold (the
 * common case — most quotes have only small fillers, which stay).
 *
 * Each excised span is bounded by the unmatched run's own first-word start and
 * last-word end. We do NOT pad the cut inward toward the neighbouring KEPT words —
 * the kept words' own [start,end] define the segment edges, and render aligns the
 * actual splice to a zero-crossing / low-energy point near each edge (click-free).
 */
function findExciseSpans(cleanedText, timeline, startIdx, endIdx, minMs = EXCISE_MIN_MS) {
  const { raw, matched } = alignCleanedToRaw(cleanedText, timeline, startIdx, endIdx);
  const spans = [];
  let r = 0;
  while (r < raw.length) {
    if (matched[r]) { r++; continue; }
    let k = r;
    while (k < raw.length && !matched[k]) k++;        // unmatched run [r..k)
    const start_ms = raw[r].start_ms;
    const end_ms   = raw[k - 1].end_ms;
    const dur_ms   = end_ms - start_ms;
    if (dur_ms >= minMs) {
      spans.push({
        start_ms, end_ms, dur_ms,
        // raw words carry the normalized token under `.norm` (alignCleanedToRaw) —
        // good enough to identify the cut in logs/tests; we are not displaying it.
        text: raw.slice(r, k).map(w => w.norm).join(' '),
      });
    }
    r = k;
  }
  return spans;
}

/**
 * Build audio_segments — the ordered KEPT sub-spans in ORIGINAL episode ms — as the
 * complement of `exciseSpans` within the padded clip window [clipStartMs, clipEndMs].
 *
 * clipStartMs / clipEndMs are render's own padded bounds: clipStartMs = start - leadIn
 * (clamped ≥0 by render), clipEndMs = end + tail. Lead-in and tail therefore attach to
 * the FIRST and LAST kept segments respectively — they are part of the window we take
 * the complement of, so the opening breath and closing tail survive excision untouched.
 *
 * Excised spans are clamped to the window and merged if they touch, then subtracted.
 * The result is always ≥1 segment (a fully-excised window is impossible: the kept
 * caption words live inside it). When there are NO excisions the result is exactly
 * one segment == the whole window, so render's concat path reduces to the old single
 * cut (back-compat — see render_reel.extractAudioClip).
 */
function buildAudioSegments(clipStartMs, clipEndMs, exciseSpans) {
  // Clamp + sort excisions to the window.
  const cuts = exciseSpans
    .map(s => ({ start_ms: Math.max(clipStartMs, s.start_ms), end_ms: Math.min(clipEndMs, s.end_ms) }))
    .filter(s => s.end_ms > s.start_ms)
    .sort((a, b) => a.start_ms - b.start_ms);

  const segments = [];
  let cursor = clipStartMs;
  for (const cut of cuts) {
    if (cut.start_ms > cursor) segments.push({ start_ms: cursor, end_ms: cut.start_ms });
    cursor = Math.max(cursor, cut.end_ms);
  }
  if (cursor < clipEndMs) segments.push({ start_ms: cursor, end_ms: clipEndMs });

  // Degenerate guard: if everything collapsed (shouldn't happen), keep the window.
  if (segments.length === 0) segments.push({ start_ms: clipStartMs, end_ms: clipEndMs });
  return segments;
}

/**
 * Total excised duration (ms) that falls BEFORE a given original-episode timestamp,
 * given the kept `segments`. Used to remap a word's absolute onset into the
 * post-excision continuous local clip timeline: a word at original ms T maps to
 * (T - clipStartMs) - excisedBefore(T) — i.e. its distance from clip start minus the
 * gaps the excision removed ahead of it.
 *
 * Implementation: the kept segments are the complement of the cuts, so the excised
 * time before T is (T - clipStartMs) minus the sum of kept-segment lengths that end
 * at or before T (plus the partial kept length for the segment containing T). We
 * compute the LOCAL position directly by summing kept lengths up to T, which is the
 * cleaner formulation and avoids tracking cuts separately.
 */
function originalMsToLocalMs(originalMs, segments) {
  let local = 0;
  for (const seg of segments) {
    if (originalMs >= seg.end_ms) {
      local += seg.end_ms - seg.start_ms;          // whole kept segment is before T
    } else if (originalMs > seg.start_ms) {
      local += originalMs - seg.start_ms;          // partial: T sits inside this kept segment
      return local;
    } else {
      return local;                                 // T is before/at this segment's start
    }
  }
  return local;
}

/**
 * Remap a words[] stream ({word,start_ms,end_ms} in ORIGINAL episode ms) onto the
 * post-excision continuous LOCAL clip timeline (0 = clip start). Each word's onset
 * and end are shifted earlier by exactly the excised duration that precedes them; a
 * word that falls INSIDE an excised span (it shouldn't — captioned words are kept by
 * construction) is snapped to the local edge of the preceding kept audio.
 *
 * AFTER this remap the big holds are gone, so any RESIDUAL small gaps (sub-threshold
 * fillers we deliberately kept) are smoothed by re-running smoothCadence on the
 * remapped stream — done by the caller so the order is explicit:
 *   reconcile → (excise spans) → remap onto local → smoothCadence.
 */
function remapWordsToLocal(words, segments) {
  return words.map(w => {
    const start = originalMsToLocalMs(w.start_ms, segments);
    let end = originalMsToLocalMs(w.end_ms, segments);
    if (end <= start) end = start + 40; // never let a clamp collapse a span to a flash
    return { word: w.word, start_ms: start, end_ms: end };
  });
}

// ── main ──────────────────────────────────────────────────────────────────────

function main() {
const quotesData     = JSON.parse(fs.readFileSync(quotesPath, 'utf8'));

// ── Choose the sharpest available timeline (ADR 0003) ────────────────────────
// Preference order:
//   1. --aligned PATH (an explicit word-aligned transcript), or
//   2. a transcript_aligned.json sitting next to --transcript, or
//   3. --transcript itself if it already carries a top-level words[] array,
//   then real word timing → buildTimelineFromWords (real gaps, real breaths).
// Otherwise fall back to interpolated segment timing → buildTimeline (the floor;
// lead-ins will clamp to 0 because interpolation leaves no inter-word gaps).
// This keeps OLD episodes (segment-only) rendering while NEW episodes that ran the
// align pass get the precise boundaries the ADR requires.
function loadJson(p) { return JSON.parse(fs.readFileSync(p, 'utf8')); }

let timeline;
let timingSource;
const transcriptData = loadJson(transcriptPath);

// Candidate aligned file: explicit flag, else sibling transcript_aligned.json.
let alignedData = null;
if (alignedPath && fs.existsSync(alignedPath)) {
  alignedData = loadJson(alignedPath);
} else if (Array.isArray(transcriptData.words) && transcriptData.words.length > 0) {
  alignedData = transcriptData; // --transcript already points at the aligned file
} else {
  const sibling = transcriptPath.replace(/transcript[^/]*\.json$/, 'transcript_aligned.json');
  if (sibling !== transcriptPath && fs.existsSync(sibling)) alignedData = loadJson(sibling);
}

// usingAligned gates words[] karaoke population below: only real word timing
// gives true onsets to reveal captions on. Interpolated timing leaves words[]
// empty so the renderer falls back to block captions (quote_text_clean).
let usingAligned = false;
if (alignedData && Array.isArray(alignedData.words) && alignedData.words.length > 0) {
  timeline = buildTimelineFromWords(alignedData.words);
  timingSource = `word-aligned (${alignedData.words.length} words, ${alignedData.align_model || 'wav2vec2'})`;
  usingAligned = true;
} else {
  const segments = transcriptData.segments || [];
  timeline = buildTimeline(segments);
  timingSource = `interpolated segment timing (${segments.length} segments) — NO real gaps, lead-ins will clamp to 0`;
}

console.log(
  `lookup_quote_timestamps: timeline source = ${timingSource} → ${timeline.length} words (total ${(timeline[timeline.length-1]?.end || 0).toFixed(0)}s)`
);

const quotes = quotesData.quotes || [];
let updated = 0;
let skipped = 0;

for (let i = 0; i < quotes.length; i++) {
  const q = quotes[i];

  // ── Add index (1-based integer) ───────────────────────────────────────────
  if (q.index == null) {
    // id is like "q01", "q02" — parse the number
    const idNum = q.id ? parseInt(q.id.replace(/\D/g, ''), 10) : NaN;
    q.index = isNaN(idNum) ? i + 1 : idNum;
  }

  // ── Add topic_slug ─────────────────────────────────────────────────────────
  if (!q.topic_slug) {
    q.topic_slug = deriveTopicSlug(q);
  }

  // ── Add topic (logging label used by inject_word_timestamps.js) ──────────
  if (!q.topic) {
    q.topic = q.topic_slug;
  }

  // ── Derive speaker_role from speaker field ────────────────────────────────
  // Brand Team outputs speaker: "Heather" or a guest name. render_reel.js uses
  // speaker_role: "host" | "guest" to choose which background image to use.
  // Derivation (alias match, not substring) lives in deriveSpeakerRole — see the
  // first-name-collision trap documented there. An explicit role is respected.
  q.speaker_role = deriveSpeakerRole(q.speaker, q.speaker_role);

  // ── Add quote_text_clean (render_reel.js block-caption fallback) ───────────
  // render_reel.js shows quote_text_clean as BLOCK captions ONLY when words[] is
  // empty (no aligned transcript). It MUST therefore span the same thought the
  // AUDIO was cut to — the full `text` — NOT the shorter reel_hook. Sourcing it
  // from the hook caused the E27 blank-band regression: audio ran the whole
  // thought, captions stopped at the hook's end. The hook is social-post copy
  // (reel_*_captions.md) only; it never drives on-screen captions. This keeps the
  // block-caption fallback and the words[] karaoke (also sourced from `text`,
  // below) reading the SAME span so they can never diverge again.
  if (!q.quote_text_clean) {
    q.quote_text_clean = q.text || q.quote_text || q.reel_hook || '';
  }

  // ── Populate timestamps if still 0 ────────────────────────────────────────
  if (!q.transcript_start_ms && !q.transcript_end_ms) {
    const text = q.text || q.quote_text || '';
    const result = findTimestamps(text, timeline);

    if (result.score >= minScore) {
      q.transcript_start_ms = result.start_ms;
      q.transcript_end_ms   = result.end_ms;
      // Emit a per-quote clamped lead-in so render can't open the clip on the
      // tail of the previous thought (ADR 0003 in-point clamp). render reads
      // q.lead_in_ms ?? RENDER_CONFIG.leadInMs everywhere it pads the start, so
      // this single value keeps the audio cut, captions, and music window in sync.
      if (result.start_idx >= 0) {
        q.lead_in_ms = safeLeadInMs(timeline, result.start_idx, DEFAULT_LEAD_IN_MS);
      }

      // ── Populate words[] (caption karaoke) in the SAME pass ─────────────────
      // The renderer reveals words[] word-by-word; an empty words[] block-falls
      // back to quote_text_clean. We reconcile the SAME `text` we just located
      // (NOT reel_hook — that was the E27 blank-band regression) against the real
      // aligned word timing, reusing the start_idx/end_idx findTimestamps already
      // computed. This makes words[] a first-class lookup output and retires the
      // standalone populate_caption_words.js for word-aligned episodes. Gated on
      // usingAligned: with interpolated segment timing there are no real onsets,
      // so we leave words[] empty and let the block-caption fallback carry it.
      if (usingAligned && result.start_idx >= 0 && result.end_idx >= result.start_idx) {
        // Reconcile cleaned tokens to real onsets (captions on real timing).
        const reconciled = reconcileCleanedWords(text, timeline, result.start_idx, result.end_idx);

        // ── ADR 0004: mid-quote audio excision ──────────────────────────────────
        // Identify the LARGE cleaned-out spans (host interjection / dropped clause)
        // and emit (a) audio_segments — the kept sub-spans render concatenates, and
        // (b) words[] remapped onto the post-excision local timeline so captions stay
        // synced once the holds are gone. Lead-in/tail define the window the segments
        // are the complement of (they attach to the first/last kept segment), so the
        // ADR 0003 boundaries are untouched — we only remove INTERIOR spans.
        const exciseSpans = findExciseSpans(text, timeline, result.start_idx, result.end_idx);
        const leadInMs = q.lead_in_ms ?? 0;
        const clipStartMs = Math.max(0, result.start_ms - leadInMs);
        const clipEndMs   = result.end_ms + RENDER_TAIL_MS;
        const segments    = buildAudioSegments(clipStartMs, clipEndMs, exciseSpans);
        q.audio_segments  = segments;

        if (exciseSpans.length > 0) {
          // Remap captions onto the local (post-excision) clip timeline, THEN smooth
          // any residual sub-threshold gaps. Order matters: remap first (collapses
          // the big holds), smooth second (eases the small ones) — running smooth on
          // the original timeline would waste its MAX_LEAD budget fighting holds we
          // are about to delete.
          const localWords = remapWordsToLocal(reconciled, segments);
          q.words = smoothCadence(localWords);
          // Claim ownership of words[]: inject_word_timestamps.js runs AFTER us in
          // publish_finalize and used to overwrite this unconditionally with raw
          // per-clip WhisperX words (REEL-05 "Gate B"). Raw words carry tokens the
          // brand team cleaned OUT — including the first word of the NEXT thought —
          // which is how a phantom "And" reached the screen on E28. This marker tells
          // inject the captions are already reconciled to the cleaned text and must
          // be left alone.
          q.words_source = 'aligned';
          // Mark words as LOCAL-timeline so render uses clip-relative (base 0) caption
          // math instead of subtracting an absolute clipStartMs. Single-segment quotes
          // keep absolute words[] (no remap) and the old base — full back-compat.
          q.words_timeline = 'local';
        } else {
          // No excision — words[] stay in ABSOLUTE episode ms (back-compat), smoothed
          // as before. audio_segments is the single full-window segment, so render's
          // concat path equals the old single -ss/-to cut.
          q.words = smoothCadence(reconciled);
          q.words_source = 'aligned'; // see the excision branch above — Gate B guard
        }
      }

      const dur = ((result.end_ms - result.start_ms) / 1000).toFixed(1);
      console.log(
        `  q${q.index} ✓ score=${result.score.toFixed(2)} ${result.start_ms}–${result.end_ms}ms (${dur}s) leadIn=${q.lead_in_ms ?? '-'}ms  "${text.slice(0, 60)}..."`
      );
      // ADR 0004 excision report — printed AFTER this quote's summary so the cut
      // lines sit under the quote they belong to (not the next one).
      if (Array.isArray(q.audio_segments) && q.audio_segments.length > 1) {
        const spans = findExciseSpans(text, timeline, result.start_idx, result.end_idx);
        const cutMs = spans.reduce((s, x) => s + x.dur_ms, 0);
        console.log(
          `    ↳ EXCISE ${spans.length} span(s), ${(cutMs/1000).toFixed(1)}s removed → ${q.audio_segments.length} audio segment(s)`
        );
        for (const sp of spans) {
          console.log(`        cut [${sp.start_ms}-${sp.end_ms}] ${(sp.dur_ms/1000).toFixed(1)}s: "${sp.text}"`);
        }
      }
      updated++;
    } else {
      console.warn(
        `  q${q.index} ⚠ LOW SCORE ${result.score.toFixed(2)} — timestamps left at 0.  "${text.slice(0, 60)}..."`
      );
      skipped++;
    }
  } else {
    console.log(
      `  q${q.index} already has timestamps (${q.transcript_start_ms}–${q.transcript_end_ms}ms) — skipping`
    );
  }
}

quotesData.quotes = quotes;
fs.writeFileSync(outPath, JSON.stringify(quotesData, null, 2), 'utf8');
console.log(
  `lookup_quote_timestamps: done — updated=${updated} skipped=${skipped} total=${quotes.length} → ${outPath}`
);
}

// Pure helpers above are exported for unit testing; the CLI work only runs when
// this file is the entry point, so importing it has no side effects.
export {
  deriveSpeakerRole, normalizeSpeakerName, HOST_ALIASES,
  buildTimeline, buildTimelineFromWords, findTimestamps, safeLeadInMs, locateSequence,
  significantWords, leadSignificant, tailSignificant,
  reconcileCleanedWords, normToken,
  smoothCadence, CADENCE_CAP_MS, MAX_LEAD_MS,
  DEFAULT_LEAD_IN_MS, RENDER_TAIL_MS,
  alignCleanedToRaw, findExciseSpans, buildAudioSegments,
  originalMsToLocalMs, remapWordsToLocal, EXCISE_MIN_MS,
};

if (isEntryPoint) {
  main();
}
