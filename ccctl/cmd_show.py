"""show command — detailed session info."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from ccctl.output import format_ago
from ccctl.sources import check_alive, find_session, read_session_messages, read_sessions
from ccctl.store import get_name, load_names


def run(args):
    sessions = read_sessions(args.claude_dir)
    ccctl_names = load_names(args.claude_dir)
    target = find_session(sessions, ccctl_names, args.id, include_name_store=True, claude_dir=args.claude_dir)

    if not target:
        print(f"No session found matching: {args.id}", file=sys.stderr)
        sys.exit(1)

    pid = target.get("pid")
    sid = target.get("sessionId", "")
    alive = check_alive(pid) if pid else False
    now = time.time()

    ccctl_name = get_name(args.claude_dir, sid)
    native_name = target.get("name", "")
    name = ccctl_name or native_name or "(unnamed)"

    messages = read_session_messages(args.claude_dir, sid)

    if args.json:
        _json_out(target, alive, name, messages)
    else:
        _text_out(target, alive, name, messages, now)


def _process_info(pid: int) -> dict | None:
    try:
        r = subprocess.run(
            ["ps", "-o", "rss=,etime=,pcpu=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        parts = r.stdout.strip().split()
        if len(parts) >= 3:
            return {"rss_mb": int(parts[0]) // 1024, "elapsed": parts[1], "cpu_pct": parts[2]}
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass
    return None


def _git_status(cwd: str) -> list[str] | None:
    if not os.path.isdir(cwd):
        return None
    try:
        r = subprocess.run(
            ["git", "status", "--short", "--branch"],
            capture_output=True, text=True, timeout=5, cwd=cwd,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().split("\n")
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _text_out(target: dict, alive: bool, name: str, messages: list[dict], now: float):
    pid = target.get("pid")
    sid = target.get("sessionId", "")
    cwd = target.get("cwd", "")
    started = target.get("startedAt", 0) / 1000 if target.get("startedAt") else None

    print(f"Session: {name}")
    print(f"  PID:        {pid} ({'alive' if alive else 'DEAD'})")
    print(f"  Session ID: {sid}")
    print(f"  CWD:        {cwd}")
    print(f"  Kind:       {target.get('kind', '-')}")
    if started:
        print(f"  Started:    {format_ago(started, now)} ago")

    if alive and pid:
        info = _process_info(pid)
        if info:
            print(f"  Memory:     {info['rss_mb']}MB")
            print(f"  Uptime:     {info['elapsed']}")
            print(f"  CPU:        {info['cpu_pct']}%")

    if messages:
        print(f"\n  Messages ({len(messages)} total):")
        shown = messages[-10:]
        if len(messages) > 10:
            print(f"  ... ({len(messages) - 10} earlier omitted)")
        for m in shown:
            ts = m["timestamp"] / 1000 if m.get("timestamp") else 0
            ago = format_ago(ts, now) if ts else "?"
            display = m.get("display", "")[:100]
            print(f"    [{ago:>5} ago] {display}")
    else:
        print("\n  No messages in recent history.")

    git = _git_status(cwd)
    if git:
        print(f"\n  Git:")
        for line in git[:8]:
            print(f"    {line}")
        if len(git) > 8:
            print(f"    ... ({len(git) - 8} more)")


def _json_out(target: dict, alive: bool, name: str, messages: list[dict]):
    pid = target.get("pid")
    data = {
        "pid": pid,
        "alive": alive,
        "session_id": target.get("sessionId", ""),
        "name": name,
        "cwd": target.get("cwd", ""),
        "kind": target.get("kind", ""),
        "started_at": target["startedAt"] / 1000 if target.get("startedAt") else None,
        "messages": messages,
        "message_count": len(messages),
    }
    if alive and pid:
        data["process"] = _process_info(pid)
    json.dump(data, sys.stdout, indent=2, ensure_ascii=False, default=str)
    print()
