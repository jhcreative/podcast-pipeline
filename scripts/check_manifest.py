#!/usr/bin/env python3
"""check_manifest.py — the deploy manifest must name everything the box runs (PROMPT-01 Part 1, DEPLOY-01).

deploy.sh pushes an explicit FILES list and proves byte parity for what is listed. It cannot see what is
missing from the list: four loaded prompts and five imported modules never shipped, and --check reported
green (PROMPT-01, 2026-10-10). This script checks the list against the code, and exits non-zero when the
code needs a file the list doesn't carry. deploy.sh runs it before it pushes or checks anything.

Rules, each printed with what it found:
  1. Every prompts/*.md is in FILES or in RETIRED_PROMPTS.
  2. Every prompt the code loads (_load_prompt("x.md") literals, and each _BRAND_TEAM_MEMBERS entry + .md)
     exists and is in FILES.
  3. Every systemd entry point (ExecStart: a .py script, or uvicorn's module:app) is in FILES.
  4. Every first-party module imported by a deployed .py (import / from-import, and importlib.import_module
     with a literal) is in FILES, with its package __init__.py where one exists. First-party means the
     name resolves to a file in this repo, from the repo root or from the importing file's own folder
     (the quote_cards scripts put their folder on sys.path).

A form the checker can't read is UNVERIFIED (exit 2), never skipped: a relative import, a dynamic import
whose names aren't literals it can see, a non-literal _load_prompt argument outside the brand-team loop, a
_BRAND_TEAM_MEMBERS that isn't a literal list, an ExecStart that names python or uvicorn but parses to nothing.

Not covered (stated, not implied): files reached through subprocess / Node spawn; asset path literals (both
stay under DEPLOY-01's remaining criteria); and the box's own systemd units, which deploy.sh doesn't manage —
rule 3 trusts that the repo's units match the box's.

Usage: python3 scripts/check_manifest.py [--root DIR]     exit 0 = complete, 1 = gaps, 2 = could not check
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

# Prompts no code loads. Listed even once deleted, so a retired prompt can never drift back into FILES.
RETIRED_PROMPTS = {
    "prompts/blog_page.md": "caller compose_blog_page removed 2026-09-24 (BLOG-02b); "
                            "file deleted 2026-10-10 (SHARE-01)",
}


def parse_files(deploy_sh: Path) -> list[str]:
    """The FILES=( ... ) array in deploy.sh: one quoted path per entry, comments ignored."""
    text = deploy_sh.read_text(encoding="utf-8")
    m = re.search(r"^FILES=\(\n(.*?)^\)", text, re.S | re.M)
    if not m:
        raise ValueError(f"no FILES=( ... ) array in {deploy_sh}")
    return [q for line in m.group(1).splitlines() for q in re.findall(r'^\s*"([^"]+)"', line)]


class Unverified(ValueError):
    """A form the checker can't read. Reported as UNVERIFIED (exit 2), never as a pass or a skip."""


def _for_parents(tree: ast.AST) -> dict[ast.AST, list[ast.For]]:
    """Every node -> the For loops that enclose it, innermost last."""
    out: dict[ast.AST, list[ast.For]] = {}

    def walk(node: ast.AST, loops: list[ast.For]) -> None:
        out[node] = loops
        inner = loops + [node] if isinstance(node, ast.For) else loops
        for child in ast.iter_child_nodes(node):
            walk(child, inner)

    walk(tree, [])
    return out


def _iterates_members(it: ast.AST) -> bool:
    """`_BRAND_TEAM_MEMBERS` or `enumerate(_BRAND_TEAM_MEMBERS)`."""
    if isinstance(it, ast.Call) and getattr(it.func, "id", None) == "enumerate" and it.args:
        it = it.args[0]
    return getattr(it, "id", None) == "_BRAND_TEAM_MEMBERS"


def _is_member_md(arg: ast.AST, target: ast.AST) -> bool:
    """True only for f"{<the loop's member name>}.md" — any other shape is not what the member list names."""
    names = [n.id for n in ast.walk(target) if isinstance(n, ast.Name)]
    return (isinstance(arg, ast.JoinedStr) and len(arg.values) == 2
            and isinstance(arg.values[0], ast.FormattedValue) and isinstance(arg.values[0].value, ast.Name)
            and arg.values[0].value.id == names[-1] and arg.values[0].conversion == -1
            and arg.values[0].format_spec is None
            and isinstance(arg.values[1], ast.Constant) and arg.values[1].value == ".md")


def loaded_prompts(client_py: Path) -> set[str]:
    """Prompt files anthropic_client.py loads: literal _load_prompt args + brand-team member names.
    The one non-literal form accepted is a call inside a loop over _BRAND_TEAM_MEMBERS (or enumerate of it)."""
    tree = ast.parse(client_py.read_text(encoding="utf-8"))
    loops = _for_parents(tree)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "_BRAND_TEAM_MEMBERS" for t in node.targets):
            if not isinstance(node.value, (ast.List, ast.Tuple)) or not all(
                    isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.value.elts):
                raise Unverified(f"_BRAND_TEAM_MEMBERS (line {node.lineno}) is not a literal list of names")
            names.update(f"{e.value}.md" for e in node.value.elts)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_load_prompt":
            arg = node.args[0] if node.args else None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.add(arg.value)
            elif not any(_iterates_members(lp.iter) and _is_member_md(arg, lp.target) for lp in loops[node]):
                raise Unverified(f"_load_prompt at line {node.lineno} takes a name this checker can't read")
    return {f"prompts/{n}" for n in names}


def systemd_entrypoints(systemd_dir: Path) -> tuple[set[str], list[str]]:
    """(repo-relative files the units start, ExecStart lines that name python/uvicorn but parse to nothing).
    Forms read: `python path/x.py`, `python -m pkg.mod`, uvicorn `pkg.mod:attr`; continuation lines joined."""
    found: set[str] = set()
    unparsed: list[str] = []
    for unit in sorted(u for u in systemd_dir.rglob("*") if u.is_file()):    # units and their .d/ drop-ins
        text = unit.read_text(encoding="utf-8").replace("\\\n", " ")
        for line in text.splitlines():
            if not line.startswith("ExecStart"):
                continue
            hits = set(re.findall(r"\s([\w./-]+\.py)\b", line))
            hits |= {m.replace(".", "/") + ".py" for m in re.findall(r"\s-m\s+([\w.]+)", line)}
            hits |= {m.replace(".", "/") + ".py" for m in re.findall(r"\s([\w.]+):\w+\b", line)}
            if hits:
                found |= hits
            elif re.search(r"python|uvicorn", line):
                unparsed.append(f"{unit.name}: {line.strip()}")
    return found, unparsed


def _imported_names(tree: ast.AST, rel: str) -> set[str]:
    """Module names a file imports. A dynamic import counts only when every name it can take is a literal
    in the loop that feeds it (worker.py's PermanentFailure loop); anything else is Unverified."""
    loops = _for_parents(tree)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise Unverified(f"{rel}:{node.lineno} relative import (this checker resolves absolute imports only)")
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Call) and (getattr(node.func, "attr", None) == "import_module"
                                             or getattr(node.func, "id", None) in ("import_module", "__import__")):
            arg = node.args[0] if node.args else None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.add(arg.value)
                continue
            feeding = [lp for lp in loops[node] if isinstance(arg, ast.Name)
                       and arg.id in {n.id for n in ast.walk(lp.target) if isinstance(n, ast.Name)}
                       and isinstance(lp.iter, (ast.List, ast.Tuple))]
            if not feeding:
                raise Unverified(f"{rel}:{node.lineno} dynamic import of a name this checker can't read")
            names.update(c.value for c in ast.walk(feeding[-1].iter)
                         if isinstance(c, ast.Constant) and isinstance(c.value, str))
    return names


def first_party_imports(root: Path, rel: str) -> set[str]:
    """Repo-relative files that deployed module `rel` imports, resolved from the root and from its folder."""
    path = root / rel
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    needed: set[str] = set()
    for name in _imported_names(tree, rel):
        parts = name.split(".")
        for base in {root, path.parent}:
            mod = base.joinpath(*parts)
            hit = mod.with_suffix(".py") if mod.with_suffix(".py").is_file() else mod / "__init__.py"
            if not hit.is_file():
                continue
            needed.add(hit.relative_to(root).as_posix())
            for i in range(1, len(parts)):                     # package __init__.py files on the way down
                init = base.joinpath(*parts[:i], "__init__.py")
                if init.is_file():
                    needed.add(init.relative_to(root).as_posix())
    return needed - {rel}


def check(root: Path) -> list[str]:
    """Print evidence for each rule; return the list of gaps (empty = complete)."""
    files = parse_files(root / "scripts" / "deploy.sh")
    listed = set(files)
    prompts = sorted(p.relative_to(root).as_posix() for p in (root / "prompts").glob("*.md"))
    loaded = loaded_prompts(root / "anthropic_client.py")
    entry, unparsed = systemd_entrypoints(root / "systemd")
    # Preconditions (PQ-5a): a check over nothing is not a pass.
    for label, items in (("FILES entries", files), ("prompts/*.md", prompts), ("loaded prompts", loaded),
                         ("systemd entry points", entry)):
        if not items:
            raise ValueError(f"found no {label}; refusing to report a pass")
    gaps: list[str] = []

    print(f"[manifest] FILES: {len(files)} entries in scripts/deploy.sh")
    print(f"[manifest] rule 1: {len(prompts)} prompts in prompts/, {len(RETIRED_PROMPTS)} retired")
    for p in prompts:
        if p not in listed and p not in RETIRED_PROMPTS:
            gaps.append(f"rule 1: {p} is neither in FILES nor in RETIRED_PROMPTS")
    for p in sorted(set(RETIRED_PROMPTS) & listed):
        gaps.append(f"rule 1: {p} is retired but still in FILES")

    print(f"[manifest] rule 2: code loads {len(loaded)} prompts: {', '.join(sorted(n[8:] for n in loaded))}")
    for p in sorted(loaded):
        if not (root / p).is_file():
            gaps.append(f"rule 2: {p} is loaded by anthropic_client.py but does not exist")
        elif p not in listed:
            gaps.append(f"rule 2: {p} is loaded by anthropic_client.py but not in FILES")

    print(f"[manifest] rule 3: systemd starts {', '.join(sorted(entry))}")
    for u in unparsed:
        gaps.append(f"rule 3: an entry point this checker can't parse: {u}")
    for e in sorted(entry):
        if e not in listed:
            gaps.append(f"rule 3: {e} is a systemd entry point but not in FILES")

    deployed_py = sorted(f for f in listed | entry if f.endswith(".py") and (root / f).is_file())
    print(f"[manifest] rule 4: scanned imports of {len(deployed_py)} deployed .py files")
    for f in deployed_py:
        for dep in sorted(first_party_imports(root, f)):
            if dep not in listed:
                gaps.append(f"rule 4: {dep} is imported by {f} but not in FILES")
    return sorted(set(gaps))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args()
    try:
        gaps = check(args.root)
    except Exception as exc:  # noqa: BLE001 — any crash is "could not check" (2), never "gaps" (1)
        print(f"[manifest] UNVERIFIED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    for g in gaps:
        print(f"[manifest] MISSING {g}", file=sys.stderr)
    if gaps:
        print(f"[manifest] FAIL: {len(gaps)} gap(s); the deploy would leave the box without them", file=sys.stderr)
        return 1
    print("[manifest] OK: every prompt, entry point and first-party import is in FILES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
