# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# --- Static FFmpeg/ffprobe (pinned) -------------------------------------------
FROM mwader/static-ffmpeg:8.0.1 AS ffmpeg

# --- Build: install locked, hash-verified dependencies into a venv -----------
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements/base.txt /tmp/requirements/base.txt
RUN /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements/base.txt

# --- Runtime -----------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
ARG APP_UID=1000
ARG APP_GID=1000
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    TZ=Asia/Kolkata

RUN groupadd --system --gid "${APP_GID}" app \
 && useradd --system --uid "${APP_UID}" --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /app/recordings /app/cache/playback /app/staticfiles \
 && chown -R app:app /app/cache

COPY --from=ffmpeg /ffmpeg /ffprobe /usr/local/bin/
COPY --from=builder /opt/venv /opt/venv
COPY --chown=root:root src/ /app/src/
WORKDIR /app/src

# Static files are collected at build time (no secrets needed for this step).
RUN DJANGO_SECRET_KEY=build-only-not-secret python manage.py collectstatic --noinput -v 0

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz/', timeout=4).status == 200 else 1)"
CMD ["gunicorn", "--config", "/app/src/gunicorn.conf.py", "config.wsgi:application"]

# --- Test image: runtime + test tooling -----------------------------------------
FROM runtime AS test
USER root
COPY requirements/dev.txt /tmp/requirements/dev.txt
RUN /opt/venv/bin/pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements/dev.txt
COPY pyproject.toml /app/pyproject.toml
COPY tests/ /app/tests/
WORKDIR /app
USER app
CMD ["pytest"]
