# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Bilingual (English/Arabic) hadith search engine plus an annotation/benchmark platform for evaluating retrieval systems. Backend is FastAPI + PostgreSQL (pgvector) holding the corpus, the BM25 postings and the Arabic embeddings (semantic search is Arabic only, through an ONNX export of `masterofaudio2077/Fada_ar_embedding`, 64 dimensions); frontend is React 19 / TypeScript / Tailwind / Vite. Deeper docs live in `docs/` (`ARCHITECTURE.md`, `EVALUATION.md`, `FINE_TUNING.md`, `CORPUS_DECISIONS.md`, `WIKI.md`).

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
tools/promote_model.sh run --registered-model NAME [--dry-run|--promote]   # best MLflow model -> ONNX -> new embeddings table -> deploy (HANDOFF item 40; needs requirements-mlops.txt)
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
tools/dead-code.sh [check|report|whitelist] [--min-confidence N]   # wrapper over vulture (also the pre-commit hook): check exits 1 on findings, report writes Markdown to $DEAD_CODE_OUT or the temp dir, whitelist prints a candidate; tools/test-dead-code.sh is its self-test
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
.venv/bin/python -m pytest tests/bdd           # behaviour tests: the Gherkin files in docs/behaviours (pytest-bdd)
.venv/bin/python -m pytest tests/security      # security tests against the real app (auth, injection, validation, headers, secrets); xfail(strict) marks known findings, see docs/HANDOFF.md item 36
```

Tests live in repo-root `tests/` (config in `pyproject.toml`, `pythonpath = backend`). Nothing needs real data: `tests/conftest.py` builds a 3-hadith corpus, inverted index, embeddings, a fake encoder and a fresh PostgreSQL schema per test (so xdist is safe); the schema fixture tags its connections with the schema name and, after each test, rolls back anything left open (ends those connections) before dropping the schema, so a failed test cannot leave locks behind (`tests/test_db_isolation.py`). Tests need a Postgres with pgvector at `TEST_DATABASE_URL` (default `postgresql+psycopg://postgres:test-only-password@localhost:55432/hadith_test`; start it with `docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=test-only-password -e POSTGRES_DB=hadith_test pgvector/pgvector:pg17`) and skip with that hint when none is reachable. Conventions: fixtures and test helpers start with `_`; router tests are async (`pytest-asyncio` auto mode + `httpx.AsyncClient` over ASGI). `tests/perf` (skipped unless `RUN_PERF=1`) times a running app against the budgets in HANDOFF item 37: `RUN_PERF=1 PERF_BASE_URL=http://APP:8000 .venv/bin/python -m pytest -n0 tests/perf -s`. Tests marked `nlp` need real NLTK data and skip without it. `tests/bdd` runs `docs/behaviours/*.feature` with pytest-bdd on the same fixtures (`.feature` files are the only copy; edit the wording there, steps are in `tests/bdd/test_*.py`; scenarios tagged `@nginx` or `@manual` are skipped by `tests/bdd/conftest.py`).

The DB layer is SQLAlchemy 2.0 ORM only (no raw SQL anywhere; `tests/test_database.py` greps for it). Models live in `backend/models/orm.py`; `database.py` reads `DATABASE_URL` (psycopg driver) and gives `get_session()` (async: `async with get_session() as session:`), `get_sync_session()`/`read_hadiths_df()`/`get_hadith_row()` for scripts and ranking, and `init_schema()`/`init_schema_sync()` which enable the `vector` and `pg_trgm` extensions and call `create_all` (no Alembic, no migrations; foreign keys are enforced). `drop_corpus_tables()` drops the hadiths and everything derived from them (with `CASCADE`; `create_database` puts the foreign keys from `annotations` and `kv_pairs` back in the same transaction). The schema is in 3NF: `books`, `chapters` and `hadith_preprocessed` hold what used to repeat on `hadiths`; `database.insert_hadith_rows()` splits flat rows, `read_hadiths_df()` with no columns joins them back (HANDOFF item 28). `scripts/kv_generator.py` wraps the async reads in `asyncio.run`.

## Architecture

- **`backend/settings.py`** — pydantic-settings `Settings` (`DATABASE_URL`, `AUTH_SECRET`, `AUTH_TOKEN_TTL_MINUTES`, `CORS_ORIGINS`, `STATIC_DIR`, `ARABIC_MODEL_DIR`, `ARABIC_ENCODER_THREADS`) and `APP_ENV` (`dev` default, `test`, `prod`). `get_settings()` is `lru_cache`d, so it reads the environment once; tests that change env vars call `get_settings.cache_clear()` (`tests/conftest.py` does it for every test and ignores the repo `.env`). `prod` needs `AUTH_SECRET` and `CORS_ORIGINS` (a `*` is refused; `docker-compose.yml` sets `APP_ENV=prod`) and hides `/docs` and `/openapi.json`. Feature flags stay in `features.py`.
- **`backend/features.py`** — feature toggles (frozen `Features`): annotation, kv_pairs, benchmark, search, dense_retrieval (Arabic encoder), eager_model. `load_features()` precedence: `FEATURE_<NAME>` env > `APP_MODE` preset > default. Presets: `annotation` (no search stack; the Docker default), `search` (lazy Arabic encoder), `research` (eager Arabic encoder).
- **`backend/main.py`** — `create_app(features=None, static_dir=None)` factory (`app = create_app()`); routers are included per feature flag, and `startup.py` creates tables and preloads indices only when search is enabled. Also serves the built SPA (`STATIC_DIR`, `../static`, or `../frontend/dist`) with an SPA fallback route, so the catch-all `/{full_path}` is registered last.
- **`backend/services/`** — decoupled logic behind the routers: `ranking.py` (all ranking as SQL over a sync `Session`: BM25/TF-IDF/term overlap from the `postings`/`terms`/`hadith_lengths` tables, dense search with pgvector cosine distance, PRF, RRF, cross-encoder candidates; `restrict=` limits a search to a set of ids), `retrieval.py` (`SearchContext(session, model)` + `SYSTEMS` registry of strategies, each declaring which feature flags it needs), `results.py` (`build_results` loads rows by id, filters, keeps ranking order), `exact_text.py` (the stored exact-match text in `hadith_exact_text`: word splitting, needles, occurrence counts, `rebuild`), `suggestions.py` (pg_trgm autocomplete and `did_you_mean`; thresholds `SUGGEST_SIMILARITY` 0.3, `HINT_SIMILARITY` 0.4, set per query with `set_config`), `agreement.py` (inter-annotator kappa/Spearman/raw agreement, pure functions).
- **`backend/routers/`** — `search.py` (`make_search_router(features)` serves `GET /api/v1/searches?method=<slug>` for the enabled systems; `get_search_context` is the `Depends` seam tests override), `annotation.py` + `auth.py` (relevance-judgment platform), `benchmark.py`, `kv_pairs.py`, `hadiths.py`, `suggestions.py` (`GET /api/v1/suggestions`, no search slot, limit at most 10), `root.py` (`GET /api/v1` link index). `get_search_context` yields one sync DB session per request.
- **REST conventions** (`backend/rest.py`, `backend/tokens.py`) — every route is under `/api/v1` and named after a resource. Errors are RFC 9457 `application/problem+json` (installed by `install_error_handlers`; raise `HTTPException` and it is formatted for you). Cacheable reads return `json_response(request, body, max_age=...)`, which adds `Cache-Control` and a content-hash `ETag` and answers `If-None-Match` with 304, so bodies must be deterministic (timing goes in the `Server-Timing` header, not the body). Responses carry `_links`; add a link to `routers/root.py` when adding a resource. Auth is a stateless HS256 JWT (`AUTH_SECRET`, 32+ chars, same on every server; no session table, so no server-side revocation). New headers the browser must read go in `EXPOSED_HEADERS`. Search spikes are queued in two places: nginx smooths each client address (`limit_req zone=search burst=20 delay=10`, `limit_conn 8`, 503 as problem+json), and `backend/limiter.py` (`SearchLimiter`, the `search_slot` dependency on `GET /api/v1/searches`) lets `SEARCH_MAX_CONCURRENT` (8) searches run, queues `SEARCH_QUEUE_SIZE` (64) more for up to `SEARCH_QUEUE_TIMEOUT_SECONDS` (5), and answers 503 + `Retry-After` beyond that; the frontend shows an Arabic 'server busy' message for 503. Other rate limiting lives in nginx, not the app: `nginx/default.conf` limits `POST /api/v1/tokens` and `/api/v1/annotators` per client address (5 tries at once, then 1 per minute, 429 as problem+json with `Retry-After`). `docker compose up` starts nginx in front of the app and publishes it on port 8000; the app itself is not published. Run `tools/test-nginx.sh` after editing the nginx config. Releases are blue/green: compose has `app-blue` and `app-green` (profiles, started by `tools/deploy.sh`), nginx picks the colour from `deploy/state/routing.conf` (generated, git-ignored; canary split by client-address hash) and responses carry `X-Release`. Both colours share one PostgreSQL, so schema changes must be additive (old release must still run on the new schema); `tests/test_deploy_script.py` tests the script against a fake docker, and `tools/test-rollback.sh` (run it by hand after editing `tools/deploy.sh`; needs Docker, not in CI) tests promote, rollback, dry run and automatic rollback against stub apps and a real nginx. Rollback: `promote` smoke-tests the new colour (`GET /api/v1/health` plus one `term-overlap` search, skipped when the app has no search) and switches back by itself on failure, `deploy.sh rollback` flips back (refuses if the old colour is not healthy), images are tagged by git sha and `deploy.sh prune-images` keeps the newest 3 (never one in use or named in `releases.env`), the database is not rolled back (HANDOFF item 35). `GET /api/v1/health` (no token, no queue slot, never cached) checks the process and `SELECT 1`. Running uvicorn alone (no nginx) has no rate limit. All kv-pairs routes require a token. Security headers and the CSP are added in `main.py` (`add_security_headers`); if the SPA needs a new external origin, add it to `CONTENT_SECURITY_POLICY`. Text inputs refuse NUL characters and integer ids are bounded to PostgreSQL's 32-bit range through the types in `backend/inputs.py` (`Text`, `DbId`); use them for new text or id parameters so bad input is a 422, not a 500. Static file serving resolves real paths so `../` cannot escape the static dir; keep it that way.
- **`backend/scripts/`** serves double duty: build-pipeline scripts run from the CLI **and** library modules imported by routers (`scripts.search`, `scripts.loading`, `scripts.preprocess`). `migrate_to_postgres.py` is the one-time copy from an old SQLite + `.npy` install.
  - Pipeline scripts (`preprocess.run`, `full_evaluation.main`, `finetune.train`, `finetune_eval.run_evaluation`) are thin orchestrators over small helpers; `eval_pipeline.py` is the evaluation flow shared by `evaluation.py` and `finetune_eval.py`.
  - `search.py` only keeps RRF fusion; the 8 original retrieval systems (term overlap, TF-IDF, BM25, hybrid, BM25+PRF, cosine, semantic rerank, RRF) and the two exact ones (`exact`, `exact-semantic-rrf`) are in `services/ranking.py`. The old in-memory versions live on as `tests/_legacy_search.py`, the oracle `tests/test_ranking.py` compares the SQL against.
  - `loading.py` holds cached loaders for the Arabic encoder (`arabic_encoder.py`, ONNX Runtime, no torch at query time), the CAMeL disambiguator and the lemmatizer; `embedding_store.py` upserts embeddings and `build_inverted_index.py` rewrites the index tables.
- **Data**: the corpus, BM25 index (`terms`, `postings`, `hadith_lengths`) and embeddings (`hadith_embeddings`, one Arabic float32 vector per hadith, no fixed dimension, exact search with no index) are PostgreSQL tables. `backend/data/` (gitignored) only holds JSON produced by the build and evaluation steps: `qrels_ungraded.json`, `build_manifest.json`, results.
- **Indexing vs. embedding text**: BM25 postings are built over preprocessed *matn only* (`Preprocessed_*_Matn` columns of `hadith_preprocessed`); embeddings cover the Arabic matn only, cleaned with `encoding_text` (diacritics removed), with no query or passage prefix. The model file is not in git: `python -m scripts.export_onnx` writes it to `backend/data/onnx/arabic` (or `ARABIC_MODEL_DIR`). Queries must be preprocessed the same way as documents (NLTK for English, CAMeL Tools for Arabic).
- **Evaluation flow**: `pooling.py` builds candidate pools -> annotators/`llm_grader.py` produce qrels -> `evaluation.py`, `full_evaluation.py`, `stats_tests.py` compute metrics; `finetune.py`/`finetune_eval.py` cover embedding fine-tuning.
- **Frontend** (`frontend/src`): `pages/`, `components/`, `layouts/`, `api/` (client), `hooks/`, `i18n/`, `types/`. The Dockerfile skips `tsc` (known pre-existing type errors) and runs only `vite build`, so `npm run build` may fail on type errors that Docker tolerates.

## Notes

- `requirements.txt` pins `torch==2.11.0`; the Dockerfile installs CPU torch first and strips that line.
- The Dockerfile has three stages: `frontend-builder` (Node), `python-builder` (compilers + venv in `/opt/venv`, NLTK/CAMeL data in `/opt/nltk_data` and `/opt/camel_tools_data`) and `runtime` (copies only those, no gcc/git, runs as uid 10001 `app`). `backend/data` is owned by `app`, so a fresh named volume is writable; a bind mount must be `chown 10001`. Anything the app downloads at runtime (e.g. HF models via `HF_HOME`) must land under that data dir.
- Image scanning: `docker build --pull -t hadith-search:hardened . && tools/scan-image.sh` runs trivy (fixable HIGH/CRITICAL CVEs fail) and dockle (WARN and above fail) from their Docker images. `.github/workflows/ci.yml` (job `docker-scan`) runs the same on pushes to main, PRs and weekly. The dockle suppressions (`settings.py` false positive, base-image apt cleanup) are commented in the script. If trivy fails on a new CVE, bump the pin in `requirements.txt` (or the base image) rather than ignoring it.
- CI (`ci.yml`): jobs lint, frontend, test and docker-scan run on the self-hosted runner (`tools/runner/`) when `tools/pick-runner.sh` finds it online and idle and the event is trusted, else on GitHub-hosted runners; fork PRs never use it (HANDOFF item 41).
- Dockerfile lint and metadata: hadolint runs as the `hadolint-docker` pre-commit hook and as the first step of the `docker-scan` job in `ci.yml` (rules suppressed on purpose are in `.hadolint.yaml`: DL3008 apt pins, DL3013 unpinned CPU torch). The image has a `HEALTHCHECK` (GET `/openapi.json`, 60s start period), runs as numeric `10001:10001`, and carries OCI labels; pass `--build-arg REVISION=$(git rev-parse HEAD) --build-arg CREATED=$(date -u +%FT%TZ)` to fill `revision` and `created` (CI does).
- Dependency floor set by the scans: `nltk>=3.10.3`, `transformers 5.x`, `starlette 1.x`, `camel_tools 1.6.0` (its `numpy>=2` requirement moved the pin from 1.26.4 to 2.5.3).
- README setup examples use Windows-style backslashes (`scripts\build_all.py`); on Linux/WSL use forward slashes.
- Embedding generation wants a CUDA GPU (~12GB VRAM); pre-built embeddings can be dropped into `backend/data/`.
- pg_trgm (HANDOFF item 43): `hadith_exact_text` and the trigram GIN indexes are additive. `create_all` does not alter existing tables, so existing databases need `tools/migrate_trgm.sh` (`--dry-run` first; it uses `CREATE INDEX CONCURRENTLY IF NOT EXISTS`). A `RetrievalSystem` with `keyword=True` (bm25, exact) gets `did_you_mean` when it finds nothing; the search router leaves the field out when empty. Do not add raw SQL strings for the trigram parts; use `func`, `op('%')` and `text()` only where the ORM has no form.
