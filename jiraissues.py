"""Helper functions for working with Jira issues."""

import logging
import queue
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import reduce
from operator import getitem
from typing import Any, List, Optional, Set
from zoneinfo import ZoneInfo

import requests
from atlassian import Jira  # type: ignore
from backoff_utils import backoff, strategies  # type: ignore

from simplestats import measure_function

_logger = logging.getLogger(__name__)

# Custom field IDs
CF_BLOCKED = "customfield_12316543"  # option
CF_BLOCKED_REASON = "customfield_12316544"  # string
CF_CONTRIBUTORS = "customfield_12315950"  # list
CF_EPIC_LINK = "customfield_12311140"  # any
CF_FEATURE_LINK = "customfield_12318341"  # issuelinks
CF_PARENT_LINK = "customfield_12313140"  # any
CF_STATUS_SUMMARY = "customfield_12320841"  # string

# Exceptions that should trigger a backoff
BACKOFF_EXCEPTIONS: list[type[Exception]] = [
    requests.exceptions.ConnectionError,
    requests.exceptions.HTTPError,
    requests.exceptions.ReadTimeout,
]
# The Jira API seems to be limited to < 10 QPS
BACKOFF_STRATEGY = strategies.Exponential(minimum=0.1, maximum=60, factor=2)


def rget(d: dict, *path, default=None) -> Any:
    # Based on:
    # https://stackoverflow.com/questions/28225552/is-there-a-recursive-version-of-the-dict-get-built-in
    """
    A recursive version of dict.get().

    Parameters:
        - d: The dictionary to search.
        - path: The path to the key to get.
        - default: The default value to return if any key along the path is not
          found.

    Returns:
        The value of the key, or the default value if the key is not found.

    Examples:
    >>> rget({"a": {"b": {"c": 42}}}, "a", "b", "c")
    42
    >>> rget({"a": {"b": {"c": 42}}}, "a", "b", "z")  # returns None
    >>> rget({"a": {"b": {"c": 42}}}, "a", "b", "z", default="Not found")
    'Not found'
    >>> rget({"a": {"b": {"c": 42}}}, "a", "b")
    {'c': 42}
    >>> rget({"a": {"b": "not a dict"}}, "a", "b", "c", default="Not found")
    'Not found'
    """
    try:
        return reduce(getitem, path, d)
    except KeyError:  # Element not found along path
        return default
    except TypeError:  # Element not a dict
        return default


def with_retry(func):
    """
    Wrapper to apply backoff to a function.

    Parameters:
        - func: The function to wrap.

    Returns:
        The result of the function.
    """
    return backoff(
        func,
        max_tries=100,
        strategy=BACKOFF_STRATEGY,
        catch_exceptions=BACKOFF_EXCEPTIONS,
    )


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


# How issues are related: MAIN <relationship> RELATED
_HOW_SUBTASK = "has a sub-task"
_HOW_INEPIC = "is the parent of"
_HOW_INPARENT = "is the parent of"


@dataclass
class RelatedIssue:
    """A reference to a related issue and how it's related."""

    key: str
    """The Jira key of the related issue"""
    how: str
    """How the issue is related to the main issue"""
    summary: str
    """The title of the issue"""
    issue_type: str
    """The type of the issue (e.g., "Task")"""
    status: str
    """The status of the issue"""
    resolution: str
    """The resolution of the issue"""

    @property
    def is_child(self) -> bool:
        """True if the related issue is a child of the main issue."""
        return self.how in [_HOW_SUBTASK, _HOW_INEPIC, _HOW_INPARENT]

    def __str__(self) -> str:
        return f"{self.key} ({self.issue_type}) - {self.summary} ({self.status}/{self.resolution})"


class User:  # pylint: disable=too-few-public-methods
    """A Jira user."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.display_name = str(data.get("displayName", ""))
        self.key = str(data.get("key", ""))
        self.name = str(data.get("name", ""))
        self.timezone = str(data.get("timeZone", ""))
        self.tzinfo = ZoneInfo(self.timezone)

    def __str__(self) -> str:
        return f"{self.display_name} ({self.key})"

    def __hash__(self) -> int:
        return hash((self.key, self.name, self.display_name, self.timezone))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, User):
            return False
        return (
            self.key == other.key
            and self.name == other.name
            and self.display_name == other.display_name
            and self.timezone == other.timezone
        )


class Issue:  # pylint: disable=too-many-instance-attributes
    """
    Represents a Jira issue as a proper object.
    """

    @measure_function
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
            CF_STATUS_SUMMARY,
            CF_BLOCKED,
            CF_BLOCKED_REASON,
            CF_CONTRIBUTORS,
            "comment",
            "assignee",
            CF_EPIC_LINK,
            CF_PARENT_LINK,
        ]
        # Need to Handle 403 errors
        # DEBUG:urllib3.connectionpool:https://server.com:443 "GET
        # /rest/api/2/issue/XXXX-16688?fields=summary,...,comment HTTP/1.1" 403
        # None
        # DEBUG:atlassian.rest_client:HTTP: GET
        # rest/api/2/issue/XXXX-16688?fields=summary,...,comment -> 403
        # Forbidden
        # DEBUG:atlassian.rest_client:HTTP: Response text ->
        # {"errorMessages":["You do not have the permission to see the specified
        # issue."],"errors":{}}
        data = check_response(
            with_retry(lambda: client.issue(issue_key, fields=",".join(fields)))
        )

        # Populate the fields
        self.summary: str = rget(data, "fields", "summary", default="")
        self.description: str = rget(data, "fields", "description", default="")
        self.issue_type: str = rget(data, "fields", "issuetype", "name", default="")
        self.project_key: str = rget(data, "fields", "project", "key", default="")
        self.status: str = rget(data, "fields", "status", "name", default="")
        self.labels: Set[str] = set(rget(data, "fields", "labels", default=[]))
        self.resolution: str = rget(
            data, "fields", "resolution", "name", default="Unresolved"
        )
        # The "last updated" time is provided w/ TZ info
        self.updated: datetime = datetime.fromisoformat(rget(data, "fields", "updated"))
        self.status_summary: str = rget(data, "fields", CF_STATUS_SUMMARY, default="")
        self._changelog: Optional[List[ChangelogEntry]] = None
        self._comments: Optional[List[Comment]] = None
        # Go ahead and parse the comments to avoid an extra API call
        self._comments = self._parse_comment_data(
            rget(data, "fields", "comment", "comments", default=[])
        )
        self._related: Optional[List[RelatedIssue]] = None
        # Some instances have None for the blocked flag instead of a value
        blocked_dict = rget(data, "fields", CF_BLOCKED, default={}) or {}
        self.blocked = str(blocked_dict.get("value", "False")).lower() in ["true"]
        self.blocked_reason: str = rget(data, "fields", CF_BLOCKED_REASON, default="")
        self.contributors = {
            User(user) for user in (rget(data, "fields", CF_CONTRIBUTORS) or [])
        }
        self.assignee = (
            User(data["fields"]["assignee"]) if data["fields"]["assignee"] else None
        )
        # The parent link can be from several sources. They are listed below in
        # reverse order of preference:
        self._parent_key: Optional[str] = rget(data, "fields", CF_EPIC_LINK)
        self._parent_key = rget(data, "fields", CF_PARENT_LINK) or self._parent_key
        self._parent_key = rget(data, "fields", "parent", "key") or self._parent_key

        _logger.info("Retrieved issue: %s", self)

    def __str__(self) -> str:
        return (
            f"{self.key} ({self.issue_type}) - "
            + f"{self.summary} ({self.status}/{self.resolution})"
        )

    def __lt__(self, other: "Issue") -> bool:
        # Issue keys consist of a prefix and a number such as ABCD-1234. We
        # define the sort order based on the prefix as a string, followed by the
        # number as an integer.
        self_prefix, self_number = self.key.split("-")
        other_prefix, other_number = other.key.split("-")
        if self_prefix != other_prefix:
            return self_prefix < other_prefix
        return int(self_number) < int(other_number)

    @measure_function
    def _fetch_changelog(self) -> List[ChangelogEntry]:
        """Fetch the changelog from the API."""
        _logger.debug("Retrieving changelog for %s", self.key)
        log = check_response(
            with_retry(
                lambda: self.client.get_issue_changelog(self.key, start=0, limit=1000)
            )
        )
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
                    author=rget(entry, "author", "displayName", default=""),
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

    @measure_function
    def _fetch_comments(self) -> List[Comment]:
        """Fetch the comments from the API."""
        _logger.debug("Retrieving comments for %s", self.key)
        comments = check_response(
            with_retry(lambda: self.client.issue(self.key, fields="comment"))
        )["fields"]["comment"]["comments"]
        return self._parse_comment_data(comments)

    def _parse_comment_data(self, comments: List[dict[str, Any]]) -> List[Comment]:
        items: List[Comment] = []
        for comment in comments:
            items.append(
                Comment(
                    author=rget(comment, "author", "displayName", default=""),
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

    @measure_function
    def _fetch_related(self) -> List[RelatedIssue]:  # pylint: disable=too-many-branches
        """Fetch the related issues from the API."""
        fields = [
            "issuelinks",
            "subtasks",
            CF_FEATURE_LINK,
        ]
        found_issues: set[str] = set()
        _logger.debug("Retrieving related links for %s", self.key)
        data = check_response(
            with_retry(lambda: self.client.issue(self.key, fields=",".join(fields)))
        )
        # Get the related issues
        related: List[RelatedIssue] = []
        for link in data["fields"]["issuelinks"]:
            if "inwardIssue" in link and link["inwardIssue"]["key"] not in found_issues:
                rfields = link["inwardIssue"]["fields"]
                related.append(
                    RelatedIssue(
                        key=link["inwardIssue"]["key"],
                        how=link["type"]["inward"],
                        summary=rfields["summary"],
                        issue_type=rfields["issuetype"]["name"],
                        status=rfields["status"]["name"],
                        resolution=rfields["status"]["statusCategory"]["name"],
                    )
                )
                found_issues.add(link["inwardIssue"]["key"])
            elif (
                "outwardIssue" in link
                and link["outwardIssue"]["key"] not in found_issues
            ):
                rfields = link["outwardIssue"]["fields"]
                related.append(
                    RelatedIssue(
                        key=link["outwardIssue"]["key"],
                        how=link["type"]["outward"],
                        summary=rfields["summary"],
                        issue_type=rfields["issuetype"]["name"],
                        status=rfields["status"]["name"],
                        resolution=rfields["status"]["statusCategory"]["name"],
                    )
                )
                found_issues.add(link["outwardIssue"]["key"])

        # Get the sub-tasks
        for subtask in data["fields"]["subtasks"]:
            if subtask["key"] not in found_issues:
                related.append(
                    RelatedIssue(
                        key=subtask["key"],
                        how=_HOW_SUBTASK,
                        summary=subtask["fields"]["summary"],
                        issue_type=subtask["fields"]["issuetype"]["name"],
                        status=subtask["fields"]["status"]["name"],
                        resolution=subtask["fields"]["status"]["statusCategory"][
                            "name"
                        ],
                    )
                )
                found_issues.add(subtask["key"])

        # The Feature Link has to be handled separately
        feature = rget(data, "fields", CF_FEATURE_LINK)
        if feature is not None and feature["key"] not in found_issues:
            related.append(
                RelatedIssue(
                    key=feature["key"],
                    how="Feature Link",
                    summary=rget(feature, "fields", "summary", default=""),
                    issue_type=rget(
                        feature, "fields", "issuetype", "name", default="unknown"
                    ),
                    status=rget(feature, "fields", "status", "name", default="unknown"),
                    resolution=rget(
                        feature,
                        "fields",
                        "status",
                        "statusCategory",
                        "name",
                        default="unknown",
                    ),
                )
            )
            found_issues.add(feature["key"])

        # Issues in the epic requires a query since there's no pointer from the epic
        # issue to it's children. epic_issues returns an error if the issue is not
        # an Epic. These are downward links to children
        if self.issue_type == "Epic":
            issues_in_epic = check_response(
                with_retry(
                    lambda: self.client.epic_issues(
                        self.key, fields="key, summary, issuetype, status"
                    )
                )
            )
            for i in issues_in_epic["issues"]:
                if i["key"] not in found_issues:
                    related.append(
                        RelatedIssue(
                            key=i["key"],
                            how=_HOW_INEPIC,
                            summary=rget(i, "fields", "summary", default=""),
                            issue_type=rget(
                                i, "fields", "issuetype", "name", default="unknown"
                            ),
                            status=rget(
                                i, "fields", "status", "name", default="unknown"
                            ),
                            resolution=rget(
                                i,
                                "fields",
                                "status",
                                "statusCategory",
                                "name",
                                default="unknown",
                            ),
                        )
                    )
                    found_issues.add(i["key"])
        else:
            # Non-epic issues use the parent link
            issues_with_parent = check_response(
                with_retry(
                    lambda: self.client.jql(
                        f"'Parent Link' = '{self.key}'",
                        limit=200,
                        fields="key, summary, issuetype, status",
                    )
                )
            )
            for i in issues_with_parent["issues"]:
                if i["key"] not in found_issues:
                    related.append(
                        RelatedIssue(
                            key=i["key"],
                            how=_HOW_INPARENT,
                            summary=rget(i, "fields", "summary", default=""),
                            issue_type=rget(
                                i, "fields", "issuetype", "name", default="unknown"
                            ),
                            status=rget(
                                i, "fields", "status", "name", default="unknown"
                            ),
                            resolution=rget(
                                i,
                                "fields",
                                "status",
                                "statusCategory",
                                "name",
                                default="unknown",
                            ),
                        )
                    )
                    found_issues.add(i["key"])

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
    @measure_function
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
        myself = get_self(self.client)
        return (
            self.last_change is not None
            and self.last_change.author == myself.display_name
        )

    @measure_function
    def update_status_summary(self, contents: str) -> None:
        """
        UPDATE the Jira issue's description ON THE SERVER.

        Parameters:
            - contents: The new description to set.
        """
        _logger.info("Sending updated status summary for %s to server", self.key)
        fields = {CF_STATUS_SUMMARY: contents}
        with_retry(
            lambda: self.client.update_issue_field(self.key, fields)  # type: ignore
        )
        self.status_summary = contents
        issue_cache.remove(self.key)  # Invalidate any cached copy

    def update_labels(self, new_labels: Set[str]) -> None:
        """
        UPDATE the Jira issue's labels ON THE SERVER.

        Parameters:
            - labels: The new set of labels for the issue.
        """
        _logger.info("Sending updated labels for %s to server", self.key)
        fields = {"labels": list(new_labels)}
        with_retry(
            lambda: self.client.update_issue_field(self.key, fields)  # type: ignore
        )
        self.labels = new_labels
        issue_cache.remove(self.key)  # Invalidate any cached copy


def check_response(response: Any) -> dict:
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


_self: Optional[User] = None


def get_self(client: Jira) -> User:
    """
    Caching function for the Myself object.
    """
    global _self  # pylint: disable=global-statement
    if _self is None:
        data = check_response(with_retry(client.myself))
        _self = User(data)
    return _self


class IssueCache:
    """
    A cache of Jira issues to avoid fetching the same issue multiple times.
    """

    @dataclass
    class Entry:
        """
        An entry in the cache.
        """

        issue: Issue
        insert_time: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
        last_used_time: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
        fetch_count: int = 0

    def __init__(self, max_size: int) -> None:
        self.lock = threading.Lock()
        self._cache: dict[str, IssueCache.Entry] = {}
        self.hits = 0
        self.tries = 0
        self.max_size = max_size

    @measure_function
    def get_issue(self, client: Jira, key: str) -> Issue:
        """
        Get an issue from the cache, or fetch it from the server if it's not
        already cached.

        Parameters:
            - client: The Jira client to use for fetching the issue.
            - key: The key of the issue to fetch.

        Returns:
            The issue object.
        """
        with self.lock:
            self.tries += 1
            if key not in self._cache:
                _logger.debug("Cache miss: %s", key)
                if len(self._cache) == self.max_size:
                    # Remove the least recently used entry from the cache as
                    # determined by last_used_time
                    del self._cache[
                        min(self._cache, key=lambda k: self._cache[k].last_used_time)
                    ]
                issue = Issue(client, key)
                self._cache[key] = IssueCache.Entry(issue)
            else:
                self.hits += 1
                _logger.debug("Cache hit: %s", key)
                self._cache[key].fetch_count += 1
                self._cache[key].last_used_time = datetime.now()
            return self._cache[key].issue

    def remove(self, key: str) -> None:
        """
        Remove an Issue from the cache.

        Parameters:
            - key: The key of the issue to remove.
        """
        with self.lock:
            if key in self._cache:
                del self._cache[key]

    def remove_older_than(self, when: datetime) -> None:
        """
        Remove all issues from the cache that were inserted before the given
        time.

        Parameters:
            - when: The time before which to remove issues.
        """
        with self.lock:
            for key in list(self._cache.keys()):
                if self._cache[key].insert_time < when:
                    del self._cache[key]

    def clear(self) -> None:
        """Clear the cache."""
        with self.lock:
            self._cache = {}

    def __str__(self) -> str:
        with self.lock:
            hr = self.hits * 100 / self.tries if self.tries > 0 else 0
            return f"Hits: {self.hits} ({hr:.1f}%), Tries: {self.tries}, Size: {len(self._cache)}"


# The global cache of issues
issue_cache = IssueCache(10000)


@measure_function
def descendants(client: Jira, issue_key: str) -> list[str]:
    """
    Get the descendants of an issue.

    Parameters:
        - client: The Jira client to use for fetching the issues.
        - issue_key: The key of the issue to get the descendants of.

    Returns:
        A list of issue keys that are descendants of the given issue.
    """
    pending: queue.SimpleQueue[str] = queue.SimpleQueue()
    pending.put(issue_key)

    desc: list[str] = []

    while not pending.empty():
        key = pending.get()
        result = check_response(
            with_retry(
                lambda: client.jql(
                    f"'Epic Link' = '{key}' or 'Parent Link' = '{key}'",
                    limit=200,
                    fields="key",
                )
            )
        )
        for issue in result["issues"]:
            issue_key = issue["key"]
            desc.append(issue_key)
            pending.put(issue_key)
    return desc
