# This Dockerfile is based on the pattern recommended by the pipenv docs:
# https://pipenv.pypa.io/en/latest/docker.html
FROM python:3.12@sha256:f71437b2bad6af0615875c8f7fbeeeae1b73e3c76b82056d283644aca5afe355 as builder

RUN pip install --no-cache-dir pipenv==2023.12.1
ENV PIPENV_VENV_IN_PROJECT=1

WORKDIR /app
COPY Pipfile Pipfile.lock /app/
RUN pipenv --no-site-packages install -v --deploy


############################################################
FROM python:3.12-slim@sha256:6d587a04cdb6bb3b4e6a388faf937255862cb900e11c0593b7da1f72458effc9 as final

RUN adduser --uid 19876 summarizer-bot && \
    mkdir /app && \
    chown summarizer-bot:summarizer-bot /app
USER 19876

# Make sure stdout gets flushed so we see it in the pod logs
ENV PYTHONUNBUFFERED=true

WORKDIR /app
COPY --from=builder --chown=summarizer-bot:summarizer-bot /app/.venv /app/.venv
COPY --chown=summarizer-bot:summarizer-bot *.py /app/

ENTRYPOINT ["/app/.venv/bin/python", "bot.py"]
