#!/usr/bin/env node

/**
 * detect_boundaries.js — Auto intro/outro detection + speaker mapping
 * v0.1 · 2026-05-18
 *
 * Companion to: render_reel.js, inject_word_timestamps.js
 * Spec: agent file v1.6 EPISODE PIPELINE section + INSTRUCTIONS.md intro/outro rules
 *
 * Reads a WhisperX JSON transcript, identifies the intro and outro phrases,
 * extracts the guest's name from the intro, maps SPEAKER_XX labels to real
 * names, and emits a boundaries.json that downstream PM steps consume.
 *
 * Usage:
 *   node detect_boundaries.js \
 *     --whisperx Episodes/EXX-slug/whisperx/EXX-Speaker-16k.json \
 *     --out      Episodes/EXX-slug/boundaries.json \
 *     [--verbose]
 *
 * Exit codes:
 *   0  success — boundaries.json written
 *   1  missing input file
 *   2  (retired 2026-05-26) — intro not found now falls back to t=0 with a warning card
 *   3  (retired 2026-05-26) — guest name miss now warns + exits 0; review card in pm_actions[]
 *   5  argument or schema error
 */

import fs from 'node:fs';
import path from 'node:path';

// ============================================================
// PATTERNS (single source of truth for detection rules)
// ============================================================

const PATTERNS = {
  // Intro: must be Heather. Regex match on normalized text (lowercase, punctuation stripped).
  // The canonical show name is "Gen X Hits Menopause" but WhisperX has been observed
  // transcribing "Hits" as "It's", "Its", or "Hit's". After normalizeText() strips the
  // apostrophe, those collapse to "it s" / "its" / "hit s". The pattern below matches
  // all of those without requiring a re-run with a better prompt. (Fix per E24 backfill,
  // 2026-05-20: "hits" → "it s" mistranscription broke detection on one episode.)
  introRegex: /hello,?\s+and\s+welcome\s+to\s+gen\s+x\s+(hits?|hit s|it s|its)\s+menopause/,

  // Guest name extraction. Heather has used multiple phrasings across episodes:
  //   E23: "today, we have Dr. Nadia Brennick-Hale with us"      → "today we have [Name]"
  //   E24: "today we are so lucky to be in the studio with Rosalind Teague"
  //                                                            → "in the studio with [Name]"
  // Each variant has its own anchor. Try in order: most specific first.
  guestNameVariants: [
    /today,?\s+we\s+have\s+((?:Dr\.?\s+)?[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){0,5})/,
    /in\s+the\s+studio\s+with\s+((?:Dr\.?\s+)?[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){0,5})/,
    /(?:today\s+)?we(?:'re|\s+are)\s+(?:so\s+)?(?:lucky|excited|thrilled|joined)\s+(?:to\s+(?:be\s+(?:in\s+the\s+studio\s+)?)?(?:with|by)\s+)?((?:Dr\.?\s+)?[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){0,5})/,
    /joining\s+us\s+(?:today\s+)?is\s+((?:Dr\.?\s+)?[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){0,5})/,
  ],

  // Outro: canonical phrase first, then email substring as fallback (rule per John 2026-05-18).
  outroCanonical: 'contact us at podcast@example.test',
  outroFallbackSubstring: 'podcast@example.test',

  // Outro must be spoken by Heather (per spec). If the matched outro speaker
  // doesn't map to Heather, that's a discrepancy — flag but don't halt.
};

// ============================================================
// CLI ARGS
// ============================================================

function parseArgs(argv) {
  const args = { whisperx: null, out: null, verbose: false };

  for (let i = 2; i < argv.length; i++) {
    const a = argv[i], n = argv[i + 1];
    if (a === '--whisperx')      { args.whisperx = n; i++; }
    else if (a === '--out')      { args.out = n; i++; }
    else if (a === '--verbose')  { args.verbose = true; }
    else if (a === '--help' || a === '-h') { printHelp(); process.exit(0); }
    else { console.error(`Unknown argument: ${a}`); process.exit(5); }
  }

  for (const k of ['whisperx', 'out']) {
    if (!args[k]) {
      console.error(`Missing required argument: --${k}`);
      printHelp();
      process.exit(5);
    }
  }

  return args;
}

function printHelp() {
  console.log(`
Usage: node detect_boundaries.js --whisperx PATH --out PATH [--verbose]

Required:
  --whisperx PATH   WhisperX JSON output (post-transcription, post-speaker-diarization)
  --out PATH        Where to write boundaries.json

Optional:
  --verbose         Step-by-step logs

Exit codes:
  0  success
  1  missing input file
  2  (retired 2026-05-26) — intro not found now falls back to t=0
  3  guest name extraction failed (PM surfaces card)
  5  argument or schema error
`);
}

// ============================================================
// LOGGING
// ============================================================

let VERBOSE = false;
function log(msg)  { console.log(`[detect] ${msg}`); }
function vlog(msg) { if (VERBOSE) console.log(`[detect:verbose] ${msg}`); }
function warn(msg) { console.warn(`[detect:warn] ${msg}`); }
function error(msg) { console.error(`[detect:error] ${msg}`); }

// ============================================================
// LOAD WHISPERX JSON
// ============================================================

function loadWhisperX(p) {
  if (!fs.existsSync(p)) {
    error(`WhisperX JSON not found: ${p}`);
    process.exit(1);
  }
  try {
    const data = JSON.parse(fs.readFileSync(p, 'utf8'));
    if (!data.segments || !Array.isArray(data.segments)) {
      error('WhisperX JSON missing segments[] array');
      process.exit(5);
    }
    return data;
  } catch (e) {
    error(`Cannot parse WhisperX JSON: ${e.message}`);
    process.exit(5);
  }
}

// ============================================================
// SEGMENT TEXT NORMALIZATION (for substring matching)
// ============================================================

function normalizeText(s) {
  return s
    .toLowerCase()
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/[^a-z0-9@.\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

// ============================================================
// INTRO DETECTION
// ============================================================

function findIntro(segments) {
  // Search segments in order. First match wins.
  for (let i = 0; i < Math.min(segments.length, 30); i++) {  // intro should be in first ~30 segments
    const seg = segments[i];
    if (!seg.text) continue;
    const normalized = normalizeText(seg.text);
    if (PATTERNS.introRegex.test(normalized)) {
      return {
        segmentIndex: i,
        startMs: Math.round((seg.start ?? 0) * 1000),
        speakerLabel: seg.speaker ?? null,
        rawText: seg.text.trim(),
      };
    }
  }
  return null;
}

// ============================================================
// GUEST NAME EXTRACTION
// ============================================================

function extractGuestName(segments, introIndex) {
  // Search intro + next ~8 segments for a guest-intro phrasing.
  // Concatenate segment text so multi-segment intro speech works too.
  const window = segments.slice(introIndex, introIndex + 8)
    .map(s => s.text || '')
    .join(' ');

  // Try each variant in order. First match wins.
  for (const re of PATTERNS.guestNameVariants) {
    const match = window.match(re);
    if (match) {
      const name = match[1].trim().replace(/\s+/g, ' ');
      // Heuristic: WhisperX sometimes inserts space inside hyphenated names ("Brennick Hale" → "Brennick-Hale").
      // We don't auto-hyphenate (could be wrong). PM applies brand-name normalization downstream.
      return name;
    }
  }
  return null;
}

// ============================================================
// OUTRO DETECTION (canonical → fallback)
// ============================================================

function findOutro(segments) {
  // Search backwards from end. Outro is in the final portion of the recording.
  const searchWindow = Math.min(segments.length, 80);  // last ~80 segments
  const startIdx = Math.max(0, segments.length - searchWindow);

  // Try canonical first.
  for (let i = segments.length - 1; i >= startIdx; i--) {
    const seg = segments[i];
    if (!seg.text) continue;
    if (normalizeText(seg.text).includes(PATTERNS.outroCanonical)) {
      return {
        segmentIndex: i,
        endMs: Math.round((seg.end ?? 0) * 1000),
        speakerLabel: seg.speaker ?? null,
        rawText: seg.text.trim(),
        method: 'canonical',
      };
    }
  }

  // Fallback: email substring.
  for (let i = segments.length - 1; i >= startIdx; i--) {
    const seg = segments[i];
    if (!seg.text) continue;
    if (normalizeText(seg.text).includes(PATTERNS.outroFallbackSubstring)) {
      return {
        segmentIndex: i,
        endMs: Math.round((seg.end ?? 0) * 1000),
        speakerLabel: seg.speaker ?? null,
        rawText: seg.text.trim(),
        method: 'email_fallback',
      };
    }
  }

  return null;
}

// ============================================================
// MAIN
// ============================================================

function main() {
  const args = parseArgs(process.argv);
  VERBOSE = args.verbose;

  log(`Reading WhisperX JSON: ${args.whisperx}`);
  const data = loadWhisperX(args.whisperx);
  vlog(`Loaded ${data.segments.length} segments`);

  // 1. Intro detection
  // Fallback per 2026-05-26: when intro phrase isn't found, default to t=0 (start of
  // first segment) and surface a review card — same graceful pattern as outro fallback.
  // Rationale: the pipeline should never block on a detection miss; Heather confirms
  // trim points during editorial review anyway. Hard exit(2) was overly conservative.
  let intro = findIntro(data.segments);
  if (!intro) {
    const firstSeg = data.segments[0];
    intro = {
      segmentIndex: 0,
      startMs: Math.round((firstSeg?.start ?? 0) * 1000),
      speakerLabel: firstSeg?.speaker ?? null,
      rawText: (firstSeg?.text || '').trim(),
      method: 'first_segment_fallback',
    };
    warn(`Intro phrase (regex: ${PATTERNS.introRegex}) not found in first 30 segments — falling back to start of first segment (${(intro.startMs/1000).toFixed(2)}s).`);
    warn('PM should surface card: "Confirm intro trim point for E{N}"');
  } else {
    intro.method = 'regex_match';
    log(`✓ Intro found at segment ${intro.segmentIndex} (${(intro.startMs/1000).toFixed(2)}s) — speaker label: ${intro.speakerLabel}`);
  }

  // 2. Guest name extraction
  const guestName = extractGuestName(data.segments, intro.segmentIndex);
  if (!guestName) {
    warn('Guest name extraction failed — pattern "today we have [Name]" not matched in intro window');
    warn('PM should surface card: "Confirm guest name for E{N}"');
    // Don't halt — still write boundaries with null guest_name
  } else {
    log(`✓ Guest name extracted: ${guestName}`);
  }

  // 3. Outro detection
  let outro = findOutro(data.segments);
  if (!outro) {
    // Fallback per spec correction 2026-05-20 (Heather): when no canonical outro
    // is spoken, trim to the end of the last segment. Surface FYI (not blocking)
    // so John can confirm the trim end on review.
    const lastSeg = data.segments[data.segments.length - 1];
    if (lastSeg && typeof lastSeg.end === 'number') {
      outro = {
        segmentIndex: data.segments.length - 1,
        endMs: Math.round(lastSeg.end * 1000),
        speakerLabel: lastSeg.speaker ?? null,
        rawText: (lastSeg.text || '').trim(),
        method: 'last_segment_fallback',
      };
      warn(`Outro phrase not detected — falling back to end of last segment at ${(outro.endMs/1000).toFixed(2)}s.`);
      log(`✓ Outro (fallback) at segment ${outro.segmentIndex} (${(outro.endMs/1000).toFixed(2)}s) — speaker label: ${outro.speakerLabel}`);
    } else {
      warn(`Outro not found (neither canonical "${PATTERNS.outroCanonical}" nor email substring "${PATTERNS.outroFallbackSubstring}") and last segment has no end timestamp`);
    }
  } else {
    log(`✓ Outro found at segment ${outro.segmentIndex} (${(outro.endMs/1000).toFixed(2)}s) — method: ${outro.method}, speaker label: ${outro.speakerLabel}`);
  }

  // 4. Speaker mapping
  // Intro speaker → Heather. Guest speaker label → guestName (if extracted).
  // Other speakers (3+ way conversation) → "Unknown" placeholder; PM surfaces card.
  const allSpeakerLabels = new Set();
  for (const seg of data.segments) {
    if (seg.speaker) allSpeakerLabels.add(seg.speaker);
  }
  vlog(`Distinct speaker labels found: ${[...allSpeakerLabels].join(', ')}`);

  const speakerMapping = {};
  if (intro.speakerLabel) speakerMapping[intro.speakerLabel] = 'Heather Reilly Hiemstra';

  for (const label of allSpeakerLabels) {
    if (label === intro.speakerLabel) continue;
    if (guestName) {
      // Assume the next non-Heather speaker label is the guest. For 2-speaker episodes
      // this is unambiguous. For 3+ speakers, PM should surface a card.
      if (!Object.values(speakerMapping).includes(guestName)) {
        speakerMapping[label] = guestName;
      } else {
        speakerMapping[label] = `Unknown — PM card needed`;
      }
    } else {
      speakerMapping[label] = `Unknown — PM card needed`;
    }
  }

  // 5. Outro speaker check (informational, not blocking)
  let outroSpeakerCheck = null;
  if (outro && outro.speakerLabel) {
    const mapped = speakerMapping[outro.speakerLabel];
    outroSpeakerCheck = {
      detected_speaker: outro.speakerLabel,
      mapped_to: mapped || 'unmapped',
      expected: 'Heather Reilly Hiemstra',
      matches_expectation: mapped === 'Heather Reilly Hiemstra',
    };
    if (!outroSpeakerCheck.matches_expectation) {
      warn(`Outro spoken by ${mapped || outro.speakerLabel} — expected Heather. Flag in Calendar invite description for John review.`);
    }
  }

  // 6. Write boundaries.json
  const output = {
    schema_version: '0.1',
    generated_at: new Date().toISOString(),
    source_whisperx: path.resolve(args.whisperx),
    intro: {
      detected: intro.method === 'regex_match',
      detection_method: intro.method,
      start_ms: intro.startMs,
      segment_index: intro.segmentIndex,
      speaker_label: intro.speakerLabel,
      raw_text: intro.rawText,
      matched_phrase: intro.method === 'regex_match' ? PATTERNS.introRegex.toString() : null,
    },
    outro: outro ? {
      detected: true,
      end_ms: outro.endMs,
      segment_index: outro.segmentIndex,
      speaker_label: outro.speakerLabel,
      raw_text: outro.rawText,
      detection_method: outro.method,
      matched_pattern: outro.method === 'canonical' ? PATTERNS.outroCanonical : PATTERNS.outroFallbackSubstring,
      speaker_check: outroSpeakerCheck,
    } : {
      detected: false,
      end_ms: null,
      pm_uncertainty: 'Outro not detected — Heather should provide trim end timestamp before pipeline proceeds.',
    },
    trim: {
      start_ms: intro.startMs,
      end_ms: outro ? outro.endMs : null,
    },
    guest_name: guestName,
    speaker_mappings: speakerMapping,
    pm_actions: buildPmActions(intro, outro, guestName, outroSpeakerCheck),
  };

  fs.writeFileSync(args.out, JSON.stringify(output, null, 2), 'utf8');
  log(`✓ boundaries.json written to ${args.out}`);

  // Exit 0 always when boundaries.json is successfully written.
  // Guest name and outro speaker issues are surfaced via pm_actions[] in
  // boundaries.json and via stderr warns above — the pipeline continues and
  // Heather confirms during editorial review. Hard exit(3) was v1 "halt" behavior;
  // retired 2026-05-26 (same rationale as exit(2) retirement for intro fallback).
  process.exit(0);
}

// ============================================================
// PM ACTION DERIVATION (cards PM should surface based on findings)
// ============================================================

function buildPmActions(intro, outro, guestName, outroSpeakerCheck) {
  const actions = [];
  if (!guestName) {
    actions.push({
      type: 'card_surface',
      audience: 'Heather',
      card: 'Confirm guest name for E{N} — intro phrase matched but "today we have [Name]" pattern did not extract a clean name',
    });
  }
  if (!outro) {
    actions.push({
      type: 'card_surface',
      audience: 'Heather',
      card: 'PM uncertainty — outro not detected AND last-segment fallback unusable for E{N}; provide trim end timestamp',
      severity: 'blocking',
    });
  } else if (outro.method === 'email_fallback') {
    actions.push({
      type: 'calendar_invite_note',
      audience: 'John',
      note: `Outro detected via email-substring fallback at ${(outro.endMs / 1000).toFixed(2)}s — confirm trim end on review`,
    });
  } else if (outro.method === 'last_segment_fallback') {
    actions.push({
      type: 'calendar_invite_note',
      audience: 'John',
      note: `No outro phrase detected — trimmed to end of last segment at ${(outro.endMs / 1000).toFixed(2)}s. Confirm trim end on review.`,
      severity: 'fyi',
    });
  }
  if (outroSpeakerCheck && !outroSpeakerCheck.matches_expectation) {
    actions.push({
      type: 'calendar_invite_note',
      audience: 'John',
      note: `Outro spoken by ${outroSpeakerCheck.mapped_to}, expected Heather — flagging for review`,
    });
  }
  return actions;
}

main();
