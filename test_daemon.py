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

from session import (
    _path_to_slug, _read_last_timestamp, get_latest_session_id,
    get_project_sessions, get_active_sessions, _parse_reset_time,
    scan_for_rate_limit,
)
from daemon import (
    get_sleep_duration, get_minutes_until_reset,
    is_monitoring_active, query_ccusage, is_block_exhausted,
    _near_stop_time, _run_renewal, _run_bulk_renewal, _try_claude,
    _launch_visible, _clean_env,
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

        old_id = "00000000-0000-0000-0000-000000000001"
        new_id = "00000000-0000-0000-0000-000000000002"
        old = proj_dir / f"{old_id}.jsonl"
        old.write_text('{"timestamp":"2026-01-01T00:00:00.000Z"}\n', encoding="utf-8")

        new = proj_dir / f"{new_id}.jsonl"
        new.write_text('{"timestamp":"2026-02-20T12:00:00.000Z"}\n', encoding="utf-8")

        with mock.patch("session.get_claude_data_dirs", return_value=[tmp_path / "projects"]):
            result = get_latest_session_id()
        assert result == new_id

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


class TestNearStopTime:
    def test_within_10min(self):
        config = {"stop_epoch": time.time() + 300}
        assert _near_stop_time(config) is True

    def test_far_from_stop(self):
        config = {"stop_epoch": time.time() + 3600}
        assert _near_stop_time(config) is False

    def test_no_stop(self):
        assert _near_stop_time({}) is False


class TestParseResetTime:
    def test_simple_pm(self):
        result = _parse_reset_time("You've hit your limit. resets 2pm (Asia/Jerusalem)")
        assert result is not None
        assert result["reset_str"] == "2pm (Asia/Jerusalem)"
        assert result["reset_epoch"] > time.time() - 86400  # within a day

    def test_simple_am(self):
        result = _parse_reset_time("limit reached. resets 11am (US/Eastern)")
        assert result is not None
        assert "11am" in result["reset_str"]
        assert "US/Eastern" in result["reset_str"]

    def test_with_minutes(self):
        result = _parse_reset_time("resets 2:30pm (Europe/London)")
        assert result is not None
        assert "2:30pm" in result["reset_str"]

    def test_no_match(self):
        assert _parse_reset_time("some random text") is None

    def test_bad_timezone(self):
        assert _parse_reset_time("resets 2pm (Fake/Zone)") is None

    def test_case_insensitive(self):
        result = _parse_reset_time("resets 2PM (UTC)")
        assert result is not None

    def test_weekly_limit_with_date(self):
        result = _parse_reset_time("87% of weekly limit. resets Mar 1, 9am (Asia/Jerusalem)")
        assert result is not None
        assert result["is_weekly"] is True
        assert "Mar 1" in result["reset_str"]
        assert "9am" in result["reset_str"]
        assert "Asia/Jerusalem" in result["reset_str"]

    def test_weekly_limit_epoch_is_future(self):
        result = _parse_reset_time("resets Mar 1, 9am (Asia/Jerusalem)")
        assert result is not None
        assert result["is_weekly"] is True
        # Reset should be in the future (or within a year)
        assert result["reset_epoch"] > time.time() - 86400

    def test_5h_block_is_not_weekly(self):
        result = _parse_reset_time("resets 2pm (Asia/Jerusalem)")
        assert result is not None
        assert result["is_weekly"] is False


class TestScanForRateLimit:
    def _make_rate_limit_entry(self, timestamp, reset_text):
        return json.dumps({
            "type": "assistant",
            "timestamp": timestamp,
            "error": "rate_limit",
            "isApiErrorMessage": True,
            "message": {
                "model": "<synthetic>",
                "content": [{"type": "text", "text": reset_text}],
            },
        })

    def test_finds_recent_limit(self, tmp_path):
        proj_dir = tmp_path / "projects" / "test-proj"
        proj_dir.mkdir(parents=True)
        f = proj_dir / "session.jsonl"
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        entry = self._make_rate_limit_entry(now_iso, "limit hit. resets 11pm (UTC)")
        f.write_text(entry + "\n", encoding="utf-8")

        with mock.patch("session.get_claude_data_dirs", return_value=[tmp_path / "projects"]):
            result = scan_for_rate_limit()
        assert result is not None
        assert "11pm" in result["reset_str"]
        assert result["session_id"] == "session"

    def test_ignores_stale_limit(self, tmp_path):
        proj_dir = tmp_path / "projects" / "test-proj"
        proj_dir.mkdir(parents=True)
        f = proj_dir / "session.jsonl"
        # 6 hours ago -- outside the 5h window
        old_iso = "2020-01-01T00:00:00.000Z"
        entry = self._make_rate_limit_entry(old_iso, "limit hit. resets 3am (UTC)")
        f.write_text(entry + "\n", encoding="utf-8")

        with mock.patch("session.get_claude_data_dirs", return_value=[tmp_path / "projects"]):
            result = scan_for_rate_limit()
        assert result is None

    def test_no_rate_limit_entries(self, tmp_path):
        proj_dir = tmp_path / "projects" / "test-proj"
        proj_dir.mkdir(parents=True)
        f = proj_dir / "session.jsonl"
        f.write_text('{"type":"user","timestamp":"2026-02-22T10:00:00.000Z"}\n', encoding="utf-8")

        with mock.patch("session.get_claude_data_dirs", return_value=[tmp_path / "projects"]):
            result = scan_for_rate_limit()
        assert result is None

    def test_empty_dirs(self, tmp_path):
        with mock.patch("session.get_claude_data_dirs", return_value=[tmp_path]):
            result = scan_for_rate_limit()
        assert result is None


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

    def test_active_block_always_false(self):
        """Active blocks are never flagged -- rate limits handled by JSONL scan."""
        block = {"isActive": True, "tokenLimitStatus": {"status": "exceeds"}}
        assert is_block_exhausted(block) is False

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

    def test_active_block_actual_end_ignored(self):
        """actualEndTime is last-activity, not a limit signal -- ignore it."""
        block = {
            "isActive": True,
            "startTime": "2026-02-22T10:00:00.000Z",
            "endTime": "2026-02-22T15:00:00.000Z",
            "actualEndTime": "2026-02-22T12:06:34.307Z",
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


class TestCleanEnv:
    def test_strips_claudecode(self):
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1", "PATH": "/usr/bin"}):
            env = _clean_env()
            assert "CLAUDECODE" not in env
            assert "PATH" in env

    def test_no_claudecode(self):
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
            env = _clean_env()
            assert "CLAUDECODE" not in env


class TestTryClaude:
    def test_success(self):
        with mock.patch("daemon.subprocess.run") as m:
            m.return_value = mock.Mock(returncode=0)
            assert _try_claude(["claude", "-p", "hi"], {}, "test") is True

    def test_failure(self):
        with mock.patch("daemon.subprocess.run") as m:
            m.return_value = mock.Mock(returncode=1, stderr="error")
            assert _try_claude(["claude", "-p", "hi"], {}, "test") is False

    def test_timeout_treated_as_success(self):
        with mock.patch("daemon.subprocess.run") as m:
            import subprocess as sp
            m.side_effect = sp.TimeoutExpired("claude", 30)
            assert _try_claude(["claude", "-p", "hi"], {}, "test") is True

    def test_not_found(self):
        with mock.patch("daemon.subprocess.run") as m:
            m.side_effect = OSError("not found")
            assert _try_claude(["claude", "-p", "hi"], {}, "test") is None


class TestRunRenewalFallback:
    """Test the resume -> fork -> continue fallback chain."""

    def _mock_run(self, results):
        """results: list of (returncode,) or exception per call."""
        call_idx = [0]
        def side_effect(*args, **kwargs):
            i = call_idx[0]
            call_idx[0] += 1
            r = results[i]
            if isinstance(r, Exception):
                raise r
            return mock.Mock(returncode=r, stderr="")
        return side_effect

    @mock.patch("daemon.write_last_activity")
    @mock.patch("daemon.notify")
    @mock.patch("daemon.subprocess.run")
    def test_resume_succeeds_first(self, mock_run, mock_notify, mock_wa):
        mock_run.return_value = mock.Mock(returncode=0)
        result = _run_renewal({}, session_id="abc-123")
        assert result is True
        # Should have called resume with -r <id> (no --session-id)
        cmd = mock_run.call_args[0][0]
        assert "--resume" in cmd
        assert "abc-123" in cmd
        assert "--session-id" not in cmd

    @mock.patch("daemon.write_last_activity")
    @mock.patch("daemon.notify")
    @mock.patch("daemon.subprocess.run")
    def test_falls_back_to_continue(self, mock_run, mock_notify, mock_wa):
        mock_run.side_effect = self._mock_run([1, 0])  # resume fails, continue succeeds
        result = _run_renewal({}, session_id="abc-123")
        assert result is True
        assert mock_run.call_count == 2
        cmd2 = mock_run.call_args_list[1][0][0]
        assert "--continue" in cmd2

    @mock.patch("daemon.write_last_activity")
    @mock.patch("daemon.notify")
    @mock.patch("daemon.subprocess.run")
    def test_all_fail(self, mock_run, mock_notify, mock_wa):
        mock_run.side_effect = self._mock_run([1, 1])
        result = _run_renewal({}, session_id="abc-123")
        assert result is False

    @mock.patch("daemon.write_last_activity")
    @mock.patch("daemon.notify")
    @mock.patch("daemon.subprocess.run")
    def test_no_session_id_skips_to_continue(self, mock_run, mock_notify, mock_wa):
        mock_run.return_value = mock.Mock(returncode=0)
        result = _run_renewal({}, session_id=None)
        assert result is True
        # Only continue strategy, no resume/fork
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert "--continue" in cmd


class TestGetActiveSessions:
    def test_finds_recent_sessions(self, tmp_path):
        proj1 = tmp_path / "projects" / "slug-A"
        proj2 = tmp_path / "projects" / "slug-B"
        proj1.mkdir(parents=True)
        proj2.mkdir(parents=True)

        id1 = "00000000-0000-0000-0000-000000000001"
        id2 = "00000000-0000-0000-0000-000000000002"
        id_old = "00000000-0000-0000-0000-000000000003"
        (proj1 / f"{id1}.jsonl").write_text(
            '{"timestamp":"2026-02-20T12:00:00.000Z"}\n', encoding="utf-8")
        (proj2 / f"{id2}.jsonl").write_text(
            '{"timestamp":"2026-02-20T14:00:00.000Z"}\n', encoding="utf-8")
        # Old session -- should be excluded
        (proj1 / f"{id_old}.jsonl").write_text(
            '{"timestamp":"2020-01-01T00:00:00.000Z"}\n', encoding="utf-8")

        cutoff = 1708416000.0  # 2024-02-20T08:00:00Z -- well before 2026 timestamps
        with mock.patch("session.get_claude_data_dirs", return_value=[tmp_path / "projects"]):
            result = get_active_sessions(cutoff)

        assert len(result) == 2
        # Newest first
        assert result[0]["session_id"] == id2
        assert result[1]["session_id"] == id1
        assert result[0]["slug"] == "slug-B"

    def test_deduplicates_by_session_id(self, tmp_path):
        """Same session ID in two slug dirs keeps only the newer one."""
        proj1 = tmp_path / "projects" / "slug-A"
        proj2 = tmp_path / "projects" / "slug-B"
        proj1.mkdir(parents=True)
        proj2.mkdir(parents=True)

        sid = "00000000-0000-0000-0000-000000000001"
        (proj1 / f"{sid}.jsonl").write_text(
            '{"timestamp":"2026-02-20T10:00:00.000Z"}\n', encoding="utf-8")
        (proj2 / f"{sid}.jsonl").write_text(
            '{"timestamp":"2026-02-20T14:00:00.000Z"}\n', encoding="utf-8")

        with mock.patch("session.get_claude_data_dirs", return_value=[tmp_path / "projects"]):
            result = get_active_sessions(0.0)

        assert len(result) == 1
        assert result[0]["slug"] == "slug-B"  # newer one wins

    def test_empty_dirs(self, tmp_path):
        with mock.patch("session.get_claude_data_dirs", return_value=[tmp_path]):
            result = get_active_sessions(0.0)
        assert result == []

    def test_all_sessions_before_cutoff(self, tmp_path):
        proj = tmp_path / "projects" / "slug-A"
        proj.mkdir(parents=True)
        (proj / "00000000-0000-0000-0000-000000000001.jsonl").write_text(
            '{"timestamp":"2020-01-01T00:00:00.000Z"}\n', encoding="utf-8")

        with mock.patch("session.get_claude_data_dirs", return_value=[tmp_path / "projects"]):
            result = get_active_sessions(time.time())
        assert result == []


class TestRunBulkRenewal:
    def _make_sessions(self, n):
        return [{"session_id": f"sid-{i}", "slug": f"slug-{i}", "last_ts": 0.0} for i in range(n)]

    @mock.patch("daemon.write_last_activity")
    @mock.patch("daemon.notify")
    @mock.patch("daemon._launch_visible")
    def test_all_succeed(self, mock_try, mock_notify, mock_wa):
        mock_try.return_value = True
        s, f = _run_bulk_renewal({}, self._make_sessions(3))
        assert s == 3
        assert f == 0
        assert mock_try.call_count == 3
        mock_wa.assert_called_once()

    @mock.patch("daemon.write_last_activity")
    @mock.patch("daemon.notify")
    @mock.patch("daemon._launch_visible")
    def test_partial_failure(self, mock_try, mock_notify, mock_wa):
        mock_try.side_effect = [True, False, True]
        s, f = _run_bulk_renewal({}, self._make_sessions(3))
        assert s == 2
        assert f == 1
        mock_wa.assert_called_once()

    @mock.patch("daemon.write_last_activity")
    @mock.patch("daemon.notify")
    @mock.patch("daemon._launch_visible")
    def test_all_fail_tries_continue(self, mock_try, mock_notify, mock_wa):
        # 2 resumes fail, then --continue fallback succeeds
        mock_try.side_effect = [False, False, True]
        s, f = _run_bulk_renewal({}, self._make_sessions(2))
        assert s == 1  # continue fallback
        assert f == 2
        # 3 calls: 2 resumes + 1 continue fallback
        assert mock_try.call_count == 3
        mock_wa.assert_called_once()

    @mock.patch("daemon.write_last_activity")
    @mock.patch("daemon.notify")
    @mock.patch("daemon._launch_visible")
    def test_all_fail_including_continue(self, mock_try, mock_notify, mock_wa):
        mock_try.return_value = False
        s, f = _run_bulk_renewal({}, self._make_sessions(2))
        assert s == 0
        assert f == 2
        mock_wa.assert_not_called()

    @mock.patch("daemon.write_last_activity")
    @mock.patch("daemon.notify")
    @mock.patch("daemon._launch_visible")
    def test_claude_not_found_stops_early(self, mock_try, mock_notify, mock_wa):
        mock_try.return_value = None  # command not found
        s, f = _run_bulk_renewal({}, self._make_sessions(3))
        assert s == 0
        assert f == 3
        # Stops after first None -- only 1 call
        assert mock_try.call_count == 1

    @mock.patch("daemon.write_last_activity")
    @mock.patch("daemon.notify")
    @mock.patch("daemon._launch_visible")
    def test_empty_sessions(self, mock_try, mock_notify, mock_wa):
        s, f = _run_bulk_renewal({}, [])
        assert s == 0
        assert f == 0
        mock_try.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
