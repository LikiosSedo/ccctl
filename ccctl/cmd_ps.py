"""ps command — list Claude Code sessions."""

from __future__ import annotations

import json
import os
import sys
import time

from ccctl.output import clean_display, derive_project, print_group_table, print_session_table
from ccctl.sources import check_alive, read_last_messages, read_sessions
from ccctl.store import load_names

# Seconds thresholds for status classification
ACTIVE_THRESHOLD = 300  # 5 min
IDLE_THRESHOLD = 86400  # 1 day


def classify(alive: bool, last_active: float | None) -> str:
    if not alive:
        return "dead"
    if last_active is None:
        return "unknown"
    age = time.time() - last_active
    if age < ACTIVE_THRESHOLD:
        return "active"
    if age < IDLE_THRESHOLD:
        return "idle"
    return "stale"


_clean_display = clean_display  # back-compat alias


def run(args):
    sessions = read_sessions(args.claude_dir)
    if not sessions:
        if args.json:
            print(json.dumps({"sessions": [], "summary": {}}))
        else:
            print("No sessions found.")
        return

    sids = {s["sessionId"] for s in sessions if "sessionId" in s}
    last_msgs = read_last_messages(args.claude_dir, sids)
    ccctl_names = load_names(args.claude_dir)

    rows = []
    for s in sessions:
        pid = s.get("pid")
        sid = s.get("sessionId", "")
        alive = check_alive(pid) if pid else False

        msg = last_msgs.get(sid)
        if msg and msg.get("timestamp"):
            last_active = msg["timestamp"] / 1000
        else:
            last_active = s.get("_mtime")

        status = classify(alive, last_active)
        started_at = s["startedAt"] / 1000 if s.get("startedAt") else None
        last_input = _clean_display(msg["display"]) if (msg and msg.get("display")) else ""

        # Prefer ccctl name > native name
        effective_name = ccctl_names.get(sid) or s.get("name") or ""

        rows.append({
            "pid": pid,
            "alive": alive,
            "session_id": sid,
            "name": effective_name,
            "project": derive_project(s.get("cwd", "")),
            "cwd": s.get("cwd", ""),
            "started_at": started_at,
            "last_active": last_active,
            "status": status,
            "last_input": last_input,
        })

    if not args.all:
        rows = [r for r in rows if r["alive"]]

    _sort(rows, args.sort)

    if args.group:
        _output_grouped(rows, args.json)
    elif args.json:
        _json_output(rows)
    else:
        print_session_table(rows, time.time())


def _sort(rows: list[dict], key: str):
    order = {"active": 0, "idle": 1, "unknown": 2, "stale": 3, "dead": 4}
    if key == "status":
        rows.sort(key=lambda r: (order.get(r["status"], 9), -(r["last_active"] or 0)))
    elif key == "active":
        rows.sort(key=lambda r: -(r["last_active"] or 0))
    elif key == "started":
        rows.sort(key=lambda r: -(r["started_at"] or 0))
    elif key == "name":
        rows.sort(key=lambda r: (r["name"] or "\xff"))


def _json_output(rows: list[dict]):
    summary: dict[str, int] = {"total": len(rows)}
    for r in rows:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    summary["alive"] = sum(1 for r in rows if r["alive"])

    json.dump(
        {"sessions": rows, "summary": summary},
        sys.stdout,
        indent=2,
        ensure_ascii=False,
        default=str,
    )
    print()


def _output_grouped(rows: list[dict], as_json: bool):
    groups: dict[str, list[dict]] = {}
    for r in rows:
        p = r["project"]
        groups.setdefault(p, []).append(r)

    if as_json:
        out = []
        for project, members in groups.items():
            out.append({
                "project": project,
                "count": len(members),
                "active": sum(1 for m in members if m["status"] == "active"),
                "sessions": members,
            })
        out.sort(key=lambda g: (-g["active"], -g["count"]))
        json.dump({"groups": out}, sys.stdout, indent=2, ensure_ascii=False, default=str)
        print()
    else:
        print_group_table(groups, time.time())
