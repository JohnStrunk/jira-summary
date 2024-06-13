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
from jiraissues import issue_cache


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
        key = request.args.get("key")
        if key is None:
            return {"error": 'Missing required parameter "key"'}, 400

        when = datetime.datetime.now(UTC) - datetime.timedelta(hours=1)
        issue_cache.remove_older_than(when)

        issue = issue_cache.get_issue(client, key)
        summary = summarizer.summarize_issue(issue, max_depth=1)
        return {
            "key": key,
            # Convert the summary into a single line, removing newlines and extra spaces
            "summary": " ".join(summary.split()),
            "user": get_jwt_identity(),
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
