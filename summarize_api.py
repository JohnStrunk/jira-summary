#! /usr/bin/env python

"""Summarize a JIRA issue"""

import datetime
import os
import sys
from datetime import UTC
from sys import argv

from atlassian import Jira  # type: ignore
from flask import Flask, request
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    decode_token,
    get_jwt_identity,
    jwt_required,
)

import summarizer
from jiraissues import Issue, issue_cache
from simplestats import Timer

_DEFAULT_RECURSION_DEPTH = 0


def issue_word_count(issue: Issue, max_depth: int = _DEFAULT_RECURSION_DEPTH) -> int:
    """
    Count the words in the text representation of a Jira issue

    Parameters:
        - issue: The Jira issue to count the words in
        - max_depth: The maximum depth to recurse into the issue's related issues
    Returns:
        - The number of words in the issue's text representation
    """
    full_prompt = summarizer.summarize_issue(issue, max_depth, return_prompt_only=True)
    # Get the text between the ``` delimiters, which is the actual Jira issue
    # text that we send to the model
    issue_text = full_prompt[full_prompt.find("```") + 3 : full_prompt.rfind("```")]
    return word_count(issue_text)


def word_count(text: str) -> int:
    """
    Count the words in a string

    Parameters:
        - text: The string to count the words in
    Returns:
        - The number of words in the string
    """
    return len(text.split())


def create_app():
    """Create the Flask app"""
    client = Jira(url=os.environ["JIRA_URL"], token=os.environ["JIRA_TOKEN"])

    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = os.environ["JWT_SECRET_KEY"]
    assert app.config["JWT_SECRET_KEY"] is not None
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = False
    JWTManager(app)

    @app.route("/api/v1/health", methods=["GET"])
    def health():
        return {"status": "ok"}

    @app.route("/api/v1/summarize-issue", methods=["GET"])
    @jwt_required()
    def summarize_issue():
        Timer.clear()
        req = Timer("Request", autostart=True)
        key = request.args.get("key")
        if key is None:
            return {"error": 'Missing required parameter "key"'}, 400

        when = datetime.datetime.now(UTC) - datetime.timedelta(hours=1)
        issue_cache.remove_older_than(when)
        issue_cache.remove(key)

        issue = issue_cache.get_issue(client, key)
        issue_words = issue_word_count(issue)
        summary = summarizer.summarize_issue(issue, max_depth=_DEFAULT_RECURSION_DEPTH)
        req.stop()
        getissue_stats = Timer.stats("IssueCache.get_issue")
        fetchrelated_stats = Timer.stats("Issue._fetch_related")
        llm_stats = Timer.stats("RetryingLCI.invoke")
        return {
            "key": key,
            # Convert the summary into a single line, removing newlines and extra spaces
            "summary": " ".join(summary.split()),
            "user": get_jwt_identity(),
            "stats": {
                "fetchrelated_time": fetchrelated_stats.elapsed_ns / 1000000000,
                "getissue_time": getissue_stats.elapsed_ns / 1000000000,
                "llm_time": llm_stats.elapsed_ns / 1000000000,
                "request_time": req.elapsed_ns / 1000000000,
                "issue_words": issue_words,
                "summary_words": word_count(summary),
            },
        }

    return app


def create_token(identity: str) -> None:
    """Create an access token for the given identity"""
    app = create_app()
    with app.app_context():
        token = create_access_token(identity=identity)
        user = decode_token(token)
        print(f"Access token for user '{user['sub']}': {token}")


# Running the script directly allows creating tokens for use w/ the API
if __name__ == "__main__":
    if len(argv) != 2:
        print(f"Usage: {argv[0]} <userid>")
        sys.exit(1)
    create_token(argv[1])
