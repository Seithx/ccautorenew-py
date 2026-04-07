# CCAutoRenew | Project Context

## Purpose
Auto-renew Claude Code 5h usage blocks | Python rewrite of bash original | Windows-first

## Structure
```
CCAUTORENEW/
  manager.py        | CLI entry: start/stop/restart/status/logs/dash/tray
  daemon.py         | Background loop: poll ccusage, renew sessions, sleep
  session.py        | JSONL scanner, slug converter, session UUID finder
  notify.py         | Desktop toast wrapper (plyer), never crashes caller
  tray.py           | System tray icon (pystray): status colors, menu, polling
  vscode_trigger.py | VS Code keystroke sender (Win32 SendInput + macro-commander)
  test_daemon.py    | 68 automated tests (pytest)
  requirements.txt  | plyer, pystray, Pillow
  PLAN.md           | Architecture, process flow, decision trees
  TODO.md           | Progress tracker with smoke test results
  RESEARCH.md       | Ecosystem research (Ralph, Auto-Claude, viwo, etc.)
  README.md         | User-facing docs
  original/         | Original bash .sh scripts (reference only, patched for Windows)
```

## Runtime data
Path: `~/.claude-autorenew/` | Files: config.json | daemon.pid | last_activity | daemon.log

## Key patterns
Python: py launcher | encoding='utf-8' everywhere | os.path.normpath for Windows paths
Commands: `_find_cmd(name)` resolves .cmd on Windows via shutil.which
Process: DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP for background daemon
PID check: tasklist on Windows (os.kill(pid,0) fails for detached)
Stop: taskkill /F (CTRL_BREAK_EVENT can't reach detached processes)
Slug: C:\Users\foo -> C--Users-foo (colons to dashes, slashes to dashes)
JSONL: timestamp top-level for user/assistant | snapshot.timestamp for file-history-snapshot

## ccusage
Command: `ccusage blocks --active --json` | fallback: npx ccusage@latest
JSON: blocks[0].endTime (ISO8601) | compute remaining = endTime - now
No timeRemaining field | usageLimitResetTime may or may not be present
--live removed in v18.0.0 | use --active instead

## Renewal decision tree
resume_enabled=true: 1st claude --continue | 2nd claude -r <uuid> | 3rd claude -p <msg>
resume_enabled=false: claude -p <msg> only
60s buffer before renewal (let old block expire) | 30s subprocess timeout
TimeoutExpired treated as success (session likely started)

## VS Code integration (macro-commander)
Extension: jeff-hykin.macro-commander | Keybinding: Ctrl+Alt+Shift+R
Macro: `dismissRateLimitAll` sends Esc + `/continue` to all terminals via `focusNext` + `sendSequence`
vscode_trigger.py: finds Code.exe windows via EnumWindows, sends chord via SendInput
Daemon calls `trigger_dismiss_all()` on rate limit reset, falls back to CMD windows if no VS Code found
Safety: User settings only, command+args actions only (no javascript/hiddenConsole macros)

## Testing
Run: `py -m pytest test_daemon.py -v`
Cannot test actual `claude -p` renewal from inside a Claude Code session

## Constraints
No emojis in code | Use py launcher | rm not del | encoding-safe (no cp1252 issues)

## Token & Context Economics
- Context Preservation Top Priority | No Raw Data Dumps
- Local Processing First - Write Python/TS/PS scripts
- Precision Extraction - Use BS4/lxml for HTML/XML, json/ConvertFrom-Json for JSON, pandas/csv for Sheets, re/readline for Logs
- Minimize Stdout - Print only final metrics or pass/fail
- Format outputs in TOON (Token-Oriented Object Notation)
- Sequential Context - Read shared source files once before branching
- Bulk Operations - Process multiple files via single script and read unified report
