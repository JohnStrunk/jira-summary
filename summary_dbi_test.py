"""Test the database interface."""

import pytest
from sqlalchemy import Engine

from summary_dbi import (
    Summary,
    get_stale_issues,
    get_summary,
    mark_stale,
    memory_db,
    update_summary,
)


class TestDBI:
    """Test the database interface."""

    @pytest.fixture
    def db(self) -> Engine:
        """Create an empty database for testing."""
        return memory_db()

    @pytest.fixture
    def with_abc_123(self, db) -> dict[str, str]:
        """Add a test summary for issue ABC-123."""
        key = "ABC-123"
        summary = "This is a test summary."
        update_summary(db, "ABC-123", "This is a test summary.", None)
        return {"key": key, "summary": summary}

    def test_db_has_summary_table(self, db):
        """Test that the database has the summary table."""
        assert db is not None
        meta = Summary.metadata
        assert meta.tables.get(Summary.__tablename__) is not None

    def test_get_summary_no_summary(self, db):
        """Test getting a summary that does not exist."""
        assert get_summary(db, "ZZZ-999") is None

    def test_get_summary(self, db, with_abc_123):
        """Test adding and fetching a summary."""
        assert get_summary(db, with_abc_123["key"]) == with_abc_123["summary"]

    def test_stale(self, db, with_abc_123):
        """Test that stale summaries are handled."""
        # Mark the summary as stale
        mark_stale(db, with_abc_123["key"])
        # By default, stale summaries are not returned
        assert get_summary(db, with_abc_123["key"]) is None
        # But they can be returned if requested
        assert (
            get_summary(db, with_abc_123["key"], stale_ok=True)
            == with_abc_123["summary"]
        )
        # Stale records can be listed
        stale_issues = get_stale_issues(db)
        assert len(stale_issues) == 1
        assert with_abc_123["key"] in stale_issues

    def test_updated_summaries_are_not_stale(self, db, with_abc_123):
        """Test that updating a summary makes it not stale."""
        mark_stale(db, with_abc_123["key"])
        update_summary(db, with_abc_123["key"], "New summary", None)
        assert get_summary(db, with_abc_123["key"]) == "New summary"

    def test_parent_is_marked_stale(self, db, with_abc_123):
        """Test that updating a child summary marks the parent as stale."""
        # Add a child issue of abc-123
        child_key = "DEF-456"
        update_summary(db, child_key, "zzz", with_abc_123["key"])
        # Parent should now be stale
        assert get_stale_issues(db) == [with_abc_123["key"]]
