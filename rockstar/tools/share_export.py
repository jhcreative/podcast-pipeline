#!/usr/bin/env python3
"""share_export.py — build the public showcase (SHARE-02) from this repo: an allowlisted, redacted copy with one commit.

    python3 -m rockstar.tools.share_export <dest> [--denylist PATH] [--redact PATH] [--skip-tests]

What leaves is what git has at HEAD (never the working tree), filtered by share/ALLOWLIST, rewritten by the private
redaction map, with share/overlay/ laid on top (the export's README and FIX-01's synthetic fixtures). Then Orly's
gates run on the result, and only when every one passes does <dest> get its single commit:

    G1  only allowlisted (or overlay) paths are present
    G2  the exported tests pass, run from <dest> with the denylist, so the private ones run too (--skip-tests skips
        this and blocks the commit)
    G3  gitleaks and trufflehog over the tree: zero findings (a scanner that isn't installed is a FAIL, not a pass)
    G4  zero hits from the private denylist and the redaction map's left sides, case-insensitive, text and binaries
        alike (bytes, UTF-16, and spaced terms with the spaces gone); `word:` entries match whole words only
    G4  (a binary's coincidental hit may be waived, pinned by sha256 and list line, in share/G4-WAIVED)
    G5  every binary has been looked at by a person: its sha256 is in share/G5-REVIEWED with a name and date
    G6  SEC-01 is in the export (rockstar/service/webhook_auth.py present), so the webhook's fail-closed fix ships
        with it
    G8  no internal agent name survives: the agents' public names (share/PUBLIC_NAMES) are applied as it copies,
        the way JHC's post engine applies them (John, 2026-10-08)

G7 (Orly's clearance, Rockstar's consent, John's go) is not a script: nothing here pushes anywhere (OPS-5).

The denylist and the redaction map are private: they name the guests, the mailboxes and the box. They live outside
every repo (default ~/jhc/secrets/rockstar-podcast/), so the list of what must not leak never leaks itself. A gate
that finds a hit names the file and the denylist LINE NUMBER, never the term.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # standalone on the Mac: no package import needed
SECRETS = Path.home() / "jhc" / "secrets" / "rockstar-podcast"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff"}
AUTHOR = "John Hiemstra <2340330+jhcreative@users.noreply.github.com>"  # his GitHub no-reply (John, 2026-10-11)
MESSAGE = "podcast-pipeline: an episode pipeline, its measurement and its evals (exported from a private repo)"


def fail(msg: str) -> None:
    print(f"[share] FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def git(*args: str, cwd: Path, data: bool = False):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=True)
    return r.stdout if data else r.stdout.decode()


# ── inputs ────────────────────────────────────────────────────────────────────────────────────────────────────────


def read_allowlist(path: Path) -> list[tuple[bool, str]]:
    rules = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rules.append((False, line[1:]) if line.startswith("!") else (True, line))
    if not rules:
        fail(f"{path} has no rules — an empty allowlist would export nothing and prove nothing")
    return rules


def allowed(path: str, rules: list[tuple[bool, str]]) -> bool:
    verdict = False
    for include, pattern in rules:
        if fnmatch.fnmatchcase(path, pattern):
            verdict = include
    return verdict


def read_denylist(path: Path) -> list[tuple[int, bytes]]:
    """Each line is a term, matched case-insensitively anywhere in a file. A line starting `word:` is matched only as a
    whole word (a first name: 'Ada' must not hit 'Canada'); those come back with a b'word:' prefix."""
    if not path.is_file():
        fail(f"denylist not found at {path} — G4 can't run without it (set --denylist or DENYLIST_PATH)")
    terms = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append((n, line.lower().encode()))
    if not terms:
        fail(f"{path} has no terms — G4 over an empty denylist is UNVERIFIED, not clean")
    return terms


def read_redactions(path: Path) -> list[tuple[str, str]]:
    if not path.is_file():
        fail(f"redaction map not found at {path} (set --redact or REDACT_PATH)")
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        if line.count("\t") != 1:
            fail(f"{path}:{n} is not 'real<TAB>replacement'")
        real, repl = line.split("\t")
        if not real:
            fail(f"{path}:{n} has an empty left side")
        rows.append((real, repl))
    return rows


def compile_redactions(rows: list[tuple[str, str]]) -> tuple[re.Pattern | None, dict[str, str]]:
    """One pass: whole-word, literal, case-sensitive, longest alternative first, so a full name wins over its surname
    and nothing is rewritten twice. Whole-word keeps 'Oliver' -> 'Oliver' from turning 'Robert' into 'Oliverert'. A row
    that maps a phrase to itself KEEPS it: 'Pete Townshend' stays, though 'Bill' alone becomes 'Bill' (Orly, PR #40)."""
    table = dict(sorted(rows, key=lambda r: -len(r[0])))
    if not table:
        return None, {}
    alts = "|".join(re.escape(k) for k in sorted(table, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9])(?:{alts})(?![A-Za-z0-9])"), table


def kept_phrases(rows: list[tuple[str, str]]) -> list[str]:
    return [real for real, repl in rows if real == repl]


# ── build ─────────────────────────────────────────────────────────────────────────────────────────────────────────


def is_text(blob: bytes) -> bool:
    if b"\0" in blob[:8192]:
        return False
    try:
        blob.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def build(src: Path, dest: Path, rules, redactions) -> list[str]:
    tracked = git("ls-tree", "-r", "--name-only", "HEAD", cwd=src).splitlines()
    picked = [p for p in tracked if allowed(p, rules)]
    swapped: dict[str, int] = {}
    pattern, table = redactions
    for p in picked:
        blob = git("show", f"HEAD:{p}", cwd=src, data=True)
        if is_text(blob):
            text = blob.decode("utf-8")
            new = pattern.sub(lambda m: table[m.group(0)], text) if pattern else text  # noqa: B023 (used at once)
            if new != text:
                swapped[p] = sum(a != b for a, b in zip(text.splitlines(), new.splitlines(), strict=False))
            blob = new.encode("utf-8")
        out = dest / p
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(blob)
        mode = git("ls-tree", "HEAD", p, cwd=src).split()[0]
        if mode == "100755":
            out.chmod(0o755)
    # The overlay comes from HEAD too: a gitignored file dropped in share/overlay/ (a .log, an .env) never leaves,
    # because git status can't see it and the clean-tree check wouldn't stop it (Toby, PR #40).
    laid = []
    for path in tracked:
        if path.startswith("share/overlay/"):
            rel = path[len("share/overlay/") :]
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            (dest / rel).write_bytes(git("show", f"HEAD:{path}", cwd=src, data=True))
            laid.append(rel)
    # The disclosure (John, 2026-10-11): every file whose text differs from the private source, and by how many lines.
    lines = [
        "# What differs from the production source",
        "",
        "This repo is exported from the private repo that runs in production. The pipeline's logic, steps and",
        "order are the same. Where a real name, mailbox, domain, ID or address appeared (in code, comments,",
        "prompts or tests), it was replaced by an invented or `example.test` value, and the agents appear under",
        "their public names. These files differ from the private source in that way, and only in that way:",
        "",
        "| File | Lines changed |",
        "|---|---|",
    ]
    lines += [f"| `{p}` | {n} |" for p, n in sorted(swapped.items())]
    lines += [
        "",
        f"{len(swapped)} files. Every other exported file is byte-for-byte the private source, except this file,",
        "the README, the LICENSE and two stand-in images (`rockstar/cards/background.png`,",
        "`BrandAssets/reel_background_host.jpg`), which replace Rockstar's artwork and the host's photo.",
        "",
    ]
    (dest / "SWAPPED.md").write_text("\n".join(lines), encoding="utf-8")
    laid.append("SWAPPED.md")
    # strip EXIF/XMP from every image (G5's metadata half); exiftool missing is a stop, not a skip
    images = [dest / p for p in picked + laid if Path(p).suffix.lower() in IMAGE_EXT]
    if images:
        if not shutil.which("exiftool"):
            fail("exiftool is not installed — image metadata can't be stripped")
        subprocess.run(["exiftool", "-q", "-all=", "-overwrite_original", *map(str, images)], check=True)
    print(f"[share] exported {len(picked)} tracked files + {len(laid)} overlay files; {len(images)} images stripped")
    return laid


# ── gates ─────────────────────────────────────────────────────────────────────────────────────────────────────────


def files_in(dest: Path) -> list[str]:
    return sorted(
        p.relative_to(dest).as_posix()
        for p in dest.rglob("*")
        if p.is_file()
        and ".git" not in p.relative_to(dest).parts
        and "__pycache__" not in p.parts
        and ".pytest_cache" not in p.parts
    )


def g1(dest: Path, rules, laid: list[str]) -> list[str]:
    bad = [p for p in files_in(dest) if not allowed(p, rules) and p not in laid]
    print(f"[share] G1 allowlist: {len(files_in(dest))} files checked, {len(bad)} outside it")
    return [f"G1 {p} is not allowlisted" for p in bad]


def g2(dest: Path, denylist: Path) -> list[str]:
    # DENYLIST_PATH is passed in, so the export's `private` tests (FIX-01 AC-3) run here rather than being skipped
    # In a throwaway git copy, as a fresh clone would be (AC-3): the deploy tests read `git ls-files`.
    clone = Path(tempfile.mkdtemp(prefix="share-g2-")) / "podcast-pipeline"
    shutil.copytree(dest, clone)
    for cmd in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["-c", "user.name=g2", "-c", "user.email=g2@example.test", "commit", "-q", "-m", "g2"],
    ):
        subprocess.run(["git", "-C", str(clone), *cmd], check=True)
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=clone,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "DENYLIST_PATH": str(denylist)},
    )
    shutil.rmtree(clone.parent, ignore_errors=True)
    tail = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
    print(f"[share] G2 exported tests, in a fresh git copy: {tail}")
    if r.returncode != 0:
        return [f"G2 the exported tests fail:\n{r.stdout[-3000:]}{r.stderr[-1000:]}"]
    if "skipped" in tail:
        return [f"G2 a test was skipped ({tail}) — before publishing, every offline test runs"]
    return []


def g3(dest: Path) -> list[str]:
    problems = []
    for tool, cmd in (
        ("gitleaks", ["gitleaks", "dir", str(dest), "--no-banner", "--redact", "--exit-code", "1"]),
        ("trufflehog", ["trufflehog", "filesystem", str(dest), "--no-update", "--fail", "--json"]),
    ):
        if not shutil.which(tool):
            problems.append(f"G3 {tool} is not installed — no scan is not a clean scan")
            continue
        version = subprocess.run(
            [tool, "version" if tool == "gitleaks" else "--version"], capture_output=True, text=True
        )
        r = subprocess.run(cmd, capture_output=True, text=True)
        found = r.returncode != 0
        print(
            f"[share] G3 {tool} {(version.stdout or version.stderr).strip()}: "
            f"{'FINDINGS' if found else 'zero findings'}"
        )
        if found:
            problems.append(f"G3 {tool} reported findings (exit {r.returncode}): {scanner_findings(r.stdout)}")
    return problems


def scanner_findings(out: str) -> str:
    """Detector, file and line for each finding, never the matched secret (trufflehog's JSON carries it raw; Orly)."""
    import json

    found = []
    for line in out.splitlines():
        try:
            f = json.loads(line)
        except ValueError:
            continue
        meta = (f.get("SourceMetadata") or {}).get("Data") or {}
        loc = next(iter(meta.values()), {}) if meta else {}
        found.append(f"{f.get('DetectorName', '?')} in {loc.get('file', '?')}:{loc.get('line', '?')}")
    return "; ".join(found) or "(see the tool's own run; its output is not echoed here)"


WAIVABLE_EXT = {
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".mp3",
    ".m4a",
    ".wav",
    ".mp4",
    ".mov",
}


def term_key(term: bytes) -> str:
    return hashlib.sha256(term).hexdigest()[:12]


def g4_waivers(src: Path) -> dict[tuple[str, str, str], str]:
    """share/G4-WAIVED: a BINARY file's hit a person judged a coincidence (a font's designer credits sharing a guest's
    first name). Each line pins the file's sha256, its path AND the one list entry it may match (its line number and
    the first 12 hex of the term's sha256, so a reordered list can't move the waiver onto another name), with who,
    when and why:
        <sha256>  <path>  denylist:<n>:<term sha12>|redaction-map:<n>:<term sha12>  <who> <date>  <why …>
    New bytes, another path or another entry, and the hit stands. Only WAIVABLE_EXT files (fonts, images, audio,
    video) can be waived: a text file, even one is_text calls binary, is scrubbed (Orly's G7, 2026-10-13)."""
    out = {}
    ledger = src / "share" / "G4-WAIVED"
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                digest, rel, which = line.split()[:3]
                out[(digest, rel, which)] = line
    return out


def g4(
    dest: Path,
    terms: list[tuple[int, bytes]],
    redact_terms: list[tuple[int, bytes]],
    waivers: dict[tuple[str, str, str], str] | None = None,
) -> list[str]:
    """Every denylist term AND the left side of every redaction row, in every file, text or binary. Redaction only
    rewrites what is_text accepts, so a term in a file it skips (Latin-1, a NUL in the first 8 KB) must still be
    caught here (Toby, PR #40). Hits name the list and line, never the term. A binary's hit can be waived by
    share/G4-WAIVED (g4_waivers); each waiver used is printed."""
    hits, waived = [], []
    waivers = waivers or {}
    binary = set(binaries(dest))
    files = files_in(dest)
    for rel in files:
        data = (dest / rel).read_bytes().lower()
        squeezed = re.sub(rb"\s+", b"", data)
        for where, listed in (("denylist line", terms), ("redaction-map line", redact_terms)):
            for n, term in listed:
                if term.startswith(b"word:"):
                    word = re.escape(term[5:])
                    hit = re.search(rb"(?<![a-z0-9])" + word + rb"(?![a-z0-9])", data) is not None
                else:
                    # spaced terms are also looked for with the spaces gone ('van der berg' as 'vanderberg')
                    hit = (
                        term in data
                        or term.decode().encode("utf-16-le") in data
                        or term.decode().encode("utf-16-be") in data  # font name tables (Orly's G7)
                        or (b" " in term and re.sub(rb"\s+", b"", term) in squeezed)
                    )
                if hit:
                    waivable = rel in binary and Path(rel).suffix.lower() in WAIVABLE_EXT
                    key = (
                        (
                            hashlib.sha256((dest / rel).read_bytes()).hexdigest(),
                            rel,
                            f"{where.split()[0]}:{n}:{term_key(term)}",
                        )
                        if waivable
                        else None
                    )
                    if key and key in waivers:
                        waived.append(f"[share] G4 waived (share/G4-WAIVED): {rel} {where} {n}")
                    else:
                        hits.append(f"G4 {rel} contains {where} {n}")
    for w in waived:
        print(w)
    print(
        f"[share] G4 denylist: {len(terms)} terms + {len(redact_terms)} redaction-map terms over {len(files)} "
        f"files (text and binary), {len(hits)} hits, {len(waived)} waived"
    )
    return hits


def redaction_terms(path: Path) -> list[tuple[int, bytes]]:
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip() and not line.startswith("#") and "\t" in line:
            out.append((n, line.split("\t")[0].lower().encode()))
    return out


def binaries(dest: Path) -> list[str]:
    return [p for p in files_in(dest) if not is_text((dest / p).read_bytes())]


def g8(dest: Path, public_names: list[tuple[str, str]]) -> list[str]:
    """An internal agent name in published text (the post engine's stop, applied to the repo). A kept phrase (a row
    mapping to itself, e.g. a real musician who shares an agent's first name) is set aside first."""
    hits = []
    keep = kept_phrases(public_names)
    for rel in files_in(dest):
        blob = (dest / rel).read_bytes()
        if not is_text(blob):
            continue
        text = blob.decode("utf-8")
        for phrase in keep:
            text = text.replace(phrase, "")
        for internal, repl in public_names:
            if internal == repl:
                continue
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(internal)}(?![A-Za-z0-9])", text):
                hits.append(f"G8 {rel} still names the agent '{internal}' (its public name is in share/PUBLIC_NAMES)")
    print(
        f"[share] G8 public agent names: {len(public_names) - len(keep)} internal names over the text files, "
        f"{len(hits)} hits"
    )
    return hits


def g5(src: Path, dest: Path) -> list[str]:
    reviewed = {}
    ledger = src / "share" / "G5-REVIEWED"
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                reviewed[line.split()[0]] = line
    missing = []
    for rel in binaries(dest):
        digest = hashlib.sha256((dest / rel).read_bytes()).hexdigest()
        if digest not in reviewed:
            missing.append(
                f"G5 {rel} has not been reviewed by a person: add '{digest}  {rel}  <who> <date>' "
                f"to share/G5-REVIEWED after looking at it"
            )
    print(f"[share] G5 binaries: {len(binaries(dest))} in the export, {len(missing)} not yet reviewed")
    return missing


def g6(dest: Path) -> list[str]:
    ok = (dest / "rockstar/service/webhook_auth.py").is_file()
    print(f"[share] G6 SEC-01: rockstar/service/webhook_auth.py {'present' if ok else 'MISSING'}")
    return [] if ok else ["G6 rockstar/service/webhook_auth.py (SEC-01) is not in the export"]


# ── main ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dest", type=Path)
    ap.add_argument("--source", type=Path, default=REPO, help="the repo to export (default: this one)")
    ap.add_argument(
        "--denylist", type=Path, default=Path(os.environ.get("DENYLIST_PATH", SECRETS / "share-denylist.txt"))
    )
    ap.add_argument("--redact", type=Path, default=Path(os.environ.get("REDACT_PATH", SECRETS / "share-redact.tsv")))
    ap.add_argument("--skip-tests", action="store_true", help="skip G2 (a dry run: never commits)")
    a = ap.parse_args(argv)

    src, dest = a.source.resolve(), a.dest.resolve()
    if git("status", "--porcelain", cwd=src).strip():
        fail(f"{src} has uncommitted changes — the export is what git has, so commit first")
    if dest.exists() and any(dest.iterdir()):
        fail(f"{dest} exists and is not empty — the export starts from nothing")
    if src in dest.parents or dest == src:
        fail("the export can't live inside this repo")
    rules = read_allowlist(src / "share" / "ALLOWLIST")
    terms = read_denylist(a.denylist)
    public_names = read_redactions(src / "share" / "PUBLIC_NAMES") if (src / "share" / "PUBLIC_NAMES").is_file() else []
    redactions = compile_redactions(read_redactions(a.redact) + public_names)

    dest.mkdir(parents=True, exist_ok=True)
    laid = build(src, dest, rules, redactions)
    problems = (
        g1(dest, rules, laid)
        + g4(dest, terms, redaction_terms(a.redact), g4_waivers(src))
        + g3(dest)
        + g5(src, dest)
        + g6(dest)
        + g8(dest, public_names)
    )
    if a.skip_tests:
        print("[share] G2 exported tests: SKIPPED (--skip-tests) — no commit")
        problems.append("G2 skipped")
    else:
        problems += g2(dest, a.denylist)

    if problems:
        for p in problems:
            print(f"[share] {p}", file=sys.stderr)
        print(
            f"[share] {len(problems)} gate problem(s) — NO commit. The tree is left in {dest} to inspect.",
            file=sys.stderr,
        )
        return 1

    name, email = AUTHOR[:-1].split(" <")
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"]):
        subprocess.run(["git", "-C", str(dest), *cmd], check=True)
    # The exported .gitignore must not drop a file the gates passed: what is committed is exactly what was gated.
    staged = sorted(git("ls-files", cwd=dest).splitlines())
    if staged != files_in(dest):
        shutil.rmtree(dest / ".git")
        dropped = sorted(set(files_in(dest)) - set(staged))
        fail(f"git would commit {len(staged)} of {len(files_in(dest))} gated files (.gitignore drops: {dropped[:5]})")
    subprocess.run(
        ["git", "-C", str(dest), "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-q", "-m", MESSAGE],
        check=True,
    )
    print(f"[share] OK: every gate passed; one commit in {dest}. Nothing was pushed (G7 is Orly, Rockstar, John).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
