"""
test_prompt01_manifest.py — PROMPT-01 Part 1: every live prompt ships, and deploy refuses a manifest with gaps.

Run: python3 -B test_prompt01_manifest.py   (plain asserts, no pytest needed)

Why (2026-10-10): four prompts anthropic_client.py loads (apple_summary, faq, brand_team_robert,
brand_team_dr_joe) and four modules the box runs (worker.py, google_auth.py, jobs/__init__.py,
jobs/round_2.py) were not in deploy.sh FILES. deploy --check proved byte parity for what was listed and
could not see what wasn't, so the next edit to the show-notes prompt would have stayed on the laptop while
--check reported green. scripts/check_manifest.py checks the list against the code; deploy.sh runs it first.

Acceptance criteria → tests:
  AC-1 deploy --check lists all four prompts; a manifest missing a loaded prompt exits non-zero
       → test_real_tree_is_complete, test_four_prompts_are_in_files, test_loaded_prompt_missing_from_files_fails,
         test_deploy_runs_the_guard_before_anything_ships
  AC-5 (Part 1 half) the guard fails on a gap of each kind and passes the cleaned tree
       → test_unlisted_unretired_prompt_fails, test_loaded_prompt_that_does_not_exist_fails,
         test_systemd_entry_point_missing_fails, test_first_party_import_missing_fails,
         test_third_party_and_stdlib_imports_are_ignored, test_retired_prompt_still_listed_fails
  Scope 3 (never overwrite a box copy blind): scripts/box_gate.sh, run for real against a fake `ssh` on PATH
       → test_gate_* (absent, same, an older committed version, edited on the box, ssh down, a box checksum
         error, a short answer, a missing local file) and test_newly_managed_files_are_listed_and_gated
  PQ-5a (a check over nothing, or over a form it can't read, is not a pass)
       → test_empty_manifest_is_unverified_not_ok, test_unreadable_forms_are_unverified,
         test_unparsed_entry_point_is_a_gap, test_brand_team_loop_and_worker_dynamic_import_are_read

Discipline: the guard and the gate under test are the real scripts, run as subprocesses against the real tree
and against small fixture trees. Only `ssh` is faked; nothing touches the box.
Cold review of PR #29 (2026-10-10): the first gate trusted a failed ssh as "absent" and the first tests only
checked where strings sat in deploy.sh. These tests run the gate.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
GUARD = REPO / "scripts" / "check_manifest.py"
DEPLOY = REPO / "scripts" / "deploy.sh"
GATE = REPO / "scripts" / "box_gate.sh"
_TMP: list[Path] = []
FOUR = ["prompts/apple_summary.md", "prompts/faq.md", "prompts/brand_team_robert.md", "prompts/brand_team_dr_joe.md"]


def _run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(GUARD), "--root", str(root)],
                          capture_output=True, text=True, timeout=60)


def _fixture(files: list[str], prompts: dict[str, str], client: str, modules: dict[str, str],
             exec_start: str = "/venv/bin/python worker.py") -> Path:
    """A minimal repo: deploy.sh with FILES, prompts/, anthropic_client.py, one systemd unit, modules."""
    root = Path(tempfile.mkdtemp(prefix="prompt01-"))
    _TMP.append(root)
    (root / "scripts").mkdir()
    entries = "\n".join(f'  "{f}"   # comment' for f in files)
    (root / "scripts" / "deploy.sh").write_text(f"#!/usr/bin/env bash\nFILES=(\n{entries}\n)\n")
    (root / "prompts").mkdir()
    for name, body in prompts.items():
        (root / "prompts" / name).write_text(body)
    (root / "anthropic_client.py").write_text(client)
    (root / "systemd").mkdir()
    (root / "systemd" / "x.service").write_text(f"[Service]\nExecStart={exec_start}\n")
    for rel, body in modules.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    return root


CLIENT = '_BRAND_TEAM_MEMBERS = ["brand_team_a"]\ndef f():\n    return _load_prompt("title.md")\n'
GOOD_FILES = ["anthropic_client.py", "worker.py", "prompts/title.md", "prompts/brand_team_a.md"]
GOOD_PROMPTS = {"title.md": "t", "brand_team_a.md": "a"}
GOOD_MODULES = {"worker.py": "import anthropic_client\n"}


def _good(**over) -> Path:
    args = dict(files=GOOD_FILES, prompts=GOOD_PROMPTS, client=CLIENT, modules=GOOD_MODULES)
    args.update(over)
    return _fixture(**args)


def test_real_tree_is_complete():
    r = _run(REPO)
    assert r.returncode == 0, f"guard failed on the real tree:\n{r.stdout}{r.stderr}"
    assert "rule 4: scanned imports of" in r.stdout and "OK:" in r.stdout, r.stdout


def test_four_prompts_are_in_files():
    text = DEPLOY.read_text()
    block = re.search(r"^FILES=\(\n(.*?)^\)", text, re.S | re.M).group(1)
    for p in FOUR + ["worker.py", "google_auth.py", "jobs/__init__.py"]:
        assert f'"{p}"' in block, f"{p} missing from FILES"


def _code_line(text: str, needle: str) -> int:
    """Line number of the first NON-comment line containing needle (a comment can't satisfy a test)."""
    for n, line in enumerate(text.splitlines(), 1):
        if needle in line and not line.lstrip().startswith("#"):
            return n
    raise AssertionError(f"no code line in deploy.sh contains {needle!r}")


def test_deploy_runs_the_guard_before_anything_ships():
    text = DEPLOY.read_text()
    guard, gate = _code_line(text, "scripts/check_manifest.py"), _code_line(text, "scripts/box_gate.sh")
    first_write = min(_code_line(text, "mkdir -p $dirs"), _code_line(text, 'scp -q "${SSH_OPTS[@]}" "$f"'))
    assert guard < first_write and gate < first_write, "guard and gate must run before any mkdir or scp"
    probe = _code_line(text, '-o BatchMode=yes "$SERVER" true')
    assert _code_line(text, "ls-files --error-unmatch") < probe < gate, "the probe comes after the git checks"
    for line in (text.splitlines()[guard - 1], text.splitlines()[gate - 1]):
        assert "|| fail" in line, f"a failure must stop deploy.sh: {line.strip()}"


def test_every_ssh_and_scp_in_deploy_times_out():
    """A box that stops answering fails the run instead of hanging it (Toby, PR #39; the v1.0.9 ssh hang)."""
    calls = [ln.strip() for ln in DEPLOY.read_text().splitlines()
             if re.match(r"\s*(remote_out=\$\()?(ssh|scp) ", ln)]
    assert len(calls) >= 6, calls
    bare = [c for c in calls if '"${SSH_OPTS[@]}"' not in c]
    assert not bare, f"ssh/scp without SSH_OPTS: {bare}"
    gate = [ln for ln in (REPO / "scripts" / "box_gate.sh").read_text().splitlines() if "$(ssh " in ln]
    assert gate and all("ConnectTimeout" in ln and "ServerAliveInterval" in ln for ln in gate), gate


def test_loaded_prompt_missing_from_files_fails():
    r = _run(_good(files=[f for f in GOOD_FILES if f != "prompts/brand_team_a.md"]))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "prompts/brand_team_a.md is loaded by anthropic_client.py but not in FILES" in r.stderr, r.stderr


def test_unlisted_unretired_prompt_fails():
    r = _run(_good(prompts={**GOOD_PROMPTS, "stray.md": "s"}))
    assert r.returncode == 1 and "prompts/stray.md is neither in FILES nor in RETIRED_PROMPTS" in r.stderr, r.stderr


def test_loaded_prompt_that_does_not_exist_fails():
    r = _run(_good(prompts={"title.md": "t"}, files=[f for f in GOOD_FILES if f != "prompts/brand_team_a.md"]))
    assert r.returncode == 1 and "brand_team_a.md is loaded by anthropic_client.py but does not exist" in r.stderr, r.stderr


def test_retired_prompt_still_listed_fails():
    r = _run(_good(files=GOOD_FILES + ["prompts/blog_page.md"], prompts={**GOOD_PROMPTS, "blog_page.md": "x"}))
    assert r.returncode == 1 and "prompts/blog_page.md is retired but still in FILES" in r.stderr, r.stderr


def test_systemd_entry_point_missing_fails():
    r = _run(_good(files=[f for f in GOOD_FILES if f != "worker.py"]))
    assert r.returncode == 1 and "worker.py is a systemd entry point but not in FILES" in r.stderr, r.stderr


def test_first_party_import_missing_fails():
    mods = {"worker.py": "import anthropic_client\nfrom jobs.run import go\n",
            "jobs/__init__.py": "", "jobs/run.py": "import helper\n", "jobs/helper.py": ""}
    r = _run(_good(modules=mods, files=GOOD_FILES + ["jobs/run.py"]))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "jobs/__init__.py is imported by worker.py but not in FILES" in r.stderr, r.stderr
    assert "jobs/helper.py is imported by jobs/run.py but not in FILES" in r.stderr, "sibling import not resolved"


def test_third_party_and_stdlib_imports_are_ignored():
    mods = {"worker.py": "import os, json\nimport anthropic\nfrom PIL import Image\nimport anthropic_client\n"}
    r = _run(_good(modules=mods))
    assert r.returncode == 0, r.stdout + r.stderr


def test_empty_manifest_is_unverified_not_ok():
    r = _run(_good(files=[]))
    assert r.returncode == 2 and "UNVERIFIED" in r.stderr and "OK:" not in r.stdout, r.stdout + r.stderr


def test_newly_managed_files_are_listed_and_gated():
    text = DEPLOY.read_text()
    newly = re.findall(r'"([^"]+)"', re.search(r"^NEWLY_MANAGED=\(\n(.*?)^\)", text, re.S | re.M).group(1))
    block = re.search(r"^FILES=\(\n(.*?)^\)", text, re.S | re.M).group(1)
    assert set(FOUR) <= set(newly), "the four prompts must be compared with the box before the first overwrite"
    assert all(f'"{f}"' in block for f in newly), "every newly managed file must also be in FILES"


def test_unreadable_forms_are_unverified():
    cases = {
        "relative import": dict(modules={"worker.py": "import anthropic_client\nfrom . import x\n"}),
        "dynamic import": dict(modules={"worker.py": "import importlib, anthropic_client\nimportlib.import_module(name)\n"}),
        "f-string prompt": dict(client=CLIENT + 'def g(x):\n    return _load_prompt(f"{x}.md")\n'),
        "computed members": dict(client=CLIENT.replace('["brand_team_a"]', 'list(("brand_team_a",))')),
        "other name in the member loop": dict(client=CLIENT + (
            'def g():\n    for m in _BRAND_TEAM_MEMBERS:\n        _load_prompt(f"brand_team_{m}_v2.md")\n')),
    }
    for label, over in cases.items():
        r = _run(_good(**over))
        assert r.returncode == 2 and "UNVERIFIED" in r.stderr, f"{label}: {r.returncode} {r.stderr}"


def test_brand_team_loop_and_worker_dynamic_import_are_read():
    client = CLIENT + 'def g():\n    for i, m in enumerate(_BRAND_TEAM_MEMBERS):\n        _load_prompt(f"{m}.md")\n'
    worker = ("import importlib, anthropic_client\n"
              "for path, cls in [(\"jobs.run\", \"X\")]:\n    importlib.import_module(path)\n")
    mods = {"worker.py": worker, "jobs/__init__.py": "", "jobs/run.py": ""}
    r = _run(_good(client=client, modules=mods))
    assert r.returncode == 1 and "jobs/run.py is imported by worker.py but not in FILES" in r.stderr, r.stderr
    r = _run(_good(client=client, modules=mods, files=GOOD_FILES + ["jobs/__init__.py", "jobs/run.py"]))
    assert r.returncode == 0, r.stdout + r.stderr


def test_unparsed_entry_point_is_a_gap():
    r = _run(_good(exec_start="/venv/bin/python worker.py\nExecStartPost=/venv/bin/python --weird-launcher"))
    assert r.returncode == 1 and "an entry point this checker can't parse" in r.stderr, r.stderr
    r = _run(_good(exec_start="/venv/bin/uvicorn worker:app \\\n  --port 8000"))
    assert r.returncode == 0, r.stdout + r.stderr


# ── the box gate, run for real against a fake ssh ─────────────────────────────

FAKE_SSH = """#!/usr/bin/env bash
# fake ssh: ignore the options and host; run the remote command (the last argument) locally, or fail as told.
[[ -n "${FAKE_SSH_FAIL:-}" ]] && { echo "ssh: connect to host: Connection refused" >&2; exit 255; }
exec bash -c "${@: -1}"
"""
FAKE_SHA = '#!/usr/bin/env bash\n[[ -n "${FAKE_SHA_FAIL:-}" ]] && exit 1\nshasum -a 256 "$@"\n'


def _gate_repo() -> tuple[Path, Path, dict]:
    """A git repo with prompts/p.md committed twice (v1, v2), a box dir, and a PATH with fake ssh/sha256sum."""
    root = Path(tempfile.mkdtemp(prefix="prompt01-gate-"))
    _TMP.append(root)
    repo, box, bin_ = root / "repo", root / "box", root / "bin"
    for d in (repo / "prompts", box / "prompts", bin_):
        d.mkdir(parents=True)
    (bin_ / "ssh").write_text(FAKE_SSH)
    (bin_ / "sha256sum").write_text(FAKE_SHA)
    for f in bin_.iterdir():
        f.chmod(0o755)
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
    git("init", "-q")
    git("config", "user.email", "t@example.test")
    git("config", "user.name", "t")
    for v in ("v1\n", "v2\n"):
        (repo / "prompts" / "p.md").write_text(v)
        git("add", "-A")
        git("commit", "-qm", v.strip())
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}"}
    return repo, box, env


def _gate(repo: Path, box: Path, env: dict, *files: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(GATE), "root@box", str(box), *(files or ("prompts/p.md",))],
                          cwd=repo, env=env, capture_output=True, text=True, timeout=60)


def test_gate_passes_absent_same_and_an_older_committed_version():
    repo, box, env = _gate_repo()
    r = _gate(repo, box, env)
    assert r.returncode == 0 and "ABSENT" in r.stdout, r.stdout + r.stderr
    (box / "prompts" / "p.md").write_text("v2\n")
    r = _gate(repo, box, env)
    assert r.returncode == 0 and "SAME" in r.stdout, r.stdout + r.stderr
    (box / "prompts" / "p.md").write_text("v1\n")        # the box holds an older shipped version
    (repo / "prompts" / "p.md").write_text("v3 local edit\n")  # Heather's next edit, not yet committed
    r = _gate(repo, box, env)
    assert r.returncode == 0 and "OURS" in r.stdout, r.stdout + r.stderr


def test_gate_stops_on_a_copy_edited_on_the_box():
    repo, box, env = _gate_repo()
    (box / "prompts" / "p.md").write_text("edited on the box\n")
    r = _gate(repo, box, env)
    assert r.returncode == 1 and "DIFFERS   prompts/p.md" in r.stderr and "OK:" not in r.stdout, r.stdout + r.stderr


def test_gate_stops_when_ssh_fails():
    repo, box, env = _gate_repo()
    (box / "prompts" / "p.md").write_text("edited on the box\n")
    r = _gate(repo, box, {**env, "FAKE_SSH_FAIL": "1"})
    assert r.returncode == 1 and "could not read the box copies" in r.stderr, r.stdout + r.stderr
    assert "ABSENT" not in r.stdout, "a failed ssh must never read as absent"


def test_gate_stops_when_the_box_cannot_checksum():
    repo, box, env = _gate_repo()
    (box / "prompts" / "p.md").write_text("v2\n")
    r = _gate(repo, box, {**env, "FAKE_SHA_FAIL": "1"})
    assert r.returncode == 1 and "could not checksum prompts/p.md" in r.stderr, r.stdout + r.stderr


def test_gate_never_matches_a_commit_without_the_file():
    repo, box, env = _gate_repo()
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
    git("rm", "-q", "prompts/p.md")
    git("commit", "-qm", "delete")
    (repo / "prompts").mkdir(exist_ok=True)
    (repo / "prompts" / "p.md").write_text("v4\n")
    git("add", "-A")
    git("commit", "-qm", "re-add")
    (box / "prompts" / "p.md").write_text("")          # empty on the box: hashes like a failed git show
    r = _gate(repo, box, env)
    assert r.returncode == 1 and "DIFFERS   prompts/p.md" in r.stderr, r.stdout + r.stderr


def test_deploy_refuses_anything_but_main_before_any_ssh():
    root = Path(tempfile.mkdtemp(prefix="prompt01-copy-"))
    _TMP.append(root)
    (root / "scripts").mkdir()
    shutil.copy(DEPLOY, root / "scripts" / "deploy.sh")
    bin_ = root / "bin"
    bin_.mkdir()
    for tool in ("ssh", "scp"):
        (bin_ / tool).write_text(f"#!/usr/bin/env bash\necho CALLED-{tool} >&2\nexit 99\n")
        (bin_ / tool).chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "HOME": str(root / "home"), "CDPATH": "/tmp"}
    (root / "home" / "RockstarPodcast").mkdir(parents=True)
    r = subprocess.run(["bash", str(root / "scripts" / "deploy.sh"), "--check"], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "deploy only from main" in r.stderr, r.stdout + r.stderr
    assert "CALLED-" not in r.stderr, "no ssh or scp may run before the refusal"
    # a real checkout on a feature branch is refused the same way
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)  # noqa: E731
    git("init", "-q", "-b", "feat/x")
    git("config", "user.email", "t@example.test")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-qm", "x")
    r = subprocess.run(["bash", str(root / "scripts" / "deploy.sh"), "--check"], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "on 'feat/x'" in r.stderr and "CALLED-" not in r.stderr, r.stderr


def test_box_sh_never_inherits_server_from_the_shell():
    root = Path(tempfile.mkdtemp(prefix="share01-box-"))
    _TMP.append(root)
    shutil.copy(REPO / "scripts" / "box.sh", root / "box.sh")
    shutil.copy(REPO / "scripts" / "box.conf.example", root / "box.conf.example")  # box.sh reads the placeholder
    env = {**os.environ, "SERVER": "root@wrong.example"}
    env.pop("BOX_SERVER_OVERRIDE", None)
    run = lambda: subprocess.run(["bash", "-c", f'source "{root}/box.sh"; echo "SERVER=$SERVER"'],  # noqa: E731
                                 env=env, capture_output=True, text=True, timeout=30)
    r = run()
    assert r.returncode == 2 and "box.conf is missing" in r.stderr and "wrong.example" not in r.stdout, r
    (root / "box.conf").write_text('SERVER="root@box.example.test"\n')  # copied, never filled in
    r = run()
    assert r.returncode == 2 and "still the example placeholder" in r.stderr and "SERVER=" not in r.stdout, r
    (root / "box.conf").write_text('SERVER="root@from-conf.example.test"\n')
    r = run()
    assert "SERVER=root@from-conf.example.test" in r.stdout, f"box.conf must win over an ambient SERVER: {r.stdout}"
    r = subprocess.run(["bash", "-c", f'source "{root}/box.sh"; echo "SERVER=$SERVER"'],
                       env={**env, "BOX_SERVER_OVERRIDE": "root@rehearsal.example.test"}, capture_output=True, text=True)
    assert "SERVER=root@rehearsal.example.test" in r.stdout, "the explicit override still works"


def test_deploy_refuses_a_dirty_or_unpushed_main_before_any_ssh():
    root = Path(tempfile.mkdtemp(prefix="share01-main-"))
    _TMP.append(root)
    (root / "scripts").mkdir()
    shutil.copy(DEPLOY, root / "scripts" / "deploy.sh")
    bin_ = root / "bin"
    bin_.mkdir()
    for tool in ("ssh", "scp"):
        (bin_ / tool).write_text(f"#!/usr/bin/env bash\necho CALLED-{tool} >&2\nexit 99\n")
        (bin_ / tool).chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "SERVER": "root@wrong.example"}
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)  # noqa: E731
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.test")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-qm", "x")
    deploy = lambda: subprocess.run(["bash", str(root / "scripts" / "deploy.sh"), "--check"], env=env,  # noqa: E731
                                    capture_output=True, text=True, timeout=60)
    (root / "scripts" / "deploy.sh").write_text((root / "scripts" / "deploy.sh").read_text() + "\n# local edit\n")
    r = deploy()
    assert r.returncode == 1 and "uncommitted changes" in r.stderr and "CALLED-" not in r.stderr, r.stderr
    git("checkout", "--", "scripts/deploy.sh")
    r = deploy()  # clean, but there is no origin to match
    assert r.returncode == 1 and "could not fetch origin/main" in r.stderr and "CALLED-" not in r.stderr, r.stderr


# A fake ssh that reports the host it was asked for, skipping options (the reachability probe passes -o ...).
_FAKE_SSH = ('#!/usr/bin/env bash\n'
             'a=("$@"); i=0; while [[ "${a[$i]}" == -* ]]; do [[ "${a[$i]}" == -o ]] && i=$((i+1)); i=$((i+1)); done\n'
             'echo "SSH-TO ${a[$i]}" >&2\n')


def test_deploy_target_is_box_conf_whatever_the_shell_exports():
    """A real clone of a bare origin, on main, clean and pushed: the only thing left to choose the host is
    box.conf. SERVER and BOX_SERVER_OVERRIDE exported in the shell are both ignored (re-review of PR #38)."""
    base = Path(tempfile.mkdtemp(prefix="share01-target-"))
    _TMP.append(base)
    origin, root, bin_ = base / "origin.git", base / "clone", base / "bin"
    bin_.mkdir()
    (bin_ / "ssh").write_text(_FAKE_SSH + 'exit 99\n')
    (bin_ / "scp").write_text('#!/usr/bin/env bash\necho "SCP" >&2\nexit 99\n')
    for tool in ("ssh", "scp"):
        (bin_ / tool).chmod(0o755)
    run = lambda *a, cwd=None: subprocess.run(list(a), cwd=cwd, check=True, capture_output=True)  # noqa: E731
    run("git", "init", "-q", "--bare", "-b", "main", str(origin))
    run("git", "clone", "-q", str(origin), str(root))
    # the repo's tracked tree (as it stands), so every FILES entry exists and is tracked and deploy.sh REACHES ssh
    tracked = subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True, check=True).stdout
    for rel in tracked.splitlines():
        src = REPO / rel
        if src.is_file():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, root / rel)
    run("git", "-C", str(root), "-c", "user.email=t@example.test", "-c", "user.name=t", "add", "-A")
    run("git", "-C", str(root), "-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-qm", "x")
    run("git", "-C", str(root), "push", "-q", "origin", "main")
    # a box.conf WITHOUT a SERVER line: the reset in box.sh is all that stands between an ambient SERVER and the box
    (root / "scripts" / "box.conf").write_text('# SERVER deliberately missing\n')
    env0 = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "SERVER": "root@ambient.example"}
    r = subprocess.run(["bash", str(root / "scripts" / "deploy.sh"), "--check"], env=env0, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 2 and "SERVER is empty" in r.stderr and "SSH-TO" not in r.stderr, r.stderr[-300:]
    (root / "scripts" / "box.conf").write_text('SERVER="root@box-from-conf.example.test"\n')
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "SERVER": "root@ambient.example",
           "BOX_SERVER_OVERRIDE": "root@override.example"}
    r = subprocess.run(["bash", str(root / "scripts" / "deploy.sh"), "--check"], env=env, capture_output=True,
                       text=True, timeout=120)
    targets = set(re.findall(r"SSH-TO (\S+)", r.stderr))
    assert targets, f"deploy.sh never reached ssh, so this proves nothing: {r.stderr[-400:]}"
    assert targets == {"root@box-from-conf.example.test"}, f"the deploy went to {targets}"
    # an unreachable box reads as unreachable, never as every file "absent on server" (2026-10-11)
    assert r.returncode == 1 and "cannot reach root@box-from-conf.example.test" in r.stderr, r.stderr[-300:]
    assert "MISSING" not in r.stdout and "checksum mismatch" not in r.stderr, r.stdout[-300:]
    # ...and a connection lost mid-check stops as incomplete, not as a list of MISSING files
    (bin_ / "ssh").write_text(_FAKE_SSH + '[[ "${@: -1}" == true ]] && exit 0\nexit 255\n')
    r = subprocess.run(["bash", str(root / "scripts" / "deploy.sh"), "--check"], env=env, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 1 and "the check is incomplete, not a mismatch" in r.stderr, r.stderr[-300:]
    assert "MISSING" not in r.stdout, r.stdout[-300:]
    # a box that answers: a present file is OK, an absent one MISSING, an unsummable one UNREADABLE (Toby, PR #39)
    (bin_ / "ssh").write_text(_FAKE_SSH + f'''[[ "${{@: -1}}" == true ]] && exit 0
f=$(printf %s "${{@: -1}}" | sed -E "s#.*-e '/opt/rockstar-pipeline/([^']*)'.*#\\1#")
case "$f" in prompts/faq.md) ;; worker.py) echo UNREADABLE ;; *) shasum -a 256 "{root}/$f" ;; esac
''')
    r = subprocess.run(["bash", str(root / "scripts" / "deploy.sh"), "--check"], env=env, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 1 and "checksum mismatch" in r.stderr, r.stderr[-300:]
    assert "MISSING prompts/faq.md" in r.stdout and "UNREADABLE worker.py" in r.stdout, r.stdout[-600:]
    assert "OK   db.py" in r.stdout and r.stdout.count("MISSING") == 1, r.stdout[-600:]
    (bin_ / "ssh").write_text(_FAKE_SSH + 'exit 99\n')
    # a FILES entry that git doesn't track is refused before any ssh (its local bytes would otherwise ship)
    run("git", "-C", str(root), "rm", "-q", "--cached", "prompts/faq.md")
    run("git", "-C", str(root), "-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-qm", "untrack")
    run("git", "-C", str(root), "push", "-q", "origin", "main")
    r = subprocess.run(["bash", str(root / "scripts" / "deploy.sh"), "--check"], env=env, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 1 and "prompts/faq.md is in FILES but not tracked by git" in r.stderr, r.stderr[-300:]
    assert "SSH-TO" not in r.stderr, "refused before any ssh"


def test_gate_stops_on_a_short_answer_and_a_missing_local_file():
    repo, box, env = _gate_repo()
    short = {**env}
    (Path(env["PATH"].split(":")[0]) / "ssh").write_text("#!/usr/bin/env bash\necho 'ABSENT prompts/p.md'\n")
    (repo / "prompts" / "q.md").write_text("q\n")
    r = _gate(repo, box, short, "prompts/p.md", "prompts/q.md")
    assert r.returncode == 1 and "expected 2 answers from the box, got 1" in r.stderr, r.stdout + r.stderr
    r = _gate(repo, box, env, "prompts/nope.md")
    assert r.returncode == 1 and "local file missing: prompts/nope.md" in r.stderr, r.stdout + r.stderr


def main() -> int:
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
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
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)
    print(f"\n{len(tests) - failed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
