"""
test_prompt01_manifest.py — the deploy ships the code whole, from git, and never overwrites a box copy blind.

Why: PROMPT-01 (2026-10-10) found four prompts and four modules the box ran that deploy.sh FILES didn't list;
scripts/check_manifest.py then checked the hand-kept list against the code. Since the 2026-10-14 remodel the list
isn't hand-kept: deploy.sh adds every tracked module of the rockstar package, every reel script and every prompt
from git, so the checker is retired and these tests prove the list instead (via `deploy.sh --list`, the real script).

  every package module and every prompt ships           → test_every_package_module_and_prompt_ships
  every prompt the code loads exists and ships          → test_every_loaded_prompt_ships
  every systemd entry point is a module that ships      → test_every_entry_point_ships
  the gate covers every shipped file; check.sh and the gate run before anything is written
                                                        → test_gate_covers_every_shipped_file,
                                                          test_deploy_runs_check_and_gate_before_anything_ships
  Scope 3 (never overwrite a box copy blind): scripts/box_gate.sh, run for real against a fake `ssh` on PATH
       → test_gate_* (absent, same, an older committed version, edited on the box, ssh down, a box checksum
         error, a short answer, a missing local file)
  the deploy guards: main only, clean and pushed, box.conf only → test_deploy_*, test_box_sh_*

Only `ssh` is faked; nothing touches the box.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
DEPLOY = REPO / "scripts" / "deploy.sh"
GATE = REPO / "scripts" / "box_gate.sh"
_TMP: list[Path] = []


def _shipped() -> list[str]:
    r = subprocess.run(["bash", str(DEPLOY), "--list"], capture_output=True, text=True, timeout=60, cwd=REPO)
    assert r.returncode == 0, r.stderr
    return r.stdout.split()


def test_every_package_module_and_prompt_ships():
    shipped = set(_shipped())
    tracked = subprocess.run(
        ["git", "ls-files", "rockstar", "prompts/*.md"], capture_output=True, text=True, cwd=REPO
    ).stdout.split()
    mac_only = {f"rockstar/tools/{n}.py" for n in ("sandbox_run", "share_export", "publish_blog_post", "run_dr_joe")}
    missing = [
        f for f in tracked if (f.endswith(".py") or f.startswith("prompts/")) and f not in shipped and f not in mac_only
    ]
    assert not missing, f"tracked but not shipped: {missing}"
    assert not [f for f in shipped if "/test" in f or f.startswith("tests/")], "tests never ship"


def test_mac_only_tools_never_ship():
    """Orly, the remodel's cold review: the sandbox harness deletes the folders it's given and spends the API key; it,
    the export, the hand blog write and the Dr. Joe re-run are laptop work and never reach the box."""
    shipped = set(_shipped())
    for name in ("sandbox_run", "share_export", "publish_blog_post", "run_dr_joe"):
        assert f"rockstar/tools/{name}.py" not in shipped, name
    assert {"rockstar/__init__.py", "rockstar/service/worker.py", "rockstar/service/webhook_auth.py"} <= shipped


def test_an_unusual_path_is_refused_before_any_ssh():
    """A tracked name reaches a root shell on the box: anything but plain characters stops the deploy (Orly, S2)."""
    root = Path(tempfile.mkdtemp(prefix="prompt01-path-"))
    _TMP.append(root)
    for d in ("scripts", "rockstar/service", "prompts"):
        (root / d).mkdir(parents=True)
    shutil.copy(DEPLOY, root / "scripts" / "deploy.sh")
    for rel in (
        "rockstar/__init__.py",
        "rockstar/service/worker.py",
        "rockstar/service/webhook_auth.py",
        "rockstar/x$(touch pwned).py",
    ):
        (root / rel).write_text("")
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    r = subprocess.run(
        ["bash", str(root / "scripts" / "deploy.sh"), "--list"], cwd=root, capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 1 and "refusing to ship an unusual path" in r.stderr, r.stdout + r.stderr
    assert not (root / "pwned").exists()


def test_every_loaded_prompt_ships():
    shipped = set(_shipped())
    src = (REPO / "rockstar" / "clients" / "anthropic_client.py").read_text()
    names = set(re.findall(r'_load_prompt\("([a-z_0-9]+\.md)"\)', src))
    team = re.search(r"_BRAND_TEAM_MEMBERS = \[(.*?)\]", src, re.S).group(1)
    names |= {f"{m}.md" for m in re.findall(r'"([a-z_]+)"', team)}
    assert len(names) >= 8, names
    missing = sorted(n for n in names if f"prompts/{n}" not in shipped or not (REPO / "prompts" / n).is_file())
    assert not missing, f"loaded but not shipped (or absent): {missing}"


def test_every_entry_point_ships():
    shipped = set(_shipped())
    starts = []
    for unit in (REPO / "systemd").glob("*.service"):
        text = unit.read_text()
        starts += re.findall(r"python -m ([a-z_.]+)", text) + re.findall(r"uvicorn ([a-z_.]+):app", text)
    assert len(starts) == 4, starts
    for mod in starts:
        assert mod.replace(".", "/") + ".py" in shipped, f"{mod} is started by systemd but doesn't ship"


def test_gate_covers_every_shipped_file():
    text = DEPLOY.read_text()
    assert 'bash scripts/box_gate.sh "$SERVER" "$DEST" "${FILES[@]}"' in text
    assert "NEWLY_MANAGED" not in text


def _code_line(text: str, needle: str) -> int:
    """Line number of the first NON-comment line containing needle (a comment can't satisfy a test)."""
    for n, line in enumerate(text.splitlines(), 1):
        if needle in line and not line.lstrip().startswith("#"):
            return n
    raise AssertionError(f"no code line in deploy.sh contains {needle!r}")


def test_deploy_runs_check_and_gate_before_anything_ships():
    text = DEPLOY.read_text()
    check, gate = _code_line(text, "scripts/check.sh"), _code_line(text, "scripts/box_gate.sh")
    first_write = min(_code_line(text, "mkdir -p $dirs"), _code_line(text, 'scp -q "${SSH_OPTS[@]}" "$f"'))
    assert check < first_write and gate < first_write, "check.sh and the gate must run before any mkdir or scp"
    probe = _code_line(text, '-o BatchMode=yes "$SERVER" true')
    assert _code_line(text, "ls-files --error-unmatch") < probe < gate, "the probe comes after the git checks"
    for line in (text.splitlines()[check - 1], text.splitlines()[gate - 1]):
        assert "|| fail" in line, f"a failure must stop deploy.sh: {line.strip()}"


def test_every_ssh_and_scp_in_deploy_times_out():
    """A box that stops answering fails the run instead of hanging it (Toby, PR #39; the v1.0.9 ssh hang)."""
    calls = [ln.strip() for ln in DEPLOY.read_text().splitlines() if re.match(r"\s*(remote_out=\$\()?(ssh|scp) ", ln)]
    assert len(calls) >= 6, calls
    bare = [c for c in calls if '"${SSH_OPTS[@]}"' not in c]
    assert not bare, f"ssh/scp without SSH_OPTS: {bare}"
    gate = [ln for ln in (REPO / "scripts" / "box_gate.sh").read_text().splitlines() if "$(ssh " in ln]
    assert gate and all("ConnectTimeout" in ln and "ServerAliveInterval" in ln for ln in gate), gate


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
    return subprocess.run(
        ["bash", str(GATE), "root@box", str(box), *(files or ("prompts/p.md",))],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_gate_passes_absent_same_and_an_older_committed_version():
    repo, box, env = _gate_repo()
    r = _gate(repo, box, env)
    assert r.returncode == 0 and "ABSENT" in r.stdout, r.stdout + r.stderr
    (box / "prompts" / "p.md").write_text("v2\n")
    r = _gate(repo, box, env)
    assert r.returncode == 0 and "SAME" in r.stdout, r.stdout + r.stderr
    (box / "prompts" / "p.md").write_text("v1\n")  # the box holds an older shipped version
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
    (box / "prompts" / "p.md").write_text("")  # empty on the box: hashes like a failed git show
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
    r = subprocess.run(
        ["bash", str(root / "scripts" / "deploy.sh"), "--check"], env=env, capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 1 and "deploy only from main" in r.stderr, r.stdout + r.stderr
    assert "CALLED-" not in r.stderr, "no ssh or scp may run before the refusal"
    # a real checkout on a feature branch is refused the same way
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)  # noqa: E731
    git("init", "-q", "-b", "feat/x")
    git("config", "user.email", "t@example.test")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-qm", "x")
    r = subprocess.run(
        ["bash", str(root / "scripts" / "deploy.sh"), "--check"], env=env, capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 1 and "on 'feat/x'" in r.stderr and "CALLED-" not in r.stderr, r.stderr


def test_box_sh_never_inherits_server_from_the_shell():
    root = Path(tempfile.mkdtemp(prefix="share01-box-"))
    _TMP.append(root)
    shutil.copy(REPO / "scripts" / "box.sh", root / "box.sh")
    shutil.copy(REPO / "scripts" / "box.conf.example", root / "box.conf.example")  # box.sh reads the placeholder
    env = {**os.environ, "SERVER": "root@wrong.example"}
    env.pop("BOX_SERVER_OVERRIDE", None)

    def run():
        return subprocess.run(
            ["bash", "-c", f'source "{root}/box.sh"; echo "SERVER=$SERVER"'],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    r = run()
    assert r.returncode == 2 and "box.conf is missing" in r.stderr and "wrong.example" not in r.stdout, r
    placeholder = next(
        line for line in (REPO / "scripts" / "box.conf.example").read_text().splitlines() if line.startswith("SERVER=")
    )
    (root / "box.conf").write_text(
        placeholder + "\n"
    )  # copied, never filled in (read from the example: they can't drift)
    r = run()
    assert r.returncode == 2 and "still the example placeholder" in r.stderr and "SERVER=" not in r.stdout, r
    (root / "box.conf").write_text('SERVER="root@from-conf.example.test"\n')
    r = run()
    assert "SERVER=root@from-conf.example.test" in r.stdout, f"box.conf must win over an ambient SERVER: {r.stdout}"
    r = subprocess.run(
        ["bash", "-c", f'source "{root}/box.sh"; echo "SERVER=$SERVER"'],
        env={**env, "BOX_SERVER_OVERRIDE": "root@rehearsal.example.test"},
        capture_output=True,
        text=True,
    )
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

    def deploy():
        return subprocess.run(
            ["bash", str(root / "scripts" / "deploy.sh"), "--check"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    (root / "scripts" / "deploy.sh").write_text((root / "scripts" / "deploy.sh").read_text() + "\n# local edit\n")
    r = deploy()
    assert r.returncode == 1 and "uncommitted changes" in r.stderr and "CALLED-" not in r.stderr, r.stderr
    git("checkout", "--", "scripts/deploy.sh")
    r = deploy()  # clean, but there is no origin to match
    assert r.returncode == 1 and "could not fetch origin/main" in r.stderr and "CALLED-" not in r.stderr, r.stderr


# A fake ssh that reports the host it was asked for, skipping options (the reachability probe passes -o ...).
_FAKE_SSH = (
    "#!/usr/bin/env bash\n"
    'a=("$@"); i=0; while [[ "${a[$i]}" == -* ]]; do [[ "${a[$i]}" == -o ]] && i=$((i+1)); i=$((i+1)); done\n'
    'echo "SSH-TO ${a[$i]}" >&2\n'
)


def test_deploy_target_is_box_conf_whatever_the_shell_exports():
    """A real clone of a bare origin, on main, clean and pushed: the only thing left to choose the host is
    box.conf. SERVER and BOX_SERVER_OVERRIDE exported in the shell are both ignored (re-review of PR #38)."""
    base = Path(tempfile.mkdtemp(prefix="share01-target-"))
    _TMP.append(base)
    origin, root, bin_ = base / "origin.git", base / "clone", base / "bin"
    bin_.mkdir()
    (bin_ / "ssh").write_text(_FAKE_SSH + "exit 99\n")
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
    (root / "scripts" / "box.conf").write_text("# SERVER deliberately missing\n")
    env0 = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "SERVER": "root@ambient.example"}
    r = subprocess.run(
        ["bash", str(root / "scripts" / "deploy.sh"), "--check"], env=env0, capture_output=True, text=True, timeout=120
    )
    assert r.returncode == 2 and "SERVER is empty" in r.stderr and "SSH-TO" not in r.stderr, r.stderr[-300:]
    (root / "scripts" / "box.conf").write_text('SERVER="root@box-from-conf.example.test"\n')
    env = {
        **os.environ,
        "PATH": f"{bin_}:{os.environ['PATH']}",
        "SERVER": "root@ambient.example",
        "BOX_SERVER_OVERRIDE": "root@override.example",
    }
    r = subprocess.run(
        ["bash", str(root / "scripts" / "deploy.sh"), "--check"], env=env, capture_output=True, text=True, timeout=120
    )
    targets = set(re.findall(r"SSH-TO (\S+)", r.stderr))
    assert targets, f"deploy.sh never reached ssh, so this proves nothing: {r.stderr[-400:]}"
    assert targets == {"root@box-from-conf.example.test"}, f"the deploy went to {targets}"
    # an unreachable box reads as unreachable, never as every file "absent on server" (2026-10-11)
    assert r.returncode == 1 and "cannot reach root@box-from-conf.example.test" in r.stderr, r.stderr[-300:]
    assert "MISSING" not in r.stdout and "checksum mismatch" not in r.stderr, r.stdout[-300:]
    # ...and a connection lost mid-check stops as incomplete, not as a list of MISSING files
    (bin_ / "ssh").write_text(_FAKE_SSH + '[[ "${@: -1}" == true ]] && exit 0\nexit 255\n')
    r = subprocess.run(
        ["bash", str(root / "scripts" / "deploy.sh"), "--check"], env=env, capture_output=True, text=True, timeout=120
    )
    assert r.returncode == 1 and "the check is incomplete, not a mismatch" in r.stderr, r.stderr[-300:]
    assert "MISSING" not in r.stdout, r.stdout[-300:]
    # a box that answers: a present file is OK, an absent one MISSING, an unsummable one UNREADABLE (Toby, PR #39)
    (bin_ / "ssh").write_text(
        _FAKE_SSH
        + f'''[[ "${{@: -1}}" == true ]] && exit 0
f=$(printf %s "${{@: -1}}" | sed -E "s#.*-e '/opt/rockstar-pipeline/([^']*)'.*#\\1#")
case "$f" in prompts/faq.md) ;; rockstar/service/worker.py) echo UNREADABLE ;; *) shasum -a 256 "{root}/$f" ;; esac
'''
    )
    r = subprocess.run(
        ["bash", str(root / "scripts" / "deploy.sh"), "--check"], env=env, capture_output=True, text=True, timeout=120
    )
    assert r.returncode == 1 and "checksum mismatch" in r.stderr, r.stderr[-300:]
    assert "MISSING prompts/faq.md" in r.stdout and "UNREADABLE rockstar/service/worker.py" in r.stdout, r.stdout[-600:]
    assert "OK   rockstar/service/db.py" in r.stdout and r.stdout.count("MISSING") == 1, r.stdout[-600:]
    (bin_ / "ssh").write_text(_FAKE_SSH + "exit 99\n")
    # a hand-listed FILES entry that git doesn't track is refused before any ssh (its local bytes would otherwise
    # ship). The code and prompts can't hit this any more: they are listed FROM git.
    run("git", "-C", str(root), "rm", "-q", "--cached", "scripts/extract_quote_clips.sh")
    run("git", "-C", str(root), "-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-qm", "untrack")
    run("git", "-C", str(root), "push", "-q", "origin", "main")
    r = subprocess.run(
        ["bash", str(root / "scripts" / "deploy.sh"), "--check"], env=env, capture_output=True, text=True, timeout=120
    )
    assert r.returncode == 1 and "scripts/extract_quote_clips.sh is in FILES but not tracked by git" in r.stderr, (
        r.stderr[-300:]
    )
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
