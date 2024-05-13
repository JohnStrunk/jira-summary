"""Helper functions for working with Jira issues."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

from atlassian import Jira  # type: ignore

_logger = logging.getLogger(__name__)

# Custom field IDs
_CF_EPIC_LINK = "customfield_12311140"  # any
_CF_FEATURE_LINK = "customfield_12318341"  # issuelinks
_CF_PARENT_LINK = "customfield_12313140"  # any
_CF_STATUS_SUMMARY = "customfield_12320841"  # string


@dataclass
class Change:
    """
    Represents a change made to a field.
    """

    field: str
    """The name of the field that was changed."""
    frm: str
    """The previous value of the field."""
    to: str
    """The new value of the field."""


@dataclass
class ChangelogEntry:
    """
    An entry in the changelog for an issue.

    A given entry is actually a set of changes that were all made at the same
    time, by the same author.
    """

    author: str
    """The name of the person who made the change."""
    created: datetime
    """When the change was made."""
    changes: list[Change] = field(default_factory=list)
    """The changes made to the issue."""


@dataclass
class Comment:
    """A comment on an issue."""

    author: str
    """The name of the person who made the comment."""
    created: datetime
    """When the comment was created."""
    body: str
    """The content of the comment."""


_HOW_SUBTASK = "has a sub-task"
_HOW_INEPIC = "is the Epic issue for"
_HOW_INPARENT = "is the parent issue of"


@dataclass
class RelatedIssue:
    """A reference to a related issue and how it's related."""

    key: str
    """The Jira key of the related issue"""
    how: str
    """How the related issue is related to the main issue"""

    @property
    def is_child(self) -> bool:
        """True if the related issue is a child of the main issue."""
        return self.how in [_HOW_SUBTASK, _HOW_INEPIC, _HOW_INPARENT]


class Issue:  # pylint: disable=too-many-instance-attributes
    """
    Represents a Jira issue as a proper object.
    """

    def __init__(self, client: Jira, issue_key: str) -> None:
        self.client = client
        self.key = issue_key

        # Only fetch the data we need
        fields = [
            "summary",
            "description",
            "issuetype",
            "parent",
            "project",
            "status",
            "labels",
            "resolution",
            "updated",
            _CF_STATUS_SUMMARY,
        ]
        data = _check(client.issue(issue_key, fields=",".join(fields)))

        # Populate the fields
        self.summary: str = data["fields"]["summary"]
        self.description: str = data["fields"]["description"] or ""
        self.issue_type: str = data["fields"]["issuetype"]["name"]
        self.project_key: str = data["fields"]["project"]["key"]
        self._parent_key: Optional[str] = (
            data.get("fields", {}).get("parent", {}).get("key", None)
        )
        self.status: str = data["fields"]["status"]["name"]
        self.labels: List[str] = data["fields"]["labels"]
        self.resolution: str = (
            data["fields"]["resolution"]["name"]
            if data["fields"]["resolution"]
            else "Unresolved"
        )
        self.updated: datetime = datetime.fromisoformat(data["fields"]["updated"])
        self.status_summary: str = data["fields"].get(_CF_STATUS_SUMMARY) or ""
        self._changelog: Optional[List[ChangelogEntry]] = None
        self._comments: Optional[List[Comment]] = None
        self._related: Optional[List[RelatedIssue]] = None
        _logger.info("Retrieved issue: %s - %s", self.key, self.summary)

    def __str__(self) -> str:
        return f"{self.key}: {self.summary} ({self.status}/{self.resolution})"

    def _fetch_changelog(self) -> List[ChangelogEntry]:
        """Fetch the changelog from the API."""
        _logger.debug("Retrieving changelog for %s", self.key)
        log = _check(self.client.get_issue_changelog(self.key, start=0, limit=1000))
        items: List[ChangelogEntry] = []
        for entry in log["histories"]:
            changes: List[Change] = []
            for change in entry["items"]:
                changes.append(
                    Change(
                        field=change["field"],
                        frm=change["fromString"],
                        to=change["toString"],
                    )
                )
            items.append(
                ChangelogEntry(
                    author=entry["author"]["displayName"],
                    created=datetime.fromisoformat(entry["created"]),
                    changes=changes,
                )
            )
        return items

    @property
    def changelog(self) -> List[ChangelogEntry]:
        """The changelog for the issue."""
        # Since it requires an additional API call, we only fetch it if it's
        # accessed, and we cache the result.
        if not self._changelog:
            self._changelog = self._fetch_changelog()
        return self._changelog

    def _fetch_comments(self) -> List[Comment]:
        """Fetch the comments from the API."""
        _logger.debug("Retrieving comments for %s", self.key)
        comments = _check(self.client.issue(self.key, fields="comment"))["fields"][
            "comment"
        ]["comments"]
        items: List[Comment] = []
        for comment in comments:
            items.append(
                Comment(
                    author=comment["author"]["displayName"],
                    created=datetime.fromisoformat(comment["created"]),
                    body=comment["body"],
                )
            )
        return items

    @property
    def comments(self) -> List[Comment]:
        """The comments on the issue."""
        if not self._comments:
            self._comments = self._fetch_comments()
        return self._comments

    def _fetch_related(self) -> List[RelatedIssue]:
        """Fetch the related issues from the API."""
        fields = [
            "issuelinks",
            "subtasks",
            _CF_EPIC_LINK,
            _CF_PARENT_LINK,
            _CF_FEATURE_LINK,
        ]
        _logger.debug("Retrieving related links for %s", self.key)
        data = _check(self.client.issue(self.key, fields=",".join(fields)))
        # Get the related issues
        related: List[RelatedIssue] = []
        for link in data["fields"]["issuelinks"]:
            if "inwardIssue" in link:
                related.append(
                    RelatedIssue(
                        key=link["inwardIssue"]["key"], how=link["type"]["inward"]
                    )
                )
            elif "outwardIssue" in link:
                related.append(
                    RelatedIssue(
                        key=link["outwardIssue"]["key"], how=link["type"]["outward"]
                    )
                )

        # Get the sub-tasks
        for subtask in data["fields"]["subtasks"]:
            related.append(RelatedIssue(key=subtask["key"], how=_HOW_SUBTASK))

        # Get the parent task(s) and epic links from the custom fields
        custom_fields = [
            (_CF_EPIC_LINK, "Epic Link"),  # Upward link to epic
            (_CF_PARENT_LINK, "Parent Link"),
        ]
        for cfield, how in custom_fields:
            if cfield in data["fields"].keys() and data["fields"][cfield] is not None:
                related.append(RelatedIssue(key=data["fields"][cfield], how=how))

        # The Feature Link has to be handled separately
        if (
            _CF_FEATURE_LINK in data["fields"].keys()
            and data["fields"][_CF_FEATURE_LINK] is not None
        ):
            related.append(
                RelatedIssue(
                    key=data["fields"][_CF_FEATURE_LINK]["key"],
                    how="Feature Link",
                )
            )

        # Issues in the epic requires a query since there's no pointer from the epic
        # issue to it's children. epic_issues returns an error if the issue is not
        # an Epic. These are downward links to children
        if self.issue_type == "Epic":
            issues_in_epic = _check(self.client.epic_issues(self.key, fields="key"))
            for i in issues_in_epic["issues"]:
                related.append(RelatedIssue(key=i["key"], how=_HOW_INEPIC))
        else:
            # Non-epic issues use the parent link
            issues_with_parent = _check(
                self.client.jql(f"'Parent Link' = '{self.key}'", limit=50, fields="key")
            )
            for i in issues_with_parent["issues"]:
                related.append(RelatedIssue(key=i["key"], how=_HOW_INPARENT))

        return related

    @property
    def related(self) -> List[RelatedIssue]:
        """Other issues that are related to this one."""
        if not self._related:
            self._related = self._fetch_related()
        return self._related

    @property
    def children(self) -> List[RelatedIssue]:
        """The child issues of this issue."""
        return [rel for rel in self.related if rel.is_child]

    @property
    def parent(self) -> Optional[str]:
        """The parent issue of this issue."""
        if self._parent_key:
            return self._parent_key
        for rel in self.related:
            if rel.how in ["Epic Link", "Parent Link"]:
                return rel.key
        return None

    @property
    def all_parents(self) -> List[str]:
        """All the parent issues of this issue."""
        parents = []
        issue = issue_cache.get_issue(self.client, self.key)
        while issue.parent:
            parents.append(issue.parent)
            issue = issue_cache.get_issue(self.client, issue.parent)
        return parents

    @property
    def level(self) -> int:
        """The level of this issue in the hierarchy."""
        # https://spaces.redhat.com/pages/viewpage.action?spaceKey=JiraAid&title=Red+Hat+Standards%3A+Issue+Types
        level_mapping: dict[str, int] = {
            "Sub-task": 1,
            ### Level 2 ###
            "Bug": 2,
            "Change Request": 2,
            "Closed Loop": 2,
            "Component Upgrade": 2,
            "Enhancement": 2,
            "Incident": 2,
            "Risk": 2,
            "Spike": 2,
            "Story": 2,
            "Support Patch": 2,
            "Task": 2,
            "Ticket": 2,
            ### Level 3 ###
            "Epic": 3,
            "Release Milestone": 3,
            ### Level 4 ###
            "Feature": 4,
            "Feature Request": 4,
            "Initiative": 4,
            "Release Tracker": 4,
            "Requirement": 4,
            ### Level 5 ###
            "Outcome": 5,
            ### Level 6 ###
            "Strategic Goal": 6,
        }
        level = level_mapping.get(self.issue_type, 0)
        if level == 0:
            _logger.warning("Unknown issue type: %s", self.issue_type)
        return level

    @property
    def last_change(self) -> Optional[ChangelogEntry]:
        """Get the last change in the changelog."""
        if not self.changelog:
            return None
        return self.changelog[len(self.changelog) - 1]

    @property
    def last_comment(self) -> Optional[Comment]:
        """Get the last comment on the issue."""
        if not self.comments:
            return None
        return self.comments[len(self.comments) - 1]

    @property
    def is_last_change_mine(self) -> bool:
        """Check if the last change in the changelog was made by me."""
        me = _check(self.client.myself())
        return (
            self.last_change is not None
            and self.last_change.author == me["displayName"]
        )

    def update_status_summary(self, contents: str) -> None:
        """
        UPDATE the Jira issue's description ON THE SERVER.

        Parameters:
            - contents: The new description to set.
        """
        _logger.info("Sending updated status summary for %s to server", self.key)
        fields = {_CF_STATUS_SUMMARY: contents}
        self.client.update_issue_field(self.key, fields)  # type: ignore
        self.status_summary = contents
        issue_cache.remove(self.key)  # Invalidate any cached copy


def _check(response: Any) -> dict:
    """
    Check the response from the Jira API and raise an exception if it's an
    error.

    This is a horrible hack of a wrapper to make the types work out. The types
    returned by Jira API (via the atlassian module) are not well defined. In
    general, when things go well, you get back a dict. Otherwise, you could get
    anything.
    """
    if isinstance(response, dict):
        return response
    raise ValueError(f"Unexpected response: {response}")


class Myself:  # pylint: disable=too-few-public-methods
    """
    Represents the current user in Jira.
    """

    def __init__(self, client: Jira) -> None:
        self.client = client
        self._data = _check(client.myself())
        # Break out the fields we care about
        self.display_name: str = self._data["displayName"]
        self.key: str = self._data["key"]
        self.timezone: str = self._data["timeZone"]
        self.tzinfo = ZoneInfo(self.timezone)


class IssueCache:
    """
    A cache of Jira issues to avoid fetching the same issue multiple times.
    """

    def __init__(self) -> None:
        self._cache: dict[str, Issue] = {}
        self.hits = 0
        self.tries = 0

    def get_issue(self, client: Jira, key: str, refresh: bool = False) -> Issue:
        """
        Get an issue from the cache, or fetch it from the server if it's not
        already cached.

        Parameters:
            - client: The Jira client to use for fetching the issue.
            - key: The key of the issue to fetch.

        Returns:
            The issue object.
        """
        self.tries += 1
        if refresh or key not in self._cache:
            _logger.debug("Cache miss: %s", key)
            self._cache[key] = Issue(client, key)
        else:
            self.hits += 1
            _logger.debug("Cache hit: %s", key)
        return self._cache[key]

    def remove(self, key: str) -> None:
        """
        Remove an Issue from the cache.

        Parameters:
            - key: The key of the issue to remove.
        """
        if key in self._cache:
            del self._cache[key]

    def clear(self) -> None:
        """Clear the cache."""
        self._cache = {}


# The global cache of issues
issue_cache = IssueCache()
