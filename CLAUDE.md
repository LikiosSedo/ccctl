# ccctl — Claude Code Session & Task Control Plane

## What This Is

A local CLI that lets AI (coordinator session) and humans observe, manage, and dispatch Claude Code sessions. It is **not** a replacement for Claude Code — it is the management layer on top.

Primary user: a Claude Code "coordinator" session that calls `ccctl --json` to understand and manage all other sessions.
Secondary user: humans who glance at `ccctl ps` or `ccctl summary -v` in their terminal.

## Core Principle: Dispatch Layer, Not Data Layer

ccctl reads Claude Code's native data in real-time. It does not cache, mirror, or duplicate.

```
Claude Code owns (read-only for ccctl):     ccctl owns:
├── ~/.claude/sessions/{pid}.json (ephemeral)   └── ~/.claude/ccctl/names.json
├── ~/.claude/history.jsonl (persistent)
└── conversation data (opaque)
```

- **Live sessions**: always read from sessions/*.json + process table. Zero caching.
- **Stopped sessions**: session files disappear (Claude Code deletes on exit). ccctl recovers cwd from history.jsonl's `project` field — no snapshot store needed.
- **ccctl's only owned data**: `names.json` (session_id → human-meaningful name).
- **session_id** is the foreign key that connects everything.

## Architecture Decisions (and why)

### No `search` command
`summary --json` is search. The coordinator AI reads JSON and understands semantics ("GPU cluster test" → matches CRD/operator/SSH tunnel). A keyword search command would be strictly weaker and add maintenance burden.

### No snapshot store
We tried this, then removed it. Claude Code deletes session files on exit — we can't prevent that. Instead of caching metadata before stop, we recover cwd from history.jsonl on demand. This is simpler and follows the "read native data" principle.

### No `rm` command (for now)
`stop` (SIGTERM, preserves data) and `gc` (bulk cleanup) cover all current needs. Explicit `rm` adds destructive surface area without clear value.

### `stop` vs `kill` naming
We use `stop` not `kill`. "Stop" implies reversibility (can resume later). "Kill" implies destruction. The semantics match: stop only sends SIGTERM, data is preserved.

### resume is best-effort
Whether a conversation can actually be resumed depends on Claude Code's internal retention policy, which we don't control. ccctl provides session_id + correct cwd — the rest is up to Claude Code.

## Commands

```
Observe:   ps [-g] [--json]     List sessions (group by project)
           show <id>            Session details (messages, process, git)
           summary [-v] [--json] Full project topology and activity

Manage:    name [target] [name]  Label sessions (accepts PID, session_id prefix, or name)
           name --auto           Auto-name unnamed live sessions
           stop <target>...      SIGTERM sessions (data preserved for resume)
           gc [--dry-run]        Stop stale + remove dead session files

Dispatch:  resume <target>       Reopen session in new terminal (iTerm2/Terminal/tmux)
           new [--name] [--cwd]  Launch new session in new terminal
```

All commands accept `--json` where applicable. Default scope for `summary`: `~/project`.

## Development Rules

### Build for the actual user
The primary user is an AI coordinator. Design JSON output first, table output second. If a feature doesn't help the coordinator make better decisions, question whether it's needed.

### Don't build what `summary --json` already solves
Before adding a new command, ask: "Can the coordinator already do this by reading summary/ps/show JSON output?" If yes, don't add the command.

### Fail fast, no speculative error handling
- Python stdlib only, no dependencies
- Let errors surface — a crash with a traceback is better than silent wrong data
- Validate at boundaries (CLI args, file reads), trust internally

### Test by using
The best test is `ccctl ps`, `ccctl summary -v`, `ccctl resume <name>` in a real workflow. If something feels awkward as a user, fix it. The fastmcp-pr resume and the name-by-session-id fix both came from actual usage friction.

### Keep it small
- One file per command (`cmd_*.py`)
- Shared utilities in `sources.py` (data readers), `output.py` (formatting), `store.py` (names)
- Pure Python 3.9+ stdlib. No dependencies. No build step beyond `pip install -e .`

## Data Sources

| Source | Location | What it provides | Persistence |
|--------|----------|-----------------|-------------|
| Session files | `~/.claude/sessions/{pid}.json` | pid, sessionId, cwd, startedAt, name | Ephemeral (deleted on exit) |
| History | `~/.claude/history.jsonl` | All user messages with sessionId, timestamp, project | Persistent |
| Process table | `pgrep claude` / `kill -0` | Process liveness, memory, CPU | Real-time |
| ccctl names | `~/.claude/ccctl/names.json` | session_id → name mapping | Persistent (ours) |

## Activity Level Definitions

Used in `summary` command:

| Level | Icon | Threshold |
|-------|------|-----------|
| hot | 🔴 | Activity in last 24h |
| warm | 🟡 | Activity in last 7d |
| cool | 🔵 | Activity in last 30d |
| cold | ⚪ | No activity in 30d+ |

## Session Status Definitions

Used in `ps` command:

| Status | Icon | Meaning |
|--------|------|---------|
| active | ● | Alive, message in last 5 min |
| idle | ○ | Alive, no message in 5min–1d |
| stale | ◌ | Alive, no message in 1d+ |
| dead | ✕ | Process not running |
