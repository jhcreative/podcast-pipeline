"""
test_hf01_token_off_cmdline.py — HF-01: the Hugging Face token never rides on a command line.

Run: python3 -B test_hf01_token_off_cmdline.py   (plain asserts, no pytest needed)

Why (2026-10-01): transcribe_fast.sh ran `whisperx ... --hf_token "$HF_TOKEN"`. On Linux a process's argv
is world-readable for its whole run (`ps`, /proc/<pid>/cmdline), so every user and process on the pipeline
box could read the token, and it was exposed in a `ps` listing during the E32 Round 1 run. The fix puts the
token in WhisperX's ENVIRONMENT instead (readable only by its own user and root). whisperx 3.8.5 with no
--hf_token passes token=None to pyannote, and huggingface_hub reads HF_TOKEN from the environment.

Acceptance criteria → tests:
  AC-1 no shipped script passes the token as an argument  → test_no_script_passes_token_as_argument
  AC-2 the REAL transcribe_fast.sh, run end to end against a stub whisperx, gives the stub HF_TOKEN in its
       environment and never in its argv, and the token appears in no file the run leaves behind
       → test_transcribe_fast_token_in_env_not_argv
  AC-3 a missing token still stops the script with exit 4 before whisperx runs
       → test_transcribe_fast_missing_token_exits_4

Discipline: the script under test is the real one (no copy, no re-implementation). Only whisperx is stubbed:
it is a model run, not the thing under test.
"""

import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
SCRIPTS = REPO / "scripts"
SENTINEL = "hf_SENTINEL_hf01_do_not_leak_0123456789abcdef"

# Patterns that would put the token on argv. Comment lines are skipped: the fix explains itself in prose.
_ARGV_PATTERNS = [
    re.compile(r"--hf[_-]token"),
    re.compile(r"--(use[_-]auth[_-])?token\b"),
    re.compile(r'"?\$\{?HF_TOKEN\}?"?\s*(\\|$|\))'),  # "$HF_TOKEN" as a bare argument / array element
]


def _code_lines(path: Path):
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        yield n, line


def test_no_script_passes_token_as_argument():
    hits = []
    for p in sorted(SCRIPTS.glob("*.sh")):
        for n, line in _code_lines(p):
            for pat in _ARGV_PATTERNS:
                if pat.search(line):
                    hits.append(f"{p.name}:{n}: {line.strip()}")
    assert not hits, "token passed as an argument:\n  " + "\n  ".join(hits)


_STUB_WHISPERX = r"""#!/bin/bash
# Stub whisperx for HF-01: record argv and whether HF_TOKEN is in the environment, then write the JSON
# transcribe_fast.sh expects. It prints nothing (whisperx.log must stay clean of the token either way).
out=""
prev=""
for a in "$@"; do
  [[ "$prev" == "--output_dir" ]] && out="$a"
  prev="$a"
done
printf '%s\n' "$@" > "$HF01_RECORD/argv.txt"
if [[ "${HF_TOKEN:-}" == "$HF01_EXPECT" ]]; then echo yes > "$HF01_RECORD/env.txt"; else echo no > "$HF01_RECORD/env.txt"; fi
printf '{"segments":[{"speaker":"SPEAKER_00","start":0.0,"end":1.0,"text":" hello"}]}' > "$out/input.json"
"""


def _make_env(tmp: Path, with_token: bool):
    venv = tmp / "venv"
    (venv / "bin").mkdir(parents=True)
    stub = venv / "bin" / "whisperx"
    stub.write_text(_STUB_WHISPERX)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    (venv / "bin" / "activate").write_text(f'PATH="{venv}/bin:$PATH"\ndeactivate() {{ :; }}\n')
    env_file = tmp / "pipeline.env"
    env_file.write_text(f"HF_TOKEN={SENTINEL}\n" if with_token else "OTHER=1\n")
    env_file.chmod(0o600)
    record = tmp / "record"
    record.mkdir()
    # The script refuses to run outside $HOME/RockstarPodcast/ (or the server's work dirs) — a safety rule kept as
    # is. So the test gives it a throwaway HOME (real path: the script compares realpath) with the episode under
    # $HOME/RockstarPodcast/, which works wherever the repo is checked out (SHARE-01: it used to need ~/RockstarPodcast).
    fake_home = Path(os.path.realpath(tmp)) / "home"
    ep = fake_home / "RockstarPodcast" / "hf01-ep"
    ep.mkdir(parents=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
         "-t", "1", "-c:a", "aac", str(ep / "hf01.m4a")],
        check=True,
    )
    env = {
        **{k: v for k, v in os.environ.items() if k != "HF_TOKEN"},
        "ENV_FILE": str(env_file),
        "VENV_PATH": str(venv),
        "WORK_DIR_BASE": str(tmp / "work"),
        "HOME": str(fake_home),
        "PROMPT_FILE": str(tmp / "no-prompt.txt"),
        "HF01_RECORD": str(record),
        "HF01_EXPECT": SENTINEL,
    }
    (tmp / "work").mkdir()
    return ep, env, record


def _run(ep: Path, env: dict):
    return subprocess.run(
        ["bash", str(SCRIPTS / "transcribe_fast.sh"), str(ep), "hf01"],
        env=env, capture_output=True, text=True, timeout=120,
    )


def _all_text_under(*roots: Path) -> str:
    chunks = []
    for root in roots:
        for f in root.rglob("*"):
            if f.is_file() and f.name != "pipeline.env":
                try:
                    chunks.append(f.read_text(errors="ignore"))
                except OSError:
                    pass
    return "\n".join(chunks)


def test_transcribe_fast_token_in_env_not_argv():
    with tempfile.TemporaryDirectory(prefix="hf01-") as t:
        tmp = Path(t)
        ep, env, record = _make_env(tmp, with_token=True)
        try:
            r = _run(ep, env)
            assert r.returncode == 0, f"script failed rc={r.returncode}: {r.stderr[-400:]}"
            argv = (record / "argv.txt").read_text()
            assert SENTINEL not in argv, "the token is on whisperx's command line"
            assert "--hf_token" not in argv, "--hf_token is still passed"
            assert (record / "env.txt").read_text().strip() == "yes", "whisperx did not get HF_TOKEN in its environment"
            assert SENTINEL not in r.stdout + r.stderr, "the token was printed by the script"
            assert SENTINEL not in _all_text_under(ep, tmp / "work"), "the token was written to a run file"
        finally:
            shutil.rmtree(ep, ignore_errors=True)


def test_transcribe_fast_missing_token_exits_4():
    with tempfile.TemporaryDirectory(prefix="hf01-") as t:
        tmp = Path(t)
        ep, env, record = _make_env(tmp, with_token=False)
        try:
            r = _run(ep, env)
            assert r.returncode == 4, f"expected exit 4 for a missing token, got {r.returncode}"
            assert not (record / "argv.txt").exists(), "whisperx ran without a token"
        finally:
            shutil.rmtree(ep, ignore_errors=True)


def main() -> int:
    tests = [
        test_no_script_passes_token_as_argument,
        test_transcribe_fast_token_in_env_not_argv,
        test_transcribe_fast_missing_token_exits_4,
    ]
    failed = 0
    print("HF-01 token off the command line — test run")
    for t in tests:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
