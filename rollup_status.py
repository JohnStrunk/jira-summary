#! /usr/bin/env python

"""Roll-up the status of Jira issues into a single document"""

import argparse
import logging
import os
import textwrap
from dataclasses import dataclass, field

from atlassian import Jira  # type: ignore

from jiraissues import Issue, User, issue_cache
from simplestats import Timer
from summarizer import get_chat_model, rollup_contributors, summarize_issue

LINK_BASE = "https://issues.redhat.com/browse/"


@dataclass
class IssueSummary:
    """Summary of an issue"""

    issue: Issue
    summary: str = ""
    exec_summary: str = ""
    contributors: set[User] = field(default_factory=set)


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
    parser.add_argument("jira_issue_key", type=str, help="JIRA issue key")

    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper()))
    issue_key: str = args.jira_issue_key

    client = Jira(url=os.environ["JIRA_URL"], token=os.environ["JIRA_TOKEN"])

    # Get the existing summaries from the Jira issues
    stime = Timer("Collect")
    stime.start()
    logging.info("Collecting issue summaries for children of %s", issue_key)
    child_inputs: list[IssueSummary] = []
    epic = issue_cache.get_issue(client, issue_key)
    for child in epic.children:
        issue = issue_cache.get_issue(client, child.key)
        text = f"{issue}\n"
        text += summarize_issue(issue, max_depth=1)
        child_inputs.append(
            IssueSummary(
                issue=issue, summary=text, contributors=rollup_contributors(issue)
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
Contributors: {', '.join(c.display_name for c in item.contributors)}"""
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

    # Determine contributors to the whole top-level issue, sorted by last name
    all_contributors = sorted(
        rollup_contributors(epic), key=lambda x: x.display_name.split()[-1]
    )

    # Generate the overall status update
    print(f"# Executive Summary for [{issue_key}]({LINK_BASE}{issue_key})")
    print()
    print(exec_paragraph)
    print()
    print(f"**Resource count:** {len(all_contributors)}")
    print()
    print(f"**Contributors:** {', '.join(c.display_name for c in all_contributors)}")
    print()
    print("## Individual issue status")
    print()
    for item in child_inputs:
        issue = item.issue
        print(f"### [{issue.key}]({LINK_BASE}{issue.key}) - {issue.summary}")
        print()
        print(item.exec_summary)
        print()
        contributors = sorted(
            item.contributors, key=lambda x: x.display_name.split()[-1]
        )
        if contributors:
            print(
                f"**Contributors:** {', '.join([c.display_name for c in contributors])}"
            )
            print()


if __name__ == "__main__":
    with Timer("Total execution"):
        main()
