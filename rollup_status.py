#! /usr/bin/env python

"""Roll-up the status of Jira issues into a single document"""

import argparse
import logging
import os
import textwrap
from dataclasses import dataclass, field

from atlassian import Confluence, Jira  # type: ignore

from cfhelper import CFElement, jiralink
from jiraissues import Issue, User, issue_cache
from simplestats import Timer
from summarizer import get_chat_model, is_active, rollup_contributors, summarize_issue

LINK_BASE = "https://issues.redhat.com/browse/"
CONFLUENCE_SPACE = "OCTOET"


@dataclass
class IssueSummary:
    """Summary of an issue"""

    issue: Issue
    summary: str = ""
    exec_summary: str = ""
    contributors: set[User] = field(default_factory=set)
    active_contributors: set[User] = field(default_factory=set)


def lookup_page(cclient: Confluence, title_or_id: str) -> int:
    """
    Look up a page by title or ID

    Parameters:
        - cclient: The Confluence client
        - title: The title or ID of the page

    Returns:
        The page ID
    """
    try:
        # If it's an integer, assume it's the ID and return it
        return int(title_or_id)
    except ValueError:
        pass

    page_id = cclient.get_page_id(CONFLUENCE_SPACE, title_or_id)
    if page_id is None:
        logging.error("Unable to find page %s", title_or_id)
        raise ValueError(f"Unable to find page {title_or_id}")
    return int(page_id)


def element_contrib_count(header: str, contributors: set[User]) -> CFElement:
    """
    Generate an element for the number of contributors

    Parameters:
        - header: The header for the tag
        - contributors: The set of contributors

    Returns:
        A CFElement representing the tag
    """
    # The initial contributor "set" ensures uniqueness of Jira User objects,
    # here we convert to a "set" of display names to catch the case of multiple
    # users with the same display name (i.e. the same person w/ multiple
    # accounts)
    contributor_names = {c.display_name for c in contributors}
    return (
        CFElement("p")
        .add(CFElement("strong", content=header))
        .add(" ")
        .add(len(contributor_names))
    )


def element_contrib_list(header: str, contributors: set[User]) -> CFElement:
    """
    Generate an element for the list of contributors

    Parameters:
        - header: The header for the tag
        - contributors: The set of contributors

    Returns:
        A CFElement representing the tag
    """
    # The initial contributor "set" ensures uniqueness of Jira User objects,
    # here we convert to a "set" of display names to catch the case of multiple
    # users with the same display name (i.e. the same person w/ multiple
    # accounts)
    contributor_names = {c.display_name for c in contributors}
    # Sort the names by last name
    contributor_names_sorted = sorted(contributor_names, key=lambda x: x.split()[-1])
    return (
        CFElement("p")
        .add(CFElement("strong", content=header + f" ({len(contributor_names)}):"))
        .add(" ")
        .add(", ".join(contributor_names_sorted))
    )


def main() -> None:  # pylint: disable=too-many-locals,too-many-statements
    """Main function"""
    # pylint: disable=duplicate-code
    parser = argparse.ArgumentParser(description="Generate an issue summary roll-up")
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level",
    )
    parser.add_argument(
        "--inactive-days",
        type=int,
        default=14,
        help="Number of days before an issue is considered inactive",
    )
    parser.add_argument(
        "-p",
        "--parent",
        type=str,
        required=True,
        help="Title or ID of the parent page",
    )
    parser.add_argument("jira_issue_key", type=str, help="JIRA issue key")

    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper()))
    issue_key: str = args.jira_issue_key
    inactive_days: int = args.inactive_days

    jclient = Jira(url=os.environ["JIRA_URL"], token=os.environ["JIRA_TOKEN"])
    cclient = Confluence(
        os.environ["CONFLUENCE_URL"], token=os.environ["CONFLUENCE_TOKEN"]
    )

    # Get the existing summaries from the Jira issues
    stime = Timer("Collect")
    stime.start()
    logging.info("Collecting issue summaries for children of %s", issue_key)
    child_inputs: list[IssueSummary] = []
    epic = issue_cache.get_issue(jclient, issue_key)
    for child in epic.children:
        issue = issue_cache.get_issue(jclient, child.key)
        if not is_active(issue, inactive_days, True):
            logging.info("Skipping inactive issue %s", issue.key)
            continue
        text = f"{issue}\n"
        text += summarize_issue(issue, max_depth=1)
        child_inputs.append(
            IssueSummary(
                issue=issue,
                summary=text,
                contributors=rollup_contributors(issue),
                active_contributors=rollup_contributors(issue, True, inactive_days),
            )
        )
    stime.stop()

    # Sort the issues by key
    child_inputs.sort(key=lambda x: x.issue.key)

    # Generate the individual exec summaries
    llm = get_chat_model("meta-llama/llama-3-70b-instruct", max_new_tokens=2048)
    for item in child_inputs:
        logging.info("Generating an executive summary for %s", item.issue.key)
        data = f"""\
{item.issue}
{item.summary}
"""
        prompt = f"""\
Condense the following technical status update into a short, high-level summary for an engineering leader.
Focus on the high-level objective, keeping the technical detail to a minimum.
Where possible, avoid mentioning specific issue IDs.

{data}

Please provide just the summary paragraph, with no header.
"""
        summary = llm.invoke(prompt, stop=["<|endoftext|>"]).strip()
        item.exec_summary = textwrap.fill(summary)

    # Generate the overall exec summary
    logging.info("Generating the overall executive summary")
    prompt = f"""\
Given the following high-level summaries of our group's work, please provide a short, one-paragraph summary of this initiative for a corporate leader:

{"\n".join([item.exec_summary for item in child_inputs])}

Please provide just the summary paragraph, with no header.
"""
    exec_paragraph = textwrap.fill(llm.invoke(prompt, stop=["<|endoftext|>"]).strip())

    # Generate the overall status update
    parent_page_id = lookup_page(cclient, args.parent)

    page_title = f"Initiative status: {epic.key} - {epic.summary}"

    # Root element for the page; tag doesn't matter as it will be stripped off later
    page = CFElement("root")

    # Top of the page; overall executive summary and initiative contributors
    page.add(CFElement("h1", content="Executive Summary"))
    page.add(CFElement("p", content=jiralink(epic.key)))
    page.add(CFElement("p", content=exec_paragraph))
    contributors = rollup_contributors(epic)
    active_contributors = rollup_contributors(epic, active_days=inactive_days)
    if active_contributors:
        page.add(element_contrib_list("Active contributors", active_contributors))
    if contributors:
        page.add(element_contrib_list("All contributors", contributors))

    # Individual issue summaries
    page.add(CFElement("h2", content="Status of individual issues"))
    sorted_issues = sorted(child_inputs, key=lambda x: x.issue)
    for item in sorted_issues:
        issue = item.issue
        page.add(CFElement("h3", content=jiralink(issue.key)))
        page.add(CFElement("p", content=item.exec_summary))
        if item.active_contributors:
            page.add(
                element_contrib_list("Active contributors", item.active_contributors)
            )
        if item.contributors:
            page.add(element_contrib_list("All contributors", item.contributors))

    cclient.update_or_create(parent_page_id, page_title, page.unwrap())


if __name__ == "__main__":
    with Timer(__name__):
        main()
