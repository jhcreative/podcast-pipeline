# What differs from the production source

This repo is exported from the private repo that runs in production. The pipeline's logic, steps and
order are the same. Where a real name, mailbox, domain, ID or address appeared (in code, comments,
prompts or tests), it was replaced by an invented or `example.test` value, and the agents appear under
their public names. These files differ from the private source in that way, and only in that way:

| File | Lines changed |
|---|---|
| `.env.example` | 3 |
| `blog_publish.py` | 2 |
| `docs/adr/0001-drive-changes-feed-trigger.md` | 3 |
| `docs/adr/0002-publisher-email-drift-tolerance.md` | 7 |
| `docs/adr/0003-reel-clip-complete-thought-framing.md` | 2 |
| `docs/adr/0004-mid-quote-audio-excision.md` | 2 |
| `docs/adr/0005-idempotent-enqueue-dedup-key.md` | 1 |
| `docs/adr/0006-phase1-title-generation-and-shared-gate.md` | 2 |
| `docs/adr/0007-caption-rules-deterministic-credit-and-hashtag-cap.md` | 1 |
| `docs/adr/0008-hand-edited-drive-artifacts-are-authoritative.md` | 4 |
| `docs/adr/0009-apple-summary-title-wins.md` | 1 |
| `docs/adr/0010-deterministic-thank-you-fill.md` | 4 |
| `docs/adr/0011-phase2-trigger-manual-kickoff.md` | 14 |
| `docs/adr/0012-square-quote-cards.md` | 1 |
| `docs/adr/0013-quote-card-captions.md` | 1 |
| `docs/adr/0014-carousel-cover-slides.md` | 2 |
| `docs/adr/0015-cards-in-ibm-plex-built-in-phase-2.md` | 2 |
| `docs/eval/test-strategy.md` | 2 |
| `docs/plans/2026-10-10-measurement-eval-showcase.md` | 18 |
| `docs/stories/BIO-01-guest-bio-from-guest-info-form.md` | 5 |
| `docs/stories/BIO-03-guest-instagram-handle-from-form.md` | 3 |
| `docs/stories/BLOG-01-derive-blog-url-apostrophe-slug.md` | 4 |
| `docs/stories/BLOG-02-tailor-blog-post-via-api.md` | 17 |
| `docs/stories/CACHE-01-brand-team-transcript-cache-never-hits.md` | 2 |
| `docs/stories/CAL-OFF-editorial-calendar-event-off.md` | 1 |
| `docs/stories/CAP-01-caption-rule-assertions.md` | 4 |
| `docs/stories/CARD-01-square-quote-cards.md` | 2 |
| `docs/stories/CARD-02-quote-cards-to-drive.md` | 1 |
| `docs/stories/CARD-03-quote-card-captions.md` | 4 |
| `docs/stories/CARD-04-carousel-cover-slides.md` | 3 |
| `docs/stories/CARD-05-cards-built-in-phase-2-in-ibm-plex.md` | 4 |
| `docs/stories/CASE-01-measurement-case-study.md` | 2 |
| `docs/stories/DASH-01-pipeline-report-charts.md` | 4 |
| `docs/stories/DEPLOY-01-files-manifest-guard.md` | 3 |
| `docs/stories/DL-01-doc-tolerant-download-tests.md` | 2 |
| `docs/stories/E30-01-manual-phase2-build.md` | 3 |
| `docs/stories/EVAL-00-eval-test-strategy-baseline-and-metrics.md` | 4 |
| `docs/stories/EVAL-01-measure-the-writing.md` | 2 |
| `docs/stories/EVAL-02-model-choice-by-measurement.md` | 5 |
| `docs/stories/EVAL-03-replay-mode-and-shareable-fixture.md` | 1 |
| `docs/stories/FIX-01-seeded-synthetic-episode-generator.md` | 3 |
| `docs/stories/HF-01-hf-token-off-the-command-line.md` | 7 |
| `docs/stories/HF-02-hf-token-out-of-the-shared-env.md` | 3 |
| `docs/stories/MEM-01-work-dirs-on-disk.md` | 1 |
| `docs/stories/OBS-01-run-ledger-steps-and-model-calls.md` | 10 |
| `docs/stories/PROMPT-01-one-source-and-every-live-prompt-ships.md` | 11 |
| `docs/stories/PUB-01-idempotent-publish-finalize.md` | 4 |
| `docs/stories/R1-01-inbox-matcher-drift-silent-noop.md` | 3 |
| `docs/stories/R1-02-05-round-1-reliability.md` | 1 |
| `docs/stories/R1-07-reprocess-chain-and-exact-target.md` | 1 |
| `docs/stories/REEL-05-e28-tail-defects-and-the-unreachable-fix.md` | 2 |
| `docs/stories/REEL-06-reel-candidate-flag-ignored.md` | 2 |
| `docs/stories/REEL-07-caption-boundary-frame-paints-two-states.md` | 1 |
| `docs/stories/REEL-08-speaker-backgrounds-and-heather-as-guest.md` | 1 |
| `docs/stories/REEL-09-host-identity-by-surname.md` | 5 |
| `docs/stories/REEL-10-zero-reels-must-not-empty-drive.md` | 1 |
| `docs/stories/SEC-01-webhook-service-fails-closed.md` | 19 |
| `docs/stories/SHARE-01-a-repo-a-stranger-can-run.md` | 9 |
| `docs/stories/SHARE-02-public-showcase-repo.md` | 1 |
| `docs/stories/TITLE-01-phase1-title-generation-and-gate.md` | 4 |
| `docs/stories/TITLE-02-brand-team-quotes-blank-in-summary.md` | 1 |
| `docs/stories/TITLE-04-apple-summary-title-precedence-tests.md` | 2 |
| `docs/stories/TRIG-01-event-driven-phase2-trigger.md` | 10 |
| `docs/stories/TRIG-02-visuals-start-phase-2.md` | 2 |
| `docs/stories/TRIG-03-late-publisher-email-swaps-links-only.md` | 14 |
| `docs/stories/TY-01-clean-e29-guest-draft.md` | 5 |
| `docs/stories/TY-02-deterministic-thank-you-fill.md` | 7 |
| `docs/stories/TY-03-cli-live-url-verify.md` | 2 |
| `drive_client.py` | 1 |
| `episode_files.py` | 1 |
| `gmail_client.py` | 1 |
| `google_auth.py` | 3 |
| `guest_form.py` | 2 |
| `host_identity.py` | 1 |
| `jobs/publish_finalize.py` | 8 |
| `jobs/round_1.py` | 2 |
| `prompts/apple_summary.md` | 1 |
| `prompts/brand_team_marcus.md` | 1 |
| `prompts/title.md` | 2 |
| `publisher_email.py` | 9 |
| `quote_cards/card_upload.py` | 1 |
| `quote_cards/cover_backfill.py` | 1 |
| `quote_cards/cover_format.py` | 3 |
| `quote_cards/make_cover_titles.py` | 1 |
| `quote_cards/make_final_proof.py` | 1 |
| `quote_cards/plex_proof.py` | 2 |
| `quote_cards/render_covers.py` | 1 |
| `scripts/box-card-setup.sh` | 1 |
| `scripts/box.sh` | 2 |
| `scripts/box_gate.sh` | 1 |
| `scripts/deploy.sh` | 4 |
| `scripts/detect_boundaries.js` | 5 |
| `scripts/extract_quote_clips.sh` | 1 |
| `scripts/lookup_quote_timestamps.js` | 2 |
| `scripts/populate_caption_words.js` | 1 |
| `scripts/share_export.py` | 4 |
| `scripts/test_audio_excision.js` | 1 |
| `scripts/test_caption_reconcile.js` | 1 |
| `scripts/test_speaker_role.js` | 5 |
| `tests/fixtures/trig03/auth_headers.json` | 21 |
| `tests/test_blog01_derive_blog_url.py` | 2 |
| `tests/test_blog02b_tailor_write.py` | 5 |
| `tests/test_fix01_no_real_names.py` | 1 |
| `tests/test_prompt01_manifest.py` | 2 |
| `tests/test_pub01_idempotent_enqueue.py` | 4 |
| `tests/test_publisher_email.py` | 6 |
| `tests/test_sched01_phase2_writeback.py` | 2 |
| `tests/test_sec01_webhook_auth.py` | 2 |
| `tests/test_share02_export.py` | 1 |
| `tests/test_title01_gen_gate.py` | 6 |
| `tests/test_trig02_visuals_trigger.py` | 1 |
| `tests/test_trig03_links_only.py` | 27 |
| `thank_you.py` | 3 |
| `tools/publish_blog_post.py` | 1 |
| `tools/reprocess_episode.py` | 2 |
| `tools/rerun_brand_team.py` | 1 |
| `tools/run_dr_joe.py` | 5 |
| `watches/renew_watches.py` | 1 |
| `webhook_service.py` | 4 |

119 files. Every other exported file is byte-for-byte the private source, except this file,
the README, the LICENSE and two stand-in images (`quote_cards/background.png`,
`BrandAssets/reel_background_host.jpg`), which replace Rockstar's artwork and the host's photo.
