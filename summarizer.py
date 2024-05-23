"""Module code to handle summarization of Jira issues."""

import io
import logging
import os
import textwrap
from datetime import UTC, datetime
from typing import List, Tuple, Union

from atlassian import Jira  # type: ignore
from genai import Client, Credentials
from genai.extensions.langchain import LangChainInterface
from genai.schema import (
    DecodingMethod,
    TextGenerationParameters,
    TextTokenizationParameters,
    TextTokenizationReturnOptions,
)
from langchain_core.language_models import LLM

import text_wrapper
from jiraissues import (
    Issue,
    RelatedIssue,
    check_response,
    descendants,
    get_self,
    issue_cache,
)

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

_wrapper = text_wrapper.TextWrapper(SUMMARY_START_MARKER, SUMMARY_END_MARKER)


def summarize_issue(  # pylint: disable=too-many-arguments,too-many-branches,too-many-locals
    issue: Issue,
    max_depth: int = 0,
    send_updates: bool = False,
    regenerate: bool = False,
    return_prompt_only: bool = False,
    current_depth: int = 0,
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
        - return_prompt_only: If True, return the prompt only and don't actually
          summarize the issue

    Returns:
        A string containing the summary
    """

    # If the current summary is up-to-date and we're not asked to regenerate it,
    # return what's there
    if not regenerate and is_summary_current(issue):
        _logger.info("Summarizing (using current): %s", issue)
        return _wrapper.get(issue.status_summary) or ""

    _logger.info("Summarizing: %s", issue)
    # if we have not reached max-depth, summarize the child issues for inclusion in this summary
    child_summaries: List[Tuple[RelatedIssue, str]] = []
    for child in issue.children:
        if current_depth < max_depth:
            child_issue = issue_cache.get_issue(issue.client, child.key)
            # If the child issue is allowed to have a summary, we restart our
            # recursion since it should get the full benefit of the recursion.
            new_depth = 0 if is_ok_to_post_summary(child_issue) else current_depth + 1
            child_summaries.append(
                (
                    child,
                    summarize_issue(
                        child_issue,
                        max_depth=max_depth,
                        send_updates=send_updates,
                        regenerate=False,
                        current_depth=new_depth,
                    ),
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
{issue.description}

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
* Include only the text of your summary in the response with no formatting.
* Limit your summary to 100 words or less.

```
{full_description}
```
"""
    if return_prompt_only:
        return llm_prompt

    _logger.info(
        "Summarizing %s (%d tokens) via LLM", issue.key, count_tokens(llm_prompt)
    )
    _logger.debug("Prompt:\n%s", llm_prompt)

    chat = _chat_model()
    summary = chat.invoke(llm_prompt, stop=["<|endoftext|>"]).strip()
    folded_summary = textwrap.fill(summary, width=_WRAP_COLUMN)
    if send_updates and is_ok_to_post_summary(issue):
        # Replace any existing AI summary w/ the updated one
        issue.update_status_summary(
            _wrapper.upsert(issue.status_summary, folded_summary)
        )
    return folded_summary


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
    if issue.last_change is None or SUMMARY_START_MARKER not in issue.status_summary:
        return last_update

    for change in issue.changelog:
        if change.author == get_self(
            issue.client
        ).display_name and "Status Summary" in [chg.field for chg in change.changes]:
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
        _logger.debug(
            "is_summary_current: no - Issue %s is not allowed to have a summary",
            issue.key,
        )
        return False  # We're not allowed to summarize it, so it's never current

    last_update = summary_last_updated(issue)
    if issue.updated > last_update:
        # It's been changed since we last updated the summary
        _logger.debug(
            "is_summary_current: no - Issue %s has been updated more recently than summary %s > %s",
            issue.key,
            issue.updated.isoformat(),
            last_update.isoformat(),
        )
        return False
    for child in issue.children:
        child_issue = issue_cache.get_issue(issue.client, child.key)
        if child_issue.updated > last_update:
            # A child issue has been updated since we last updated the summary
            _logger.debug(
                "is_summary_current: no - Issue %s has more recently updated child %s",
                issue.key,
                child_issue.key,
            )
            return False
    _logger.debug("is_summary_current: yes - Issue %s is current", issue.key)
    return True


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


def _genai_client() -> Client:
    genai_key = os.environ["GENAI_KEY"]
    genai_url = os.environ["GENAI_API"]
    credentials = Credentials(api_key=genai_key, api_endpoint=genai_url)
    client = Client(credentials=credentials)
    return client


def _chat_model(model_name: str = _MODEL_ID) -> LLM:
    """
    Return a chat model to use for summarization.

    This function creates a chat model using the IBM GenAI API, and requires the
    API endpoint (GENAI_API) and API key (GENAI_KEY) to be present via
    environment variables.
    """
    # https://ibm.github.io/ibm-generative-ai/v2.3.0/rst_source/examples.extensions.langchain.langchain_chat_stream.html
    client = _genai_client()

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
    client: Jira,
    since: datetime = datetime.fromisoformat("2020-01-01"),
    limit: int = 25,
) -> tuple[List[str], datetime]:
    """
    Get a list of issues to summarize.

    This function returns a list of issues that are labeled with the
    SUMMARY_ALLOWED_LABEL label.

    Parameters:
        - client: The Jira client to use
        - since: Only return issues updated after this time
        - limit: The maximum number of issues to return

    Returns:
        A list of issue keys
    """
    # The time format for the query needs to be in the local timezone of the
    # user, so we need to convert
    user_zi = get_self(client).tzinfo
    since_string = since.astimezone(user_zi).strftime("%Y-%m-%d %H:%M")
    updated_issues = check_response(
        client.jql(
            f"labels = '{SUMMARY_ALLOWED_LABEL}' and updated >= '{since_string}' ORDER BY updated ASC",  # pylint: disable=line-too-long
            limit=limit,
            fields="key,updated",
        )
    )
    keys: List[str] = [issue["key"] for issue in updated_issues["issues"]]
    # Filter out any issues that are not in the allowed projects
    filtered_keys = []
    most_recent = since
    issue_cache.clear()  # Clear the cache to ensure we have the latest data
    for key in keys:
        issue = issue_cache.get_issue(client, key)
        if is_ok_to_post_summary(issue):
            filtered_keys.append(key)
            most_recent = max(most_recent, issue.updated)
    keys = filtered_keys

    _logger.info(
        "Issues updated since %s: (%d) %s",
        since.isoformat(),
        len(keys),
        ", ".join(keys),
    )

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
                issue = issue_cache.get_issue(client, parent)
                if is_ok_to_post_summary(issue):
                    all_keys.append(parent)
                else:
                    break
    # Sort the keys by level so that we summarize the children before the
    # parents, making the updated summaries available to the parents.
    keys = sorted(set(all_keys), key=lambda x: issue_cache.get_issue(client, x).level)
    _logger.info(
        "Total keys: %d, most recent modification: %s",
        len(keys),
        most_recent.isoformat(),
    )
    return (keys, most_recent)


def count_tokens(text: Union[str, list[str]]) -> int:
    """
    Count the number of tokens in a string.

    This function counts the number of tokens in a string

    Parameters:
        - text: The text to count tokens in

    Returns:
        The number of tokens in the text
    """
    client = _genai_client()
    response = client.text.tokenization.create(
        model_id=_MODEL_ID,
        input=text,  # str | list[str]
        parameters=TextTokenizationParameters(
            return_options=TextTokenizationReturnOptions(
                input_text=False,
                tokens=False,
            ),
        ),
    )

    total_tokens = 0
    for resp in response:
        for result in resp.results:
            total_tokens += result.token_count
    return total_tokens


def add_summary_label(issue: Issue) -> None:
    """
    Add the SUMMARY_ALLOWED_LABEL label to the issue.

    Parameters:
        - issue: The issue to add the label to
    """
    if SUMMARY_ALLOWED_LABEL in issue.labels:
        return
    labels = issue.labels.copy()
    labels.add(SUMMARY_ALLOWED_LABEL)
    _logger.debug("Adding %s label to %s", SUMMARY_ALLOWED_LABEL, issue.key)
    issue.update_labels(labels)


def remove_summary_label(issue: Issue) -> None:
    """
    Remove the SUMMARY_ALLOWED_LABEL label from the issue.

    Parameters:
        - issue: The issue to remove the label from
    """
    if SUMMARY_ALLOWED_LABEL not in issue.labels:
        return
    labels = issue.labels.copy()
    labels.remove(SUMMARY_ALLOWED_LABEL)
    _logger.debug("Removing %s label from %s", SUMMARY_ALLOWED_LABEL, issue.key)
    issue.update_labels(labels)


def add_summary_label_to_descendants(client: Jira, issue_key: str) -> None:
    """
    Add the SUMMARY_ALLOWED_LABEL label to the issue and its descendants.

    Parameters:
        - client: The Jira client to use
        - issue_key: The key of the issue to add the label to
    """
    desc = descendants(client, issue_key)
    desc.append(issue_key)
    for key in desc:
        issue = issue_cache.get_issue(client, key)
        add_summary_label(issue)
