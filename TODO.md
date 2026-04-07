# TODO - CCAutoRenew

## Status Key: [ ] pending | [x] done | [-] skipped

---

## Completed Phases (summary)

Phases 0-10 complete. Key milestones:
- **P0-P5**: Setup, notify.py, session.py, daemon.py (two-layer detection, clock fallback), manager.py CLI, bash fixes
- **P6**: 68/68 automated tests passing
- **P7**: System tray icon (pystray, colored circles, menu, polling)
- **P8**: Passive JSONL rate limit scanning + weekly limit detection (`is_weekly` flag)
- **P9**: Session-aware renewal (resume -> continue fallback chain), retry cap, `_clean_env()`
- **P10**: Multi-session bulk renewal, `_launch_visible()` CMD windows with banner, weekly limit notify-only branch, +120s reset buffer

Bugs fixed: `tasklist` for PID checks, `.cmd` resolution via `shutil.which()`, `taskkill /F` for stops, `OSError` handling, block exhaustion detection, CMD pipe-in-title, stdout swallowing, window auto-close.

---

## Pending manual smoke tests
- [ ] Next-day scheduling: `--stop HH:MM`, wait for expiry, check "Advanced schedule to tomorrow"
- [ ] Actual renewal: `--resume --disable-ccusage` from external terminal, verify claude session starts
- [ ] Tray icon: appears, start/stop works, logs open, exit works
- [ ] JSONL scan: restart daemon, confirm "No rate limit found" + ccusage sleep in logs
- [ ] Rate limit: hit rate limit, verify "Rate limited, resets at ..." in logs

## Open questions
- [ ] Bulk renewal may resume a session the user is actively typing in. Options: skip very-recent-activity sessions, or let user configure which to auto-resume.

## Pending
- [ ] Fork repo to Seithx/CCAutoRenew and push (no write access to aniketkarne/CCAutoRenew)

## Observed Issues (2026-02-24 live test)
- [ ] ccusage returns empty blocks right after renewal -- may need delay/retry before querying
- [ ] npx ccusage fallback broken (`EUNSUPPORTEDPROTOCOL` on `runtime:^24.11.0`)
- [ ] Stale session IDs: JSONL persists longer than Claude's resumable data. `--resume` returns "No conversation found". Accept silent failures + `--continue` fallback, or validate liveness first.
- [ ] `--resume` on active session requires Enter press (not an issue post-rate-limit, sessions are idle)

## Notification Improvements
- [ ] Severity titles: "Rate Limited (5h)" vs "WEEKLY LIMIT HIT"
- [ ] Include time remaining: "resets in 2h 15m (3:00pm)"
- [ ] Renewal summary: list project slugs succeeded/failed
- [ ] Longer timeout for weekly limit notifications

---

## Next improvements
- [ ] Filter stale sessions before resume: in `get_active_sessions()`, skip sessions with no assistant messages, very few messages (<2), or old file mtime. Reduces wasted `--resume` attempts on dead sessions. See RESEARCH.md (coding_agent_session_search) for heuristics.
- [ ] VS Code terminal automation (macro-commander):
  - [x] Extension installed, macro works inside VS Code (`focusNext` + `sendSequence`)
  - [x] Confirmed: sends `/continue` to all open terminals
  - [x] Keybinding set: `Ctrl+Alt+Shift+R` -> `macros.dismissRateLimitAll`
  - [x] `vscode_trigger.py` written: finds Code.exe via EnumWindows, sends chord via SendInput
  - [x] External trigger tested: `py vscode_trigger.py --debug` sends chord, macro fires in VS Code
  - [ ] Integrate into `_run_bulk_renewal()` as alternative to CMD windows

## Future (v2)
- [ ] PyInstaller packaging: single .exe
- [ ] Daemon resilience: Windows Service / Task Scheduler
