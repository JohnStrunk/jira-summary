#! /usr/bin/env python

"""Summarize a JIRA issue"""

import argparse
import logging
import os

from atlassian import Jira  # type: ignore

from jiraissues import Issue
from summarizer import summarize_issue


def main():
    """Main function"""
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
        "-p",
        "--prompt-only",
        action="store_true",
        help="Print the LLM prompt, but do not generate the summary",
    )
    parser.add_argument(
        "-r",
        "--regenerate",
        action="store_true",
        help="Force regeneration of summaries",
    )
    parser.add_argument("jira_issue_key", type=str, help="JIRA issue key")

    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s:%(name)s - %(message)s",
        # datefmt="%Y-%m-%d %H:%M:%S.%f",
    )
    max_depth = args.max_depth
    regenerate = args.regenerate
    send_updates = not args.no_update
    prompt_only = args.prompt_only

    jira = Jira(url=os.environ["JIRA_URL"], token=os.environ["JIRA_TOKEN"])

    issue = Issue(jira, args.jira_issue_key)
    out = summarize_issue(
        issue,
        regenerate=regenerate,
        max_depth=max_depth,
        send_updates=send_updates,
        return_prompt_only=prompt_only,
    )
    print(out)


if __name__ == "__main__":
    main()
