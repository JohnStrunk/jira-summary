"""Helper functions for working with Jira issues."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional

from atlassian import Jira  # type: ignore

_logger = logging.getLogger(__name__)


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

    client: Jira
    """The Jira client to use for API calls."""
    key: str
    """The key of the issue."""
    summary: str
    """The summary of the issue."""
    description: str
    """The description of the issue."""
    issue_type: str
    """The type of the issue."""
    labels: List[str]
    """The labels on the issue."""
    resolution: str
    """The resolution of the issue."""
    status: str
    """The status of the issue."""
    _changelog: Optional[List[ChangelogEntry]]
    _comments: Optional[List[Comment]]
    _related: Optional[List[RelatedIssue]]

    def __init__(self, client: Jira, issue_key: str):
        self.client = client
        self.key = issue_key

        # Only fetch the data we need
        fields = [
            "summary",
            "description",
            "status",
            "labels",
            "resolution",
            "issuetype",
        ]
        data = _check(client.issue(issue_key, fields=",".join(fields)))

        # Populate the fields
        self.summary = data["fields"]["summary"]
        self.description = data["fields"]["description"]
        self.issue_type = data["fields"]["issuetype"]["name"]
        self.status = data["fields"]["status"]["name"]
        self.labels = data["fields"]["labels"]
        self.resolution = (
            data["fields"]["resolution"]["name"]
            if data["fields"]["resolution"]
            else "Unresolved"
        )
        self._changelog = None
        self._comments = None
        self._related = None
        _logger.info(f"Retrieved issue: {self.key} - {self.summary}")

    def __str__(self) -> str:
        return f"{self.key}: {self.summary} ({self.status}/{self.resolution})"

    def _fetch_changelog(self) -> List[ChangelogEntry]:
        """Fetch the changelog from the API."""
        _logger.debug(f"Retrieving changelog for {self.key}")
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
        _logger.debug(f"Retrieving comments for {self.key}")
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
            "customfield_12311140",
            "customfield_12313140",
            "customfield_12318341",
        ]
        _logger.debug(f"Retrieving related links for {self.key}")
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
            ("customfield_12311140", "Epic Link"),  # Upward link to epic
            ("customfield_12313140", "Parent Link"),
        ]
        for cfield, how in custom_fields:
            if cfield in data["fields"].keys() and data["fields"][cfield] is not None:
                related.append(RelatedIssue(key=data["fields"][cfield], how=how))

        # The Feature Link has to be handled separately
        if (
            "customfield_12318341" in data["fields"].keys()
            and data["fields"]["customfield_12318341"] is not None
        ):
            related.append(
                RelatedIssue(
                    key=data["fields"]["customfield_12318341"]["key"],
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
    def last_change(self) -> Optional[ChangelogEntry]:
        """Get the last change in the changelog."""
        if not self.changelog:
            return None
        return self.changelog[len(self.changelog) - 1]

    @property
    def is_last_change_mine(self) -> bool:
        """Check if the last change in the changelog was made by me."""
        me = _check(self.client.myself())
        return (
            self.last_change is not None
            and self.last_change.author == me["displayName"]
        )

    def update_description(self, new_description: str) -> None:
        """
        UPDATE the Jira issue's description ON THE SERVER.

        Parameters:
            - new_description: The new description to set.
        """
        _logger.info(f"Sending updated description for {self.key} to server")
        fields = {"description": new_description}
        self.client.update_issue_field(self.key, fields)  # type: ignore
        self.description = new_description


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
