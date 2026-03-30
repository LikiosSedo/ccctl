"""name command — name or rename sessions."""

from __future__ import annotations

import os
import sys

from ccctl.output import format_ago, shorten_path
from ccctl.sources import check_alive, read_last_messages, read_sessions
from ccctl.store import load_names, save_names, set_name
import time


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


def _set_name(args):
    sessions = read_sessions(args.claude_dir)
    target = None
    for s in sessions:
        if str(s.get("pid")) == str(args.pid):
            target = s
            break
    if not target:
        print(f"No session with PID {args.pid}", file=sys.stderr)
        sys.exit(1)

    sid = target["sessionId"]
    if not args.name_value:
        # Suggest
        suggestion = _derive_project(target.get("cwd", ""))
        print(f"Suggestion: ccctl name {args.pid} {suggestion}")
        return

    set_name(args.claude_dir, sid, args.name_value)
    print(f"Named PID {args.pid} → {args.name_value}")


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
