"""Session detection and continuation logic for Claude CLI."""

import json
import logging
import os
from pathlib import Path

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
