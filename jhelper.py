"""Helper functions for working with Jira issues."""

from dataclasses import dataclass
from typing import Any

from atlassian import Jira  # type: ignore


@dataclass
class RelatedIssue:
    """Structure to hold a related issue and how it's related."""

    key: str
    """The Jira key of the related issue"""
    how: str
    """How the related issue is related to the main issue"""


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


def changelog(jira: Jira, key: str) -> list[dict]:
    """
    Given an issue key, return the changelog for the issue.

    Parameters:
        jira: The Jira object to use for querying the API.
        key: The issue key to get the changelog for.

    Returns:
        A list of dicts with the changelog. Each entry has the following fields:
        - who: The name of the person who made the change.
        - when: The timestamp of the change.
        - what: A list of dicts with the changes. Each change has the following fields:
            - field: The name of the field that was changed.
            - from: The old value of the field. (may be empty)
            - to: The new value of the field. (may be empty)
    """
    log = jira.get_issue_changelog(key, start=0, limit=100)
    items = []
    for entry in log["histories"]:
        changes = []
        for change in entry["items"]:
            changes.append(
                {
                    "field": change["field"],
                    "from": change["fromString"],
                    "to": change["toString"],
                }
            )
        items.append(
            {
                "who": entry["author"]["displayName"],
                "when": entry["created"],
                "what": changes,
            }
        )
    return items


def get_issue(jira: Jira, key: str) -> dict:
    """
    Given an issue key, return the issue.

    Parameters:
        jira: The Jira object to use for querying the API.
        key: The issue key to get the issue for.

    Returns:
        A dict with the issue.
    """
    return _check(jira.get_issue(key))


def related_issues(jira: Jira, key: str) -> list[RelatedIssue]:
    """
    Given an issue, return a list of the related issues and their relationship.

    Parameters:
        jira: The Jira object to use for querying the API.
        key: The issue key to get the related issues for.

    Returns:
        A list of RelatedIssue objects with the related issues
    """
    related = []

    issue = get_issue(jira, key)

    # Get the related issues
    for link in issue["fields"]["issuelinks"]:
        if "inwardIssue" in link:
            related.append(
                RelatedIssue(key=link["inwardIssue"]["key"], how=link["type"]["inward"])
            )
        elif "outwardIssue" in link:
            related.append(
                RelatedIssue(
                    key=link["outwardIssue"]["key"], how=link["type"]["outward"]
                )
            )

    # Get the sub-tasks
    for subtask in issue["fields"]["subtasks"]:
        related.append(RelatedIssue(key=subtask["key"], how="sub-task"))

    # Get the parent task(s) and epic links from the custom fields
    custom_fields = [
        ("customfield_12311140", "Epic Link"),
        ("customfield_12313140", "Parent Link"),
    ]
    for field, how in custom_fields:
        if field in issue["fields"].keys() and issue["fields"][field] is not None:
            related.append(RelatedIssue(key=issue["fields"][field], how=how))

    # The Feature Link has to be handled separately
    if (
        "customfield_12318341" in issue["fields"].keys()
        and issue["fields"]["customfield_12318341"] is not None
    ):
        related.append(
            RelatedIssue(
                key=issue["fields"]["customfield_12318341"]["key"],
                how="Feature Link",
            )
        )

    # Issues in the epic requires a query since there's no pointer from the epic
    # issue to it's children. epic_issues returns an error if the issue is not
    # an Epic
    if issue["fields"]["issuetype"]["name"] == "Epic":
        issues_in_epic = _check(jira.epic_issues(key, fields="key"))
        for i in issues_in_epic["issues"]:
            related.append(RelatedIssue(key=i["key"], how="In this Epic"))

    return related


def issue_one_liner(issue: dict) -> str:
    """
    Return a one-liner for the issue.

    Parameters:
        issue: The issue to generate a one-liner for.

    Returns:
        A string with the issue key, summary, status, and resolution.
    """
    key = issue["key"]
    summary = issue["fields"]["summary"]
    status = issue["fields"]["status"]["name"]
    resolution = (
        issue["fields"]["resolution"]["name"]
        if issue["fields"]["resolution"]
        else "Unresolved"
    )
    return f"{key}: {summary} ({status}/{resolution})"


def comments(jira: Jira, key: str) -> list[dict]:
    """
    Given an issue key, return the comments for the issue.

    Parameters:
        jira: The Jira object to use for querying the API.
        key: The issue key to get the comments for.

    Returns:
        A list of dicts with the comments. Each comment has the following fields:
        - who: The name of the person who made the comment.
        - when: The timestamp of the comment.
        - what: The content of the comment.
    """
    comments = get_issue(jira, key)["fields"]["comment"]["comments"]
    items = []
    for comment in comments:
        items.append(
            {
                "who": comment["author"]["displayName"],
                "when": comment["created"],
                "what": comment["body"],
            }
        )
    return items
