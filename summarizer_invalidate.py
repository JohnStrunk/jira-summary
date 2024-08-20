"""Watches the Jira API and invalidates summaries when the issues are updated."""

import argparse
import logging
import os
from datetime import UTC, datetime, timedelta
from time import sleep

from atlassian import Jira  # type: ignore

from jiraissues import check_response, get_self, with_retry
from summary_dbi import mariadb_db, mark_stale


def main() -> None:  # pylint: disable=too-many-locals
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Watch the Jira API and invalidate summaries when issues are updated"
    )
    # pylint: disable=duplicate-code
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
    user_tz = get_self(jira).tzinfo

    # The window must be at least 1 munute due to the granularity of the jql
    # query syntax.
    window = timedelta(minutes=1)
    while True:
        start_time = datetime.now(tz=UTC)
        until = start_time - window
        until_string = until.astimezone(user_tz).strftime("%Y-%m-%d %H:%M")
        since = until - window
        since_string = since.astimezone(user_tz).strftime("%Y-%m-%d %H:%M")
        issues = check_response(
            with_retry(
                lambda: jira.jql(
                    f"updated >= '{since_string}' AND updated < '{
                        until_string}' ORDER BY updated DESC",
                    limit=1000,
                    fields="key",
                )
            )
        )
        logging.info(
            "Found %d issues updated between %s and %s",
            len(issues["issues"]),
            since_string,
            until_string,
        )
        for issue in issues["issues"]:
            key = issue["key"]
            marked = mark_stale(db, key, add_ok=False)
            if marked:
                logging.info("Marked %s as stale", key)
        sleep((window - (datetime.now(tz=UTC) - start_time)).seconds)


if __name__ == "__main__":
    main()
