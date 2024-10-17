# This Dockerfile is based on the pattern recommended by the pipenv docs:
# https://pipenv.pypa.io/en/latest/docker.html
FROM python:3.12@sha256:8768f0b89d43d8552f28a8586ebdae4938074ca9ef4df76265efc6ff1450c6cb as builder

RUN pip install --no-cache-dir pipenv==2023.12.1
ENV PIPENV_VENV_IN_PROJECT=1

WORKDIR /app
COPY Pipfile Pipfile.lock /app/
RUN pipenv --no-site-packages install -v --deploy


############################################################
FROM python:3.12-slim@sha256:a0a4a07b7ace1b39ec7edbc38156f187b3a4fe0e6856e3e8a169ff9aa6f8c850 as final

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
