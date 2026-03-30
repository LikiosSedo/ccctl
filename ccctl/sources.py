"""Data source readers for Claude Code local state."""

from __future__ import annotations

import json
import os
from pathlib import Path


def read_sessions(claude_dir: Path) -> list[dict]:
    """Read session metadata from ~/.claude/sessions/*.json."""
    sessions_dir = claude_dir / "sessions"
    if not sessions_dir.exists():
        return []

    sessions = []
    for f in sessions_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            data["_mtime"] = f.stat().st_mtime
            sessions.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return sessions


def check_alive(pid: int) -> bool:
    """Check if process is running via kill(0)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just can't signal
    except OSError:
        return False


def read_last_messages(claude_dir: Path, session_ids: set[str]) -> dict[str, dict]:
    """Find the most recent user message per session from history.jsonl.

    Reads up to 2MB from the tail of the file for efficiency.
    Returns {session_id: {"timestamp": int_ms, "display": str}}.
    """
    history = claude_dir / "history.jsonl"
    if not history.exists():
        return {}

    result: dict[str, dict] = {}
    remaining = set(session_ids)

    file_size = history.stat().st_size
    if file_size == 0:
        return {}

    read_size = min(file_size, 2 * 1024 * 1024)

    with open(history, "rb") as f:
        f.seek(file_size - read_size)
        if file_size > read_size:
            f.readline()  # discard partial first line
        raw = f.read()

    for line in reversed(raw.split(b"\n")):
        if not remaining:
            break
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        sid = entry.get("sessionId")
        if sid in remaining:
            result[sid] = {
                "timestamp": entry.get("timestamp"),
                "display": entry.get("display", ""),
            }
            remaining.discard(sid)

    return result


def read_session_messages(claude_dir: Path, session_id: str) -> list[dict]:
    """Read ALL messages for a specific session from history.jsonl."""
    history = claude_dir / "history.jsonl"
    if not history.exists():
        return []

    messages = []
    with open(history) as f:
        for line in f:
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if entry.get("sessionId") == session_id:
                messages.append({
                    "timestamp": entry.get("timestamp"),
                    "display": entry.get("display", ""),
                })
    return messages


def lookup_session_project(claude_dir: Path, session_id: str) -> str | None:
    """Find the project directory (cwd) for a session from history.jsonl.

    Supports exact match and prefix match on session_id.
    Reads from the tail for efficiency.
    """
    history = claude_dir / "history.jsonl"
    if not history.exists():
        return None

    file_size = history.stat().st_size
    if file_size == 0:
        return None

    read_size = min(file_size, 4 * 1024 * 1024)

    with open(history, "rb") as f:
        f.seek(file_size - read_size)
        if file_size > read_size:
            f.readline()
        raw = f.read()

    for line in reversed(raw.split(b"\n")):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        sid = entry.get("sessionId", "")
        if sid == session_id or sid.startswith(session_id):
            return entry.get("project")

    return None


def find_session(
    sessions: list[dict],
    ccctl_names: dict[str, str],
    query: str,
    include_name_store: bool = False,
) -> dict | None:
    """Find a session by PID, ccctl name, native name, or session_id prefix.

    Searches in order: PID → ccctl name → native name → session_id prefix.
    If include_name_store is True, also checks the name store for sessions
    whose session files are gone (for resume of stopped sessions).
    """
    # By PID
    for s in sessions:
        if str(s.get("pid")) == query:
            return s
    # By ccctl name
    for s in sessions:
        sid = s.get("sessionId", "")
        if ccctl_names.get(sid) == query:
            return s
    # By native name
    for s in sessions:
        if s.get("name") == query:
            return s
    # By session_id prefix
    for s in sessions:
        if s.get("sessionId", "").startswith(query):
            return s
    # Name store fallback for dead sessions
    if include_name_store:
        name_to_sid = {v: k for k, v in ccctl_names.items()}
        if query in name_to_sid:
            sid = name_to_sid[query]
            return {"sessionId": sid, "name": query, "_needs_cwd_lookup": True}
    return None


def resolve_session_id(claude_dir: Path, prefix: str) -> str | None:
    """Resolve a session_id prefix to a full session_id from history.jsonl."""
    history = claude_dir / "history.jsonl"
    if not history.exists():
        return None

    file_size = history.stat().st_size
    if file_size == 0:
        return None

    read_size = min(file_size, 4 * 1024 * 1024)

    with open(history, "rb") as f:
        f.seek(file_size - read_size)
        if file_size > read_size:
            f.readline()
        raw = f.read()

    for line in reversed(raw.split(b"\n")):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        sid = entry.get("sessionId", "")
        if sid.startswith(prefix):
            return sid

    return None
