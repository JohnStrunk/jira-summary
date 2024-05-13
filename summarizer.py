"""Module code to handle summarization of Jira issues."""

import io
import logging
import os
import textwrap
from datetime import UTC, datetime
from typing import List, Tuple

from atlassian import Jira  # type: ignore
from genai import Client, Credentials
from genai.extensions.langchain import LangChainInterface
from genai.schema import DecodingMethod, TextGenerationParameters
from langchain_core.language_models import LLM

from jiraissues import Issue, Myself, RelatedIssue, issue_cache

_logger = logging.getLogger(__name__)


# The default model ID to use for summarization. It must be one of the models
# supported by IBM's GenAI.
# _MODEL_ID = "mistralai/mistral-7b-instruct-v0-2"
# _MODEL_ID = "ibm/granite-13b-lab-incubation"
# _MODEL_ID = "ibm-mistralai/merlinite-7b"
_MODEL_ID = "mistralai/mixtral-8x7b-instruct-v01"

# The marker that indicates the start of the AI summary.
SUMMARY_START_MARKER = "=== AI SUMMARY START ==="
# The marker that indicates the end of the AI summary.
SUMMARY_END_MARKER = "=== AI SUMMARY END ==="

# The label that indicates that an issue is allowed to have an AI summary.
SUMMARY_ALLOWED_LABEL = "AISummary"

# The default column width to wrap text to.
_WRAP_COLUMN = 78


# pylint: disable=too-many-locals
def summarize_issue(
    issue: Issue,
    max_depth: int = 0,
    send_updates: bool = False,
    regenerate: bool = False,
) -> str:
    """
    Summarize a Jira issue.

    Note: If send_updates is True, summaries may be updated for more than just
    the requested Issue.

    Parameters:
        - issue: The issue to summarize
        - max_depth: The maximum depth of child issues to examine while
          generating the summary
        - send_updates: If True, update the issue summaries on the server
        - regenerate: If True, regenerate the summary even if it is already
          up-to-date

    Returns:
        A string containing the summary
    """

    _logger.info("Summarizing %s...", issue.key)
    # If the current summary is up-to-date and we're not asked to regenerate it,
    # return what's there
    if not regenerate and is_summary_current(issue):
        _logger.debug("Summary for %s is current, using that.", issue.key)
        return get_aisummary(issue.description)

    # if we have not reached max-depth, summarize the child issues for inclusion in this summary
    child_summaries: List[Tuple[RelatedIssue, str]] = []
    for child in issue.children:
        if max_depth > 0:
            child_issue = issue_cache.get_issue(issue.client, child.key)
            child_summaries.append(
                (
                    child,
                    summarize_issue(child_issue, max_depth - 1, send_updates, False),
                )
            )
        else:
            child_summaries.append((child, ""))

    # The log of comments
    comment_block = io.StringIO()
    for comment in issue.comments:
        comment_block.write(f"On {comment.created}, {comment.author} said:\n")
        comment_block.write(
            textwrap.fill(
                comment.body,
                width=_WRAP_COLUMN,
                initial_indent="  ",
                subsequent_indent="  ",
            )
            + "\n"
        )

    related_block = io.StringIO()
    # Only summarize the non-child related issues
    non_children = [rel for rel in issue.related if not rel.is_child]
    for related in non_children:
        ri = issue_cache.get_issue(issue.client, related.key)
        how = related.how
        if how == "Parent Link":
            how = "is a child of the parent issue"
        if how == "Epic Link":
            how = "is a child of the Epic issue"
        related_block.write(f"* {issue.key} {how} {ri}\n")

    for child, summary in child_summaries:
        if not summary:
            ri = issue_cache.get_issue(issue.client, child.key)
            related_block.write(f"* {issue.key} {child.how} {ri}\n")
        else:
            related_block.write(
                f"* {issue.key} {child.how} {child.key} which can be summarized as:\n"
            )
            related_block.write(
                textwrap.fill(
                    summary,
                    width=_WRAP_COLUMN,
                    initial_indent="  ",
                    subsequent_indent="  ",
                )
                + "\n"
            )

    full_description = f"""\
Title: {issue.key} - {issue.summary}
Status/Resolution: {issue.status}/{issue.resolution}

=== Description ===
{strip_aisummary(issue.description)}

=== Comments ===
{comment_block.getvalue()}

=== Related Issues ===
{related_block.getvalue()}
"""

    llm_prompt = f"""\
You are a helpful assistant who is an expert in software development.
* Summarize the status of the following Jira issue in a few sentences.
* Include an overview of any significant discussions or decisions, with their reasoning and outcome.
* Highlight any recent updates or changes that effect the completion of the issue.
* Use only the information below to create your summary.

```
{full_description}
```

Here is a short summary in less than 100 words:
"""

    _logger.info("Summarizing %s via LLM", issue.key)
    _logger.debug("Prompt:\n%s", llm_prompt)

    chat = _chat_model()
    summary = chat.invoke(llm_prompt, stop=["<|endoftext|>"]).strip()
    if send_updates and is_ok_to_post_summary(issue):
        # Replace any existing AI summary w/ the updated one
        new_description = (
            strip_aisummary(issue.description) + "\n\n" + wrap_aisummary(summary)
        )
        issue.update_status_summary(new_description)

    return textwrap.fill(summary, width=_WRAP_COLUMN)


def wrap_aisummary(text: str, width: int = _WRAP_COLUMN) -> str:
    """
    Wrap the AI summary in markers so it can be stripped later, and wrap the
    text to the specified width so that it is easier to read.

    Parameters:
        - text: The text to wrap.
        - width: The width to wrap the text to.

    Returns:
        The wrapped text.
    """
    return f"{SUMMARY_START_MARKER}\n{textwrap.fill(text, width=width)}\n{SUMMARY_END_MARKER}"


def strip_aisummary(text: str) -> str:
    """
    Remove the AI summary from a block of text. This removes the summary by
    finding the start and end markers, and removing all the text beween.

    Parameters:
        - text: The text to strip.

    Returns:
        The text with the summary removed.
    """
    start = text.find(SUMMARY_START_MARKER)
    end = text.find(SUMMARY_END_MARKER)
    if start == -1 or end == -1:
        return text
    return text[:start] + text[end + len(SUMMARY_END_MARKER) :]


def get_aisummary(text: str) -> str:
    """
    Extract the AI summary from a block of text. This extracts the summary by
    finding the start and end markers, and returning the text beween.

    Parameters:
        - text: The text to extract the summary from.

    Returns:
        The extracted summary.
    """
    start = text.find(SUMMARY_START_MARKER)
    end = text.find(SUMMARY_END_MARKER)
    if start == -1 or end == -1:
        return ""
    return text[start + len(SUMMARY_START_MARKER) : end].strip()


def summary_last_updated(issue: Issue) -> datetime:
    """
    Get the last time the summary was updated.

    Parameters:
        - issue: The issue to check

    Returns:
        The last time the summary was updated
    """
    last_update = datetime.fromisoformat("1900-01-01").astimezone(UTC)

    # The summary is never in the initial creation of the issue, therefore,
    # there will be a record of it in the changelog.
    if issue.last_change is None or SUMMARY_START_MARKER not in issue.description:
        return last_update

    for change in issue.changelog:
        if change.author == Myself(issue.client).display_name and "description" in [
            chg.field for chg in change.changes
        ]:
            last_update = max(last_update, change.created)

    return last_update


def is_summary_current(issue: Issue) -> bool:
    """
    Determine if the AI summary is up-to-date for the issue.

    This is actually an approximation, as we are only checking if the last
    change was made by us and included a change to the issue description.

    Parameters:
        - issue: The issue to check

    Returns:
        True if the summary is current, False otherwise
    """
    if SUMMARY_ALLOWED_LABEL not in issue.labels:
        return True  # We're not allowed to summarize it, so it's always current

    last_update = summary_last_updated(issue)
    for child in issue.children:
        child_issue = issue_cache.get_issue(issue.client, child.key)
        if child_issue.updated > last_update:
            return False
    return issue.updated == last_update


def is_ok_to_post_summary(issue: Issue) -> bool:
    """
    Determine if it's ok for us to add a summary to the Jira issue.

    We only want to post summaries to issues that we are allowed to.

    Parameters:
        - issue: The issue to check

    Returns:
        True if it's ok to summarize, False otherwise
    """
    has_summary_label = SUMMARY_ALLOWED_LABEL in issue.labels
    is_in_allowed_project = issue.project_key in os.environ.get(
        "ALLOWED_PROJECTS", ""
    ).split(",")
    return has_summary_label and is_in_allowed_project


def _chat_model(model_name: str = _MODEL_ID) -> LLM:
    """
    Return a chat model to use for summarization.

    This function creates a chat model using the IBM GenAI API, and requires the
    API endpoint (GENAI_API) and API key (GENAI_KEY) to be present via
    environment variables.
    """
    # https://ibm.github.io/ibm-generative-ai/v2.3.0/rst_source/examples.extensions.langchain.langchain_chat_stream.html
    genai_key = os.environ["GENAI_KEY"]
    genai_url = os.environ["GENAI_API"]
    credentials = Credentials(api_key=genai_key, api_endpoint=genai_url)
    client = Client(credentials=credentials)

    return LangChainInterface(
        model_id=model_name,
        client=client,
        parameters=TextGenerationParameters(
            decoding_method=DecodingMethod.SAMPLE,
            max_new_tokens=4000,
            min_new_tokens=10,
            temperature=0.5,
            top_k=50,
            top_p=1,
            beam_width=None,
            random_seed=None,
            repetition_penalty=None,
            stop_sequences=None,
            time_limit=None,
            truncate_input_tokens=None,
            typical_p=None,
        ),
    )


def get_issues_to_summarize(
    client: Jira, since: datetime = datetime.fromisoformat("2020-01-01")
) -> List[str]:
    """
    Get a list of issues to summarize.

    This function returns a list of issues that are labeled with the
    SUMMARY_ALLOWED_LABEL label.

    Parameters:
        - client: The Jira client to use
        - since: Only return issues updated after this time

    Returns:
        A list of issue keys
    """
    # The time format for the query needs to be in the local timezone of the
    # user, so we need to convert
    user_zi = Myself(client).tzinfo
    since_string = since.astimezone(user_zi).strftime("%Y-%m-%d %H:%M")
    updated_issues = client.jql(
        f"labels = '{SUMMARY_ALLOWED_LABEL}' and updated >= '{since_string}' ORDER BY updated DESC",
        limit=50,
        fields="key,updated",
    )
    if not isinstance(updated_issues, dict):
        return []
    keys: List[str] = [issue["key"] for issue in updated_issues["issues"]]
    # Filter out any issues that are not in the allowed projects
    filtered_keys = []
    for key in keys:
        # Refresh the issue cache to ensure we have the latest data in cache.
        # This is definitely needed since the keys are the result of the query
        # for recently updated issues.
        issue = issue_cache.get_issue(client, key, refresh=True)
        if is_ok_to_post_summary(issue):
            filtered_keys.append(key)
    keys = filtered_keys

    _logger.info("Issues updated since %s: %s", since_string, ", ".join(keys))

    # Given the updated issues, we also need to propagate the summaries up the
    # hierarchy. We first need to add the parent issues of all the updated
    # issues to the list of issues to summarize.
    all_keys = keys.copy()
    for key in keys:
        parents = issue_cache.get_issue(client, key).all_parents
        # Go through the parent issues, and add them to the list of issues to
        # summarize, but only if they are marked for summarization.
        for parent in parents:
            if parent not in all_keys:
                issue = issue_cache.get_issue(client, parent, refresh=True)
                if is_ok_to_post_summary(issue):
                    all_keys.append(parent)
                else:
                    break
    # Sort the keys by level so that we summarize the children before the
    # parents, making the updated summaries available to the parents.
    keys = sorted(set(all_keys), key=lambda x: issue_cache.get_issue(client, x).level)
    return keys
