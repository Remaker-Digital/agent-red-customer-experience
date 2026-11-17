# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
# Build the complete application using public base images and local inputs.
FROM node:20-bookworm-slim AS frontend
WORKDIR /build
COPY admin/ ./admin/
COPY widget/ ./widget/
RUN npm ci --prefix admin --no-audit --no-fund \
    && npm ci --prefix admin/shopify --no-audit --no-fund \
    && npm ci --prefix admin/standalone --no-audit --no-fund \
    && npm ci --prefix admin/provider --no-audit --no-fund \
    && npm ci --prefix widget --no-audit --no-fund
RUN npm run build --prefix admin/shopify \
    && npm run build --prefix admin/standalone \
    && npm run build --prefix admin/provider \
    && npm run build --prefix widget

FROM python:3.12-slim-bookworm AS runtime
LABEL maintainer="Michael Palmeter <dev@remakerdigital.com>"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ENVIRONMENT=development UVICORN_WORKERS=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
ARG BUILD_VERSION=2.0.24
ENV BUILD_VERSION=${BUILD_VERSION}
COPY src/ ./src/
COPY config/ ./config/
COPY pyproject.toml LICENSE NOTICE ./
COPY docs-site/docs/ ./docs-site/docs/
COPY --from=frontend /build/admin/shopify/dist/ ./admin/shopify/dist/
COPY --from=frontend /build/admin/standalone/dist/ ./admin/standalone/dist/
COPY --from=frontend /build/admin/provider/dist/ ./admin/provider/dist/
COPY --from=frontend /build/widget/dist/ ./widget/dist/
RUN groupadd --gid 1000 agentred \
    && useradd --uid 1000 --gid agentred --shell /bin/sh --no-create-home agentred \
    && chown -R agentred:agentred /app
USER agentred
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5).read()"]
ENTRYPOINT ["tini", "--"]
CMD ["sh", "-c", "export INTERNAL_VERIFICATION_SECRET=${INTERNAL_VERIFICATION_SECRET:-$(python -c 'import secrets; print(secrets.token_hex(32))')} && exec uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-1} --log-level info --no-server-header"]

FROM runtime AS test-host
USER root
COPY requirements-test.txt ./
RUN pip install --no-cache-dir -r requirements-test.txt
COPY --chown=agentred:agentred tests/ ./tests/
COPY --chown=agentred:agentred test_host/ ./test_host/
COPY --chown=agentred:agentred shopify.app.toml ./
ENV ENVIRONMENT=development
USER agentred
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8001/health', timeout=5).read()"]
CMD ["uvicorn", "test_host.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]

# The default build remains the application, not the optional test service.
FROM runtime AS application
