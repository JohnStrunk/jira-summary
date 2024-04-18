#! /usr/bin/env python

"""Summarize a JIRA issue"""

import argparse
import logging
import os

from atlassian import Jira

from jiraissues import Issue
from summarizer import summarize_issue


def main():
    parser = argparse.ArgumentParser(description="Summarize a JIRA issue")
    parser.add_argument(
        "-d",
        "--max-depth",
        type=int,
        default=1,
        help="Maximum depth to recursively examine issues while summarizing",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level",
    )
    parser.add_argument(
        "-n",
        "--no-update",
        action="store_true",
        help="Do not update the Jira issues with the summaries",
    )
    parser.add_argument(
        "-r",
        "--regenerate",
        action="store_true",
        help="Force regeneration of summaries",
    )
    parser.add_argument("jira_issue_key", type=str, help="JIRA issue key")

    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level))
    max_depth = args.max_depth
    regenerate = args.regenerate
    send_updates = not args.no_update

    jira = Jira(url=os.environ["JIRA_URL"], token=os.environ["JIRA_TOKEN"])

    issue = Issue(jira, args.jira_issue_key)
    out = summarize_issue(
        issue, regenerate=regenerate, max_depth=max_depth, send_updates=send_updates
    )
    print(out)


if __name__ == "__main__":
    main()
