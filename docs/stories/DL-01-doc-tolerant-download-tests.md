# DL-01 — Tests for Doc-tolerant download_file

**Owner:** Jen · **PO:** Angela · **Priority:** Medium (fast-follow to ADR-0008)
**Status:** Ready · **Created:** 2026-06-26 · **Raised by:** Bill (fix/download-export-google-docs)

---

## Why

ADR-0008 made `drive_client.download_file` export Google-Apps-Editors files to text instead
of 403-ing on binary download. Bill verified it by hand against real Drive (Doc → text with
BOM stripped; binary file unchanged), but it's not in the suite. This is a shared, critical
path — every artifact download goes through it — so it earns automated coverage.

## Acceptance criteria

1. **Editors export** — a `application/vnd.google-apps.document` file downloads as its text
   export (mock `files().get` to return the Doc mime, assert `export_media` is called with
   `text/plain`, not `get_media`).
2. **Mime mapping** — `spreadsheet → text/csv`, `presentation → text/plain`; an unmapped
   Editors mime raises a clear RuntimeError (does not silently 403).
3. **BOM strip** — an export whose bytes begin with the UTF-8 BOM is written without it
   (downstream `json.loads` on the result succeeds).
4. **Binary unchanged** — a non-Editors mime (`audio/x-m4a`, `image/jpeg`, `text/plain`)
   takes the `get_media` path; bytes are written verbatim, no BOM handling.
5. **One metadata call** — the added `files().get(fields="mimeType,name")` happens once per
   download.

## Notes

Pure unit tests with a mocked Drive service are enough — no live Drive needed. The integration
behavior is already proven against production Drive in the PR.
