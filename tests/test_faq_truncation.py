"""
test_faq_truncation.py — a reply cut off at max_tokens never reaches the page (E13, 2026-10-12).

Two of E13's four FAQ runs stopped at max_tokens=1024 mid-sentence ("…nourish the endocannab") and went into the blog
draft as written. compose_faq and compose_apple_summary now refuse a max_tokens stop, and the FAQ has 2048 tokens.
The Anthropic client is a fake; run_ledger and anthropic_client are the real code.
"""

import os
import sys
import tempfile
import types
from pathlib import Path

import _swap  # noqa: E402 — tests/: fakes go on the package too

REPO = Path(__file__).resolve().parent.parent  # the repo root: tests/ is one level down
sys.path.insert(0, str(REPO))
os.environ["RUN_LOG_PATH"] = str(Path(tempfile.mkdtemp(prefix="faqcut-")) / "run_log.jsonl")

if "anthropic" not in sys.modules:
    _a = types.ModuleType("anthropic")
    _a.Anthropic = type("Anthropic", (), {"__init__": lambda self, *a, **k: None})
    _swap.put("anthropic", _a)

from rockstar.clients import anthropic_client  # noqa: E402

CUT = "Q: What is it?\nA: A three-part system that helps nourish the endocannab"
WHOLE = "Q: What is it?\nA: A three-part system.\n"


class _Usage:
    input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens = 10, 10, 0, 0


def _fake(text, stop):
    calls = []

    def create(**kw):
        calls.append(kw)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(text=text)], stop_reason=stop, usage=_Usage(), model="m"
        )

    anthropic_client._get_client = lambda: types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
    anthropic_client._load_prompt = lambda name: f"system prompt for {name}"
    return calls


def _refused(fn):
    try:
        fn()
    except RuntimeError as exc:
        return str(exc)
    return None


def test_cut_faq_is_refused():
    _fake(CUT, "max_tokens")
    err = _refused(lambda: anthropic_client.compose_faq("transcript", "summary"))
    assert err and "max_tokens" in err and "compose_faq" in err, err


def test_cut_apple_summary_is_refused():
    _fake("# Title\n\nA summary that stops mid", "max_tokens")
    err = _refused(lambda: anthropic_client.compose_apple_summary("transcript"))
    assert err and "compose_apple_summary" in err, err


def test_whole_faq_passes_with_room():
    calls = _fake(WHOLE, "end_turn")
    assert anthropic_client.compose_faq("transcript", "summary") == WHOLE.strip()
    assert calls[0]["max_tokens"] >= 2048, calls[0]["max_tokens"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("  ok ", name)
