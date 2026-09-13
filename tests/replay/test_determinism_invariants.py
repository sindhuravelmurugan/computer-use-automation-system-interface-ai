"""docs/replay-spec.md §5: enforced by test, not convention."""

from __future__ import annotations

import ast
from pathlib import Path

REPLAY_DIR = Path(__file__).resolve().parents[2] / "src" / "replay"
FORBIDDEN_MODULES = {"playwright", "anthropic", "google.genai"}


def _replay_py_files() -> list[Path]:
    return sorted(REPLAY_DIR.rglob("*.py"))


def test_no_llm_or_browser_imports_under_src_replay():
    offenders: list[str] = []
    for path in _replay_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.module else []
            else:
                continue
            for name in names:
                if name is None:
                    continue
                if any(name == forbidden or name.startswith(forbidden + ".") for forbidden in FORBIDDEN_MODULES):
                    offenders.append(f"{path.relative_to(REPLAY_DIR.parent.parent)}: {name}")
    assert not offenders, f"forbidden imports found: {offenders}"


def test_no_sleep_call_under_src_replay():
    offenders: list[str] = []
    for path in _replay_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name == "sleep":
                    offenders.append(f"{path.relative_to(REPLAY_DIR.parent.parent)}:{node.lineno}")
    assert not offenders, f"sleep() call(s) found under src/replay: {offenders}"
