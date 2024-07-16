"""This package abstracts the interface to the AI summary database."""

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import DateTime, Engine, String, UnicodeText, create_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    Session,
    mapped_column,
)

# Maximum length of a Jira issue key (i.e. 'ABC-123')
_MAX_ISSUE_KEY_LEN = 20


class _Base(
    MappedAsDataclass, DeclarativeBase
):  # pylint: disable=too-few-public-methods
    pass


class Summary(_Base):  # pylint: disable=too-few-public-methods
    """
    Database record for storing AI summaries.

    The table has the following columns:
        - issue_key: Jira issue key (e.g. 'ABC-123')
        - ai_summary: The generated AI summary text (nullable, if not yet
          generated)
        - summary_ts: Timestamp when the AI summary was generated (nullable, if
          not yet generated)
        - stale_ts: Timestamp when the AI summary was marked as stale (nullable,
          if the summary is not stale)
        - parent_key: Jira issue key of the parent issue (nullable, if the issue
          does not have a parent)

    Table semantics:
        - The primary key is the issue_key
        - The ai_summary and summary_ts columns are nullable, since the summary
          may not have been generated yet.
        - The stale_ts column indicates whether and when the summary was marked
          as stale
        - Any time the summary text is updated, the summary_ts should be
          updated, and the stale_ts should be cleared.
    """

    __tablename__ = "ai_summary"

    issue_key: Mapped[str] = mapped_column(
        String(_MAX_ISSUE_KEY_LEN),
        primary_key=True,
        comment="Jira issue key (e.g. 'ABC-123')",
    )
    ai_summary: Mapped[Optional[str]] = mapped_column(
        UnicodeText(),
        nullable=True,
        default=None,
        comment="The generated AI summary text",
    )
    summary_ts: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="Timestamp when the AI summary was generated",
    )
    stale_ts: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="Timestamp when the AI summary was marked as stale",
    )
    parent_key: Mapped[Optional[str]] = mapped_column(
        String(_MAX_ISSUE_KEY_LEN),
        nullable=True,
        default=None,
        comment="Jira issue key of the parent issue",
    )


def memory_db() -> Engine:
    """Create an in-memory AI summary database."""
    engine = create_engine("sqlite+pysqlite:///:memory:", echo=True)

    # Create the DB table(s) if they don't exist
    _Base.metadata.create_all(engine)

    return engine


def get_summary(db: Engine, issue_key: str, stale_ok: bool = False) -> Optional[str]:
    """
    Get the AI summary for the given Jira issue key if it exists.

    Parameters:
        - db: Database engine
        - issue_key: Jira issue key
        - stale_ok: Whether to return a potentially stale summary

    Returns:
        - The AI summary text, or None if the summary does not exist or is stale
    """
    with Session(db) as session:
        record = session.get(Summary, issue_key)
    if record is None or (not stale_ok and record.stale_ts is not None):
        return None
    return record.ai_summary


def update_summary(
    db: Engine, issue_key: str, summary: str, parent_key: Optional[str]
) -> None:
    """
    Update the AI summary for the given Jira issue key.

    Parameters:
        - db: Database engine
        - issue_key: Jira issue key
        - summary: The AI summary text
        - parent_key: Jira issue key of the parent issue
    """
    now = datetime.now(tz=UTC)
    with Session(db) as session:
        record = Summary(
            issue_key=issue_key,
            ai_summary=summary,
            parent_key=parent_key,
            summary_ts=now,
            stale_ts=None,
        )
        session.merge(record)
        # Since the parent summaries are influenced by their children, mark the
        # parent summary as stale when the child summary is updated
        if parent_key is not None:
            parent = session.get(Summary, parent_key)
            if parent is not None and parent.stale_ts is None:
                parent.stale_ts = now
        session.commit()


def mark_stale(db: Engine, issue_key: str) -> None:
    """
    Mark the AI summary for the given Jira issue key as stale.

    Parameters:
        - db: Database engine
        - issue_key: Jira issue key
    """
    with Session(db) as session:
        record = session.get(Summary, issue_key)
        if record is None:
            # If the record doesn't exist, create a new one that will be marked
            # as stale. This allows the refresh process to eventually generate a
            # summary for it.
            record = Summary(issue_key=issue_key)
            session.add(record)
        if record.stale_ts is None:
            record.stale_ts = datetime.now(tz=UTC)
            session.commit()


def get_stale_issues(db: Engine, limit: int = 0) -> list[str]:
    """
    Get a list of Jira issue keys that have a stale AI summary, starting with
    the most out-of-date.

    Parameters:
        - db: Database engine
        - limit: Maximum number of stale issues to return (0 for no limit)

    Returns:
        - A list of Jira issue keys
    """
    with Session(db) as session:
        query = (
            session.query(Summary)
            .filter(Summary.stale_ts.isnot(None))
            .order_by(Summary.stale_ts.asc())
        )
        if limit > 0:
            query = query.limit(limit)
    return [record.issue_key for record in query.all()]
