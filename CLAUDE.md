# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Bilingual (English/Arabic) hadith search engine plus an annotation/benchmark platform for evaluating retrieval systems. Backend is FastAPI + SQLite + pickled BM25 indices + `.npy` E5 embeddings; frontend is React 19 / TypeScript / Tailwind / Vite. Deeper docs live in `docs/` (`ARCHITECTURE.md`, `EVALUATION.md`, `FINE_TUNING.md`, `CORPUS_DECISIONS.md`, `WIKI.md`).

## Commands

Run backend commands from `backend/` (use the repo-root `.venv`) (paths like `data/` and `scripts.*` imports are relative to it).

```bash
# Setup (after pip install -r ../requirements.txt; mkdir backend/data)
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

docker compose up --build                      # Dockerfile builds frontend and serves it from FastAPI
```

### Lint / format

```bash
.venv/bin/ruff check backend tests   # add --fix to autofix (E, F, W, I rules; E402 ignored in a few scripts on purpose)
.venv/bin/black backend tests        # line length 100; config in pyproject.toml
```

Copy `.env.example` to `.env` for local settings (Jina key is `JINA_API_KEY`).

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

Tests live in repo-root `tests/` (config in `pyproject.toml`, `pythonpath = backend`). Nothing needs real data: `tests/conftest.py` builds a 3-hadith corpus, inverted index, embeddings, a fake E5 model, a fake Jina API, and a per-test temp SQLite DB (so xdist is safe). Conventions: fixtures and test helpers start with `_`; router tests are async (`pytest-asyncio` auto mode + `httpx.AsyncClient` over ASGI). Tests marked `nlp` need real NLTK data and skip without it.

The DB layer is SQLAlchemy 2.0 ORM only (no raw SQL anywhere; `tests/test_database.py` greps for it). Models live in `backend/models/orm.py`; `database.py` gives `get_session()` (async, aiosqlite: `async with get_session() as session:`), `get_sync_session()`/`read_hadiths_df()`/`get_hadith_row()` for scripts, and `init_*_table()` helpers that call `create_all` (no Alembic, no migrations; FK pragma intentionally not set). `scripts/kv_generator.py` wraps async calls in `asyncio.run`.

## Architecture

- **`backend/features.py`** — feature toggles (frozen `Features`): annotation, kv_pairs, benchmark, search, dense_retrieval (E5), cross_encoder (Jina), eager_model, finetuned_adapter_path. `load_features()` precedence: `FEATURE_<NAME>` env > `APP_MODE` preset > default (cross_encoder defaults to `bool(JINA_API_KEY)`). Presets: `annotation` (no search stack; the Docker default), `search` (lazy E5 model), `research` (eager E5 model).
- **`backend/main.py`** — `create_app(features=None, static_dir=None)` factory (`app = create_app()`); routers are included per feature flag, and `startup.py` creates tables and preloads indices only when search is enabled. Also serves the built SPA (`STATIC_DIR`, `../static`, or `../frontend/dist`) with an SPA fallback route, so the catch-all `/{full_path}` is registered last.
- **`backend/services/`** — decoupled logic behind the routers: `retrieval.py` (`SearchContext` of injected accessors + `SYSTEMS` registry of retrieval strategies, each declaring which feature flags it needs; strangler-fig facade over the legacy `scripts.search` functions, which offline scripts still call), `results.py` (`build_results` filtering/mapping), `agreement.py` (inter-annotator kappa/Spearman/raw agreement, pure functions).
- **`backend/routers/`** — `search.py` (`make_search_router(features)` serves `GET /api/v1/searches?method=<slug>` for the enabled systems; `get_search_context` is the `Depends` seam tests override), `annotation.py` + `auth.py` (relevance-judgment platform), `benchmark.py`, `kv_pairs.py`, `hadiths.py`, `root.py` (`GET /api/v1` link index). `database.py` initializes the annotation/kv tables in the same SQLite file as the corpus.
- **REST conventions** (`backend/rest.py`, `backend/tokens.py`) — every route is under `/api/v1` and named after a resource. Errors are RFC 9457 `application/problem+json` (installed by `install_error_handlers`; raise `HTTPException` and it is formatted for you). Cacheable reads return `json_response(request, body, max_age=...)`, which adds `Cache-Control` and a content-hash `ETag` and answers `If-None-Match` with 304, so bodies must be deterministic (timing goes in the `Server-Timing` header, not the body). Responses carry `_links`; add a link to `routers/root.py` when adding a resource. Auth is a stateless HS256 JWT (`AUTH_SECRET`, 32+ chars, same on every server; no session table, so no server-side revocation). New headers the browser must read go in `EXPOSED_HEADERS`. Rate limiting is `backend/ratelimit.py` (SlowAPI: `RATE_LIMIT_DEFAULT` for everything, `RATE_LIMIT_AUTH` via `@limiter.limit(auth_limit)` on sign-in/sign-up; 429 is problem+json with `Retry-After`). A decorated route needs a `request: Request` parameter, and test apps must call `install_rate_limiting(app)`; tests turn the limiter off in an autouse fixture. All kv-pairs routes require a token. Security headers and the CSP are added in `main.py` (`add_security_headers`); if the SPA needs a new external origin, add it to `CONTENT_SECURITY_POLICY`. Static file serving resolves real paths so `../` cannot escape the static dir; keep it that way.
- **`backend/scripts/`** serves double duty: build-pipeline scripts run from the CLI **and** library modules imported by routers (`scripts.search`, `scripts.loading`, `scripts.preprocess`).
  - Pipeline scripts (`preprocess.run`, `full_evaluation.main`, `finetune.train`, `finetune_eval.run_evaluation`) are thin orchestrators over small helpers; `eval_pipeline.py` is the evaluation flow shared by `evaluation.py` and `finetune_eval.py`.
  - `search.py` implements the 11 retrieval systems (term overlap, TF-IDF, BM25, hybrid, BM25+PRF, cosine, semantic rerank, RRF, cross-encoder rerank, final pipeline).
  - `loading.py` holds cached loaders (indices, doc lengths, hadith IDs, DataFrame, E5 model) — use these rather than loading artifacts directly.
- **Data artifacts in `backend/data/`** (gitignored, produced by the build pipeline): `hadiths.db`, `{english,arabic}_inverted_index.pkl`, `document_lengths.pkl`, `{english,arabic}_embeddings.npy`, `hadith_ids.npy`, `qrels_ungraded.json`, `build_manifest.json`. Embeddings and `hadith_ids.npy` must stay row-aligned.
- **Indexing vs. embedding text**: BM25 indices are built over preprocessed *matn only* (`Preprocessed_*_Matn` columns); embeddings cover chapter title + matn using E5 `passage:` / `query:` prefixes. Queries must be preprocessed the same way as documents (NLTK for English, CAMeL Tools for Arabic).
- **Evaluation flow**: `pooling.py` builds candidate pools -> annotators/`llm_grader.py` produce qrels -> `evaluation.py`, `full_evaluation.py`, `stats_tests.py` compute metrics; `finetune.py`/`finetune_eval.py` cover embedding fine-tuning.
- **Frontend** (`frontend/src`): `pages/`, `components/`, `layouts/`, `api/` (client), `hooks/`, `i18n/`, `types/`. The Dockerfile skips `tsc` (known pre-existing type errors) and runs only `vite build`, so `npm run build` may fail on type errors that Docker tolerates.

## Notes

- `requirements.txt` pins `torch==2.11.0`; the Dockerfile installs CPU torch first and strips that line.
- The Dockerfile has three stages: `frontend-builder` (Node), `python-builder` (compilers + venv in `/opt/venv`, NLTK/CAMeL data in `/opt/nltk_data` and `/opt/camel_tools_data`) and `runtime` (copies only those, no gcc/git, runs as uid 10001 `app`). `backend/data` is owned by `app`, so a fresh named volume is writable; a bind mount must be `chown 10001`. Anything the app downloads at runtime (e.g. HF models via `HF_HOME`) must land under that data dir.
- Image scanning: `docker build --pull -t hadith-search:hardened . && tools/scan-image.sh` runs trivy (fixable HIGH/CRITICAL CVEs fail) and dockle (WARN and above fail) from their Docker images. `.github/workflows/docker-scan.yml` runs the same on pushes to main, PRs and weekly. The dockle suppressions (`settings.py` false positive, base-image apt cleanup) are commented in the script. If trivy fails on a new CVE, bump the pin in `requirements.txt` (or the base image) rather than ignoring it.
- Dockerfile lint and metadata: hadolint runs as the `hadolint-docker` pre-commit hook and as the first step of `docker-scan.yml` (rules suppressed on purpose are in `.hadolint.yaml`: DL3008 apt pins, DL3013 unpinned CPU torch). The image has a `HEALTHCHECK` (GET `/openapi.json`, 60s start period), runs as numeric `10001:10001`, and carries OCI labels; pass `--build-arg REVISION=$(git rev-parse HEAD) --build-arg CREATED=$(date -u +%FT%TZ)` to fill `revision` and `created` (CI does).
- Dependency floor set by the scans: `nltk>=3.10.3`, `transformers 5.x`, `starlette 1.x`, `camel_tools 1.6.0` (its `numpy>=2` requirement moved the pin from 1.26.4 to 2.5.3). Pickled indices built under numpy 1.x were not re-checked against numpy 2 (no real data in the dev checkout); rebuild or verify them before deploying.
- README setup examples use Windows-style backslashes (`scripts\build_all.py`); on Linux/WSL use forward slashes.
- Embedding generation wants a CUDA GPU (~12GB VRAM); pre-built embeddings can be dropped into `backend/data/`.
