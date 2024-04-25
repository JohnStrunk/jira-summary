#! /usr/bin/env python

"""A bot to summarize marked Jira issues"""

import argparse
import logging
import os
import time
from datetime import datetime

from atlassian import Jira

from jiraissues import Issue
from summarizer import get_issues_to_summarize, summarize_issue


def main():
    parser = argparse.ArgumentParser(description="Summarizer bot")
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
        "-m",
        "--modified-since",
        type=str,
        default="2024-01-01",
        help="Summarize issues that have been modified since this date/time",
    )
    parser.add_argument(
        "-n",
        "--no-update",
        action="store_true",
        help="Do not update the Jira issues with the summaries",
    )
    parser.add_argument(
        "-s",
        "--seconds",
        type=int,
        default=300,
        help="Seconds to wait between iterations",
    )

    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level))
    max_depth = args.max_depth
    send_updates = not args.no_update
    delay = args.seconds
    since = datetime.fromisoformat(args.modified_since)

    jira = Jira(url=os.environ["JIRA_URL"], token=os.environ["JIRA_TOKEN"])

    while True:
        start_time = datetime.now()
        issue_keys = get_issues_to_summarize(jira, since)
        for issue_key in issue_keys:
            issue_start_time = datetime.now()
            issue = Issue(jira, issue_key)
            summary = summarize_issue(
                issue, max_depth=max_depth, send_updates=send_updates
            )
            elapsed = datetime.now() - issue_start_time
            print(f"Summarized {issue_key} ({elapsed}s):\n{summary}\n")
        print(f"Iteration elapsed time: {datetime.now() - start_time}")
        print(f"Sleeping for {delay} seconds...")
        time.sleep(delay)
        since = start_time


if __name__ == "__main__":
    main()
