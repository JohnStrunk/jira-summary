"""Helper functions for working with Jira issues."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional

from atlassian import Jira  # type: ignore


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


@dataclass
class RelatedIssue:
    """A reference to a related issue and how it's related."""

    key: str
    """The Jira key of the related issue"""
    how: str
    """How the related issue is related to the main issue"""


class Issue:  # pylint: disable=too-many-instance-attributes
    """
    Represents a Jira issue as a proper object.
    """

    _client: Jira
    """The Jira client to use for API calls."""
    key: str
    """The key of the issue."""
    summary: str
    """The summary of the issue."""
    description: str
    """The description of the issue."""
    issue_type: str
    """The type of the issue."""
    status: str
    """The status of the issue."""
    resolution: str
    """The resolution of the issue."""
    _changelog: Optional[List[ChangelogEntry]]
    _comments: Optional[List[Comment]]
    _related: Optional[List[RelatedIssue]]

    def __init__(self, client: Jira, issue_key: str):
        self._client = client
        self.key = issue_key

        # Only fetch the data we need
        fields = [
            "summary",
            "description",
            "status",
            "resolution",
            "issuetype",
        ]
        data = _check(client.issue(issue_key, fields=",".join(fields)))

        # Populate the fields
        self.summary = data["fields"]["summary"]
        self.description = data["fields"]["description"]
        self.issue_type = data["fields"]["issuetype"]["name"]
        self.status = data["fields"]["status"]["name"]
        self.resolution = (
            data["fields"]["resolution"]["name"]
            if data["fields"]["resolution"]
            else "Unresolved"
        )
        self._changelog = None
        self._comments = None
        self._related = None

    def __str__(self) -> str:
        return f"{self.key}: {self.summary} ({self.status}/{self.resolution})"

    def _fetch_changelog(self) -> List[ChangelogEntry]:
        """Fetch the changelog from the API."""
        log = _check(self._client.get_issue_changelog(self.key, start=0, limit=100))
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
        # comments = self._raw_data["fields"]["comment"]["comments"]
        comments = _check(self._client.issue(self.key, fields="comment"))["fields"][
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
        data = _check(self._client.issue(self.key, fields=",".join(fields)))
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
            related.append(RelatedIssue(key=subtask["key"], how="sub-task"))

        # Get the parent task(s) and epic links from the custom fields
        custom_fields = [
            ("customfield_12311140", "Epic Link"),
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
        # an Epic
        if self.issue_type == "Epic":
            issues_in_epic = _check(self._client.epic_issues(self.key, fields="key"))
            for i in issues_in_epic["issues"]:
                related.append(RelatedIssue(key=i["key"], how="In this Epic"))

        return related

    @property
    def related(self) -> List[RelatedIssue]:
        """Other issues that are related to this one."""
        if not self._related:
            self._related = self._fetch_related()
        return self._related


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
