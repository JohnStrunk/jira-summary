#! /usr/bin/env python

"""Estimate the issue change rate and necessary token throughput"""

import argparse
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from atlassian import Jira  # type: ignore
from sqlalchemy import Engine

from jiraissues import Issue, check_response, get_self, issue_cache, with_retry
from summarizer import count_tokens, summarize_issue
from summary_dbi import mariadb_db


@dataclass
class IssueEstimate:
    """Data class to hold the estimate information"""

    key: str
    issue_type: str
    updated: datetime
    child_count: int
    comment_count: int
    tokens: int

    def __str__(self) -> str:
        return f"{self.key} ({self.issue_type}): {self.tokens} tokens"

    @classmethod
    def csv_header(cls) -> str:
        """Return the CSV header line"""
        return "key,issue_type,updated,child_count,comment_count,tokens"

    def as_csv(self) -> str:
        """Return the CSV representation of the data"""
        return ",".join(
            [
                self.key,
                self.issue_type,
                self.updated.isoformat(),
                str(self.child_count),
                str(self.comment_count),
                str(self.tokens),
            ]
        )


def estimate_issue(issue: Issue, db: Engine) -> IssueEstimate:
    """Estimate the number of tokens needed to summarize the issue"""
    prompt = summarize_issue(
        issue,
        db,
        return_prompt_only=True,
    )
    tokens = -1
    try:
        tokens = count_tokens(prompt)
    except ValueError:
        # If the prompt is too large, we can't count the tokens
        pass

    return IssueEstimate(
        key=issue.key,
        issue_type=issue.issue_type,
        updated=issue.updated,
        child_count=len(issue.children),
        comment_count=len(issue.comments),
        tokens=tokens,
    )


def get_modified_issues(client: Jira, since: datetime) -> list[Issue]:
    """Get issues modified since the given date/time"""
    user_zi = get_self(client).tzinfo
    since_string = since.astimezone(user_zi).strftime("%Y-%m-%d %H:%M")

    issues = check_response(
        with_retry(
            lambda: client.jql(
                f"updated >= '{since_string}' ORDER BY updated DESC",
                limit=1000,
                fields="key",
            )
        )
    )
    for issue in issues["issues"]:
        issue_cache.remove(issue["key"])

    issue_list: list[Issue] = []
    for idict in issues["issues"]:
        issue = issue_cache.get_issue(client, idict["key"])
        issue_list.append(issue)
    return issue_list


def main() -> None:
    """Main function"""
    parser = argparse.ArgumentParser(description="Estimator")
    # pylint: disable=duplicate-code
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=argparse.FileType("a", encoding="utf-8"),
        default="-",
        help="Output file for estimates (CSV format), default: stdout",
    )
    parser.add_argument(
        "-s",
        "--seconds",
        type=int,
        default=1800,
        help="Seconds to wait between iterations",
    )
    parser.add_argument(
        "-w",
        "--window",
        type=int,
        default=300,
        help="Window size in seconds to check for updated issues",
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
    delay: int = args.seconds
    outfile = args.output
    window = timedelta(seconds=args.window)
    db_host = str(args.db_host)
    db_port = int(args.db_port)

    jira = Jira(url=os.environ["JIRA_URL"], token=os.environ["JIRA_TOKEN"])
    db = mariadb_db(host=db_host, port=db_port)

    print(IssueEstimate.csv_header(), file=outfile)
    while True:
        start_time = datetime.now()
        logging.info("Starting iteration at %s", start_time.isoformat())
        processed = 0
        since = datetime.now() - window
        issues = get_modified_issues(jira, since)
        print(f"Found {len(issues)} issues modified since {since}")
        for issue in issues:
            print(estimate_issue(issue, db).as_csv(), file=outfile, flush=True)
            processed += 1
        print(issue_cache)
        print(f"Issues processed: {processed}")
        print(f"Iteration elapsed time: {datetime.now() - start_time}")
        print(f"{'='*20} Sleeping for {delay} seconds {'='*20}")
        time.sleep(delay)


if __name__ == "__main__":
    main()
