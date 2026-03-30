"""dashboard command — local web UI for session overview and dispatch."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ccctl.cmd_ps import classify
from ccctl.output import clean_display, derive_project
from ccctl.output import applescript_str
from ccctl.sources import check_alive, read_last_messages, read_sessions, lookup_session_project
from ccctl.store import load_names, load_config, save_config

_claude_dir: Path = Path.home() / ".claude"


def _detect_terminal() -> str:
    term = os.environ.get("TERM_PROGRAM", "")
    if "iTerm" in term:
        return "iterm"
    if "Apple_Terminal" in term:
        return "terminal"
    if os.environ.get("TMUX"):
        return "tmux"
    return "terminal"


def _read_terminal_states(pids: list[int]) -> dict[int, dict]:
    """Read session ready/busy state from status files written by hooks.

    Claude Code hooks write 'idle' or 'working' to
    ~/.claude/ccctl/status/<pid>. Zero AppleScript overhead.

    Returns {pid: {"ready": bool}}.
    """
    status_dir = _claude_dir / "ccctl" / "status"
    result = {}
    for pid in pids:
        f = status_dir / str(pid)
        if f.exists():
            try:
                state = f.read_text().strip()
                result[pid] = {"ready": state == "idle"}
            except OSError:
                pass
    return result


def _build_rows() -> list[dict]:
    sessions = read_sessions(_claude_dir)
    if not sessions:
        return []

    sids = {s["sessionId"] for s in sessions if "sessionId" in s}
    last_msgs = read_last_messages(_claude_dir, sids)
    ccctl_names = load_names(_claude_dir)
    config = load_config(_claude_dir)
    coordinator_sid = config.get("coordinator", "")
    now = time.time()

    rows = []
    for s in sessions:
        pid = s.get("pid")
        sid = s.get("sessionId", "")
        alive = check_alive(pid) if pid else False
        if not alive:
            continue

        msg = last_msgs.get(sid)
        if msg and msg.get("timestamp"):
            last_active = msg["timestamp"] / 1000
        else:
            last_active = s.get("_mtime")

        status = classify(alive, last_active)
        last_input = clean_display(msg["display"]) if (msg and msg.get("display")) else ""
        name = ccctl_names.get(sid) or s.get("name") or sid[:8]

        rows.append({
            "pid": pid,
            "session_id": sid,
            "name": name,
            "project": derive_project(s.get("cwd", "")),
            "cwd": s.get("cwd", ""),
            "status": status,
            "last_active": last_active,
            "last_active_ago": _ago(last_active, now),
            "last_input": last_input,
            "is_coordinator": sid == coordinator_sid,
        })

    rows.sort(key=lambda r: -(r["last_active"] or 0))
    return rows


def _build_history(query: str = "") -> list[dict]:
    """Build history sessions from history.jsonl (not currently live)."""
    history = _claude_dir / "history.jsonl"
    if not history.exists():
        return []

    file_size = history.stat().st_size
    if file_size == 0:
        return []

    # Read last 4MB
    read_size = min(file_size, 4 * 1024 * 1024)
    with open(history, "rb") as f:
        f.seek(file_size - read_size)
        if file_size > read_size:
            f.readline()
        raw = f.read()

    # Collect per-session info
    session_data: dict[str, dict] = {}
    for line in raw.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        sid = entry.get("sessionId", "")
        if not sid:
            continue
        ts = entry.get("timestamp")
        display = entry.get("display", "")
        project = entry.get("project", "")
        if sid not in session_data:
            session_data[sid] = {
                "session_id": sid,
                "first_input": display,
                "last_input": display,
                "last_ts": ts,
                "first_ts": ts,
                "project": project,
                "msg_count": 0,
            }
        d = session_data[sid]
        d["msg_count"] += 1
        if ts and (not d["last_ts"] or ts > d["last_ts"]):
            d["last_ts"] = ts
            d["last_input"] = display
        if ts and (not d["first_ts"] or ts < d["first_ts"]):
            d["first_ts"] = ts
            d["first_input"] = display

    # Filter out currently live sessions
    live_sessions = read_sessions(_claude_dir)
    live_sids = set()
    for s in live_sessions:
        sid = s.get("sessionId", "")
        if sid and check_alive(s.get("pid")):
            live_sids.add(sid)

    ccctl_names = load_names(_claude_dir)
    now = time.time()
    query_lower = query.lower()

    rows = []
    for sid, d in session_data.items():
        if sid in live_sids:
            continue

        name = ccctl_names.get(sid) or sid[:8]
        project = os.path.basename(d["project"]) if d["project"] else ""
        first_input = clean_display(d["first_input"]) if d.get("first_input") else ""
        last_input = clean_display(d["last_input"]) if d.get("last_input") else ""

        # Search filter
        if query_lower:
            searchable = f"{name} {project} {first_input} {last_input}".lower()
            if query_lower not in searchable:
                continue

        last_active = d["last_ts"] / 1000 if d["last_ts"] else None

        rows.append({
            "session_id": sid,
            "name": name,
            "project": project,
            "last_active": last_active,
            "last_active_ago": _ago(last_active, now),
            "first_input": first_input[:80] if first_input else "",
            "last_input": last_input[:80] if last_input else "",
            "msg_count": d["msg_count"],
        })

    rows.sort(key=lambda r: -(r["last_active"] or 0))
    return rows[:50]  # cap at 50


from ccctl.output import format_ago as _format_ago


def _ago(ts: float | None, now: float) -> str:
    return _format_ago(ts, now)


def _focus_terminal_tty(tty_path: str) -> tuple[bool, str | None]:
    terminal = _detect_terminal()

    if terminal == "iterm":
        script = f'''
            tell application "iTerm2"
                activate
                repeat with w in windows
                    tell w
                        repeat with i from 1 to count of tabs
                            set t to tab i
                            repeat with s in sessions of t
                                if tty of s is "{tty_path}" then
                                    select t
                                    set index of w to 1
                                    return "ok"
                                end if
                            end repeat
                        end repeat
                    end tell
                end repeat
                return "not found"
            end tell
        '''
        not_found = "Tab not found in iTerm2"
    elif terminal == "terminal":
        script = f'''
            tell application "Terminal"
                activate
                repeat with w in windows
                    repeat with t in tabs of w
                        if tty of t is "{tty_path}" then
                            set selected of t to true
                            set frontmost of w to true
                            return "ok"
                        end if
                    end repeat
                end repeat
                return "not found"
            end tell
        '''
        not_found = "Tab not found in Terminal.app"
    else:
        return False, f"Focus not supported in {terminal}"

    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
        if r.stdout.strip() == "ok":
            return True, None
        return False, not_found
    except (subprocess.TimeoutExpired, OSError):
        return False, "AppleScript failed"


def _inject_prompt(tty_path: str, prompt: str) -> tuple[bool, str | None]:
    terminal = _detect_terminal()
    escaped = applescript_str(prompt)

    if terminal == "iterm":
        script = f'''
            tell application "iTerm2"
                repeat with w in windows
                    repeat with t in tabs of w
                        repeat with s in sessions of t
                            if tty of s is "{tty_path}" then
                                tell s to write text (ASCII character 27) & "i" & "{escaped}"
                                return "ok"
                            end if
                        end repeat
                    end repeat
                end repeat
            end tell
        '''
    elif terminal == "terminal":
        focused, error = _focus_terminal_tty(tty_path)
        if not focused:
            return False, error
        script = f'''
            set old_clipboard to the clipboard
            set the clipboard to "{escaped}"
            tell application "System Events"
                key code 53
                keystroke "i"
                keystroke "v" using command down
            end tell
            delay 0.05
            set the clipboard to old_clipboard
            return "ok"
        '''
    else:
        return False, f"Prompt injection not supported in {terminal}"

    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
        if r.stdout.strip() == "ok":
            return True, None
        return False, "Session tab not found"
    except (subprocess.TimeoutExpired, OSError):
        return False, "AppleScript failed"


def _do_focus(target: str, prompt: str | None) -> dict:
    """Focus a session, optionally send prompt. Returns result dict."""
    sessions = read_sessions(_claude_dir)
    ccctl_names = load_names(_claude_dir)

    session = None
    for s in sessions:
        sid = s.get("sessionId", "")
        name = ccctl_names.get(sid) or s.get("name") or ""
        if str(s.get("pid")) == target or name == target or sid.startswith(target):
            session = s
            break

    if not session:
        return {"ok": False, "error": f"Session not found: {target}"}

    pid = session.get("pid")
    if not check_alive(pid):
        return {"ok": False, "error": f"Session not alive (PID {pid})"}

    try:
        r = subprocess.run(
            ["ps", "-o", "tty=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
        tty = r.stdout.strip()
        if not tty or tty == "??":
            return {"ok": False, "error": f"No TTY for PID {pid}"}
        tty_path = applescript_str(f"/dev/{tty}")
    except (subprocess.TimeoutExpired, OSError):
        return {"ok": False, "error": "Failed to get TTY"}

    focused, error = _focus_terminal_tty(tty_path)
    if not focused:
        return {"ok": False, "error": error}

    result = {"ok": True, "focused": True}

    if prompt:
        try:
            r = subprocess.run(
                ["ps", "-o", "tpgid=,pgid=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            )
            parts = r.stdout.strip().split()
            if len(parts) < 2 or parts[0] != parts[1]:
                result["prompt_sent"] = False
                result["prompt_error"] = "Session not at prompt"
                return result
        except (subprocess.TimeoutExpired, OSError):
            result["prompt_sent"] = False
            result["prompt_error"] = "Failed to check foreground"
            return result

        sent, send_error = _inject_prompt(tty_path, prompt)
        result["prompt_sent"] = sent
        if send_error:
            result["prompt_error"] = send_error

    return result


_DISPATCH_PREFIX = (
    "[ccctl dispatch] You are the coordinator session. "
    "Execute the instruction below using ccctl CLI commands "
    "(ps, new, resume, focus, stop, summary, dashboard) as needed.\n\n"
)

_COORDINATOR_INIT = (
    "[ccctl coordinator init]\n"
    "You are now the coordinator session. Your role:\n"
    "1. Manage and dispatch Claude Code sessions via ccctl CLI\n"
    "2. When you receive [ccctl dispatch] messages, execute the instruction\n\n"
    "## First Step\n"
    "Run `ccctl ps --json` now to understand the current session topology.\n\n"
    "## Key Commands\n"
    "- `ccctl ps --json` — current live sessions (run FIRST, and before every dispatch)\n"
    "- `ccctl summary -v` — full project topology and history\n"
    "- `ccctl new --name <n> --cwd <dir> \"prompt\"` — start new session\n"
    "- `ccctl resume <name>` — resume stopped session\n"
    "- `ccctl focus <name> \"prompt\"` — switch to session + send prompt\n"
    "- `ccctl stop <name>` — stop session (preserves data)\n"
    "- `ccctl name <target> <name>` — rename session\n\n"
    "## Rules\n"
    "- ALWAYS run `ccctl ps --json` before acting on a dispatch to get fresh state\n"
    "- Use `--json` output for reliable parsing\n"
    "- When creating sessions, use meaningful names and correct project cwd\n"
    "- Confirm destructive actions (stop) before executing"
)


def _do_send(target: str, prompt: str, as_coordinator: bool = False) -> dict:
    """Send prompt to a session WITHOUT activating/focusing its window."""
    if as_coordinator:
        prompt = _DISPATCH_PREFIX + prompt
    sessions = read_sessions(_claude_dir)
    ccctl_names = load_names(_claude_dir)

    session = None
    for s in sessions:
        sid = s.get("sessionId", "")
        name = ccctl_names.get(sid) or s.get("name") or ""
        if str(s.get("pid")) == target or name == target or sid.startswith(target):
            session = s
            break

    if not session:
        return {"ok": False, "error": f"Session not found: {target}"}

    pid = session.get("pid")
    if not check_alive(pid):
        return {"ok": False, "error": f"Session not alive (PID {pid})"}

    # Check foreground
    try:
        r = subprocess.run(
            ["ps", "-o", "tty=,tpgid=,pgid=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
        parts = r.stdout.strip().split()
        if len(parts) < 3:
            return {"ok": False, "error": "Cannot read process info"}
        tty, tpgid, pgid = parts[0], parts[1], parts[2]
        if not tty or tty == "??":
            return {"ok": False, "error": f"No TTY for PID {pid}"}
        if tpgid != pgid:
            return {"ok": False, "error": "Session not at prompt"}
        tty_path = applescript_str(f"/dev/{tty}")
    except (subprocess.TimeoutExpired, OSError):
        return {"ok": False, "error": "Failed to check process"}

    sent, error = _inject_prompt(tty_path, prompt.replace("\n", " "))
    if sent:
        return {"ok": True, "sent": True}
    return {"ok": False, "error": error or "Send failed"}


def _do_rename(session_id: str, new_name: str) -> dict:
    """Rename a session (ccctl store + /rename injection)."""
    from ccctl.store import set_name
    from ccctl.cmd_name import _inject_rename

    sessions = read_sessions(_claude_dir)
    set_name(_claude_dir, session_id, new_name)

    # Try to sync via /rename injection
    synced = False
    for s in sessions:
        if s.get("sessionId") == session_id:
            pid = s.get("pid")
            if pid and check_alive(pid):
                synced = _inject_rename(pid, new_name)
            break

    return {"ok": True, "name": new_name, "synced": synced}


def _do_stop(target: str) -> dict:
    """Stop a session by SIGTERM."""
    sessions = read_sessions(_claude_dir)
    ccctl_names = load_names(_claude_dir)

    session = None
    for s in sessions:
        sid = s.get("sessionId", "")
        name = ccctl_names.get(sid) or s.get("name") or ""
        if str(s.get("pid")) == target or name == target or sid.startswith(target):
            session = s
            break

    if not session:
        return {"ok": False, "error": f"Session not found: {target}"}

    pid = session.get("pid")
    if not check_alive(pid):
        return {"ok": False, "error": f"Already stopped (PID {pid})"}

    try:
        os.kill(pid, signal.SIGTERM)
        return {"ok": True, "stopped": True, "pid": pid}
    except ProcessLookupError:
        return {"ok": False, "error": f"Process gone (PID {pid})"}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def _do_resume(session_id: str) -> dict:
    """Resume a session in a new terminal tab."""
    from ccctl.cmd_open import _open_in_new_window

    ccctl_names = load_names(_claude_dir)
    name = ccctl_names.get(session_id) or session_id[:8]
    cwd = lookup_session_project(_claude_dir, session_id) or os.path.expanduser("~")

    cmd = f"claude --resume {shlex.quote(session_id)}"
    if name:
        cmd += f" --name {shlex.quote(name)}"

    try:
        where = _open_in_new_window(cmd, cwd, title=name)
        return {"ok": True, "resumed": True, "name": name, "where": where}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._html()
        elif parsed.path == "/api/ps":
            self._json_response(_build_rows())
        elif parsed.path == "/api/status":
            rows = _build_rows()
            pids = [r["pid"] for r in rows if r["pid"]]
            states = _read_terminal_states(pids)
            # Return {pid: {ready, preview}}
            self._json_response({str(k): v for k, v in states.items()})
        elif parsed.path == "/api/history":
            qs = parse_qs(parsed.query)
            query = qs.get("q", [""])[0]
            self._json_response(_build_history(query))
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        if length > 65536:
            self.send_error(413, "Request body too large")
            return
        body = json.loads(self.rfile.read(length)) if length else {}

        if parsed.path == "/api/focus":
            target = body.get("target", "")
            prompt = body.get("prompt")
            self._json_response(_do_focus(target, prompt if prompt else None))
        elif parsed.path == "/api/send":
            target = body.get("target", "")
            prompt = body.get("prompt", "")
            coordinator = body.get("coordinator", False)
            self._json_response(_do_send(target, prompt, as_coordinator=coordinator))
        elif parsed.path == "/api/stop":
            target = body.get("target", "")
            self._json_response(_do_stop(target))
        elif parsed.path == "/api/resume":
            sid = body.get("session_id", "")
            self._json_response(_do_resume(sid))
        elif parsed.path == "/api/rename":
            sid = body.get("session_id", "")
            new_name = body.get("name", "")
            if not sid or not new_name:
                self._json_response({"ok": False, "error": "Missing session_id or name"})
            else:
                self._json_response(_do_rename(sid, new_name))
        elif parsed.path == "/api/pin":
            sid = body.get("session_id", "")
            config = load_config(_claude_dir)
            if config.get("coordinator") == sid:
                config.pop("coordinator", None)
                save_config(_claude_dir, config)
                self._json_response({"ok": True, "unpinned": True})
            else:
                config["coordinator"] = sid
                save_config(_claude_dir, config)
                # Find target name, send init, then focus
                target_name = None
                for r in _build_rows():
                    if r["session_id"] == sid:
                        target_name = r["name"]
                        break
                if target_name:
                    _do_send(target_name, _COORDINATOR_INIT, as_coordinator=False)
                    _do_focus(target_name, prompt=None)
                self._json_response({"ok": True, "pinned": True})
        else:
            self.send_error(404)

    def _json_response(self, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self):
        body = HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *a):
        pass


HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ccctl dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, "SF Mono", Menlo, monospace;
    background: #1a1a2e;
    color: #e0e0e0;
    padding: 20px;
    min-height: 100vh;
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
    padding-bottom: 12px;
    border-bottom: 1px solid #333;
  }
  header h1 { font-size: 18px; font-weight: 600; color: #8be9fd; }
  .header-right { display: flex; align-items: center; gap: 16px; }
  header .stats { font-size: 13px; color: #888; }
  .tabs {
    display: flex;
    gap: 0;
    margin-bottom: 16px;
  }
  .tab {
    padding: 8px 20px;
    font-size: 13px;
    cursor: pointer;
    border: 1px solid #333;
    background: transparent;
    color: #888;
    font-family: inherit;
  }
  .tab:first-child { border-radius: 6px 0 0 6px; }
  .tab:last-child { border-radius: 0 6px 6px 0; }
  .tab.active { background: #8be9fd22; color: #8be9fd; border-color: #8be9fd44; }
  .search-row {
    display: flex;
    gap: 8px;
    margin-bottom: 16px;
  }
  .search-row input {
    flex: 1;
    background: #16213e;
    border: 1px solid #444;
    border-radius: 6px;
    color: #e0e0e0;
    padding: 8px 12px;
    font-size: 13px;
    font-family: inherit;
    outline: none;
  }
  .search-row input:focus { border-color: #8be9fd; }
  .search-row input::placeholder { color: #555; }
  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
    gap: 12px;
  }
  .card {
    background: #16213e;
    border: 1px solid #333;
    border-radius: 8px;
    padding: 14px;
    cursor: pointer;
    transition: border-color 0.15s, box-shadow 0.15s;
    position: relative;
  }
  .card:hover { border-color: #8be9fd; box-shadow: 0 0 12px rgba(139,233,253,0.15); }
  .card.active { border-left: 3px solid #50fa7b; }
  .card.idle { border-left: 3px solid #f1fa8c; }
  .card.stale { border-left: 3px solid #ff79c6; }
  .card.dead { border-left: 3px solid #666; opacity: 0.7; }
  .card .top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }
  .card .name { font-size: 15px; font-weight: 600; color: #f8f8f2; }
  .card .badge {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 10px;
    font-weight: 500;
  }
  .badge.active { background: #50fa7b22; color: #50fa7b; }
  .badge.idle { background: #f1fa8c22; color: #f1fa8c; }
  .badge.stale { background: #ff79c622; color: #ff79c6; }
  .badge.dead { background: #66666622; color: #888; }
  .card .meta {
    font-size: 12px;
    color: #888;
    margin-bottom: 6px;
  }
  .card .last-input {
    font-size: 12px;
    color: #aaa;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-bottom: 10px;
    padding: 6px 8px;
    background: #1a1a2e;
    border-radius: 4px;
  }
  .rename-btn {
    font-size: 14px;
    color: #888;
    cursor: pointer;
    margin-left: 6px;
    padding: 0 4px;
    opacity: 0;
    transition: opacity 0.15s;
  }
  .card:hover .rename-btn { opacity: 1; }
  .rename-btn:hover { color: #8be9fd; background: #8be9fd22; border-radius: 3px; }
  .rename-input {
    background: #1a1a2e;
    border: 1px solid #8be9fd;
    border-radius: 4px;
    color: #f8f8f2;
    padding: 2px 6px;
    font-size: 14px;
    font-family: inherit;
    font-weight: 600;
    outline: none;
    width: 180px;
  }
  .card .preview {
    font-size: 11px;
    color: #6272a4;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-bottom: 8px;
    padding: 4px 8px;
    background: #1a1a2e;
    border-radius: 4px;
    border-left: 2px solid #6272a4;
    font-style: italic;
  }
  .card .actions {
    display: flex;
    gap: 6px;
  }
  .card input {
    flex: 1;
    background: #1a1a2e;
    border: 1px solid #444;
    border-radius: 4px;
    color: #e0e0e0;
    padding: 6px 8px;
    font-size: 12px;
    font-family: inherit;
    outline: none;
  }
  .card input:focus { border-color: #8be9fd; }
  .card input::placeholder { color: #555; }
  .btn {
    background: #8be9fd22;
    border: 1px solid #8be9fd44;
    color: #8be9fd;
    border-radius: 4px;
    padding: 6px 12px;
    font-size: 12px;
    cursor: pointer;
    font-family: inherit;
    white-space: nowrap;
  }
  .btn:hover { background: #8be9fd33; }
  .btn.danger {
    background: #ff555522;
    border-color: #ff555544;
    color: #ff5555;
  }
  .btn.danger:hover { background: #ff555533; }
  .btn.resume {
    background: #50fa7b22;
    border-color: #50fa7b44;
    color: #50fa7b;
  }
  .btn.resume:hover { background: #50fa7b33; }
  .btn.pin {
    background: #bd93f922;
    border-color: #bd93f944;
    color: #bd93f9;
    font-size: 11px;
    padding: 4px 8px;
  }
  .btn.pin:hover { background: #bd93f933; }
  .btn.pin.active { background: #bd93f944; }
  .card.coordinator {
    border: 1px solid #bd93f966;
    box-shadow: 0 0 8px rgba(189,147,249,0.15);
  }
  .card.coordinator .name::before {
    content: "\\u2605 ";
    color: #bd93f9;
  }
  .dispatch-bar {
    background: #16213e;
    border: 1px solid #bd93f944;
    border-radius: 8px;
    padding: 12px 16px;
    margin-bottom: 16px;
    display: none;
  }
  .dispatch-bar.visible { display: block; }
  .dispatch-bar .label {
    font-size: 12px;
    color: #bd93f9;
    margin-bottom: 8px;
    font-weight: 600;
  }
  .dispatch-bar .row {
    display: flex;
    gap: 8px;
  }
  .dispatch-bar input {
    flex: 1;
    background: #1a1a2e;
    border: 1px solid #444;
    border-radius: 6px;
    color: #e0e0e0;
    padding: 10px 14px;
    font-size: 14px;
    font-family: inherit;
    outline: none;
  }
  .dispatch-bar input:focus { border-color: #bd93f9; }
  .dispatch-bar input::placeholder { color: #555; }
  .dispatch-bar button {
    background: #bd93f922;
    border: 1px solid #bd93f944;
    color: #bd93f9;
    border-radius: 6px;
    padding: 10px 20px;
    font-size: 14px;
    cursor: pointer;
    font-family: inherit;
    font-weight: 600;
  }
  .dispatch-bar button:hover { background: #bd93f933; }
  .toast {
    position: fixed;
    bottom: 20px;
    right: 20px;
    background: #333;
    color: #e0e0e0;
    padding: 10px 16px;
    border-radius: 6px;
    font-size: 13px;
    opacity: 0;
    transition: opacity 0.3s;
    pointer-events: none;
  }
  .toast.show { opacity: 1; }
  .toast.ok { border-left: 3px solid #50fa7b; }
  .toast.err { border-left: 3px solid #ff5555; }
  .empty {
    text-align: center;
    color: #666;
    padding: 60px 20px;
    font-size: 15px;
  }
  .confirm-overlay {
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.6);
    display: flex; align-items: center; justify-content: center;
    z-index: 100;
  }
  .confirm-box {
    background: #16213e;
    border: 1px solid #444;
    border-radius: 8px;
    padding: 24px;
    min-width: 320px;
    text-align: center;
  }
  .confirm-box p { margin-bottom: 16px; font-size: 14px; }
  .confirm-box .btns { display: flex; gap: 12px; justify-content: center; }
  .group-header {
    font-size: 13px;
    font-weight: 600;
    color: #8be9fd;
    padding: 8px 0 6px 2px;
    margin-top: 12px;
    border-bottom: 1px solid #333;
    margin-bottom: 10px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .group-header:first-child { margin-top: 0; }
  .group-header .count { font-weight: 400; color: #666; font-size: 12px; }
  .view-toggle {
    display: flex;
    gap: 0;
    margin-left: 12px;
  }
  .view-toggle button {
    padding: 4px 10px;
    font-size: 11px;
    cursor: pointer;
    border: 1px solid #444;
    background: transparent;
    color: #666;
    font-family: inherit;
  }
  .view-toggle button:first-child { border-radius: 4px 0 0 4px; }
  .view-toggle button:last-child { border-radius: 0 4px 4px 0; }
  .view-toggle button.active { background: #8be9fd22; color: #8be9fd; border-color: #8be9fd44; }
</style>
</head>
<body>
<header>
  <h1>ccctl dashboard</h1>
  <div class="header-right">
    <div class="stats" id="stats"></div>
  </div>
</header>

<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
  <div class="tabs">
    <button class="tab active" onclick="switchTab('live')">Live Sessions</button>
    <button class="tab" onclick="switchTab('history')">History</button>
  </div>
  <div class="view-toggle" id="view-toggle">
    <button class="active" onclick="setView('flat')">Flat</button>
    <button onclick="setView('group')">By Project</button>
    <button onclick="setView('status')">By Status</button>
  </div>
</div>

<div id="dispatch-bar" class="dispatch-bar">
  <div class="label" id="dispatch-label"></div>
  <div class="row">
    <input id="dispatch-input" placeholder="Send instruction to coordinator...">
    <button onclick="sendToCoordinator()">Dispatch</button>
  </div>
</div>

<div id="search-row" class="search-row" style="display:none">
  <input id="search" placeholder="Search sessions by name, project, or content..."
    oninput="debounceSearch()">
</div>

<div class="cards" id="cards"></div>
<div class="toast" id="toast"></div>

<script>
let sessions = [];
let history = [];
let termStatus = {};
let currentTab = "live";
let viewMode = "flat";
let searchTimer = null;
let _composing = false;
document.addEventListener("compositionstart", () => _composing = true);
document.addEventListener("compositionend", () => _composing = false);
function onEnter(event, fn) { if (event.key === "Enter" && !_composing) fn(); }

async function refresh() {
  try {
    const r = await fetch("/api/ps");
    sessions = await r.json();
    if (currentTab === "live") render();
  } catch (e) {}
}

async function refreshStatus() {
  try {
    const r = await fetch("/api/status");
    termStatus = await r.json();
    if (currentTab === "live") render();
  } catch (e) {}
}

async function searchHistory() {
  const q = document.getElementById("search").value.trim();
  try {
    const r = await fetch("/api/history?q=" + encodeURIComponent(q));
    history = await r.json();
    if (currentTab === "history") render();
  } catch (e) {}
}

function debounceSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(searchHistory, 300);
}

function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll(".tab").forEach((el, i) => {
    el.classList.toggle("active", (i === 0 && tab === "live") || (i === 1 && tab === "history"));
  });
  document.getElementById("search-row").style.display = tab === "history" ? "flex" : "none";
  document.getElementById("view-toggle").style.display = tab === "live" ? "flex" : "none";
  if (tab === "history" && !history.length) searchHistory();
  render();
}

function setView(mode) {
  viewMode = mode;
  document.querySelectorAll(".view-toggle button").forEach((el, i) => {
    el.classList.toggle("active", (i === 0 && mode === "flat") || (i === 1 && mode === "group") || (i === 2 && mode === "status"));
  });
  render();
}

function saveInputs() {
  const saved = {};
  document.querySelectorAll(".card input").forEach(el => {
    if (el.value) saved[el.id] = el.value;
  });
  const di = document.getElementById("dispatch-input");
  if (di && di.value) saved["dispatch-input"] = di.value;
  return saved;
}

function restoreInputs(saved) {
  for (const [id, val] of Object.entries(saved)) {
    const el = document.getElementById(id);
    if (el) el.value = val;
  }
}

function render() {
  // Skip render if user is typing in a card input (not dispatch bar)
  const active = document.activeElement;
  if (active && active.tagName === "INPUT" && active.id !== "dispatch-input") return;
  const saved = saveInputs();
  if (currentTab === "live") renderLive();
  else renderHistory();
  restoreInputs(saved);
}

function renderCard(s, i) {
  const coordClass = s.is_coordinator ? " coordinator" : "";
  const pinLabel = s.is_coordinator ? "Unpin" : "Pin";
  const pinClass = s.is_coordinator ? " active" : "";
  const ts = termStatus[String(s.pid)] || {};
  const ready = ts.ready;
  const preview = ts.preview || "";
  const readyDot = ready ? '<span style="color:#50fa7b" title="Ready for input">\\u25cf</span>' : '<span style="color:#ffb86c" title="Working...">\\u25cb</span>';
  const previewHtml = preview ? '<div class="preview">' + esc(preview) + '</div>' : '';
  return `
    <div class="card ${s.status}${coordClass}" onclick="doFocus('${esc(s.name)}', event)">
      <div class="top">
        <span class="name">${readyDot} ${esc(s.name)} <span class="rename-btn" onclick="event.stopPropagation();startRename('${s.session_id}','${esc(s.name)}',this.parentElement)" title="Rename">&#9998;</span></span>
        <span class="badge ${s.status}">${s.status} \\u00b7 ${esc(s.last_active_ago)}</span>
      </div>
      <div class="meta">${esc(s.project)} \\u00b7 PID ${s.pid}</div>
      <div class="last-input">${esc(s.last_input || "-")}</div>
      ${previewHtml}
      <div class="actions" onclick="event.stopPropagation()">
        <input id="p${i}" placeholder="send prompt..." onkeyup="onEnter(event, ()=>sendPrompt('${esc(s.name)}',${i}))">
        <button class="btn" onclick="sendPrompt('${esc(s.name)}',${i})">Send</button>
        <button class="btn pin${pinClass}" onclick="togglePin('${s.session_id}')">${pinLabel}</button>
        <button class="btn danger" onclick="confirmStop('${esc(s.name)}',${s.pid})">Stop</button>
      </div>
    </div>`;
}

function renderLive() {
  const el = document.getElementById("cards");
  const stats = document.getElementById("stats");
  const bar = document.getElementById("dispatch-bar");
  const label = document.getElementById("dispatch-label");

  if (!sessions.length) {
    el.innerHTML = '<div class="empty">No live sessions</div>';
    stats.textContent = "";
    bar.classList.remove("visible");
    return;
  }

  const coord = sessions.find(s => s.is_coordinator);
  if (coord) {
    bar.classList.add("visible");
    label.textContent = "\\u2605 Coordinator: " + coord.name;
  } else {
    bar.classList.remove("visible");
  }

  const counts = {};
  sessions.forEach(s => counts[s.status] = (counts[s.status] || 0) + 1);
  const parts = [sessions.length + " live"];
  for (const k of ["active", "idle", "stale"]) {
    if (counts[k]) parts.push(counts[k] + " " + k);
  }
  stats.textContent = parts.join("  \\u00b7  ");

  // Sort: coordinator first, then by last_active
  const sorted = [...sessions].sort((a, b) => {
    if (a.is_coordinator !== b.is_coordinator) return a.is_coordinator ? -1 : 1;
    return (b.last_active || 0) - (a.last_active || 0);
  });

  if (viewMode === "flat") {
    el.className = "cards";
    el.innerHTML = sorted.map((s, i) => renderCard(s, i)).join("");
  } else {
    el.className = "";
    const key = viewMode === "group" ? "project" : "status";
    const groups = {};
    sorted.forEach((s, i) => {
      const k = s[key] || "other";
      if (!groups[k]) groups[k] = [];
      groups[k].push({s, i});
    });
    const sortedGroups = Object.entries(groups).sort((a, b) => {
      const aMax = Math.max(...a[1].map(x => x.s.last_active || 0));
      const bMax = Math.max(...b[1].map(x => x.s.last_active || 0));
      return bMax - aMax;
    });
    let html = "";
    for (const [groupName, items] of sortedGroups) {
      html += '<div class="group-header"><span>' + esc(groupName) + '</span><span class="count">' + items.length + '</span></div>';
      html += '<div class="cards">' + items.map(({s, i}) => renderCard(s, i)).join("") + '</div>';
    }
    el.innerHTML = html;
  }
}

function renderHistory() {
  const el = document.getElementById("cards");
  const stats = document.getElementById("stats");
  stats.textContent = history.length ? history.length + " results" : "";

  if (!history.length) {
    el.innerHTML = '<div class="empty">No history sessions found</div>';
    return;
  }

  el.innerHTML = history.map(s => {
    const hasName = s.name && !/^[0-9a-f]{8}$/.test(s.name);
    const title = hasName ? s.name : (s.first_input || s.last_input || s.session_id.slice(0,12));
    const subtitle = hasName
      ? (s.first_input || s.last_input || "")
      : "";
    return `
    <div class="card dead">
      <div class="top">
        <span class="name">${esc(title.slice(0,60))}${title.length > 60 ? "\\u2026" : ""}</span>
        <span class="badge dead">${esc(s.last_active_ago)} ago \\u00b7 ${s.msg_count} msgs</span>
      </div>
      <div class="meta">${esc(s.project)}${hasName ? "" : " \\u00b7 " + s.session_id.slice(0,8)}</div>
      ${subtitle ? '<div class="last-input">' + esc(subtitle.slice(0,80)) + '</div>' : ''}
      <div class="actions" onclick="event.stopPropagation()">
        <button class="btn resume" onclick="doResume('${s.session_id}','${esc(s.name)}')">Resume</button>
      </div>
    </div>`;
  }).join("");
}

function esc(s) {
  if (!s) return "";
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML.replace(/'/g, "&#39;");
}

async function doFocus(name, event) {
  if (event.target.tagName === "INPUT" || event.target.tagName === "BUTTON") return;
  const r = await fetch("/api/focus", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({target: name}),
  });
  const res = await r.json();
  if (res.ok) window.blur();
  toast(res.ok ? "Focused \\u2192 " + name : (res.error || "Failed"), res.ok);
}

async function sendPrompt(name, idx) {
  const input = document.getElementById("p" + idx);
  const prompt = input.value.trim();
  if (!prompt) return;
  const r = await fetch("/api/focus", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({target: name, prompt: prompt}),
  });
  const res = await r.json();
  if (res.ok && res.prompt_sent !== false) {
    toast("Sent to " + name, true);
    input.value = "";
  } else {
    toast(res.prompt_error || res.error || "Failed", false);
  }
}

function confirmStop(name, pid) {
  const overlay = document.createElement("div");
  overlay.className = "confirm-overlay";
  overlay.innerHTML = `
    <div class="confirm-box">
      <p>Stop <strong>${esc(name)}</strong> (PID ${pid})?</p>
      <p style="font-size:12px;color:#888">Session data preserved. Can resume later.</p>
      <div class="btns">
        <button class="btn" onclick="this.closest('.confirm-overlay').remove()">Cancel</button>
        <button class="btn danger" onclick="doStop('${esc(name)}',this)">Stop</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });
}

async function doStop(name, btn) {
  btn.closest(".confirm-overlay").remove();
  const r = await fetch("/api/stop", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({target: name}),
  });
  const res = await r.json();
  toast(res.ok ? "Stopped " + name : (res.error || "Failed"), res.ok);
  if (res.ok) setTimeout(refresh, 500);
}

async function togglePin(sid) {
  const r = await fetch("/api/pin", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({session_id: sid}),
  });
  const res = await r.json();
  toast(res.ok ? (res.pinned ? "Pinned \\u2014 init sent" : "Unpinned") : "Failed", res.ok);
  if (res.ok) await refresh();
}

async function sendToCoordinator() {
  const input = document.getElementById("dispatch-input");
  const prompt = input.value.trim();
  if (!prompt) return;
  const coord = sessions.find(s => s.is_coordinator);
  if (!coord) { toast("No coordinator pinned", false); return; }
  const r = await fetch("/api/send", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({target: coord.name, prompt: prompt, coordinator: true}),
  });
  const res = await r.json();
  if (res.ok) {
    toast("Dispatched to " + coord.name, true);
    input.value = "";
  } else {
    toast(res.error || "Failed", false);
  }
}

function startRename(sid, oldName, el) {
  const input = document.createElement("input");
  input.className = "rename-input";
  input.value = oldName;
  el.innerHTML = "";
  el.appendChild(input);
  input.focus();
  input.select();
  const finish = async () => {
    const newName = input.value.trim();
    if (newName && newName !== oldName) {
      const r = await fetch("/api/rename", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({session_id: sid, name: newName}),
      });
      const res = await r.json();
      toast(res.ok ? "Renamed \\u2192 " + newName + (res.synced ? " (synced)" : "") : (res.error || "Failed"), res.ok);
    }
    refresh();
  };
  input.addEventListener("blur", finish);
  input.addEventListener("keydown", e => {
    if (e.key === "Enter") input.blur();
    if (e.key === "Escape") { input.value = oldName; input.blur(); }
  });
}

async function doResume(sid, name) {
  const r = await fetch("/api/resume", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({session_id: sid}),
  });
  const res = await r.json();
  toast(res.ok ? "Resumed " + name : (res.error || "Failed"), res.ok);
}

function toast(msg, ok) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = "toast show " + (ok ? "ok" : "err");
  setTimeout(() => el.className = "toast", 2000);
}

refresh();
refreshStatus();
setInterval(refresh, 5000);
setInterval(refreshStatus, 5000);
</script>
</body>
</html>'''


def run(args):
    global _claude_dir
    _claude_dir = args.claude_dir

    port = getattr(args, "port", 8420)
    server = HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"ccctl dashboard \u2192 {url}")
    print("Ctrl+C to stop")
    webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()
