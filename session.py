"""Session detection and continuation logic for Claude CLI."""

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("ccautorenew")


def _path_to_slug(p: Path) -> str:
    """Convert an absolute path to a Claude project slug.

    Example: C:\\Users\\asafl\\Desktop\\CCAUTORENEW -> C--Users-asafl-Desktop-CCAUTORENEW
    """
    s = str(p.resolve())
    s = s.replace(":", "-")
    s = s.replace("\\", "-")
    s = s.replace("/", "-")
    return s


def get_claude_data_dirs() -> list[Path]:
    """Return existing Claude project directories, ordered by priority."""
    candidates = [
        Path.home() / ".claude" / "projects",
    ]
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        candidates.append(Path(config_dir) / "projects")
    # Linux/Mac alternate
    candidates.append(Path.home() / ".config" / "claude" / "projects")

    return [d for d in candidates if d.is_dir()]


def _is_valid_uuid(s: str) -> bool:
    """Check if a string is a valid UUID (session IDs are UUIDs)."""
    try:
        uuid.UUID(s)
        return True
    except ValueError:
        return False


def _read_last_timestamp(filepath: Path) -> float | None:
    """Read the last non-empty line of a JSONL file and extract its timestamp."""
    try:
        with open(filepath, encoding="utf-8") as f:
            last_line = None
            for line in f:
                stripped = line.strip()
                if stripped:
                    last_line = stripped
            if not last_line:
                return None
            data = json.loads(last_line)
            # Timestamp can be top-level or nested in snapshot
            ts_str = data.get("timestamp")
            if not ts_str:
                ts_str = data.get("snapshot", {}).get("timestamp")
            if not ts_str:
                return None
            from datetime import datetime, timezone

            # Handle ISO 8601 with trailing Z
            ts_str = ts_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts_str)
            return dt.timestamp()
    except Exception as exc:
        log.debug("Failed to read timestamp from %s: %s", filepath, exc)
        return None


def get_latest_session_id() -> str | None:
    """Find the most recent session UUID across all Claude data dirs."""
    best_id = None
    best_ts = 0.0

    for data_dir in get_claude_data_dirs():
        for jsonl_file in data_dir.rglob("*.jsonl"):
            if not _is_valid_uuid(jsonl_file.stem):
                continue
            ts = _read_last_timestamp(jsonl_file)
            if ts is not None and ts > best_ts:
                best_ts = ts
                best_id = jsonl_file.stem

    if best_id:
        log.debug("Latest session: %s (epoch %.0f)", best_id, best_ts)
    else:
        log.debug("No session files found")
    return best_id


def get_project_sessions(project_cwd: Path) -> list[tuple[str, float]]:
    """Return sessions for a specific project directory, newest first.

    Returns list of (session_id, epoch) tuples.
    """
    slug = _path_to_slug(project_cwd)
    log.debug("Looking for sessions with slug: %s", slug)
    sessions = []

    for data_dir in get_claude_data_dirs():
        project_dir = data_dir / slug
        if not project_dir.is_dir():
            continue
        for jsonl_file in project_dir.glob("*.jsonl"):
            ts = _read_last_timestamp(jsonl_file)
            if ts is not None:
                sessions.append((jsonl_file.stem, ts))

    sessions.sort(key=lambda x: x[1], reverse=True)
    log.debug("Found %d sessions for %s", len(sessions), slug)
    return sessions


def _parse_reset_time(text: str, reference_epoch: float | None = None) -> dict | None:
    """Parse reset time from a rate limit message.

    Expects text like: "... resets 2pm (Asia/Jerusalem)"
    reference_epoch: when the rate limit was logged (used to resolve the correct day).
                     Falls back to now if not provided.
    Returns {"reset_epoch": float, "reset_str": str} or None.
    """
    # Match "resets 2pm (TZ)" or "resets Mar 1, 9am (TZ)"
    m = re.search(
        r"resets\s+(?:(\w+\s+\d{1,2}),\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm))\s+\(([^)]+)\)",
        text, re.IGNORECASE,
    )
    if not m:
        return None
    date_str, time_str, tz_name = m.group(1), m.group(2), m.group(3)
    try:
        tz = ZoneInfo(tz_name)
    except (KeyError, Exception):
        log.debug("Unknown timezone in rate limit message: %s", tz_name)
        return None
    # Parse time (e.g. "2pm", "2:30pm", "11am")
    time_str = time_str.strip()
    for fmt in ("%I:%M%p", "%I%p"):
        try:
            t = datetime.strptime(time_str.upper(), fmt).time()
            break
        except ValueError:
            continue
    else:
        log.debug("Could not parse time from rate limit message: %s", time_str)
        return None
    # Use entry timestamp as reference so we resolve the correct day
    if reference_epoch is not None:
        ref_dt = datetime.fromtimestamp(reference_epoch, tz)
    else:
        ref_dt = datetime.now(tz)
    if date_str:
        # Weekly limit: explicit date like "Mar 1"
        try:
            month_day = datetime.strptime(date_str, "%b %d")
            reset_dt = ref_dt.replace(
                month=month_day.month, day=month_day.day,
                hour=t.hour, minute=t.minute, second=0, microsecond=0,
            )
            # If date is in the past, it's next year
            if reset_dt <= ref_dt:
                reset_dt = reset_dt.replace(year=reset_dt.year + 1)
        except ValueError:
            log.debug("Could not parse date from rate limit message: %s", date_str)
            return None
        is_weekly = True
    else:
        # 5h block: resolve to next occurrence of this time
        reset_dt = ref_dt.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if reset_dt <= ref_dt:
            from datetime import timedelta
            reset_dt += timedelta(days=1)
        is_weekly = False
    reset_epoch = reset_dt.timestamp()
    display_date = f"{date_str}, " if date_str else ""
    reset_str = f"{display_date}{time_str} ({tz_name})"
    return {"reset_epoch": reset_epoch, "reset_str": reset_str, "is_weekly": is_weekly}


def get_active_sessions(since_epoch: float) -> list[dict]:
    """Find sessions with activity after since_epoch.

    Returns [{"session_id": str, "slug": str, "last_ts": float}], newest first.
    """
    seen = {}  # session_id -> dict
    for data_dir in get_claude_data_dirs():
        for jsonl_file in data_dir.rglob("*.jsonl"):
            ts = _read_last_timestamp(jsonl_file)
            if ts is None or ts <= since_epoch:
                continue
            sid = jsonl_file.stem
            if not _is_valid_uuid(sid):
                continue
            if sid not in seen or ts > seen[sid]["last_ts"]:
                seen[sid] = {
                    "session_id": sid,
                    "slug": jsonl_file.parent.name,
                    "last_ts": ts,
                }
    sessions = sorted(seen.values(), key=lambda x: x["last_ts"], reverse=True)
    log.debug("get_active_sessions: found %d sessions since epoch %.0f", len(sessions), since_epoch)
    return sessions


def scan_for_rate_limit() -> dict | None:
    """Scan JSONL session files for recent rate_limit errors.

    Only considers entries from the last 5 hours.
    Returns {"reset_epoch": float, "reset_str": str, "session_id": str} or None.
    """
    cutoff = time.time() - 18000  # 5 hours ago
    best = None  # (entry_epoch, parsed_dict, session_id)

    for data_dir in get_claude_data_dirs():
        for jsonl_file in data_dir.rglob("*.jsonl"):
            try:
                with open(jsonl_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        if '"rate_limit"' not in line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if entry.get("error") != "rate_limit":
                            continue
                        ts_str = entry.get("timestamp")
                        if not ts_str:
                            continue
                        ts_str = ts_str.replace("Z", "+00:00")
                        try:
                            entry_epoch = datetime.fromisoformat(ts_str).timestamp()
                        except ValueError:
                            continue
                        if entry_epoch < cutoff:
                            continue
                        # Extract reset time from message
                        content = entry.get("message", {}).get("content", [])
                        if not content:
                            continue
                        text = content[0].get("text", "") if isinstance(content[0], dict) else ""
                        parsed = _parse_reset_time(text, reference_epoch=entry_epoch)
                        if not parsed:
                            continue
                        # Skip if reset time already passed
                        if parsed["reset_epoch"] < time.time():
                            continue
                        if best is None or entry_epoch > best[0]:
                            best = (entry_epoch, parsed, jsonl_file.stem)
            except OSError as exc:
                log.debug("Failed to read %s: %s", jsonl_file, exc)

    if best:
        result = best[1]
        result["session_id"] = best[2]
        log.info("Found rate limit: resets at %s (session %s)", result["reset_str"], result["session_id"])
        return result
    return None
