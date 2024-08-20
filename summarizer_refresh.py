"""Watches the database for stale summaries and refreshes them."""

import argparse
import logging
import os
from time import sleep

from atlassian import Jira  # type: ignore

from jiraissues import Issue
from summarizer import summarize_issue
from summary_dbi import db_stats, get_stale_issues, mariadb_db, update_summary


def main() -> None:  # pylint: disable=duplicate-code
    """Main function"""
    parser = argparse.ArgumentParser(description="Refresh stale summaries")
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level",
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
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s:%(name)s - %(message)s",
        # datefmt="%Y-%m-%d %H:%M:%S.%f",
    )
    db_host = str(args.db_host)
    db_port = int(args.db_port)

    db = mariadb_db(host=db_host, port=db_port)
    jira = Jira(url=os.environ["JIRA_URL"], token=os.environ["JIRA_TOKEN"])

    while True:
        stats = db_stats(db)
        logging.info(
            "Summary DB entries: %d total, %d stale, %d fresh",
            stats["total"],
            stats["stale"],
            stats["fresh"],
        )
        stale_keys = get_stale_issues(db, limit=100)
        if not stale_keys:
            logging.debug("No stale issues found, sleeping...")
            sleep(60)
            continue
        # Sort by level so that we regenerate summaries of children before parents
        issues = [Issue(jira, key) for key in stale_keys]
        issues.sort(key=lambda x: x.level)
        for issue in issues:
            logging.info("Refreshing summary for %s", issue.key)
            summary = summarize_issue(issue, db)
            update_summary(db, issue.key, summary, issue.parent)
            logging.debug("Updated issue %s summary: %s", issue.key, summary)


if __name__ == "__main__":
    main()
