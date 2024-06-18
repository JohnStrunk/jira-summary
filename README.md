# AI Jira Summarizer

This project generates summaries of Jira issues and posts them back into the
Jira issue description

## Required environment variables

It is recommended that you provide the variables via a `.env` file since they
will contain sensitive information. *There is already a `.gitignore` file that
will ignore the `.env` file.*

The following variables are required:

- `ALLOWED_PROJECTS`: A comma-separated list of Jira project keys that the bot
  is allowed to summarize (e.g., `ALLOWED_PROJECTS=OCTO,OCTOET`) (for the bot)
- `GENAI_API`: The API endpoint for the IBM AI model (e.g., `https://...`)
- `GENAI_KEY`: Your API key for the IBM AI model
- `JIRA_TOKEN`: A JIRA PAT token that will allow retrieving issues from Jira as
  well as posting the AI summaries
- `JIRA_URL`: The URL for the Jira instance (e.g., `https://...`)
- `JWT_SECRET_KEY`: The secret key for the JWT token (for the API)

## Commands

### Summarize a single Jira issue: `summarize_issue.py`

```console
$ pipenv run ./summarize_issue.py --help
Loading .env environment variables...
usage: summarize_issue.py [-h] [-d MAX_DEPTH] [--log-level
            {DEBUG,INFO,WARNING,ERROR,CRITICAL}] [-n] [-r] jira_issue_key

Summarize a JIRA issue

positional arguments:
  jira_issue_key        JIRA issue key

options:
  -h, --help            show this help message and exit
  -d MAX_DEPTH, --max-depth MAX_DEPTH
                        Maximum depth to recursively examine issues while summarizing
  --log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                        Set the logging level
  -n, --no-update       Do not update the Jira issues with the summaries
  -r, --regenerate      Force regeneration of summaries
```

### Summarizer bot: `bot.py`

```console
$ pipenv run ./bot.py --help
Loading .env environment variables...
usage: bot.py [-h] [-d MAX_DEPTH] [--log-level
            {DEBUG,INFO,WARNING,ERROR,CRITICAL}] [-m MODIFIED_SINCE] [-n] [-s SECONDS]

Summarizer bot

options:
  -h, --help            show this help message and exit
  -d MAX_DEPTH, --max-depth MAX_DEPTH
                        Maximum depth to recursively examine issues while summarizing
  --log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                        Set the logging level
  -m MODIFIED_SINCE, --modified-since MODIFIED_SINCE
                        Summarize issues that have been modified since this date/time
  -n, --no-update       Do not update the Jira issues with the summaries
  -s SECONDS, --seconds SECONDS
                        Seconds to wait between iterations
```

#### Methods to run the summarizer bot

Note: All these methods assume that the required environment variables are
configured via a `.env` file, as described above.

- **Run locally via pipenv**:
  - Install pipenv: `pip install pipenv`
  - Install the dependencies: `pipenv install`
  - Run the bot: `pipenv run ./bot.py <bot-args>`
- **Run locally via Docker**:
  - Build the Docker image: `docker build -t jira-summarizer .`
  - Run the Docker container: `docker run --env-file .env jira-summarizer
    <bot-args>`
- **Run in Kubernetes**:
  - Create a Namespace for the project: `kubectl create namespace
    jira-summarizer`
  - Create a Kubernetes secret with the environment variables: `kubectl -n
    jira-summarizer create secret generic jira-summarizer-secret
    --from-env-file=.env`
  - Apply the Kubernetes manifest: `kubectl -n jira-summarizer apply -f
    jira-summarizer.yaml`
  *You may need to adjust the image specification in the manifest to point to
  your Docker image repository*

### Summarizer API: `summarize_api.py`

Generate JWT tokens for users to access the summarizer API:

```console
$ pipenv run ./summarize_api.py
Loading .env environment variables...
Usage: ./summarize_api.py <userid>
```

Run locally for development/testing:

```console
$ pipenv run flask --app summarize_api.py run
Loading .env environment variables...
 * Tip: There are .env or .flaskenv files present. Do "pip install
   python-dotenv" to use them.
 * Serving Flask app 'summarize_api.py'
 * Debug mode: off
WARNING: This is a development server. Do not use it in a production deployment.
Use a production WSGI server instead.
 * Running on http://127.0.0.1:5000
Press CTRL+C to quit
```

The API can then be tested via curl:

```console
$ curl -s -H "Authorization: Bearer YOUR_GENERATED_API_TOKEN" \
http://127.0.0.1:5000/api/v1/summarize-issue?key=PROJ-1234 | jq . -r
{
  "key": "PROJ-1234",
  "stats": {
    "fetchrelated_time": 1.436011292,
    "getissue_time": 0.284554751,
    "llm_time": 5.364057366,
    "request_time": 7.088276041
  },
  "summary": "This is the issue summary.",
  "user": "zzz"
}
```

To run in OpenShift:

- Create the Namespace and Secret as described above
- Apply the Kubernetes manifest: `kubectl -n jira-summarizer apply -f summarize-api.yaml`
