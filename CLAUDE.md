# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Bilingual (English/Arabic) hadith search engine plus an annotation/benchmark platform for evaluating retrieval systems. Backend is FastAPI + PostgreSQL (pgvector) holding the corpus, the BM25 postings and the E5 embeddings; frontend is React 19 / TypeScript / Tailwind / Vite. Deeper docs live in `docs/` (`ARCHITECTURE.md`, `EVALUATION.md`, `FINE_TUNING.md`, `CORPUS_DECISIONS.md`, `WIKI.md`).

## Commands

Run backend commands from `backend/` (use the repo-root `.venv`) (paths like `data/` and `scripts.*` imports are relative to it).

```bash
# Setup (after pip install -r ../requirements.txt; mkdir backend/data)
# Needs PostgreSQL with pgvector and DATABASE_URL set (see .env.example), e.g.
#   docker run -d -p 5432:5432 -e POSTGRES_USER=hadith -e POSTGRES_PASSWORD=change-me -e POSTGRES_DB=hadith pgvector/pgvector:pg17
python -m nltk.downloader punkt_tab stopwords wordnet averaged_perceptron_tagger averaged_perceptron_tagger_eng
camel_data -i disambig-mle-calima-msa-r13

# Build data artifacts (corpus -> DB -> preprocess -> BM25 index -> embeddings -> pooling)
python scripts/build_all.py                    # --skip-embeddings to skip embeddings+pooling, --force to overwrite without prompts
python scripts/<step>.py                       # data_creation, profile, preprocess, build_inverted_index, build_embeddings, pooling

# Run
uvicorn main:app --reload --port 8000          # backend
cd ../frontend && npm run dev                  # frontend on :5173
cd ../frontend && npm run lint                 # eslint
cd ../frontend && npm run build                # tsc -b && vite build

tools/deploy.sh init                           # docker compose: postgres + nginx (:8000) + the blue app; Dockerfile builds the frontend and FastAPI serves it
tools/deploy.sh deploy --build && tools/deploy.sh canary 10 && tools/deploy.sh promote   # blue/green release (see docs/HANDOFF.md item 21)
```

### Lint / format

```bash
.venv/bin/ruff check backend tests   # add --fix to autofix (E, F, W, I rules; E402 ignored in a few scripts on purpose)
.venv/bin/black backend tests        # line length 100; config in pyproject.toml
```

Copy `.env.example` to `.env` for local settings. No API keys are needed to run the app.

### Pre-commit hooks and git workflow

```bash
.venv/bin/pre-commit install             # once per clone
.venv/bin/pre-commit run --all-files     # gitleaks, private-key check, whitespace, ruff, black, vulture
```

`.pre-commit-config.yaml` runs gitleaks on every commit's staged diff, then ruff (with `--fix`), black and vulture from the versions in `requirements-dev.txt`. A failing or file-modifying hook aborts the commit; re-stage and commit again. Never bypass with `--no-verify`. Work on a branch off `main` (`chore/...`, `feat/...`, `fix/...`) and open a PR; do not commit to `main` directly. Commit messages are short, imperative and specific, with no filler words.

### Code-quality tooling

```bash
.venv/bin/ruff check .                   # includes C901: every function must stay at cyclomatic complexity <= 10
.venv/bin/radon cc backend -a -nb -s     # prints nothing above grade B when clean
.venv/bin/vulture                        # dead code; config in pyproject.toml ([tool.vulture], backend/models excluded)
.venv/bin/python -m pytest --cov=backend --cov-report=term-missing:skip-covered
tools/mutation.sh run --max-children 6   # mutmut on the pure-logic modules; tools/mutation.sh results lists survivors
```

`tools/mutation.sh` stages `backend/` flattened into `.mutation/` (mutmut derives module keys from paths, and the code imports `features`, not `backend.features`); the module list lives in `tools/mutation.pyproject.toml`. Keep new logic in small single-purpose functions, inject dependencies (`SearchContext`, `Features`, `Depends`) instead of importing globals, and put new toggles in `backend/features.py`.

### Imports

Use the package-level exports instead of reaching into submodules: `from models import Hadith, KvPair, SearchRequest` (ORM `Hadith`; the pydantic one is `HadithSchema`), `from database import get_session, read_hadiths_df, Hadith`, `from routers import auth_router, search_router`, `from scripts import bm25, get_model, preprocess_english`. `scripts` and `routers` resolve names lazily (PEP 562) so importing them stays cheap and APP_MODE=annotation never loads the search stack. New public names must be added to the map in `scripts/__init__.py` / `routers/__init__.py`. Inside `scripts/search|loading|preprocess|evaluation` keep importing sibling modules directly (avoids circular imports).

### Tests

```bash
uv venv --python 3.12 .venv                    # 3.12 matches the Dockerfile; pinned numpy 1.26 has no 3.14 wheels
uv pip install --python .venv/bin/python torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv/bin/python -r requirements-dev.txt   # keeps the CPU torch installed above (2.11.0+cpu satisfies ==2.11.0)
.venv/bin/python -m pytest                     # parallel via pytest-xdist (-n auto in pyproject.toml)
.venv/bin/python -m pytest -n0 tests/test_search.py::test_rrf_fusion_combines_lists   # single test, serial
```

Tests live in repo-root `tests/` (config in `pyproject.toml`, `pythonpath = backend`). Nothing needs real data: `tests/conftest.py` builds a 3-hadith corpus, inverted index, embeddings, a fake E5 model and a fresh PostgreSQL schema per test (so xdist is safe). Tests need a Postgres with pgvector at `TEST_DATABASE_URL` (default `postgresql+psycopg://postgres:test-only-password@localhost:55432/hadith_test`; start it with `docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=test-only-password -e POSTGRES_DB=hadith_test pgvector/pgvector:pg17`) and skip with that hint when none is reachable. Conventions: fixtures and test helpers start with `_`; router tests are async (`pytest-asyncio` auto mode + `httpx.AsyncClient` over ASGI). Tests marked `nlp` need real NLTK data and skip without it.

The DB layer is SQLAlchemy 2.0 ORM only (no raw SQL anywhere; `tests/test_database.py` greps for it). Models live in `backend/models/orm.py`; `database.py` reads `DATABASE_URL` (psycopg driver) and gives `get_session()` (async: `async with get_session() as session:`), `get_sync_session()`/`read_hadiths_df()`/`get_hadith_row()` for scripts and ranking, and `init_schema()`/`init_schema_sync()` which enable the `vector` extension and call `create_all` (no Alembic, no migrations; foreign keys are enforced). `drop_corpus_tables()` drops the hadiths and everything derived from them. `scripts/kv_generator.py` wraps the async reads in `asyncio.run`.

## Architecture

- **`backend/features.py`** — feature toggles (frozen `Features`): annotation, kv_pairs, benchmark, search, dense_retrieval (E5), eager_model, finetuned_adapter_path. `load_features()` precedence: `FEATURE_<NAME>` env > `APP_MODE` preset > default. Presets: `annotation` (no search stack; the Docker default), `search` (lazy E5 model), `research` (eager E5 model).
- **`backend/main.py`** — `create_app(features=None, static_dir=None)` factory (`app = create_app()`); routers are included per feature flag, and `startup.py` creates tables and preloads indices only when search is enabled. Also serves the built SPA (`STATIC_DIR`, `../static`, or `../frontend/dist`) with an SPA fallback route, so the catch-all `/{full_path}` is registered last.
- **`backend/services/`** — decoupled logic behind the routers: `ranking.py` (all ranking as SQL over a sync `Session`: BM25/TF-IDF/term overlap from the `postings`/`terms`/`hadith_lengths` tables, dense search with pgvector cosine distance, PRF, RRF, cross-encoder candidates; `restrict=` limits a search to a set of ids), `retrieval.py` (`SearchContext(session, model)` + `SYSTEMS` registry of strategies, each declaring which feature flags it needs), `results.py` (`build_results` loads rows by id, filters, keeps ranking order), `agreement.py` (inter-annotator kappa/Spearman/raw agreement, pure functions).
- **`backend/routers/`** — `search.py` (`make_search_router(features)` serves `GET /api/v1/searches?method=<slug>` for the enabled systems; `get_search_context` is the `Depends` seam tests override), `annotation.py` + `auth.py` (relevance-judgment platform), `benchmark.py`, `kv_pairs.py`, `hadiths.py`, `root.py` (`GET /api/v1` link index). `get_search_context` yields one sync DB session per request.
- **REST conventions** (`backend/rest.py`, `backend/tokens.py`) — every route is under `/api/v1` and named after a resource. Errors are RFC 9457 `application/problem+json` (installed by `install_error_handlers`; raise `HTTPException` and it is formatted for you). Cacheable reads return `json_response(request, body, max_age=...)`, which adds `Cache-Control` and a content-hash `ETag` and answers `If-None-Match` with 304, so bodies must be deterministic (timing goes in the `Server-Timing` header, not the body). Responses carry `_links`; add a link to `routers/root.py` when adding a resource. Auth is a stateless HS256 JWT (`AUTH_SECRET`, 32+ chars, same on every server; no session table, so no server-side revocation). New headers the browser must read go in `EXPOSED_HEADERS`. Rate limiting lives in nginx, not the app: `nginx/default.conf` limits `POST /api/v1/tokens` and `/api/v1/annotators` per client address (5 tries at once, then 1 per minute, 429 as problem+json with `Retry-After`). `docker compose up` starts nginx in front of the app and publishes it on port 8000; the app itself is not published. Run `tools/test-nginx.sh` after editing the nginx config. Releases are blue/green: compose has `app-blue` and `app-green` (profiles, started by `tools/deploy.sh`), nginx picks the colour from `deploy/state/routing.conf` (generated, git-ignored; canary split by client-address hash) and responses carry `X-Release`. Both colours share one PostgreSQL, so schema changes must be additive (old release must still run on the new schema); `tests/test_deploy_script.py` tests the script against a fake docker. Running uvicorn alone (no nginx) has no rate limit. All kv-pairs routes require a token. Security headers and the CSP are added in `main.py` (`add_security_headers`); if the SPA needs a new external origin, add it to `CONTENT_SECURITY_POLICY`. Static file serving resolves real paths so `../` cannot escape the static dir; keep it that way.
- **`backend/scripts/`** serves double duty: build-pipeline scripts run from the CLI **and** library modules imported by routers (`scripts.search`, `scripts.loading`, `scripts.preprocess`). `migrate_to_postgres.py` is the one-time copy from an old SQLite + `.npy` install.
  - Pipeline scripts (`preprocess.run`, `full_evaluation.main`, `finetune.train`, `finetune_eval.run_evaluation`) are thin orchestrators over small helpers; `eval_pipeline.py` is the evaluation flow shared by `evaluation.py` and `finetune_eval.py`.
  - `search.py` only keeps RRF fusion; the 8 retrieval systems (term overlap, TF-IDF, BM25, hybrid, BM25+PRF, cosine, semantic rerank, RRF) are in `services/ranking.py`. The old in-memory versions live on as `tests/_legacy_search.py`, the oracle `tests/test_ranking.py` compares the SQL against.
  - `loading.py` holds cached loaders for the E5 model, the CAMeL disambiguator and the lemmatizer; `embedding_store.py` upserts embeddings and `build_inverted_index.py` rewrites the index tables.
- **Data**: the corpus, BM25 index (`terms`, `postings`, `hadith_lengths`) and embeddings (`hadith_embeddings`, one `english` and one `arabic` float32 vector per hadith, no fixed dimension, exact search with no index) are PostgreSQL tables. `backend/data/` (gitignored) only holds JSON produced by the build and evaluation steps: `qrels_ungraded.json`, `build_manifest.json`, results.
- **Indexing vs. embedding text**: BM25 postings are built over preprocessed *matn only* (`Preprocessed_*_Matn` columns); embeddings cover chapter title + matn using E5 `passage:` / `query:` prefixes. Queries must be preprocessed the same way as documents (NLTK for English, CAMeL Tools for Arabic).
- **Evaluation flow**: `pooling.py` builds candidate pools -> annotators/`llm_grader.py` produce qrels -> `evaluation.py`, `full_evaluation.py`, `stats_tests.py` compute metrics; `finetune.py`/`finetune_eval.py` cover embedding fine-tuning.
- **Frontend** (`frontend/src`): `pages/`, `components/`, `layouts/`, `api/` (client), `hooks/`, `i18n/`, `types/`. The Dockerfile skips `tsc` (known pre-existing type errors) and runs only `vite build`, so `npm run build` may fail on type errors that Docker tolerates.

## Notes

- `requirements.txt` pins `torch==2.11.0`; the Dockerfile installs CPU torch first and strips that line.
- The Dockerfile has three stages: `frontend-builder` (Node), `python-builder` (compilers + venv in `/opt/venv`, NLTK/CAMeL data in `/opt/nltk_data` and `/opt/camel_tools_data`) and `runtime` (copies only those, no gcc/git, runs as uid 10001 `app`). `backend/data` is owned by `app`, so a fresh named volume is writable; a bind mount must be `chown 10001`. Anything the app downloads at runtime (e.g. HF models via `HF_HOME`) must land under that data dir.
- Image scanning: `docker build --pull -t hadith-search:hardened . && tools/scan-image.sh` runs trivy (fixable HIGH/CRITICAL CVEs fail) and dockle (WARN and above fail) from their Docker images. `.github/workflows/docker-scan.yml` runs the same on pushes to main, PRs and weekly. The dockle suppressions (`settings.py` false positive, base-image apt cleanup) are commented in the script. If trivy fails on a new CVE, bump the pin in `requirements.txt` (or the base image) rather than ignoring it.
- Dockerfile lint and metadata: hadolint runs as the `hadolint-docker` pre-commit hook and as the first step of `docker-scan.yml` (rules suppressed on purpose are in `.hadolint.yaml`: DL3008 apt pins, DL3013 unpinned CPU torch). The image has a `HEALTHCHECK` (GET `/openapi.json`, 60s start period), runs as numeric `10001:10001`, and carries OCI labels; pass `--build-arg REVISION=$(git rev-parse HEAD) --build-arg CREATED=$(date -u +%FT%TZ)` to fill `revision` and `created` (CI does).
- Dependency floor set by the scans: `nltk>=3.10.3`, `transformers 5.x`, `starlette 1.x`, `camel_tools 1.6.0` (its `numpy>=2` requirement moved the pin from 1.26.4 to 2.5.3).
- README setup examples use Windows-style backslashes (`scripts\build_all.py`); on Linux/WSL use forward slashes.
- Embedding generation wants a CUDA GPU (~12GB VRAM); pre-built embeddings can be dropped into `backend/data/`.
