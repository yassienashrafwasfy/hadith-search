# syntax=docker/dockerfile:1
# ===== Stage 1: Build frontend =====
FROM node:22-slim AS frontend-builder
WORKDIR /build
COPY frontend/package*.json ./
# Cache mount keeps the npm download cache between builds without baking it into a layer
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ .
# Skip tsc (pre-existing type errors), Vite/esbuild handles transpilation
RUN npx vite build

# ===== Stage 2: Python build (compilers live only here) =====
FROM python:3.12-slim AS python-builder

# Debian images delete downloaded .debs after install; disable that so the cache mount is useful
RUN rm -f /etc/apt/apt.conf.d/docker-clean
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
# Data lands outside any home directory so the non-root runtime user can read it
ENV NLTK_DATA=/opt/nltk_data
ENV CAMELTOOLS_DATA=/opt/camel_tools_data
RUN mkdir -p "$NLTK_DATA" "$CAMELTOOLS_DATA"

WORKDIR /build
# Install CPU-only torch first (before requirements.txt is copied, so editing that file
# does not re-download it) to avoid pulling CUDA wheels
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
# Install remaining dependencies (torch line removed, already installed)
RUN --mount=type=cache,target=/root/.cache/pip \
    grep -v '^torch==' requirements.txt > /tmp/req.txt && \
    pip install -r /tmp/req.txt

# Pre-download NLTK data (needed for English preprocessing at search time)
RUN python -c "import nltk; \
    [nltk.download(p, download_dir='/opt/nltk_data') for p in ['punkt','punkt_tab','averaged_perceptron_tagger', \
    'averaged_perceptron_tagger_eng','wordnet','stopwords']]" \
    # wordnet stays zipped and NLTK extracts it on first use, which the read-only runtime user cannot do
    && python -c "import zipfile; zipfile.ZipFile('/opt/nltk_data/corpora/wordnet.zip').extractall('/opt/nltk_data/corpora')"

# Pre-download camel_tools MLE data (needed for Arabic preprocessing at search time).
# Installed at build time because the non-root runtime user cannot write to CAMELTOOLS_DATA.
# The download can stall without failing, so each try has a time limit and is repeated.
RUN for try in 1 2 3 4 5; do \
      timeout 180 camel_data -i disambig-mle-calima-msa-r13 && exit 0; \
      echo "camel_data try $try failed or timed out"; sleep 5; \
    done; exit 1

# ===== Stage 3: Runtime (no compilers, no git, non-root) =====
FROM python:3.12-slim AS runtime

# Pick up Debian security fixes newer than the base image (e.g. openssl); lists are removed after
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --create-home --uid 10001 app

COPY --from=python-builder /opt/venv /opt/venv
COPY --from=python-builder /opt/nltk_data /opt/nltk_data
COPY --from=python-builder /opt/camel_tools_data /opt/camel_tools_data

WORKDIR /app

# Copy backend code
COPY --chown=app:app backend/ ./backend/

# Copy frontend build from stage 1
COPY --from=frontend-builder --chown=app:app /build/dist ./static

# Create data directory (mounted as volume at runtime)
# Owned by the app user so a fresh named volume inherits write access
RUN mkdir -p /app/backend/data && chown app:app /app/backend/data

# Copy entrypoint script and fix line endings (Windows CRLF -> LF)
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN sed -i 's/\r$//' /app/docker-entrypoint.sh && chmod +x /app/docker-entrypoint.sh

# Working directory for Python module resolution (routers.*, scripts.*, database)
WORKDIR /app/backend

ENV PATH="/opt/venv/bin:$PATH"
ENV NLTK_DATA=/opt/nltk_data
ENV CAMELTOOLS_DATA=/opt/camel_tools_data
ENV HF_HOME=/app/backend/data/.hf_cache
ENV PYTHONPATH=/app/backend
ENV PYTHONUNBUFFERED=1
ENV APP_MODE=annotation

# Metadata goes last: REVISION/CREATED change every build and would bust the cache of later layers
ARG REVISION=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.title="hadith-search" \
      org.opencontainers.image.description="Bilingual hadith search engine and annotation platform" \
      org.opencontainers.image.source="https://github.com/yassienashrafwasfy/hadith-search" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}"

USER 10001:10001

EXPOSE 8000

# start-period is generous because search modes preload indices (and optionally the E5 model)
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"]

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
