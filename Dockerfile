# This Dockerfile is based on the pattern recommended by the pipenv docs:
# https://pipenv.pypa.io/en/latest/docker.html
FROM python:3.12@sha256:27d499edab2db8f3e28c2506c053075922fab1ae6cdd38a10f04fa2c9f8c5b41 as builder

RUN pip install --no-cache-dir pipenv==2023.12.1
ENV PIPENV_VENV_IN_PROJECT=1

WORKDIR /app
COPY Pipfile Pipfile.lock /app/
RUN pipenv --no-site-packages install -v --deploy


############################################################
FROM python:3.12-slim@sha256:44fa7d815050f3dc43b1d5d50c3f4d19870a83c96301c9653e8cf908ccd7892b as final

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
