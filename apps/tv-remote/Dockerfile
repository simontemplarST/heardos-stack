# TV Remote — a single FastAPI container that serves the remote UI and proxies commands to Home Assistant.
# Pinned to the multi-arch index digest of python:3.12-slim (looked up 2026-11-16). To update:
#   skopeo inspect --raw docker://docker.io/library/python:3.12-slim | sha256sum
FROM docker.io/library/python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS deps
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

FROM docker.io/library/python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /app
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY server.py ./
COPY static/ ./static/
RUN groupadd -r app && useradd -r -g app app && chown -R app:app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --retries=3 CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["python3", "server.py"]
