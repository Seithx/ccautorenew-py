# TODO - CCAutoRenew

## Status Key: [ ] pending | [x] done | [-] skipped

---

## Phase 0: Setup
- [x] `py -m pip install plyer` (v2.1.0 installed)
- [x] Verify ccusage JSON schema (`blocks[0].endTime`)
- [x] Inspect real `.jsonl` session files for timestamp format

## Phase 1: Notifications (notify.py)
- [x] `notify()` wrapper: plyer, try/except, never crash
- [x] `enabled` flag for `--no-notify`

## Phase 2: Session Module (session.py)
- [x] `get_claude_data_dirs()`, `get_latest_session_id()`, `get_project_sessions()`
- [x] Project slug: `C:\path` -> `C--path`

## Phase 3: Core Daemon (daemon.py)
- [x] Config, logging, PID file, signal handling
- [x] `query_ccusage()` -> returns block dict; fallback ccusage -> npx
- [x] `get_minutes_until_reset()` -> minutes from block endTime
- [x] `is_block_exhausted()` -> detects rate-limit vs natural expiry
- [x] `should_renew()` -> only renews on exhaustion or <=2 min left
- [x] `start_claude_session()` -> --continue / -r <uuid> / -p <msg> decision tree
- [x] `main_loop()` -> monitor, renew, sleep; next-day scheduling
- [x] Clock-based fallback when ccusage unavailable
- [x] Auto-install ccusage via `npm install -g` on first `start`

## Phase 4: Manager CLI (manager.py)
- [x] Subcommands: start, stop, restart, status, logs, dash
- [x] All flags: --at, --stop, --resume, --message, --disable-ccusage, --no-notify, --cwd
- [x] Detached process launch with PID verification

## Phase 5: Bash .sh Fixes
- [x] ASCII-safe progress bars, portable date helpers, Windows warnings

## Phase 6: Testing
### Automated (test_daemon.py) -- 41/41 passing
- [x] Sleep duration thresholds, should_renew logic, clock fallback
- [x] Session ID scanning, JSONL parsing, project slugs, Windows paths
- [x] ccusage JSON parsing (endTime, usageLimitResetTime, empty blocks)
- [x] Block exhaustion detection (early end, tokenLimitStatus, natural end)

### Manual smoke tests
- [x] start/stop/restart/status cycle
- [x] `--at HH:MM` scheduled start
- [x] `--resume` session resume chain
- [x] Dashboard renders without encoding errors
- [x] Notification toast appears in external terminal
- [ ] Next-day scheduling: `--stop HH:MM`, wait for expiry, check "Advanced schedule to tomorrow"
- [ ] Actual renewal: `--resume --disable-ccusage` from external terminal, verify claude session starts

---

## Bugs fixed during smoke testing
- [x] `os.kill(pid, 0)` -> `tasklist` for detached process checks
- [x] `.cmd` resolution -> `_find_cmd()` via `shutil.which()`
- [x] Missing `signal` import in manager.py
- [x] `CTRL_BREAK_EVENT` -> `taskkill /F` for detached stops
- [x] `FileNotFoundError` uncaught in subprocess calls -> added `except OSError`
- [x] Renewal triggered on natural block expiry -> added `is_block_exhausted()` detection

---

## Phase 7: System Tray Icon (tray.py)
- [x] `tray.py`: pystray icon with Pillow-generated colored circles (green/yellow/red)
- [x] Tooltip: stopped / waiting / running with remaining time
- [x] Right-click menu: Start/Stop/Restart, status info, Open Logs, Exit Tray
- [x] Background polling thread (10s interval) updates icon color + tooltip
- [x] `manager.py tray` subcommand launches tray as detached process
- [x] `requirements.txt` updated: pystray>=0.19.5, Pillow>=10.0.0
- [ ] Manual smoke: tray icon appears, start/stop works, logs open, exit works

---

## Future (v2)
- [ ] PyInstaller packaging: single .exe
- [ ] Daemon resilience: Windows Service / Task Scheduler
- [ ] Fork as Windows-focused variant
