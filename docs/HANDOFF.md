# Handoff: changes since Marawan's last commit

Marawan's last commit is `93a4ff9` ("Align matn embeddings, training passages, and evaluation"). Everything below was added on top of it in 14 commits (the last 4 are the REST API, items 16 and 17): about 130 files. All 354 tests pass. The work sits on the branch `feat/rest-api-v1`, which builds on `chore/dockerfile-hardening` and `chore/precommit-hooks`, and has not been pushed.

Each change has the same three lines: which files, why this is the normal way to do it, and what you get out of it.

## Read this first

1. **The Jina key was renamed.** The code used to read `JINA_API_KEY2`. It now reads `JINA_API_KEY`. Rename it in your `.env` and in the server settings, or reranking stops working.
2. **The database no longer enforces foreign keys.** The old code turned on `PRAGMA foreign_keys`. We dropped it on purpose. The links between tables are still declared in the models, but SQLite will not block a bad link or cascade deletes. Tables are created with `create_all`: it adds missing tables and never changes existing ones, and there are no migrations.
3. **The diff looks bigger than the real change.** Every Python file was reformatted to one style, so many lines only moved or wrapped differently. Read the commit messages first, then the files.
4. **Libraries were upgraded** to fix known security holes: numpy 1.26 to 2.5, transformers 4.43 to 5.17, starlette 0.52 to 1.3, nltk 3.9 to 3.10. The tests pass, but I could not test saved index files (`.pkl`) built with the old numpy, because this copy has no real data. If loading fails, rebuild them. A full fine-tuning run and the real E5 model were also not re-run.
5. **Every API URL changed, and there are no old aliases.** Everything now lives under `/api/v1`, the frontend is updated, and anything else that calls the API (scripts, bookmarks) must move. The map is in item 16 below. Sign-in also works differently: set `AUTH_SECRET` (32+ characters) in `.env`, and use the same value on every server.
6. **The Docker container no longer runs as root.** It runs as user 10001. If you mount a folder for `backend/data` instead of using a Docker volume, run `chown 10001 <folder>` on it once.

`uvicorn main:app` still starts the server the same way, and the scripts still run from the command line.

## Database and async

### 1. Database through models instead of hand-written SQL

**Files:** `backend/database.py`, `backend/models/orm.py` (new), `backend/routers/annotation.py`, `auth.py`, `kv_pairs.py`, `backend/scripts/data_creation.py`, `kv_generator.py`, `build_embeddings.py`

**Why it's best practice:** SQL written by hand in strings is easy to get wrong and open to injection. Model classes (SQLAlchemy) describe each table once, and the library builds the queries. A test fails if anyone adds raw SQL again.

**Benefit:** Fewer bugs, safer queries, and one place to read what each table looks like.

### 2. Async database access

**Files:** `backend/database.py`, `backend/routers/annotation.py`, `auth.py`, `kv_pairs.py`, `backend/startup.py`, `backend/scripts/kv_generator.py`

**Why it's best practice:** A web server answers many people at once. If a request waits on the database in the normal blocking way, everyone behind it waits too. With async (`aiosqlite`), the server does other work while the database answers. Each request gets its own session (`get_db_session`) that is always closed, even on errors.

**Benefit:** Requests no longer block each other on database calls. Command-line scripts keep a plain blocking helper (`get_sync_session`, `read_hadiths_df`), and `kv_generator.py` wraps the async calls with `asyncio.run`, so nothing about how you run scripts changed.

### 3. Async tests

**Files:** `tests/conftest.py`, `tests/test_routers.py`, `test_search_api.py`, `test_app_factory.py`, `test_startup.py`, `test_services.py`, `pyproject.toml`

**Why it's best practice:** Async code has to be tested as async. 32 tests use `async def` and call the real app through `httpx.AsyncClient`, with no server started and no network. pytest-asyncio runs in auto mode, so no decorator is needed on each test.

**Benefit:** The tests exercise the same path a browser request takes, including the database session handling.

## Tests

### 4. Test setup, speed and naming

**Files:** `tests/` (22 test files), `tests/conftest.py`, `pyproject.toml`, `requirements-dev.txt`

**Why it's best practice:** Tests need no real data, internet or GPU. `conftest.py` builds a 3-hadith corpus, a small search index, fake embeddings, a fake E5 model and a fake Jina API. Every test gets its own temporary database file, so `pytest-xdist` can run them in parallel (`-n auto`) without clashing. Fixtures and helpers start with `_` so it is clear they are not called directly. The virtual environment is named `.venv` and uses Python 3.12, the same as the Dockerfile.

**Benefit:** The full suite takes under half a minute and runs the same on any machine. Run it with `.venv/bin/python -m pytest`.

### 5. Snapshot tests

**Files:** `tests/_snapshot.py`, `tests/test_snapshots.py`, `tests/snapshots/` (29 saved files)

**Why it's best practice:** Before the big cleanup, the statistics and table output of the evaluation scripts were saved to files. Tests compare fresh output against them. This is the standard safety net for restructuring code that has no tests yet.

**Benefit:** We could split the long evaluation scripts into small functions and prove the numbers did not change. If a number changes on purpose, run the tests with `UPDATE_SNAPSHOTS=1` to save the new version.

### 6. Checks that the tests are actually good

**Files:** `tools/mutation.sh`, `tools/mutation.pyproject.toml`, `tests/test_mutation_killers.py`; vulture settings in `pyproject.toml`

**Why it's best practice:** Line coverage only says code ran. Mutation testing (mutmut) makes small deliberate breaks in the code and checks that some test fails. Vulture lists code nobody calls.

**Benefit:** We found weak spots and wrote tests for them. Vulture reports no dead code now.

## Code structure

### 7. Settings in one place, and an app factory

**Files:** `backend/features.py` (new), `backend/main.py`, `.env.example` (new)

**Why it's best practice:** `main.py` used to read environment variables and build the app at import time. Now `create_app()` builds it and takes its settings as arguments, so tests can build an app with any combination of switches. Switches for optional parts (annotation, key-value pairs, benchmark, search, dense retrieval, Jina reranking, fine-tuned adapter) live in `features.py`. `APP_MODE` still works as a preset, and any `FEATURE_<NAME>` variable overrides it.

**Benefit:** You can run a light version (annotation only) or the full search stack without editing code. `.env.example` lists every variable without holding real keys.

### 8. Retrieval methods as a list

**Files:** `backend/services/` (new: `retrieval.py`, `results.py`, `agreement.py`), `backend/routers/search.py` (284 lines down to 37)

**Why it's best practice:** Each search method is one entry in a registry that says which switches it needs. The router builds one `POST /search/<name>` route per enabled entry. What a route needs (indices, model) is passed in (`SearchContext`, FastAPI `Depends`) and not imported as a global. The old functions in `scripts/search.py` were kept and the new code wraps them, so old callers keep working while code moves over step by step.

**Benefit:** Adding a search method means adding one entry, not editing a big function. Tests can swap in fake indices and a fake model.

### 9. Long scripts split into short functions

**Files:** `backend/scripts/preprocess.py`, `full_evaluation.py`, `finetune.py`, `finetune_eval.py`, `evaluation.py`, `profile.py`, `stats_tests.py`, `data_creation.py`, `build_embeddings.py`, `llm_validation.py`, `build_all.py`, and the new `eval_pipeline.py`

**Why it's best practice:** A function that does one thing is easy to read and to test. Ruff now fails any function with more than 10 branches. The steps shared by `evaluation.py` and `finetune_eval.py` moved into `eval_pipeline.py`, so they are written once.

**Benefit:** Bugs are easier to find, and there is less copy-pasted code.

### 10. Shorter import paths

**Files:** `backend/models/__init__.py`, `backend/routers/__init__.py`, `backend/scripts/__init__.py`, `backend/lazy_exports.py`

**Why it's best practice:** Code imports from the package (`from models import Hadith`) and does not need to know which file holds the class.

**Benefit:** Files can be moved or split later without breaking every import. Heavy modules still load only when used, so annotation mode never loads the search stack.

## Tooling and safety

### 11. Style checks and pre-commit hooks

**Files:** `pyproject.toml`, `.pre-commit-config.yaml`, `.gitignore`, `requirements-dev.txt`

**Why it's best practice:** Ruff and black decide spacing and import order, so reviews only show real changes. Hooks run before every commit and stop mistakes at the earliest point: gitleaks looks for passwords and API keys, and the other hooks run ruff, black, vulture and Hadolint, plus checks for large files and merge leftovers. `.gitignore` had two entries joined on one line (`*.pyc` and `mutants/`), which we fixed.

**Benefit:** A leaked key never reaches the repository history, where it would be hard to remove. Run `.venv/bin/pre-commit install` once after cloning.

### 12. Smaller, safer Docker image

**Files:** `Dockerfile`, `.dockerignore`

**Why it's best practice:** The Dockerfile has three stages, so compilers and build tools stay out of the final image. The app runs as a normal user instead of root, so a break-in gets less. Downloaded packages are cached between builds, and slow steps come first so small code edits rebuild fast. `.dockerignore` was excluding `docker-entrypoint.sh`, which the Dockerfile needs, and was not skipping `.venv` or the tests.

**Benefit:** Faster rebuilds, a smaller attack surface, and the CAMeL Arabic data now really downloads at build time (the old step always failed quietly).

### 13. Security scans and library upgrades

**Files:** `tools/scan-image.sh`, `.github/workflows/docker-scan.yml`, `requirements.txt`

**Why it's best practice:** Trivy checks the image for known security holes and dockle checks it against container good-practice rules. The workflow runs both on every push, every pull request and every Monday, because new holes are found without any code change.

**Benefit:** The first scan found 46 fixable problems. After the library upgrades (see "Read this first") there are none. Run it locally with `docker build --pull -t hadith-search:hardened . && tools/scan-image.sh`.

### 14. Dockerfile lint, health check, labels

**Files:** `Dockerfile`, `.hadolint.yaml`

**Why it's best practice:** Hadolint reads the Dockerfile for mistakes. A health check lets Docker see when the app has stopped answering. Labels record what the image is and which commit built it.

**Benefit:** Docker can restart a hung container by itself. Anyone holding an image can tell which commit it came from.

### 15. Notes for the next person

**Files:** `CLAUDE.md`, `docs/WIKI.md` (key rename), this file

**Why it's best practice:** Setup steps and rules written down in the repo stay with the code.

**Benefit:** A new developer, or an AI assistant, can start work without asking what the commands are.

## REST API

### 16. Resource URLs under /api/v1

**Files:** `backend/rest.py` (new), `backend/tokens.py` (new), `backend/routers/` (`auth.py`, `annotation.py`, `kv_pairs.py`, `benchmark.py`, `search.py` rewritten; `hadiths.py` and `root.py` new), `backend/main.py`, `frontend/src/api/`, `frontend/src/pages/`, `tests/`

**Why it's best practice:** A REST API names things (annotators, assignments, searches), not actions, and uses the HTTP verb for the action. Reads use GET so browsers and proxies can cache them. Answers use the standard status codes: 201 for created (with a `Location` header), 401 without a valid token, 403 for someone else's data, 404 for missing, 409 for a duplicate username, 422 for bad input. Error bodies use the standard `application/problem+json` shape. Each response carries `_links` to related URLs, and `GET /api/v1` lists the entry points, so a client can follow links instead of hard-coding paths.

**Benefit:** Repeat searches and benchmark reads come from the browser cache or get a 304 (the `ETag` is a hash of the body). Timing moved from the body into the `Server-Timing` header, because a changing number in the body would break that. Nothing is kept in server memory between requests, so more servers can be added behind a load balancer.

| Old | New |
| --- | --- |
| `POST /search/{method}` (JSON body) | `GET /api/v1/searches?q=&method=&lang=&grade_filter=&book_filter=` |
| `GET /hadith/{id}` | `GET /api/v1/hadiths/{id}` |
| `POST /auth/signup`, `/auth/signin` | `POST /api/v1/annotators`, `POST /api/v1/tokens` |
| `GET /auth/me`, `POST /auth/signout` | `GET /api/v1/annotators/me` (no sign-out call: the client drops the token) |
| `GET /annotation/queries`, `/annotation/{id}/current` | `GET /api/v1/assignments`, `/api/v1/assignments/{id}` |
| `POST /annotation/{id}/label` | `PUT /api/v1/assignments/{id}/labels/{hadith_id}` with `{"label": 0-2}` |
| `POST /annotation/{id}/navigate?index=` | `PUT /api/v1/assignments/{id}/progress` with `{"index": n}` |
| `/kv-pairs/stats`, `POST /kv-pairs/{id}/verify`, `/kv-pairs/export` | `/api/v1/kv-pairs/statistics`, `PATCH /api/v1/kv-pairs/{id}`, `?status=verified` |
| `/benchmark/*` | `/api/v1/benchmark/*` |

Behavior that differs from before:

- Saving a label no longer moves the cursor. The frontend calls the progress endpoint after it.
- A query that is not assigned to you is now 404, not 403.
- A missing benchmark file is now 404, not a 200 with `{"error": ...}`.
- The `mode` parameter of `/benchmark/finetuned` accepts only letters, digits and underscores. It used to build a file path from raw input.
- The `response_time_ms` field is gone from search results; read the `Server-Timing` header.

### 17. Signed tokens instead of a sessions table

**Files:** `backend/tokens.py`, `backend/routers/auth.py`, `backend/models/orm.py`, `backend/database.py`, `.env.example`

**Why it's best practice:** The old code stored each login in a database table and looked it up on every request. A signed token (JWT) carries the annotator id and an expiry, and any server that knows `AUTH_SECRET` can check it. That is what makes it possible to run more than one server.

**Benefit:** No database read per request for auth. The cost: a token cannot be cancelled before it expires (12 hours by default, `AUTH_TOKEN_TTL_MINUTES`), and if `AUTH_SECRET` is unset the server makes a random one, so logins break on restart. The old `auth_sessions` table is no longer created; existing rows are ignored.

**Still open:** the `/kv-pairs` routes have no login check, as before. Running several servers also needs a database they all share; the SQLite file is per machine.

## Quick start after pulling

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/pre-commit install
.venv/bin/python -m pytest
cp .env.example .env    # then fill in JINA_API_KEY and anything else you need
```

The commit list, oldest first: `360399f` pre-commit hooks, `4d800cf` backend rewrite, `55d9704` tests and tooling, `92759cf` Docker stages and non-root user, `4b963eb` build caching, `8535ee5` library upgrades, `ea50b33` image scans, `56d8081` Hadolint, health check and labels, `5964375` and `1306ddc` this note, `1981518` REST routes and tokens, `65f4c77` tests for them, `111fb0d` frontend on the new URLs, `b88692b` docs for the REST changes. The last edit to this note is the commit after those.
