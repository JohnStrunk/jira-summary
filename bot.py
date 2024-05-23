#! /usr/bin/env python

"""A bot to summarize marked Jira issues"""

import argparse
import logging
import os
import time
from datetime import UTC, datetime

import requests
from atlassian import Jira  # type: ignore

from jiraissues import issue_cache
from summarizer import get_issues_to_summarize, summarize_issue


def main() -> None:  # pylint: disable=too-many-locals,too-many-statements
    """Main function for the bot."""
    # pylint: disable=duplicate-code
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
        "-l",
        "--limit",
        type=int,
        default=150,
        help="Maximum number of issues to summarize in each iteration",
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
    delay: int = args.seconds
    limit: int = args.limit
    since = datetime.fromisoformat(args.modified_since).astimezone(UTC)

    jira = Jira(url=os.environ["JIRA_URL"], token=os.environ["JIRA_TOKEN"])

    most_recent_modification = since
    while True:
        start_time = datetime.now(UTC)
        logging.info("Starting iteration at %s", start_time.isoformat())
        issue_keys: list[str] = []
        successful = False
        while not successful:
            try:
                (issue_keys, most_recent_modification) = get_issues_to_summarize(
                    jira, since, limit
                )
                successful = True
            except requests.exceptions.HTTPError as error:
                logging.error(
                    "HTTPError exception (%s): %s",
                    error.request.url,
                    error.response.reason,
                )
                time.sleep(5)
            except requests.exceptions.ReadTimeout as error:
                logging.error("ReadTimeout exception: %s", error, exc_info=True)
                time.sleep(5)

        if len(issue_keys) < limit - 5:
            # We retrieved all the modified issues, so we can advance farther
            # and avoid re-fetching old issues
            most_recent_modification = start_time
        logging.info("Got updates through %s", most_recent_modification.isoformat())

        for issue_key in issue_keys:
            successful = False
            while not successful:
                try:
                    issue_start_time = datetime.now(UTC)
                    issue = issue_cache.get_issue(jira, issue_key)
                    summary = summarize_issue(
                        issue, max_depth=max_depth, send_updates=send_updates
                    )
                    elapsed = datetime.now(UTC) - issue_start_time
                    print(f"Summarized {issue_key} ({elapsed}s):\n{summary}\n")
                    successful = True
                except requests.exceptions.HTTPError as error:
                    logging.error(
                        "HTTPError exception (%s): %s",
                        error.request.url,
                        error.response.reason,
                    )
                    time.sleep(5)
                except requests.exceptions.ReadTimeout as error:
                    logging.error("ReadTimeout exception: %s", error, exc_info=True)
                    time.sleep(5)
        since = most_recent_modification  # Only update if we succeeded
        logging.info("Cache stats: %s", issue_cache)
        now = datetime.now(UTC)
        elapsed = now - start_time
        print(f"Iteration elapsed time: {elapsed}")
        sleep_time = max(delay - elapsed.total_seconds(), 0)
        print(f"Sleeping for {sleep_time} seconds...")
        time.sleep(sleep_time)


if __name__ == "__main__":
    main()
