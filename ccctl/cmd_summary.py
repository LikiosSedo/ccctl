"""summary — full topology and activity overview across all projects.

Reads history.jsonl to build a complete map of:
  - Every project directory
  - Every session within each project
  - Message counts, time ranges, content previews
  - Cross-referenced with live sessions

Activity levels:
  hot   — activity in last 24h
  warm  — activity in last 7d
  cool  — activity in last 30d
  cold  — no activity in 30d+
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import defaultdict

from ccctl.output import format_ago
from ccctl.sources import check_alive, read_sessions
from ccctl.store import load_names

# Activity thresholds (seconds)
HOT = 86400       # 24h
WARM = 7 * 86400  # 7d
COOL = 30 * 86400 # 30d

_PASTED_RE = re.compile(r"\[Pasted text #\d+[^\]]*\]")
_IMAGE_RE = re.compile(r"\[Image #\d+\]")


def _clean(text: str) -> str:
    text = _PASTED_RE.sub("", text)
    text = _IMAGE_RE.sub("", text)
    return text.replace("\n", " ").strip()


def _activity_level(last_ts_ms: int, now_ms: int) -> str:
    age = (now_ms - last_ts_ms) / 1000
    if age < HOT:
        return "hot"
    if age < WARM:
        return "warm"
    if age < COOL:
        return "cool"
    return "cold"


def _derive_project(path: str, scope: str) -> str:
    rel = path[len(scope):].strip("/")
    wt = ".claude/worktrees/"
    if wt in rel:
        base, name = rel.split("/.claude/worktrees/", 1)
        return f"{base} (wt:{name.rstrip('/')})"
    return rel if rel else "(root)"


def run(args):
    scope = args.scope or os.path.expanduser("~/project")
    scope = scope.rstrip("/")
    history = args.claude_dir / "history.jsonl"

    if not history.exists():
        print("No history found.")
        return

    now_ms = int(time.time() * 1000)
    days_cutoff = None
    if args.days:
        days_cutoff = now_ms - args.days * 86400 * 1000

    # --- 1. Read history ---
    session_data: dict[str, dict] = {}

    with open(history) as f:
        for line in f:
            try:
                e = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            proj = e.get("project", "")
            if not proj.startswith(scope):
                continue

            ts = e.get("timestamp", 0)
            if days_cutoff and ts < days_cutoff:
                continue

            sid = e.get("sessionId", "")
            display = e.get("display", "")

            if sid not in session_data:
                session_data[sid] = {
                    "session_id": sid,
                    "project_path": proj,
                    "project": _derive_project(proj, scope),
                    "msg_count": 0,
                    "first_ts": ts,
                    "last_ts": ts,
                    "first_msg": display,
                    "last_msg": display,
                }
            s = session_data[sid]
            s["msg_count"] += 1
            if ts < s["first_ts"]:
                s["first_ts"] = ts
                s["first_msg"] = display
            if ts > s["last_ts"]:
                s["last_ts"] = ts
                s["last_msg"] = display

    if not session_data:
        print("No sessions found in scope.")
        return

    # --- 2. Cross-reference with live sessions ---
    live_sessions = read_sessions(args.claude_dir)
    live_map: dict[str, dict] = {}
    for ls in live_sessions:
        sid = ls.get("sessionId", "")
        pid = ls.get("pid")
        if pid and check_alive(pid):
            live_map[sid] = ls

    ccctl_names = load_names(args.claude_dir)

    for sid, s in session_data.items():
        live = live_map.get(sid)
        s["alive"] = live is not None
        s["pid"] = live.get("pid") if live else None
        s["name"] = ccctl_names.get(sid) or (live.get("name") if live else "") or ""
        s["activity"] = _activity_level(s["last_ts"], now_ms)
        s["first_msg_clean"] = _clean(s["first_msg"])
        s["last_msg_clean"] = _clean(s["last_msg"])

    # --- 3. Group by project ---
    projects: dict[str, dict] = {}

    for sid, s in session_data.items():
        pname = s["project"]
        if pname not in projects:
            projects[pname] = {
                "project": pname,
                "path": s["project_path"],
                "sessions": [],
                "total_msgs": 0,
                "first_ts": s["first_ts"],
                "last_ts": s["last_ts"],
                "live_count": 0,
            }
        p = projects[pname]
        p["sessions"].append(s)
        p["total_msgs"] += s["msg_count"]
        p["first_ts"] = min(p["first_ts"], s["first_ts"])
        p["last_ts"] = max(p["last_ts"], s["last_ts"])
        if s["alive"]:
            p["live_count"] += 1

    for p in projects.values():
        p["activity"] = _activity_level(p["last_ts"], now_ms)
        p["session_count"] = len(p["sessions"])
        # Sort sessions: alive first, then by last_ts descending
        p["sessions"].sort(key=lambda s: (-s["alive"], -s["last_ts"]))

    # Sort projects: hot first, then by last_ts descending
    activity_order = {"hot": 0, "warm": 1, "cool": 2, "cold": 3}
    sorted_projects = sorted(
        projects.values(),
        key=lambda p: (activity_order.get(p["activity"], 9), -p["last_ts"]),
    )

    # --- 4. Output ---
    if args.json:
        _json_output(sorted_projects, now_ms, scope, args.days)
    else:
        _table_output(sorted_projects, now_ms, scope, args.days, args.verbose)


def _json_output(projects: list[dict], now_ms: int, scope: str, days: int | None):
    # Clean up internal fields for JSON
    clean_projects = []
    for p in projects:
        cp = {
            "project": p["project"],
            "path": p["path"],
            "activity": p["activity"],
            "session_count": p["session_count"],
            "live_count": p["live_count"],
            "total_messages": p["total_msgs"],
            "first_active": p["first_ts"],
            "last_active": p["last_ts"],
            "sessions": [
                {
                    "session_id": s["session_id"],
                    "name": s["name"],
                    "alive": s["alive"],
                    "pid": s["pid"],
                    "activity": s["activity"],
                    "messages": s["msg_count"],
                    "first_active": s["first_ts"],
                    "last_active": s["last_ts"],
                    "first_message": s["first_msg_clean"][:200],
                    "last_message": s["last_msg_clean"][:200],
                }
                for s in p["sessions"]
            ],
        }
        clean_projects.append(cp)

    # Summary stats
    total_sessions = sum(p["session_count"] for p in projects)
    total_msgs = sum(p["total_msgs"] for p in projects)
    activity_counts = defaultdict(int)
    for p in projects:
        activity_counts[p["activity"]] += 1

    output = {
        "scope": scope,
        "days": days,
        "generated_at": now_ms,
        "summary": {
            "projects": len(projects),
            "sessions": total_sessions,
            "messages": total_msgs,
            "hot": activity_counts["hot"],
            "warm": activity_counts["warm"],
            "cool": activity_counts["cool"],
            "cold": activity_counts["cold"],
        },
        "projects": clean_projects,
    }
    json.dump(output, sys.stdout, indent=2, ensure_ascii=False, default=str)
    print()


_ACTIVITY_ICON = {"hot": "🔴", "warm": "🟡", "cool": "🔵", "cold": "⚪"}


def _table_output(projects: list[dict], now_ms: int, scope: str, days: int | None, verbose: bool):
    now = now_ms / 1000

    # Header
    period = f"last {days} days" if days else "all time"
    total_s = sum(p["session_count"] for p in projects)
    total_m = sum(p["total_msgs"] for p in projects)
    live_total = sum(p["live_count"] for p in projects)
    print(f"Scope: {scope} ({period})")
    print(f"{len(projects)} projects, {total_s} sessions, {total_m} messages, {live_total} live\n")

    for p in projects:
        icon = _ACTIVITY_ICON.get(p["activity"], " ")
        last_ago = format_ago(p["last_ts"] / 1000, now)
        span_days = int((p["last_ts"] - p["first_ts"]) / 1000 / 86400)
        live_str = f"  [{p['live_count']} live]" if p["live_count"] else ""

        print(f"{icon} {p['project']:<42} {p['session_count']:>3}s {p['total_msgs']:>5}m  last: {last_ago:>4}{live_str}")

        if verbose:
            for s in p["sessions"][:5]:  # top 5 sessions per project
                alive_mark = "●" if s["alive"] else " "
                name = s["name"] or s["session_id"][:8]
                last = format_ago(s["last_ts"] / 1000, now)
                msg_preview = s["last_msg_clean"][:50] or "-"
                if len(s["last_msg_clean"]) > 50:
                    msg_preview += "…"
                print(f"    {alive_mark} {name:<22} {s['msg_count']:>3}m  {last:>4}  {msg_preview}")
            remaining = len(p["sessions"]) - 5
            if remaining > 0:
                print(f"    ... +{remaining} more sessions")
            print()

    if not verbose:
        print(f"\nUse -v for per-session details.")
