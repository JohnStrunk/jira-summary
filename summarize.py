#! /usr/bin/env python

"""
Summarize a Jira issue.

Usage: python summarize.py <jira_issue_key>

Required environment variables:
- JIRA_URL: The URL of the Jira instance.
- JIRA_TOKEN: An API token for the Jira instance.
"""

import io
import os
import sys
import textwrap

from atlassian import Jira  # type: ignore
from langchain_core.language_models import LLM
from pydantic.v1.types import SecretStr

from jhelper import comments, get_issue, issue_one_liner, related_issues
from streaming_together import StreamingTogether


def chat_model() -> LLM:
    """Return a chat model to use for summarization."""
    # source: https://github.com/langchain-ai/langchain/tree/master/libs/partners/together
    # Models: https://docs.together.ai/docs/inference-models#chat-models
    api_key = SecretStr(os.environ["TOGETHER_API_KEY"])
    return StreamingTogether(
        model="mistralai/Mistral-7B-Instruct-v0.2", together_api_key=api_key
    )


def build_query(jira: Jira, key: str) -> str:
    """
    Obtain the raw information used to summarize a Jira issue.

    Parameters:
        jira: A Jira instance.
        key: The key of the Jira issue to summarize.

    Returns:
        A multi-line string containing the raw information.
    """
    buffer = io.StringIO()

    issue = get_issue(jira, key)

    # Start w/ the issue key, summary, and description
    buffer.write(f"{key}: {issue['fields']['summary']}\n")
    buffer.write(issue["fields"]["description"] + "\n")

    # Add the issue status and resolution
    status = issue["fields"]["status"]["name"]
    resolution = (
        issue["fields"]["resolution"]["name"]
        if issue["fields"]["resolution"]
        else "Unresolved"
    )
    buffer.write(f"Issue status: {status}/{resolution}\n")

    # Add the log of comments
    buffer.write("Comments:\n")
    for comment in comments(jira, key):
        buffer.write(f"On {comment['when']}, {comment['who']} said:\n")
        for line in textwrap.wrap(comment["what"], width=72):
            buffer.write(f"  {line}\n")

    # List the related issues
    buffer.write("Related issues:\n")
    for related in related_issues(jira, key):
        ri = get_issue(jira, related.key)
        buffer.write(f"  {related.how} -- {issue_one_liner(ri)}\n")

    return buffer.getvalue()


def main():
    """Summarize a Jira issue."""
    if len(sys.argv) != 2:
        print("Usage: python summarize.py <jira_issue_key>")
        sys.exit(1)
    jira_issue_key = sys.argv[1]

    jira = Jira(url=os.environ["JIRA_URL"], token=os.environ["JIRA_TOKEN"])

    print(f"Querying Jira -- {issue_one_liner(get_issue(jira, jira_issue_key))}")
    raw_data = build_query(jira, jira_issue_key)

    prompt = "You are a helpful assistant who is an expert in software development. Here is a Jira issue that I would like you to summarize:\n\n"
    prompt += f"{raw_data}\n\n"
    prompt += "Please summarize the above issue in a few sentences. Provide an overview of any relevant discussions or decisions including the reasoning and outcome.\n\n"

    print("Summarizing...")

    # Send the prompt to the chat model and stream the response
    chat = chat_model()
    for chunk in chat.stream(prompt, max_tokens=4000):
        print(chunk, end="", flush=True)
    print("\n")

    # print(f"{prompt}\n\nPrompt characters: {len(prompt)}")


if __name__ == "__main__":
    main()
