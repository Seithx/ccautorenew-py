# ccautorenew-py

Windows-first Python daemon that auto-renews Claude Code 5-hour usage blocks. Detects rate limits passively from JSONL logs and bulk-renews all active sessions on reset.

> **Separate project**, inspired by [aniketkarne/CCAutoRenew](https://github.com/aniketkarne/CCAutoRenew) (MIT). The original is a set of bash scripts for Linux/macOS. This is a ground-up Python reimplementation with a different detection model, multi-session support, and Windows-native integration. Not a drop-in replacement -- see [Relationship to the original](#relationship-to-the-original).

## Status

Active development. Windows-tested. Current version: **0.3** (multi-session bulk renewal + passive JSONL detection).

- 68 automated tests pass; several manual smoke tests still pending (see [What's wired vs planned](#whats-wired-vs-planned))
- Known gaps tracked in [issues](https://github.com/Seithx/ccautorenew-py/issues)

## The Problem

Claude Code uses 5-hour usage blocks. When you hit a rate limit, every active session is blocked until the timer resets. ccautorenew-py watches your sessions, reads the reset time directly from Claude's own logs, and renews every session the moment the block lifts -- so you don't lose hours to a browser tab you forgot to reopen.

## Quick Start

```
git clone https://github.com/Seithx/ccautorenew-py.git
cd ccautorenew-py
pip install -r requirements.txt
py manager.py start --at 09:00 --stop 17:00
```

## Commands

```
py manager.py start [options]   Start the background daemon
py manager.py stop              Stop the daemon
py manager.py restart [options] Restart with new options
py manager.py status            Show daemon state and timing
py manager.py logs [-f]         Show logs (optionally follow)
py manager.py dash              Live dashboard with progress bar
py manager.py tray              Launch system tray icon
```

## Start Options

| Flag | Effect |
|------|--------|
| `--at HH:MM` | Start monitoring at this time (skips if past) |
| `--stop HH:MM` | Stop monitoring at this time |
| `--disable-ccusage` | Use clock-based timing only |
| `--no-notify` | Suppress desktop notifications |
| `--cwd /path` | Set working directory for Claude sessions |

> `--resume` and `--message "text"` are parsed but not used by the current daemon (tracked in [issue #9](https://github.com/Seithx/ccautorenew-py/issues/9)). Resume is always attempted first (the renewal chain is `--resume <id>` -> `--continue`), and the renewal prompt is hardcoded to `"continue working"` for now.

## Why ccautorenew-py

Most renewal scripts use a simple timer and restart a single session. This project takes a different approach:

- **Multi-session bulk renewal** -- detects and renews every active session across every project, not just the one you were last using
- **Passive detection** -- reads rate limit timestamps directly from Claude's JSONL logs; no fragile text parsing of CLI output, no wasted API calls
- **In-place VS Code resume** -- sessions resume in their original terminals with full context and permissions intact; no orphaned windows
- **Weekly limit awareness** -- distinguishes 5-hour blocks from weekly limits; notifies without wasting renewal attempts on limits that can't be bypassed
- **Windows-native** -- `SendInput`, `tasklist`, `taskkill`, detached processes; no WSL or cygwin required
- **System tray** -- colored status icon with start/stop/logs menu

## How It Works

The daemon uses a two-layer detection system:

### Layer 1: Passive rate limit detection (JSONL scanning)

1. Scans Claude's JSONL session files for `"error":"rate_limit"` entries
2. Parses the reset time from the message (e.g. "resets 2pm (Asia/Jerusalem)")
3. Differentiates 5-hour blocks from weekly limits
4. Collects all active sessions across all projects
5. Sleeps until reset time + 2 minute buffer
6. Bulk-renews every active session via `claude --resume <session_id> -p "continue working"`
7. Weekly limits: notifies only (no renewal possible until the weekly timer resets)

### Layer 2: ccusage timing (completed-block early termination)

1. Polls [ccusage](https://github.com/ryoppippi/ccusage) for block state
2. If a **completed** block's `actualEndTime` is >5 min before its scheduled `endTime` (early-terminated by rate limit), waits 60s for cleanup, then renews the latest session
3. Falls back to clock-based timing (5h from last activity) if ccusage is unavailable

> Note: active blocks approaching their 5-hour boundary are **not** handled by Layer 2 anymore. Layer 1's JSONL scan catches those via the rate-limit entry Claude itself logs, which is more reliable than inferring from time remaining.

### Renewal strategy

- **Current (daemon)**: for each active session, opens a CMD window running `claude --resume <session_id> -p "continue working"`. If all resumes fail: `claude --continue -p "continue working"` as last resort.
- **VS Code mode (planned, manual for now)**: `vscode_trigger.py` sends `Ctrl+Alt+Shift+R` to all VS Code windows via Win32 `SendInput`, which fires a [macro-commander](https://github.com/jeff-hykin/macro-commander) macro that sends `/continue` to every terminal. Works standalone today; daemon integration tracked in [issue #6](https://github.com/Seithx/ccautorenew-py/issues/6).
- Claude has full conversation context when resuming
- Polling: capped at 2 min so the JSONL rate-limit scan runs frequently (base cadence is 10/2/0.5 min by time remaining, but capped to 2 min in the main loop)

### Scheduling

- At stop time, automatically advances schedule to tomorrow (+24h)
- Within 10 minutes of stop time, skips new renewals to avoid wasting a block
- Retry cap: 5 consecutive failures triggers a 30-minute cooldown

## VS Code Integration (macro-commander)

**Status**: standalone trigger works today; daemon integration pending ([issue #6](https://github.com/Seithx/ccautorenew-py/issues/6)). The daemon currently opens CMD windows on renewal regardless of whether VS Code is running.

One-time setup:
1. Install: `code --install-extension jeff-hykin.macro-commander`
2. Add the `dismissRateLimitAll` macro to VS Code User Settings JSON (see RESEARCH.md)
3. Add keybinding: `Ctrl+Alt+Shift+R` -> `macros.dismissRateLimitAll`

Manual trigger: `py vscode_trigger.py` or press `Ctrl+Alt+Shift+R` in VS Code. Both fire the macro, which sends `/continue` to every open VS Code terminal.

## System Tray

- Green = running, Yellow = waiting, Red = stopped
- Right-click menu: Start/Stop/Restart, status info, Open Logs, Exit

## What's Wired vs Planned

### Covered by automated tests (68 pytest tests)
- JSONL scanning and rate-limit message parsing (weekly + 5h variants)
- Active session discovery across all projects
- ccusage block queries and fallback logic
- Scheduling math (stop time, next-day advance)
- Renewal command construction, env cleaning, retry caps

### Manually verified on Windows
- `start` / `stop` / `restart` / `status` / `logs` / `dash` commands
- Tray icon with colored status circles
- `vscode_trigger.py` as a standalone trigger (sends chord, macro fires)

### Pending manual verification ([issue #1](https://github.com/Seithx/ccautorenew-py/issues/1))
- Next-day reschedule after `--stop` cutoff
- Live renewal from an external terminal
- JSONL rate-limit scan fire-through in logs

### Known gaps and quirks
- **VS Code bulk integration not yet wired into the daemon** ([issue #6](https://github.com/Seithx/ccautorenew-py/issues/6)). Manual trigger works; daemon still opens CMD windows.
- Bulk renewal may resume a session you're actively typing in ([issue #2](https://github.com/Seithx/ccautorenew-py/issues/2))
- Stale session IDs sometimes return "No conversation found" -- `--continue` fallback handles it ([issue #3](https://github.com/Seithx/ccautorenew-py/issues/3))
- ccusage may report "No active block" immediately after renewal; daemon falls back to clock-based timing

## Requirements

- Python 3.10+
- Claude CLI (`npm install -g @anthropic-ai/claude-code`)
- Optional: [ccusage](https://github.com/ryoppippi/ccusage) (`npm install -g ccusage`)
- Windows: `pip install -r requirements.txt` (includes plyer, pystray, Pillow, tzdata)

## Project Structure

```
manager.py          CLI entry point (start/stop/restart/status/logs/dash/tray)
daemon.py           Background monitoring loop (two-layer detection, bulk renewal)
session.py          JSONL scanner, rate limit parser, session UUID finder
notify.py           Desktop notification wrapper (plyer, never crashes)
tray.py             System tray icon (pystray, colored status circles)
vscode_trigger.py   VS Code keystroke sender (Win32 SendInput, macro-commander)
test_daemon.py      Automated tests (68 tests)
requirements.txt    Python dependencies
original/           Original bash scripts (reference, patched for Windows)
```

## Testing

```
py -m pytest test_daemon.py -v
```

## Relationship to the original

This started as a Windows-compatibility fork of [aniketkarne/CCAutoRenew](https://github.com/aniketkarne/CCAutoRenew), then grew into a separate project. The original bash scripts are preserved in `original/` for reference and credit.

**What's the same**: the goal (keep 5-hour blocks renewed), ccusage as an optional timing source, `claude --continue` as a fallback strategy.

**What's different**:

| | aniketkarne/CCAutoRenew | ccautorenew-py |
|---|:---:|:---:|
| Language | bash | Python 3.10+ |
| Platform | Linux / macOS | Windows-first (cross-platform Python) |
| Detection | Timer + ccusage polling | JSONL log scanning + ccusage |
| Sessions renewed | Single (last used) | All active across all projects |
| Rate limit handling | Reactive (on expiry) | Proactive (reads reset time from logs) |
| Weekly limit awareness | -- | Yes |
| VS Code integration | -- | macro-commander + SendInput |
| System tray | -- | Yes |
| UI | CLI + dashboard | CLI + dashboard + tray |
| Tests | -- | 68 pytest |

The two projects have diverged enough that upstreaming isn't practical -- use whichever fits your environment. Bug fixes that apply to both are welcome in both places.

## Changelog

### 0.3.0 (2026-04)
- Multi-session bulk renewal across all projects
- Passive JSONL rate-limit scanning replaces ccusage probing for exhausted blocks
- Weekly limit detection (notify-only branch)
- `_launch_visible()` opens CMD windows with banner titles
- +120s buffer after reset before renewal fires

### 0.2.0 (2026-03)
- Session-aware renewal chain: `--resume <id>` -> `--continue` -> `-p <msg>`
- `_clean_env()` strips `CLAUDECODE` to avoid nested-session block
- Retry cap: 5 consecutive failures -> 30-minute cooldown
- System tray (pystray): colored status, right-click menu, background polling
- `vscode_trigger.py`: external keystroke sender via Win32 `SendInput`

### 0.1.0 (2026-02)
- Python rewrite of the original bash scripts
- ccusage block polling with clock-based fallback
- JSONL session scanner, project slug converter
- Desktop notifications via plyer
- Manager CLI: `start` / `stop` / `restart` / `status` / `logs` / `dash`
- 68 pytest tests

## License

MIT -- same as the original. See `LICENSE`.
