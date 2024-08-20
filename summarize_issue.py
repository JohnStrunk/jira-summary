#! /usr/bin/env python

"""Summarize a JIRA issue"""

import argparse
import logging
import os

from atlassian import Jira  # type: ignore

from jiraissues import Issue
from simplestats import Timer
from summarizer import get_or_update_summary, summarize_issue
from summary_dbi import mariadb_db


def main() -> None:
    """Main function"""
    parser = argparse.ArgumentParser(description="Summarize a JIRA issue")
    # pylint: disable=duplicate-code
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
    if prompt_only:
        prompt_txt = summarize_issue(
            issue,
            db,
            return_prompt_only=prompt_only,
        )
        print(prompt_txt)
    else:
        summary = get_or_update_summary(issue, db)
        # print(textwrap.fill(summary))
        print(summary)


if __name__ == "__main__":
    with Timer(__name__):
        main()
