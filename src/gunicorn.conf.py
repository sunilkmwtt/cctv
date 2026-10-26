"""Gunicorn settings tuned for a low-memory home server."""

import os

bind = "0.0.0.0:8000"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
# gthread keeps memory low and lets remux jobs run in a background thread.
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = 60
graceful_timeout = 30
keepalive = 5
# Do not recycle workers: an in-flight remux thread would be killed.
max_requests = 0
# Request logging is done by core.middleware.RequestLogMiddleware.
accesslog = None
errorlog = "-"
loglevel = os.environ.get("DJANGO_LOG_LEVEL", "info").lower()
forwarded_allow_ips = "*"
worker_tmp_dir = "/dev/shm"
