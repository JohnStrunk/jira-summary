#! /usr/bin/env python

"""A bot to summarize marked Jira issues"""

import argparse
import logging
import os
import time
from datetime import UTC, datetime
from sys import stdout

from atlassian import Jira  # type: ignore

from jiraissues import issue_cache
from simplestats import Timer
from summarizer import (
    get_issues_to_summarize,
    get_or_update_summary,
    is_ok_to_post_summary,
)
from summary_dbi import mariadb_db


def main() -> (
    None
):  # pylint: disable=too-many-locals,too-many-statements,duplicate-code
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
        help="Summarize issues that have been modified since this date/time (YYYY-MM-DD)",
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
    parser.add_argument(
        "--db-host",
        default="localhost",
        type=str,
        help="MariaDB host",
    )
    parser.add_argument(
        "--db-port",
        default=3306,
        type=int,
        help="MariaDB port",
    )

    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level))
    send_updates = not args.no_update
    delay: int = args.seconds
    limit: int = args.limit
    since = datetime.fromisoformat(args.modified_since).astimezone(UTC)
    db_host = str(args.db_host)
    db_port = int(args.db_port)

    jira = Jira(url=os.environ["JIRA_URL"], token=os.environ["JIRA_TOKEN"])
    db = mariadb_db(host=db_host, port=db_port)

    most_recent_modification = since
    while True:
        start_time = datetime.now(UTC)
        logging.info("Starting iteration at %s", start_time.isoformat())
        issue_keys: list[str] = []
        (issue_keys, most_recent_modification) = get_issues_to_summarize(
            jira, since, limit
        )

        if len(issue_keys) < limit - 5:
            # We retrieved all the modified issues, so we can advance farther
            # and avoid re-fetching old issues
            most_recent_modification = start_time
        logging.info("Got updates through %s", most_recent_modification.isoformat())

        for issue_key in issue_keys:
            issue_start_time = datetime.now(UTC)
            issue = issue_cache.get_issue(jira, issue_key)
            summary = get_or_update_summary(issue, db)
            if is_ok_to_post_summary(issue) and send_updates:
                # NEED TO POST THE SUMMARY TO THE JIRA ISSUE...
                # CODE WAS REMOVED FROM summarize_issue DURING THE REFACTOR
                pass
            elapsed = datetime.now(UTC) - issue_start_time
            print(f"Summarized {issue_key} ({elapsed}s):\n{summary}\n")
        since = most_recent_modification
        logging.info("Cache stats: %s", issue_cache)
        Timer.dump(stdout)
        now = datetime.now(UTC)
        elapsed = now - start_time
        print(f"Iteration elapsed time: {elapsed}")
        sleep_time = max(delay - elapsed.total_seconds(), 0)
        print(f"Sleeping for {sleep_time} seconds...")
        time.sleep(sleep_time)


if __name__ == "__main__":
    main()
