"""ccctl's own persistent metadata — names only.

ccctl is a dispatch layer, not a data store. The only thing it owns
that Claude Code doesn't track is human-meaningful session names.

Store location: ~/.claude/ccctl/names.json
"""

from __future__ import annotations

import json
from pathlib import Path


def _store_dir(claude_dir: Path) -> Path:
    d = claude_dir / "ccctl"
    d.mkdir(exist_ok=True)
    return d


def load_names(claude_dir: Path) -> dict[str, str]:
    """Load {session_id: name} mapping."""
    f = _store_dir(claude_dir) / "names.json"
    if f.exists():
        return json.loads(f.read_text())
    return {}


def save_names(claude_dir: Path, names: dict[str, str]):
    f = _store_dir(claude_dir) / "names.json"
    f.write_text(json.dumps(names, indent=2, ensure_ascii=False))


def set_name(claude_dir: Path, session_id: str, name: str):
    names = load_names(claude_dir)
    names[session_id] = name
    save_names(claude_dir, names)


def get_name(claude_dir: Path, session_id: str) -> str | None:
    return load_names(claude_dir).get(session_id)
