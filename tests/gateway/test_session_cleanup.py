"""Tests for session archive and orphan cleanup features."""

import gzip
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gateway.session import SessionStore


@pytest.fixture
def tmp_sessions_dir(tmp_path):
    """Create a temporary sessions directory."""
    d = tmp_path / "sessions"
    d.mkdir()
    return d


@pytest.fixture
def mock_db():
    """Create a mock session DB with no records."""
    db = MagicMock()
    db._conn.execute.return_value.fetchall.return_value = []
    return db


class TestArchiveOldTopicSessions:
    """Tests for SessionStore.archive_old_topic_sessions()."""

    def test_returns_zero_when_disabled(self, tmp_sessions_dir):
        store = SessionStore.__new__(SessionStore)
        assert store.archive_old_topic_sessions(0, tmp_sessions_dir) == 0

    def test_returns_zero_when_no_sessions(self, tmp_sessions_dir):
        store = SessionStore.__new__(SessionStore)
        assert store.archive_old_topic_sessions(7, tmp_sessions_dir) == 0

    def test_archives_old_forum_topic_session(self, tmp_sessions_dir):
        store = SessionStore.__new__(SessionStore)
        # Create a forum topic session file that's old enough
        session_file = tmp_sessions_dir / "sess-abc123.json"
        data = {"_source": {"chat_type": "forum", "thread_id": "42"}, "messages": []}
        session_file.write_text(json.dumps(data))
        # Set mtime to 60 days ago
        old_time = time.time() - (60 * 86400)
        os.utime(session_file, (old_time, old_time))

        archived = store.archive_old_topic_sessions(30, tmp_sessions_dir)
        assert archived == 1
        assert not session_file.exists()
        archive_dir = tmp_sessions_dir / "archive"
        assert archive_dir.exists()
        gz_file = archive_dir / "sess-abc123.json.gz"
        assert gz_file.exists()
        # Verify the archived content is valid
        with gzip.open(gz_file, "rt") as f:
            restored = json.load(f)
        assert restored["_source"]["chat_type"] == "forum"

    def test_skips_recent_forum_session(self, tmp_sessions_dir):
        store = SessionStore.__new__(SessionStore)
        session_file = tmp_sessions_dir / "sess-recent.json"
        data = {"_source": {"chat_type": "forum"}, "messages": []}
        session_file.write_text(json.dumps(data))
        # mtime is now (recent)

        archived = store.archive_old_topic_sessions(30, tmp_sessions_dir)
        assert archived == 0
        assert session_file.exists()

    def test_skips_non_forum_session(self, tmp_sessions_dir):
        store = SessionStore.__new__(SessionStore)
        session_file = tmp_sessions_dir / "sess-private.json"
        data = {"_source": {"chat_type": "private"}, "messages": []}
        session_file.write_text(json.dumps(data))
        old_time = time.time() - (60 * 86400)
        os.utime(session_file, (old_time, old_time))

        archived = store.archive_old_topic_sessions(30, tmp_sessions_dir)
        assert archived == 0
        assert session_file.exists()

    def test_skips_sessions_json(self, tmp_sessions_dir):
        store = SessionStore.__new__(SessionStore)
        sessions_json = tmp_sessions_dir / "sessions.json"
        sessions_json.write_text("{}")

        archived = store.archive_old_topic_sessions(30, tmp_sessions_dir)
        assert archived == 0
        assert sessions_json.exists()

    def test_multiple_sessions_partial_archive(self, tmp_sessions_dir):
        store = SessionStore.__new__(SessionStore)
        old_time = time.time() - (60 * 86400)

        # Old forum topic - should be archived
        f1 = tmp_sessions_dir / "sess-forum-old.json"
        f1.write_text(json.dumps({"_source": {"chat_type": "forum"}}))
        os.utime(f1, (old_time, old_time))

        # Recent forum topic - should NOT be archived
        f2 = tmp_sessions_dir / "sess-forum-recent.json"
        f2.write_text(json.dumps({"_source": {"chat_type": "forum"}}))

        # Old DM session - should NOT be archived (not forum)
        f3 = tmp_sessions_dir / "sess-dm-old.json"
        f3.write_text(json.dumps({"_source": {"chat_type": "private"}}))
        os.utime(f3, (old_time, old_time))

        archived = store.archive_old_topic_sessions(30, tmp_sessions_dir)
        assert archived == 1
        assert not f1.exists()
        assert f2.exists()
        assert f3.exists()


class TestCleanupOrphanSessions:
    """Tests for SessionStore.cleanup_orphan_sessions()."""

    def test_returns_zero_when_disabled(self, tmp_sessions_dir, mock_db):
        store = SessionStore.__new__(SessionStore)
        assert store.cleanup_orphan_sessions(tmp_sessions_dir, mock_db, 0) == 0

    def test_returns_zero_when_db_is_none(self, tmp_sessions_dir):
        store = SessionStore.__new__(SessionStore)
        # Create an old orphan file
        f = tmp_sessions_dir / "sess-orphan.json"
        f.write_text("{}")
        old_time = time.time() - (30 * 86400)
        os.utime(f, (old_time, old_time))

        # With db=None, no orphans should be cleaned
        assert store.cleanup_orphan_sessions(tmp_sessions_dir, None, 7) == 0
        assert f.exists()

    def test_removes_orphan_json_files(self, tmp_sessions_dir, mock_db):
        store = SessionStore.__new__(SessionStore)
        f = tmp_sessions_dir / "sess-orphan.json"
        f.write_text("{}")
        old_time = time.time() - (30 * 86400)
        os.utime(f, (old_time, old_time))

        removed = store.cleanup_orphan_sessions(tmp_sessions_dir, mock_db, 7)
        assert removed == 1
        assert not f.exists()

    def test_removes_orphan_gz_files(self, tmp_sessions_dir, mock_db):
        store = SessionStore.__new__(SessionStore)
        f = tmp_sessions_dir / "sess-orphan.json.gz"
        f.write_bytes(b"fake gzip content")
        old_time = time.time() - (30 * 86400)
        os.utime(f, (old_time, old_time))

        removed = store.cleanup_orphan_sessions(tmp_sessions_dir, mock_db, 7)
        assert removed == 1
        assert not f.exists()

    def test_keeps_known_session_files(self, tmp_sessions_dir):
        store = SessionStore.__new__(SessionStore)
        db = MagicMock()
        db._conn.execute.return_value.fetchall.return_value = [("sess-keep",)]

        f = tmp_sessions_dir / "sess-keep.json"
        f.write_text("{}")
        old_time = time.time() - (30 * 86400)
        os.utime(f, (old_time, old_time))

        removed = store.cleanup_orphan_sessions(tmp_sessions_dir, db, 7)
        assert removed == 0
        assert f.exists()

    def test_keeps_recent_files(self, tmp_sessions_dir, mock_db):
        store = SessionStore.__new__(SessionStore)
        f = tmp_sessions_dir / "sess-recent-orphan.json"
        f.write_text("{}")
        # mtime is now (recent)

        removed = store.cleanup_orphan_sessions(tmp_sessions_dir, mock_db, 7)
        assert removed == 0
        assert f.exists()

    def test_skips_sessions_json(self, tmp_sessions_dir, mock_db):
        store = SessionStore.__new__(SessionStore)
        sessions_json = tmp_sessions_dir / "sessions.json"
        sessions_json.write_text("{}")

        removed = store.cleanup_orphan_sessions(tmp_sessions_dir, mock_db, 7)
        assert removed == 0
        assert sessions_json.exists()
