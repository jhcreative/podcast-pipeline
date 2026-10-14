"""
_swap.py — put a fake in place of a pipeline module, for a suite run as a script.

Since the pipeline became the `rockstar` package (2026-10-14), a module is found as an ATTRIBUTE of its package once
its package has loaded it: `from rockstar.clients import drive_client` reads `rockstar.clients.drive_client` before
it looks in sys.modules. A fake written only into sys.modules loses to a real module already imported. These helpers
write both, and `pop` removes both, so the next import loads fresh.
"""

import sys
from pathlib import Path

# A bare pipeline module name ("drive_client") means its place in the package ("rockstar.clients.drive_client"), so a
# suite may name a fake either way. Built from the package's own files: nothing to keep in sync.
_PKG = Path(__file__).resolve().parent.parent / "rockstar"
_DOTTED = {
    p.stem: ".".join(p.relative_to(_PKG.parent).with_suffix("").parts)
    for p in _PKG.rglob("*.py")
    if p.name != "__init__.py"
}


def _name(name):
    return _DOTTED.get(name, name)


def _parent(name):
    pkg, _, leaf = name.rpartition(".")
    return (sys.modules.get(pkg), leaf) if pkg else (None, leaf)


def put(name, module):
    name = _name(name)
    sys.modules[name] = module
    pkg, leaf = _parent(name)
    if pkg is not None:
        setattr(pkg, leaf, module)
    return module


def update(mapping=(), **more):
    for name, module in {**dict(mapping), **more}.items():
        put(name, module)


def setdefault(name, module):
    name = _name(name)
    return sys.modules[name] if name in sys.modules else put(name, module)


def pop(name, *default):
    name = _name(name)
    module = sys.modules.pop(name, *default)
    pkg, leaf = _parent(name)
    if pkg is not None and leaf in vars(pkg):
        delattr(pkg, leaf)
    return module
