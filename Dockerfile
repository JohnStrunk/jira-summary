# This Dockerfile is based on the pattern recommended by the pipenv docs:
# https://pipenv.pypa.io/en/latest/docker.html
FROM python:3.12@sha256:f78ea8a345769eb3aa1c86cf147dfd68f1a4508ed56f9d7574e4687b02f44dd1 as builder

RUN pip install --no-cache-dir pipenv==2023.12.1
ENV PIPENV_VENV_IN_PROJECT=1

WORKDIR /app
COPY Pipfile Pipfile.lock /app/
RUN pipenv --no-site-packages install -v --deploy


############################################################
FROM python:3.12-slim@sha256:2be8daddbb82756f7d1f2c7ece706aadcb284bf6ab6d769ea695cc3ed6016743 as final

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY *.py /app/

ENTRYPOINT ["/app/.venv/bin/python", "bot.py"]
