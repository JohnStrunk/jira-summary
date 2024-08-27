#! /usr/bin/env python

"""
Provide an API for summarizing Jira issues

This module provides a REST API for summarizing Jira issues via a Flask app.
"""

import os
import sys
from logging.config import dictConfig
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
from sqlalchemy import Engine

import summarizer
from jiraissues import Issue, issue_cache
from simplestats import Timer
from summary_dbi import db_stats, mariadb_db, mark_stale, memory_db


def _issue_word_count(issue: Issue, db: Engine) -> int:
    """
    Count the words in the text representation of a Jira issue

    Parameters:
        - issue: The Jira issue to count the words in
        - db: The database connection to use for caching summaries
    Returns:
        - The number of words in the issue's text representation
    """
    full_prompt = summarizer.summarize_issue(issue, db, return_prompt_only=True)
    # Get the text between the ``` delimiters, which is the actual Jira issue
    # text that we send to the model
    issue_text = full_prompt[full_prompt.find("```") + 3 : full_prompt.rfind("```")]
    return _word_count(issue_text)


def _word_count(text: str) -> int:
    """
    Count the words in a string

    Parameters:
        - text: The string to count the words in
    Returns:
        - The number of words in the string
    """
    return len(text.split())


def create_app(skip_db: bool = False) -> Flask:
    """Create the Flask app"""
    client = Jira(url=os.environ["JIRA_URL"], token=os.environ["JIRA_TOKEN"])
    if not skip_db:
        db = mariadb_db(
            host=os.environ.get("MARIADB_HOST", "localhost"),
            port=int(os.environ.get("MARIADB_PORT", 3306)),
        )
    else:
        db = memory_db()

    dictConfig(
        {
            "version": 1,
            "formatters": {
                "default": {
                    "format": "[%(asctime)s] [%(levelname)s] in %(module)s: %(message)s",
                }
            },
            "handlers": {
                "wsgi": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://flask.logging.wsgi_errors_stream",
                    "formatter": "default",
                }
            },
            "root": {"level": "INFO", "handlers": ["wsgi"]},
        }
    )

    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = os.environ["JWT_SECRET_KEY"]
    assert app.config["JWT_SECRET_KEY"] is not None
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = False
    JWTManager(app)

    @app.route("/api/v1/health", methods=["GET"])
    def health():
        # This should really verify that the Jira and MariaDB connections are working
        return {"status": "ok"}

    @app.route("/api/v1/summarize-issue", methods=["GET"])
    @jwt_required()
    def summarize_issue():
        Timer.clear()
        req = Timer("Request", autostart=True)
        key = request.args.get("key")
        if key is None:
            return {"error": 'Missing required parameter "key"'}, 400

        app.logger.info("/api/v1/summarize-issue: %s", key)

        issue_cache.clear()

        issue = Issue(client, key)
        issue_words = _issue_word_count(issue, db)
        summary = summarizer.get_or_update_summary(issue, db)
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
                "summary_words": _word_count(summary),
            },
        }

    @app.route("/api/v1/enqueue", methods=["GET"])
    @jwt_required()
    def enqueue():
        key = request.args.get("key")
        if key is None:
            return {"error": 'Missing required parameter "key"'}, 400

        app.logger.info("/api/v1/enqueue: %s", key)

        mark_stale(db, key, add_ok=True)
        return {
            "key": key,
            "user": get_jwt_identity(),
        }

    @app.route("/api/v1/dbstats", methods=["GET"])
    @jwt_required()
    def dbstats():
        app.logger.info("/api/v1/dbstats")
        stats = db_stats(db)
        return {
            "user": get_jwt_identity(),
            "total_records": stats["total"],
            "stale_records": stats["stale"],
            "fresh_records": stats["fresh"],
        }

    return app


def _create_token(identity: str) -> None:
    """Create an access token for the given identity"""
    app = create_app(skip_db=True)
    with app.app_context():
        token = create_access_token(identity=identity)
        user = decode_token(token)
        print(f"Access token for user '{user['sub']}': {token}")


# Running the script directly allows creating tokens for use w/ the API
if __name__ == "__main__":
    if len(argv) != 2:
        print(f"Usage: {argv[0]} <userid>")
        sys.exit(1)
    _create_token(argv[1])
