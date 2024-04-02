"""
Summarize a Jira issue.

Usage: python summarize.py <jira_issue_key>

Required environment variables:
- JIRA_URL: The URL of the Jira instance.
- JIRA_TOKEN: An API token for the Jira instance.
"""

import os
import sys

import atlassian  # type: ignore

from jhelper import get_issue, issue_one_liner


def main():
    """Summarize a Jira issue."""
    if len(sys.argv) != 2:
        print("Usage: python summarize.py <jira_issue_key>")
        sys.exit(1)
    jira_issue_key = sys.argv[1]

    jira = atlassian.Jira(url=os.environ["JIRA_URL"], token=os.environ["JIRA_TOKEN"])

    print(f"Summarizing -- {issue_one_liner(get_issue(jira, jira_issue_key))}")


if __name__ == "__main__":
    main()
