"""Automated tests for CCAutoRenew."""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest

# Ensure imports work from project root
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from session import _path_to_slug, _read_last_timestamp, get_latest_session_id, get_project_sessions
from daemon import (
    get_sleep_duration, should_renew, get_minutes_until_reset,
    is_monitoring_active, query_ccusage, is_block_exhausted,
)


# -- session.py tests -------------------------------------------------------

class TestProjectSlug:
    def test_windows_path(self):
        p = Path("C:\\Users\\foo\\bar")
        assert _path_to_slug(p) == "C--Users-foo-bar"

    def test_forward_slashes(self):
        p = Path("C:/Users/foo/bar")
        slug = _path_to_slug(p)
        # On Windows, Path normalizes to backslashes, so result is the same
        assert "Users" in slug
        assert ":" not in slug

    def test_no_trailing_dash(self):
        p = Path("C:\\Projects\\MyApp")
        slug = _path_to_slug(p)
        assert not slug.endswith("-")


class TestReadLastTimestamp:
    def test_valid_jsonl(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text('{"type":"user","timestamp":"2026-02-20T10:00:00.000Z"}\n', encoding="utf-8")
        ts = _read_last_timestamp(f)
        assert ts is not None
        assert ts > 0

    def test_snapshot_timestamp(self, tmp_path):
        f = tmp_path / "test.jsonl"
        data = {"type": "file-history-snapshot", "snapshot": {"timestamp": "2026-02-20T10:00:00.000Z"}}
        f.write_text(json.dumps(data) + "\n", encoding="utf-8")
        ts = _read_last_timestamp(f)
        assert ts is not None

    def test_empty_file(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text("", encoding="utf-8")
        assert _read_last_timestamp(f) is None

    def test_corrupt_json(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text("not json at all\n", encoding="utf-8")
        assert _read_last_timestamp(f) is None

    def test_missing_timestamp(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text('{"type":"unknown"}\n', encoding="utf-8")
        assert _read_last_timestamp(f) is None


class TestGetLatestSessionId:
    def test_finds_newest(self, tmp_path):
        proj_dir = tmp_path / "projects" / "test-project"
        proj_dir.mkdir(parents=True)

        old = proj_dir / "aaaa-old.jsonl"
        old.write_text('{"timestamp":"2026-01-01T00:00:00.000Z"}\n', encoding="utf-8")

        new = proj_dir / "bbbb-new.jsonl"
        new.write_text('{"timestamp":"2026-02-20T12:00:00.000Z"}\n', encoding="utf-8")

        with mock.patch("session.get_claude_data_dirs", return_value=[tmp_path / "projects"]):
            result = get_latest_session_id()
        assert result == "bbbb-new"

    def test_no_files(self, tmp_path):
        with mock.patch("session.get_claude_data_dirs", return_value=[tmp_path]):
            result = get_latest_session_id()
        assert result is None


class TestGetProjectSessions:
    def test_returns_sorted(self, tmp_path):
        proj_dir = tmp_path / "projects" / "C--Test"
        proj_dir.mkdir(parents=True)

        for i, ts in enumerate(["2026-01-01T00:00:00.000Z", "2026-02-01T00:00:00.000Z", "2026-01-15T00:00:00.000Z"]):
            f = proj_dir / f"session-{i}.jsonl"
            f.write_text(f'{{"timestamp":"{ts}"}}\n', encoding="utf-8")

        with mock.patch("session.get_claude_data_dirs", return_value=[tmp_path / "projects"]):
            with mock.patch("session._path_to_slug", return_value="C--Test"):
                sessions = get_project_sessions(Path("C:\\Test"))

        assert len(sessions) == 3
        # Newest first
        assert sessions[0][0] == "session-1"


# -- daemon.py tests --------------------------------------------------------

class TestGetSleepDuration:
    def test_above_30_minutes(self):
        assert get_sleep_duration(31) == 600
        assert get_sleep_duration(100) == 600

    def test_6_to_30_minutes(self):
        assert get_sleep_duration(30) == 120
        assert get_sleep_duration(6) == 120

    def test_5_or_less(self):
        assert get_sleep_duration(5) == 30
        assert get_sleep_duration(0) == 30

    def test_none_no_activity(self):
        with mock.patch("daemon.read_last_activity", return_value=None):
            assert get_sleep_duration(None) == 300

    def test_none_with_recent_activity(self):
        with mock.patch("daemon.read_last_activity", return_value=time.time() - 3600):
            # 1h elapsed, 4h left -> >30min -> 600
            assert get_sleep_duration(None) == 600


class TestShouldRenew:
    def test_ccusage_2min(self):
        block = {"isActive": True}
        assert should_renew(block, 2, {}) is True

    def test_ccusage_3min(self):
        block = {"isActive": True}
        assert should_renew(block, 3, {}) is False

    def test_near_stop_time(self):
        block = {"isActive": True}
        config = {"stop_epoch": time.time() + 300}  # 5 min from now
        assert should_renew(block, 1, config) is False

    def test_clock_fallback_5h_ago(self):
        with mock.patch("daemon.read_last_activity", return_value=time.time() - 18001):
            assert should_renew(None, None, {}) is True

    def test_clock_fallback_recent(self):
        with mock.patch("daemon.read_last_activity", return_value=time.time() - 3600):
            assert should_renew(None, None, {}) is False

    def test_no_activity_starts_session(self):
        with mock.patch("daemon.read_last_activity", return_value=None):
            assert should_renew(None, None, {}) is True

    def test_block_exhausted_early(self):
        block = {
            "isActive": False,
            "actualEndTime": "2026-02-21T18:00:00.000Z",
            "endTime": "2026-02-21T22:00:00.000Z",
        }
        assert should_renew(block, 0, {}) is True

    def test_block_not_exhausted(self):
        block = {
            "isActive": True,
            "endTime": "2026-02-21T22:00:00.000Z",
        }
        assert should_renew(block, 200, {}) is False


class TestIsMonitoringActive:
    def test_before_start(self):
        config = {"start_epoch": time.time() + 3600}
        assert is_monitoring_active(config) == "before start time"

    def test_after_stop(self):
        config = {"stop_epoch": time.time() - 60}
        assert is_monitoring_active(config) == "past stop time"

    def test_within_window(self):
        config = {
            "start_epoch": time.time() - 3600,
            "stop_epoch": time.time() + 3600,
        }
        assert is_monitoring_active(config) is None

    def test_no_bounds(self):
        assert is_monitoring_active({}) is None


class TestCcusageJsonParsing:
    """Test that get_minutes_until_reset and query_ccusage correctly parse ccusage JSON."""

    def _make_block(self, end_time_iso: str, reset_time: str | None = None):
        block = {
            "startTime": "2026-02-20T09:00:00.000Z",
            "endTime": end_time_iso,
            "isActive": True,
        }
        if reset_time:
            block["usageLimitResetTime"] = reset_time
        return block

    def test_parse_endtime(self):
        from datetime import datetime, timezone, timedelta
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        iso = future.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        block = self._make_block(iso)
        result = get_minutes_until_reset(block)

        assert result is not None
        assert 115 <= result <= 125  # approximately 2 hours

    def test_prefers_reset_time(self):
        from datetime import datetime, timezone, timedelta
        end = datetime.now(timezone.utc) + timedelta(hours=2)
        reset = datetime.now(timezone.utc) + timedelta(hours=1)
        block = self._make_block(
            end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            reset.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        )
        result = get_minutes_until_reset(block)

        assert result is not None
        assert 55 <= result <= 65  # approximately 1 hour (uses resetTime, not endTime)

    def test_empty_blocks(self):
        result = get_minutes_until_reset(None)
        assert result is None

    def test_disabled(self):
        result = query_ccusage({"disable_ccusage": True})
        assert result is None


class TestBlockExhaustion:
    """Test is_block_exhausted detection logic."""

    def test_active_block_no_limit_status(self):
        block = {"isActive": True, "endTime": "2026-02-21T22:00:00.000Z"}
        assert is_block_exhausted(block) is False

    def test_active_block_exceeds(self):
        block = {"isActive": True, "tokenLimitStatus": {"status": "exceeds"}}
        assert is_block_exhausted(block) is True

    def test_active_block_warning(self):
        block = {"isActive": True, "tokenLimitStatus": {"status": "warning"}}
        assert is_block_exhausted(block) is True

    def test_completed_block_early_end(self):
        block = {
            "isActive": False,
            "actualEndTime": "2026-02-21T18:00:00.000Z",
            "endTime": "2026-02-21T22:00:00.000Z",
        }
        assert is_block_exhausted(block) is True

    def test_completed_block_natural_end(self):
        block = {
            "isActive": False,
            "actualEndTime": "2026-02-21T21:57:00.000Z",
            "endTime": "2026-02-21T22:00:00.000Z",
        }
        assert is_block_exhausted(block) is False

    def test_none_block(self):
        assert is_block_exhausted(None) is False


class TestWindowsPathChdir:
    def test_backslash_path(self, tmp_path):
        target = str(tmp_path).replace("/", "\\")
        os.chdir(os.path.normpath(target))
        assert os.path.samefile(os.getcwd(), tmp_path)

    def test_forward_slash_path(self, tmp_path):
        target = str(tmp_path).replace("\\", "/")
        os.chdir(os.path.normpath(target))
        assert os.path.samefile(os.getcwd(), tmp_path)

    def test_mixed_slash_path(self, tmp_path):
        parts = str(tmp_path).split(os.sep)
        mixed = "/".join(parts[:2]) + "\\" + "\\".join(parts[2:])
        os.chdir(os.path.normpath(mixed))
        assert os.path.samefile(os.getcwd(), tmp_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
