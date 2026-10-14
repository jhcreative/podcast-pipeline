#!/usr/bin/env node

/**
 * test_speaker_role.js — unit matrix for deriveSpeakerRole()
 * v1.0 · 2026-06-18
 *
 * Born from a real incident: E27's reels rendered with the HOST background over a
 * GUEST's words. The guest "Lorna Vashti-Mercer" shares the host's first name
 * "Heather", and the old derivation was a bare substring test
 * (`speaker.includes('heather') ? 'host' : 'guest'`), so every guest quote was
 * mislabeled 'host'. The fix replaced the substring with a HOST_ALIASES exact
 * match; this matrix locks that behavior in (PQ-5: honest systems — prove the
 * discriminator, don't assume it).
 *
 * Zero dependencies — plain node, same loud PASS/FAIL + exit-code convention as
 * verify_reel_spec.js. Imports the pure helper directly from the source script.
 *
 * Usage:   node test_speaker_role.js
 * Exit:    0 = all cases pass · 1 = one or more failed
 */

import { deriveSpeakerRole } from './lookup_quote_timestamps.js';

// Each case: [label, speaker, existingRole, expected, AC].
// existingRole is the speaker_role already present on the quote (AC1 path).
const CASES = [
  ['bare "Heather" → host (Brand Team shorthand for the host)',
    'Heather', undefined, 'host', 'AC3'],

  ['host full name "Heather Reilly Hiemstra" → host',
    'Heather Reilly Hiemstra', undefined, 'host', 'AC4'],

  ['host full name hyphenated "Heather Reilly-Hiemstra" → host',
    'Heather Reilly-Hiemstra', undefined, 'host', 'AC4'],

  ['guest sharing first name "Lorna Vashti-Mercer" → guest',
    'Lorna Vashti-Mercer', undefined, 'guest', 'AC2'],

  ['other guest "Nadia Brennick-Hale" → guest',
    'Nadia Brennick-Hale', undefined, 'guest', 'AC5'],

  ['other guest "Dr. Joe" → guest',
    'Dr. Joe', undefined, 'guest', 'AC5'],

  ['explicit speaker_role:"guest" on a "Heather" is respected, not overwritten',
    'Heather', 'guest', 'guest', 'AC1'],
];

function main() {
  let failed = 0;

  for (const [label, speaker, existingRole, expected, ac] of CASES) {
    const got = deriveSpeakerRole(speaker, existingRole);
    if (got === expected) {
      console.log(`[role] PASS ${ac}  ${label}`);
    } else {
      failed++;
      console.error(`[role] FAIL ${ac}  ${label}`);
      console.error(`[role]   ✗ speaker=${JSON.stringify(speaker)} existingRole=${JSON.stringify(existingRole)} — expected ${expected}, got ${got}`);
    }
  }

  const total = CASES.length;
  console.log(`[role] ${total - failed}/${total} passed`);
  process.exit(failed === 0 ? 0 : 1);
}

main();
