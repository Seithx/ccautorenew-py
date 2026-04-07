# RESEARCH - CCAutoRenew Ecosystem

Reference notes from GitHub alternatives research (Feb 2026).

For each repo: what problem of ours it might solve, what we found, and open questions.

---

## Ralph -- https://github.com/snarktank/ralph

**Our problem it might solve**: How to know when a resumed session is "done" working, so we don't leave orphaned CMD windows.

**What we found**: Ralph runs Claude in a loop (up to N times). After each run, it checks if Claude printed a special phrase (`<promise>COMPLETE</promise>`). If yes, stop. If no, run again. That's the entire logic -- much simpler than the PDF claimed.

**Open questions**: None. We got what we needed. The "completion signal" idea is nice-to-have but not a priority.

---

## Auto-Claude -- https://github.com/AndyMik90/Auto-Claude

**Our problem it might solve**: If we ever run multiple sessions in parallel, how to keep them from stepping on each other's files.

**What we found**: Auto-Claude gives each task its own copy of the project folder using git worktrees (like a temporary branch with its own directory). It handles cleanup, detects orphaned worktrees, and even knows which dependency folders to share vs recreate per language. It also has a Kanban board that limits how many tasks run at once (1-10).

**Open questions**: None right now. This is v2 material -- only relevant if we move beyond "just resume sessions" into "run multiple agents at once."

---

## ClaudeNightsWatch -- https://github.com/DantonGodworthy/ClaudeNightsWatch

**Our problem it might solve**: Instead of always saying "continue working" when resuming, could we send smarter prompts?

**What we found**: ClaudeNightsWatch lets users write two files: `task.md` (what to do) and `rules.md` (safety rules like "don't delete files"). Before each run, it glues them together into one prompt. It also logs every prompt and response for auditing. Its scheduling (start times, adaptive polling intervals) is the same pattern we already use.

**Open questions**:
- [ ] Is "continue working" actually causing problems? If sessions resume fine with it, this is over-engineering.
- [ ] If we do want smarter prompts: should users write a `resume-rules.md` file, or should we auto-generate context (e.g. last git diff)?

---

## coding_agent_session_search -- https://github.com/Dicklesworthstone/coding_agent_session_search

**Our problem it might solve**: We have stale sessions -- JSONL log files exist on disk, but Claude can't actually resume them ("No conversation found"). We waste time trying to resume dead sessions.

**What we found**:
- It builds a real index (SQLite + Tantivy full-text search), not just scanning files each time.
- It reads all JSONL files under `~/.claude/projects/`, extracts session ID, working directory, git branch, and message timestamps from each line.
- It skips files that haven't changed since last scan (checks file modification time), so re-indexing is fast.
- It normalizes each session into: start time, end time, message count, file path, session ID.
- It **cannot** check if Claude can actually resume a session -- it only sees the log files, not Claude's internal state.

**What we can borrow** (no need to install the tool -- just reuse the logic):
- Skip JSONL files older than X days (check file modification time before parsing).
- Require at least 1 assistant message (sessions with only user messages are likely dead/incomplete).
- Prefer sessions with recent end timestamps or recent file modification.
- Sessions with very few messages (e.g. < 2) are likely dead -- deprioritize them.

**Open questions**: All answered. We don't need the tool itself, just its filtering heuristics applied to our `get_active_sessions()`.

---

## viwo -- https://github.com/OverseedAI/viwo

**Our problem it might solve**: If we ever want to run Claude unattended (overnight), how to do it safely.

**What we found**: viwo runs each Claude session inside a Docker container. The container can only see one project folder (mounted at /workspace). There's no fancy permission system inside -- Docker isolation IS the security. It tracks session state (starting, running, done, error, cleaned up) and can recover if the daemon crashes by checking what containers are actually running vs what it expected.

**Open questions**: None right now. Docker sandboxing is v2 at best. The "check actual state vs expected state on startup" idea is useful if our daemon ever needs crash recovery.

---

## macro-commander -- https://github.com/jeff-hykin/macro-commander

**Our problem it solves**: When Claude hits a rate limit in a VS Code terminal, it shows a prompt and waits for user input. Our current workaround opens separate CMD windows. Ideally, we'd dismiss the prompt and continue in the original VS Code terminal.

**What we found**: macro-commander is a VS Code extension that can type into terminals programmatically. We can: press Esc (dismiss prompt), then type `/continue` + Enter. For multiple terminals, we loop through them one by one using `focusAtIndex`. Our daemon can trigger it via `code --execute-command macros.{macroName}`.

**Security analysis**:
- Extension uses `eval()` for JS macros and `execSync` for shell macros -- both are full code execution.
- Attack vector: malicious `.vscode/settings.json` in a cloned repo could inject macros.
- Zero npm dependencies (no supply chain risk).

**Safety rules for our use**:
1. Macros go in **User** settings.json only (never workspace settings).
2. Use only `command` + `args` actions -- no `javascript`, `javascriptPath`, or `hiddenConsole`.
3. Extra `focusAtIndex` calls to non-existent terminals fail silently (safe to over-count).

**Safe macro (no eval, handles up to 6 terminals)**:
Add to User settings.json under `"macros"`:
```json
"macros": {
  "dismissRateLimitAll": [
    { "command": "workbench.action.terminal.focusAtIndex", "args": { "index": 0 } },
    { "command": "workbench.action.terminal.sendSequence", "args": { "text": "\u001b" } },
    { "command": "workbench.action.terminal.sendSequence", "args": { "text": "/continue\n" } },
    { "command": "workbench.action.terminal.focusAtIndex", "args": { "index": 1 } },
    { "command": "workbench.action.terminal.sendSequence", "args": { "text": "\u001b" } },
    { "command": "workbench.action.terminal.sendSequence", "args": { "text": "/continue\n" } },
    { "command": "workbench.action.terminal.focusAtIndex", "args": { "index": 2 } },
    { "command": "workbench.action.terminal.sendSequence", "args": { "text": "\u001b" } },
    { "command": "workbench.action.terminal.sendSequence", "args": { "text": "/continue\n" } },
    { "command": "workbench.action.terminal.focusAtIndex", "args": { "index": 3 } },
    { "command": "workbench.action.terminal.sendSequence", "args": { "text": "\u001b" } },
    { "command": "workbench.action.terminal.sendSequence", "args": { "text": "/continue\n" } },
    { "command": "workbench.action.terminal.focusAtIndex", "args": { "index": 4 } },
    { "command": "workbench.action.terminal.sendSequence", "args": { "text": "\u001b" } },
    { "command": "workbench.action.terminal.sendSequence", "args": { "text": "/continue\n" } },
    { "command": "workbench.action.terminal.focusAtIndex", "args": { "index": 5 } },
    { "command": "workbench.action.terminal.sendSequence", "args": { "text": "\u001b" } },
    { "command": "workbench.action.terminal.sendSequence", "args": { "text": "/continue\n" } }
  ]
}
```

**How daemon triggers it**: `code --execute-command macros.dismissRateLimitAll`

**Install**: `code --install-extension jeff-hykin.macro-commander`

**Limitations**:
- Can only type into the terminal that's currently focused (must switch between them in a loop)
- Can't read what's on screen -- so it can't detect whether the prompt is actually showing
- Only works inside VS Code, not external CMD windows
- Needs VS Code to be running

**Open questions**:
- [ ] What happens if we send Esc + /continue to a terminal that's NOT showing the rate limit prompt? Does Claude just ignore it, or does it cause issues?

**Testing in isolated VS Code** (see below).

---

## macro-commander: Isolated Test Plan

Use a **VS Code Profile** to test without affecting your main setup.

**Setup** (from any terminal):
```
code --install-extension jeff-hykin.macro-commander --profile "macro-test"
code --profile "macro-test"
```
This opens a clean VS Code with only macro-commander installed. Your main VS Code extensions/settings are untouched.

**Add the macro**: In the profile's VS Code, open Settings JSON (Ctrl+Shift+P > "Preferences: Open User Settings (JSON)") and paste the `"macros"` block from above.

**Test 1 -- Basic sendSequence**:
1. Open a terminal in the profile VS Code (Ctrl+`)
2. Open Command Palette (Ctrl+Shift+P), type "Run Macro", pick `dismissRateLimitAll`
3. Expected: terminal receives Esc + `/continue` + Enter. You'll see `/continue` typed into the shell prompt.

**Test 2 -- Multi-terminal**:
1. Open 3 terminals (Ctrl+Shift+`)
2. Run the macro
3. Expected: each terminal gets `/continue` typed into it. Watch them switch focus one by one.

**Test 3 -- CLI trigger from external terminal**:
1. Keep the profile VS Code open with terminals
2. From a separate CMD/PowerShell: `code --execute-command macros.dismissRateLimitAll`
3. Expected: same result as Test 2, but triggered externally (this is how our daemon would call it)

**Test 4 -- With Claude rate limit prompt** (when it happens naturally):
1. Have Claude running in the profile VS Code terminal
2. Hit rate limit, see `/rate-limit-options` prompt
3. Run macro from external terminal
4. Expected: Esc dismisses prompt, `/continue` resumes the session

**Cleanup**: `code --uninstall-extension jeff-hykin.macro-commander --profile "macro-test"` or just delete the profile from VS Code settings.

**Key question to answer**: Does sending Esc + `/continue` to a terminal that's NOT rate-limited cause any harm? Test by running the macro when Claude is actively working (not at a prompt).
