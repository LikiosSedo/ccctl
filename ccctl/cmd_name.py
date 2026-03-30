"""name command — name or rename sessions."""

from __future__ import annotations

import os
import sys

import time

from ccctl.output import format_ago, shorten_path
from ccctl.sources import check_alive, lookup_session_project, read_last_messages, read_sessions, resolve_session_id
from ccctl.store import load_names, save_names, set_name


def _derive_project(cwd: str) -> str:
    """Derive short project name from cwd."""
    wt = "/.claude/worktrees/"
    if wt in cwd:
        base, name = cwd.split(wt, 1)
        return f"{os.path.basename(base)}-{name.rstrip('/')}"
    return os.path.basename(cwd) or "session"


def run(args):
    if args.pid:
        _set_name(args)
    elif args.auto:
        _auto_name(args)
    else:
        _list_names(args)


def _find_target(args) -> tuple[str, str]:
    """Find session by PID, name, or session_id prefix. Returns (sid, label)."""
    query = args.pid
    sessions = read_sessions(args.claude_dir)
    ccctl_names = load_names(args.claude_dir)

    # Live: by PID
    for s in sessions:
        if str(s.get("pid")) == query:
            return s["sessionId"], f"PID {query}"
    # Live: by ccctl name
    for s in sessions:
        sid = s.get("sessionId", "")
        if ccctl_names.get(sid) == query:
            return sid, query
    # Live: by native name
    for s in sessions:
        if s.get("name") == query:
            return s["sessionId"], query
    # Live: by session_id prefix
    for s in sessions:
        if s.get("sessionId", "").startswith(query):
            return s["sessionId"], query[:12]
    # History: by session_id (full or prefix) — resolve from history.jsonl
    full_sid = resolve_session_id(args.claude_dir, query)
    if full_sid:
        return full_sid, query[:12]
    # Name store: by existing ccctl name
    name_to_sid = {v: k for k, v in ccctl_names.items()}
    if query in name_to_sid:
        return name_to_sid[query], query

    return "", ""


def _set_name(args):
    sid, label = _find_target(args)
    if not sid:
        print(f"No session found: {args.pid}", file=sys.stderr)
        sys.exit(1)

    if not args.name_value:
        # Try to suggest from cwd
        cwd = lookup_session_project(args.claude_dir, sid)
        suggestion = _derive_project(cwd) if cwd else sid[:12]
        print(f"Suggestion: ccctl name {args.pid} {suggestion}")
        return

    set_name(args.claude_dir, sid, args.name_value)
    print(f"Named {label} → {args.name_value}")


def _auto_name(args):
    sessions = read_sessions(args.claude_dir)
    existing_ccctl = load_names(args.claude_dir)
    now = time.time()

    # Only auto-name alive, unnamed sessions
    unnamed = []
    for s in sessions:
        pid = s.get("pid")
        sid = s.get("sessionId", "")
        if not check_alive(pid):
            continue
        if s.get("name") or existing_ccctl.get(sid):
            continue
        unnamed.append(s)

    if not unnamed:
        print("All alive sessions already have names.")
        return

    # Group by project to number duplicates
    project_counts: dict[str, int] = {}
    assignments: list[tuple[dict, str]] = []

    for s in unnamed:
        project = _derive_project(s.get("cwd", ""))
        project_counts[project] = project_counts.get(project, 0) + 1
        assignments.append((s, project))

    # Assign names: project if unique, project-N if multiple
    used_counts: dict[str, int] = {}
    for s, project in assignments:
        if project_counts[project] > 1:
            used_counts[project] = used_counts.get(project, 0) + 1
            name = f"{project}-{used_counts[project]}"
        else:
            name = project
        sid = s["sessionId"]
        existing_ccctl[sid] = name
        print(f"  PID {s['pid']:>5} → {name}")

    save_names(args.claude_dir, existing_ccctl)
    print(f"\nNamed {len(unnamed)} sessions.")


def _list_names(args):
    sessions = read_sessions(args.claude_dir)
    ccctl_names = load_names(args.claude_dir)
    now = time.time()

    sids = {s["sessionId"] for s in sessions if "sessionId" in s}
    last_msgs = read_last_messages(args.claude_dir, sids)

    print(f"{'PID':>6}  {'STATUS':<7} {'NATIVE NAME':<20} {'CCCTL NAME':<20} {'CWD':<30} LAST INPUT")
    print("─" * 120)

    for s in sessions:
        pid = s.get("pid")
        sid = s.get("sessionId", "")
        alive = check_alive(pid) if pid else False
        if not alive:
            continue

        native = s.get("name") or "-"
        ccctl = ccctl_names.get(sid, "-")
        cwd = shorten_path(s.get("cwd", ""), 28)

        msg = last_msgs.get(sid)
        last_input = (msg.get("display", "") if msg else "")[:40]
        if not last_input:
            last_input = "-"

        status = "named" if (native != "-" or ccctl != "-") else "UNNAMED"
        print(f"{pid:>6}  {status:<7} {native:<20} {ccctl:<20} {cwd:<30} {last_input}")

    unnamed_count = sum(
        1 for s in sessions
        if check_alive(s.get("pid", 0))
        and not s.get("name")
        and not ccctl_names.get(s.get("sessionId", ""))
    )
    if unnamed_count:
        print(f"\n{unnamed_count} unnamed sessions. Run: ccctl name --auto")
