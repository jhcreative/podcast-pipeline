#!/usr/bin/env bash
# check.sh — the one local gate (DOD-1): lint, format, every Python and Node test, every module imports.
#
# deploy.sh refuses to ship without it. Run it before every commit you'd put up for review:
#   bash scripts/check.sh            # everything
#   bash scripts/check.sh --fast     # skip pytest (lint, format, Node, imports: seconds)
#
# The ruff it runs must be the pinned one (requirements-dev.txt): a different ruff formats differently, and the
# 2026-10-13 review found the Mac on 0.16.1 against a 0.17.0 pin with nothing noticing.

set -euo pipefail

ROOT="$(CDPATH= cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
FAST=0
[[ "${1:-}" == "--fast" ]] && FAST=1

fail() { echo "✗ check: $*" >&2; exit 1; }
step() { echo "── $*"; }

[[ -x "$PY" ]] || fail "no Python at $PY (python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt)"

step "ruff is the pinned version"
want="$(sed -n 's/^ruff==//p' requirements-dev.txt)"
have="$("$PY" -m ruff --version | awk '{print $2}')"
[[ "$have" == "$want" ]] || fail "ruff $have, pinned $want (.venv/bin/pip install -r requirements-dev.txt)"

step "ruff check"
"$PY" -m ruff check . || fail "ruff check"

step "ruff format --check"
"$PY" -m ruff format --check . || fail "ruff format (run: $PY -m ruff format <your files>)"

step "every shell script parses (bash -n)"
while IFS= read -r f; do bash -n "$f" || fail "$f does not parse"; done < <(git ls-files 'scripts/*.sh')

step "Node tests"
node_tests=()  # bash 3.2 on the Mac: no mapfile
while IFS= read -r f; do node_tests+=("$f"); done < <(git ls-files 'reels/test_*.js' 'reels/*.test.mjs')
(( ${#node_tests[@]} )) || fail "no Node tests found"
node --test "${node_tests[@]}" > /tmp/check-node.$$ 2>&1 || { tail -40 /tmp/check-node.$$; rm -f /tmp/check-node.$$; fail "Node tests"; }
grep -E '^ℹ (tests|pass|fail) ' /tmp/check-node.$$; rm -f /tmp/check-node.$$

step "every module imports"
"$PY" - <<'PY' || fail "a module does not import"
import subprocess, sys
from pathlib import Path
files = [Path(p) for p in subprocess.check_output(["git", "ls-files", "*.py"], text=True).split()]
pkg = [p for p in files if p.parts[0] == "rockstar"]
loose = [p for p in files if p.parts[0] in {"scripts", "evals"} and p.name != "__init__.py"]
bad = []
env = {"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin", "HOME": str(Path.home())}
for p in pkg + loose:
    # each in its own interpreter, from the checkout: the package by its dotted name, as production imports it;
    # a loose script by its path, as it runs
    if p in pkg:
        code = f"import importlib; importlib.import_module({'.'.join(p.with_suffix('').parts).removesuffix('.__init__')!r})"
    else:
        code = (f"import importlib.util as u, sys; s = u.spec_from_file_location({p.stem!r}, {str(p)!r}); "
                f"m = u.module_from_spec(s); sys.modules[{p.stem!r}] = m; s.loader.exec_module(m)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=Path.cwd())
    if r.returncode != 0:
        bad.append(f"{p}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else r.returncode}")
print(f"{len(pkg) + len(loose) - len(bad)}/{len(pkg) + len(loose)} import")
for b in bad:
    print("  ✗", b)
sys.exit(1 if bad else 0)
PY

if (( FAST == 0 )); then
  step "pytest (every offline suite, each in its own process)"
  "$PY" -m pytest -q -p no:cacheprovider || fail "pytest"
fi

echo "✓ check: green"
