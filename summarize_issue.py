#! /usr/bin/env python

"""Summarize a JIRA issue"""

import argparse
import logging
import os
import textwrap
from typing import Optional

from atlassian import Jira
from click import Option  # type: ignore

from jiraissues import Issue
from simplestats import Timer
from summarizer import summarize_issue
from summary_dbi import get_summary, mariadb_db, memory_db, update_summary


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Summarize a JIRA issue")
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level",
    )
    parser.add_argument(
        "-p",
        "--prompt-only",
        action="store_true",
        help="Print the LLM prompt, but do not generate the summary",
    )
    parser.add_argument("jira_issue_key", type=str, help="JIRA issue key")

    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s:%(name)s - %(message)s",
        # datefmt="%Y-%m-%d %H:%M:%S.%f",
    )
    prompt_only = args.prompt_only

    jira = Jira(url=os.environ["JIRA_URL"], token=os.environ["JIRA_TOKEN"])

    issue = Issue(jira, args.jira_issue_key)
    db = mariadb_db()
    summary: Optional[str] = None
    if prompt_only:
        summary = summarize_issue(
            issue,
            db,
            return_prompt_only=prompt_only,
        )
        print(summary)
    else:
        summary = get_summary(db, issue.key)
        if not summary:
            summary = summarize_issue(
                issue,
                db,
                return_prompt_only=prompt_only,
            )
            update_summary(db, issue.key, summary, issue.parent)
        print(textwrap.fill(summary))


if __name__ == "__main__":
    with Timer(__name__):
        main()
