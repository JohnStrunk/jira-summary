#! /usr/bin/env python

"""Estimate the issue change rate and necessary token throughput"""

import argparse
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests
from atlassian import Jira  # type: ignore

from jiraissues import Issue, check_response, get_self, issue_cache
from summarizer import count_tokens, summarize_issue


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


def estimate_issue(issue: Issue) -> IssueEstimate:
    """Estimate the number of tokens needed to summarize the issue"""
    prompt = summarize_issue(
        issue,
        max_depth=0,
        send_updates=False,
        regenerate=False,
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
        client.jql(
            f"updated >= '{since_string}' ORDER BY updated DESC",
            limit=1000,
            fields="key",
        )
    )
    issue_cache.clear()
    return [issue_cache.get_issue(client, issue["key"]) for issue in issues["issues"]]


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
        "-s",
        "--seconds",
        type=int,
        default=300,
        help="Seconds to wait between iterations",
    )

    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level))
    delay: int = args.seconds

    jira = Jira(url=os.environ["JIRA_URL"], token=os.environ["JIRA_TOKEN"])

    print(IssueEstimate.csv_header())
    since = datetime.now() + timedelta(seconds=-delay)
    while True:
        start_time = datetime.now()
        logging.info("Starting iteration at %s", start_time.isoformat())
        try:
            issues = get_modified_issues(jira, since)
            for issue in issues:
                print(estimate_issue(issue).as_csv())
            since = start_time  # Only update if we succeeded
        except requests.exceptions.HTTPError as error:
            logging.error("HTTPError exception: %s", error.response.reason)
        except requests.exceptions.ReadTimeout as error:
            logging.error("ReadTimeout exception: %s", error, exc_info=True)
        logging.info(
            "Cache stats: %d hits, %d total", issue_cache.hits, issue_cache.tries
        )
        print(f"Iteration elapsed time: {datetime.now() - start_time}")
        print(f"{'='*20} Sleeping for {delay} seconds {'='*20}")
        time.sleep(delay)


if __name__ == "__main__":
    main()
