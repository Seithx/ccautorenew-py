"""Scan JSONL sessions for tool_use requests, classify against settings.json permissions."""

import json
import fnmatch
import re
from pathlib import Path
from collections import Counter

CLAUDE_DIR = Path("C:/Users/asafl/.claude")
PROJECTS_DIR = CLAUDE_DIR / "projects"
SETTINGS_PATH = CLAUDE_DIR / "settings.json"

# Load permission rules
settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
perms = settings.get("permissions", {})
allow_rules = perms.get("allow", [])
deny_rules = perms.get("deny", [])
ask_rules = perms.get("ask", [])


def parse_rule(rule: str):
    """Parse 'Tool(pattern)' into (tool, pattern)."""
    m = re.match(r'^(\w+)\((.+)\)$', rule)
    if m:
        return m.group(1), m.group(2)
    return None, None


def classify_tool_use(tool_name: str, command: str) -> str:
    """Return 'allow', 'deny', 'ask', or 'unmatched'."""
    for rule in deny_rules:
        rtool, rpat = parse_rule(rule)
        if rtool == tool_name and fnmatch.fnmatch(command, rpat):
            return "deny"
    for rule in allow_rules:
        rtool, rpat = parse_rule(rule)
        if rtool == tool_name and fnmatch.fnmatch(command, rpat):
            return "allow"
    for rule in ask_rules:
        rtool, rpat = parse_rule(rule)
        if rtool == tool_name and fnmatch.fnmatch(command, rpat):
            return "ask"
    return "unmatched"


# Scan all JSONL files
tool_uses = []  # (tool, command, classification, project, file)
files_scanned = 0
errors = 0

for project_dir in PROJECTS_DIR.iterdir():
    if not project_dir.is_dir():
        continue
    project = project_dir.name
    for jsonl_file in project_dir.glob("*.jsonl"):
        files_scanned += 1
        try:
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # JSONL format: type="assistant", tool data in message.content
                    if msg.get("type") != "assistant":
                        continue
                    content = msg.get("message", {}).get("content", [])
                    if isinstance(content, str):
                        continue
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") != "tool_use":
                            continue
                        tool_name = block.get("name", "")
                        inp = block.get("input", {})
                        if tool_name == "Bash":
                            cmd = inp.get("command", "")
                            classification = classify_tool_use("Bash", cmd)
                            tool_uses.append((tool_name, cmd, classification, project, jsonl_file.name))
                        elif tool_name in ("Read", "Edit", "Write"):
                            fp = inp.get("file_path", "")
                            classification = classify_tool_use(tool_name, fp)
                            tool_uses.append((tool_name, fp, classification, project, jsonl_file.name))
        except Exception as e:
            errors += 1

# Report
print(f"Files scanned: {files_scanned}")
print(f"Tool uses found: {len(tool_uses)}")
print(f"Errors: {errors}")
print()

# Count by classification
by_class = Counter(c for _, _, c, _, _ in tool_uses)
print("=== Classification Summary ===")
for cls in ["allow", "ask", "deny", "unmatched"]:
    print(f"  {cls}: {by_class.get(cls, 0)}")
print()

# Count by tool + classification
by_tool_class = Counter((t, c) for t, _, c, _, _ in tool_uses)
print("=== By Tool x Classification ===")
for (tool, cls), count in sorted(by_tool_class.items()):
    print(f"  {tool:6s} {cls:10s} {count:5d}")
print()

# Show unmatched Bash commands grouped by first word
unmatched_first_word = Counter()
for t, cmd, c, proj, _ in tool_uses:
    if c == "unmatched" and t == "Bash":
        parts = cmd.split()
        first = parts[0] if parts else "(empty)"
        unmatched_first_word[first] += 1

print("=== Unmatched Bash commands by first word (these get prompted) ===")
for word, count in unmatched_first_word.most_common(40):
    print(f"  {count:4d}x  {word}")

# Show sample unmatched Bash commands
print()
print("=== Sample unmatched Bash commands (first 50 unique) ===")
seen = set()
samples = 0
for t, cmd, c, proj, _ in tool_uses:
    if c == "unmatched" and t == "Bash":
        short = cmd[:150]
        if short not in seen:
            seen.add(short)
            print(f"  {short}")
            samples += 1
            if samples >= 50:
                break

# Show unmatched Read/Edit/Write paths
print()
print("=== Sample unmatched file operations (first 30 unique) ===")
seen = set()
samples = 0
for t, cmd, c, proj, _ in tool_uses:
    if c == "unmatched" and t in ("Read", "Edit", "Write"):
        short = f"{t}: {cmd[:120]}"
        if short not in seen:
            seen.add(short)
            print(f"  {short}")
            samples += 1
            if samples >= 30:
                break
